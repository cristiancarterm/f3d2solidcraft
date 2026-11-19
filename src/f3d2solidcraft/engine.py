"""A persistent SolidCraft modelling session over MCP stdio."""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Self

from f3d2solidcraft.tools import long_path


class EngineError(RuntimeError):
    """The engine died, hung, or answered with a protocol error."""


# How long one body's measurement gets. A body that goes over it is not a slow design but a
# pathological one: measured on `Hypercube Evo+.f3z`, 56 of its original's 74 bodies answer in 0.05
# to 6 s each while `NEMA17_Stepper_Motor+Readscrew_F01 (3)` does not answer in 610 s, and its two
# siblings of the same definition answer in seconds (spec section 98).
BODY_TIMEOUT_SECONDS = 45.0


class BodyUnmeasurable(EngineError):
    """One body did not answer in time. The engine is gone with it, and the body is named."""

    def __init__(self, body: str, said: str) -> None:
        super().__init__(said)
        self.body = body


class CommandRefused(EngineError):
    """The engine refused a command; the design is unchanged."""

    def __init__(self, command: str, message: str) -> None:
        super().__init__(f"{command}: {message}")
        self.command = command
        self.message = message


# Advice SolidCraft appends to a tool result's text, after the JSON. 0.22.1 adds one to every
# single result ("this is a headless session because the SolidCraft app was not found…"), which is
# fair enough for a person reading it and fatal for `json.loads`. The answers themselves are
# unchanged from 0.16.2 — same tools, same keys, same shapes — so the whole 0.16.2-to-0.22.1 break
# was this trailing sentence making every parse fail and `call` hand back the raw string instead.
_ADVICE = re.compile(r"\s*Note: [^\n]*?\bheadless session\b.*\Z", re.DOTALL)


def _without_advice(text: str) -> str:
    return _ADVICE.sub("", text).strip()


def _answer(text: str) -> Any:
    """A tool's answer: its JSON, ignoring any prose the engine appends after it.

    Parsed with `raw_decode` rather than `loads` so it does not matter what follows the JSON or
    which version wrote it — the first value is the answer. Text that is not JSON at all comes back
    as text, as it always did, with the advice taken off.
    """
    stripped = text.lstrip()
    try:
        value, end = json.JSONDecoder().raw_decode(stripped)
    except ValueError:
        return _without_advice(text) or text
    # Only the advice may follow the answer. Without this, prose that merely starts like JSON comes
    # back as a truncated value: raw_decode reads "12 bodies removed" as the number 12 and drops the
    # rest, where `loads` used to fail and hand back the sentence.
    if _without_advice(stripped[end:]):
        return _without_advice(text) or text
    return value


# How long one engine call may take before the session is given up as out of step. 120 s was the
# value until a real design of the user's measured past it: importing the 38.6 MB STEP of
# `Hypercube Evo+.f3z`'s original — 104 B-rep bodies — takes **195 s** on this machine, and the
# conversion died on a call that was working. The point of the limit is to notice an engine that
# will never answer, not to cap a call that is simply big, so it is ten minutes now: still far
# under the 1800 s an all-bodies `measure` of that design failed to finish in, and far over any
# call the corpus takes (spec section 98).
CALL_TIMEOUT_SECONDS = 600.0


_TRACE = bool(os.environ.get("F3D2SC_TRACE"))


class Engine:
    def __init__(
        self, cli: Path, allow_paths: list[Path] | None = None, timeout: float = CALL_TIMEOUT_SECONDS
    ) -> None:
        self.cli = Path(cli)
        # Spelt out in full: the engine refuses a Windows 8.3 short name in an allowed path too.
        self.allow_paths = [long_path(p) for p in (allow_paths or [])]
        self.timeout = timeout
        # Whether a whole-design reading must enumerate placements rather than definitions. False
        # until `cli` proves otherwise: it is the strict reading, and on a design the engine cannot
        # measure in one call it is not available at all (spec section 98).
        self.read_placements = False
        # Bodies this engine has already been proved unable to measure, so no reading asks again.
        # `cli` accumulates them across its retries and the report names every one (section 98).
        self.unmeasurable: set[str] = set()
        self.mutations = 0  # successful commands run, not undo steps (see undo_depth)
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._next_id = 0

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def start(self) -> None:
        args = [str(self.cli), "mcp"]
        for path in self.allow_paths:
            args += ["--allow-path", str(path)]
        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            # The engine speaks UTF-8; Windows would read the pipe in its ANSI code page, which
            # garbles an accented body name or stops the reader on a byte that code page lacks.
            encoding="utf-8",
            bufsize=1,
        )
        self._lines = queue.Queue()
        threading.Thread(target=self._pump, args=(self._proc.stdout, self._lines), daemon=True).start()
        try:
            self._rpc(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "f3d2solidcraft", "version": "0"},
                },
            )
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _pump(stdout: Any, lines: queue.Queue[str | None]) -> None:
        try:
            for line in stdout:
                lines.put(line)
        except (OSError, ValueError):
            pass
        lines.put(None)

    def _send(self, message: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise EngineError("engine is not running")
        try:
            self._proc.stdin.write(json.dumps(message) + "\n")
            self._proc.stdin.flush()
        except OSError:
            self.close()
            raise EngineError("solidcraft-cli mcp exited") from None

    def _rpc(self, method: str, params: dict, timeout: float | None = None) -> dict:
        self._next_id += 1
        rid = self._next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        limit = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + limit
        while True:
            remaining = deadline - time.monotonic()
            try:
                if remaining <= 0:
                    raise queue.Empty
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                # The session is now out of step with the engine: never reuse it.
                self.close()
                raise EngineError(f"{method}: no answer within {limit:.0f} s") from None
            if line is None:
                self.close()
                raise EngineError("solidcraft-cli mcp exited")
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self.close()
                raise EngineError(f"{method}: engine wrote non-JSON output: {line[:200]!r}") from None
            if message.get("id") != rid:
                continue
            if "error" in message:
                raise EngineError(f"{method}: {message['error']}")
            return message["result"]

    def call(self, tool: str, args: dict | None = None, timeout: float | None = None) -> Any:
        # `F3D2SC_TRACE=1` prints one line per engine call with what it cost. Added for the
        # question section 98 could not answer any other way — which call of a 554-feature design
        # was the one taking minutes — and kept because that question recurs: the engine is the only
        # part of a conversion whose cost is not ours to read off the code.
        if _TRACE:
            started = time.monotonic()
            try:
                return self._answer(tool, args, timeout)
            finally:
                print(f"[engine] {time.monotonic() - started:8.2f} s  {tool}", file=sys.stderr, flush=True)
        return self._answer(tool, args, timeout)

    def _answer(self, tool: str, args: dict | None = None, timeout: float | None = None) -> Any:
        result = self._rpc("tools/call", {"name": tool, "arguments": args or {}}, timeout)
        text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        if result.get("isError"):
            raise CommandRefused(tool, _without_advice(text))
        return _answer(text)

    def execute(self, command: str, params: dict | None = None) -> Any:
        """Run a command. One that changes the design is one undo step; a query (`expr.evaluate`) is none."""
        try:
            out = self.call("execute", {"command": command, "params": params or {}})
        except CommandRefused as exc:
            raise CommandRefused(command, exc.message) from None
        self.mutations += 1
        return out

    def batch(self, commands: list[dict]) -> list[Any]:
        """Run several commands in one round trip; all or nothing."""
        try:
            out = self.call("batch", {"commands": commands, "rollback": True})
        except CommandRefused as exc:
            try:
                failed = json.loads(exc.message)["failed"]
            except (json.JSONDecodeError, KeyError, TypeError):
                raise exc from None
            raise CommandRefused(failed["command"], failed["error"]) from None
        self.mutations += len(commands)
        return out["results"]

    def measure(self) -> dict:
        return self.call("measure", {})

    def body_names(self) -> list[str]:
        """Which bodies the design has, without measuring them.

        `measure` recomputes volume, area, faces, edges, vertices and centre of mass for every
        body, and the engine only caches that until the geometry changes. Measured on the corpus's
        heaviest file, one such recomputation costs 24 s while this answers in 2 ms, so a caller
        that only needs to know *which* bodies are there asks this instead.
        """
        design = self.call("inspect_design", {"measure": False, "sketches": False})
        return [str(b.get("name")) for b in design.get("bodies") or []]

    def topology_of(self, body: str) -> dict:
        """One body's faces, edges and vertices, bounded the same way a measurement is.

        The same pathological geometry that will not be measured will not be inspected either:
        measured on `Hypercube Evo+.f3z`, `body_topology` on
        `NEMA17_Stepper_Motor+Readscrew_F01 (3)` does not answer in **610 s**, which killed a
        conversion that had already walked 336 calls. A body in `unmeasurable` is refused outright
        rather than asked again, and a body that goes over the limit joins that set through the
        caller's retry (spec section 98).
        """
        try:
            return self.call("body_topology", {"body": body}, timeout=BODY_TIMEOUT_SECONDS)
        except BodyUnmeasurable:
            raise
        except EngineError as exc:
            raise BodyUnmeasurable(body, f"the topology of {body!r} could not be read: {exc}") from None

    def placement_count(self) -> int:
        """How many body **placements** the design holds, read off the component graph.

        `inspect_design` lists one body per definition and an all-bodies `measure` one per
        placement, and the two differ wherever a component is placed more than once. This counts
        the second from the first: every component's bodies, once per path that reaches it.

        It is **free** and it is exact. Measured against the ground truth on the corpus's largest
        package, `corpus-09.f3z`: the graph gives 494 in 0.00 s and the all-bodies measure
        gives 494 in 1.74 s, against 462 definitions. On `Hypercube Evo+.f3z` it gives 177 against
        74 definitions (spec section 98).
        """
        return sum(self.placements_by_body().values())

    def placements_by_body(self) -> Counter[str]:
        """How many times each body is placed, by name, from one `component.list`.

        The same walk as `placement_count`, kept per body so that a count which drops can say
        **which** bodies lost placements. It is what lets the conversion check the design it saves
        against the one it measured without measuring it again: a component placed N times whose
        bodies are moved into a component placed once leaves the same names and N - 1 fewer
        placements of each, and only the component graph shows that.
        """
        components = {c["id"]: c for c in self.execute("component.list", {})["components"]}
        placed: dict[int, int] = {}

        def walk(cid: int, times: int) -> None:
            placed[cid] = placed.get(cid, 0) + times
            for child in components[cid].get("children") or []:
                walk(child["component"], times)

        children = {ch["component"] for c in components.values() for ch in (c.get("children") or [])}
        for root in (cid for cid in components if cid not in children):
            walk(root, 1)
        bodies: Counter[str] = Counter()
        for cid, component in components.items():
            for body in component.get("bodies") or []:
                bodies[str(body)] += placed.get(cid, 0)
        return bodies

    def body_readings(self, names: list[str] | None = None) -> list[dict]:
        """Every body's measurement, assembled one body at a time.

        `measure` with no body asks the engine for all of them at once, and that call does not
        scale. Measured on `Hypercube Evo+.f3z`, a real design of the user's whose original holds 74
        imported B-rep bodies: it does **not answer in 1800 s**, while the same bodies measured one
        at a time cost about 0.1 s each — nine seconds in all, a factor of over two hundred. On the
        corpus's largest package, `corpus-09.f3z`, 462 bodies one at a time also beat the
        single call (under a second against 1.7 s). So this is the cheaper path everywhere and the
        only possible one on a big assembly (spec section 98).

        What it does **not** do is expand placements: `measure` with no body lists one body per
        placement — 494 on that package against the 462 definitions `inspect_design` lists — and a
        per-body call answers only for the definition it names. Section 90's defect was not the
        enumeration but the **mismatch**: a reference list of 462 against a design reading of 494
        left 32 bodies unpaired and told a correct conversion it had 32 extra. Both sides of a
        comparison therefore read through this, and the comparison is per definition.
        """
        if self.read_placements and names is None:
            # The design places a body more than once somewhere, and only this call enumerates
            # those placements. It is the strict reading, and `cli` has already proved in a
            # throwaway session that this engine can answer it (spec section 98).
            return list(self.call("measure", {}).get("bodies") or [])
        wanted = self.body_names() if names is None else names
        readings: list[dict] = []
        for name in wanted:
            if name in self.unmeasurable:
                continue  # already proved it does not answer; the report says so
            try:
                answered = self.call("measure", {"body": name}, timeout=BODY_TIMEOUT_SECONDS)
            except EngineError as exc:
                raise BodyUnmeasurable(name, f"the body {name!r} could not be measured: {exc}") from None
            readings.extend(answered.get("bodies") or [])
        return readings

    def total_volume(self, readings: list[dict] | None = None) -> float:
        """The volume `measure`'s own total carries, summed from the bodies instead.

        An open body has no volume and contributes nothing, which is what the engine's total does
        with it too.
        """
        rows = self.body_readings() if readings is None else readings
        return sum(float(b["volume_mm3"]) for b in rows if b.get("volume_mm3") is not None)

    def timeline_ids(self) -> list[int]:
        """The ids of the features in the timeline, in order.

        The witness `undo_depth` cannot be: **the engine's undo stack caps at 200 steps** (measured:
        259 boxes in one design leave `undo_depth` reading 200 for the last 59), so past that an
        undo-depth target is reached without undoing anything. The timeline says what is actually
        there.
        """
        design = self.call("inspect_design", {"measure": False, "sketches": False})
        return [int(f["id"]) for f in design.get("timeline") or [] if f.get("id") is not None]

    def undo(self) -> None:
        self.call("undo", {})

    def undo_depth(self) -> int:
        """Steps on the engine's undo stack (not every command pushes one, e.g. sketch.finish)."""
        return self.call("inspect_design", {"measure": False, "sketches": False})["undo"]

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def roll_back(engine: Engine, depth: int, timeline: list[int] | None = None) -> None:
    """Undo until the engine's undo stack is back at ``depth`` (bounded, never loops forever).

    The bound is the point: `while engine.undo_depth() > depth: engine.undo()` spins forever against
    an engine whose depth stops moving, and it is the depth reading itself that is least trustworthy
    after something went wrong. One counted pass, then an explicit error if the stack is still above
    the target, so a design left dirty is reported instead of silently replayed on.

    **The undo stack caps at 200 steps**, so on a long design the depth target is met without a
    single undo and this used to return quietly having done nothing. Measured on
    `Caja parametrica v2`, whose twelve sketches take the design past the cap: two of the oracle's
    trial extrudes survived into the saved file, named `f3d2solidcraft trial`, and a feature reported
    `failed` kept whatever it had already built. So callers pass the timeline they expect to be left
    with, and anything that outlived the undo is deleted by id — which is sound precisely here,
    because a trial and a failed feature are both the last thing in the design, with nothing built on
    them.
    """
    for _ in range(max(engine.undo_depth() - depth, 0)):
        engine.undo()
    if engine.undo_depth() > depth:
        raise EngineError(f"rollback left the undo stack above depth {depth}")
    if timeline is None:
        return
    expected = set(timeline)
    extra = [feature for feature in engine.timeline_ids() if feature not in expected]
    if not extra:
        return
    try:
        engine.execute("timeline.delete", {"features": extra})
    except EngineError as exc:
        raise EngineError(
            f"the undo left {len(extra)} feature(s) behind and deleting them failed: {exc}"
        ) from None
    left = [feature for feature in engine.timeline_ids() if feature not in expected]
    if left:
        raise EngineError(f"{len(left)} feature(s) the undo could not reach are still in the design")


@contextmanager
def rolled_back(engine: Engine, depth: int) -> Iterator[None]:
    """Run a trial inside this block and undo it afterwards, whether or not it worked.

    While an exception is already on its way out the rollback is best effort. A timed-out RPC has
    already closed the engine, so `undo_depth()` would raise "engine is not running" from the
    cleanup and that would be the only message the user ever sees — in place of
    "no answer within 120 s", which is what actually happened. The first exception wins; a cleanup
    that cannot run on a dead engine has nothing left to say.
    """
    timeline = engine.timeline_ids()
    try:
        yield
    except BaseException:
        try:
            roll_back(engine, depth, timeline)
        except EngineError:
            pass  # the exception in flight already says what went wrong; this one would hide it
        raise
    roll_back(engine, depth, timeline)
