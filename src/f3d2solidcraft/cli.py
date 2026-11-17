"""f3d2solidcraft command line."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import zstandard

from f3d2solidcraft.engine import BodyUnmeasurable, CommandRefused, Engine, EngineError
from f3d2solidcraft.fallback import apply_fallback, name_bodies, original_step
from f3d2solidcraft.mappers import MAPPERS
from f3d2solidcraft.mappers.components import reconcile_components
from f3d2solidcraft.oracle import Oracle, OracleUnavailable, PlacementsUnmeasurable
from f3d2solidcraft.reader import read_design
from f3d2solidcraft.replay import replay
from f3d2solidcraft.report import build_report, summary
from f3d2solidcraft.tools import ToolFailed, ToolMissing, Tools, find_tools, long_path, tool_version
from f3d2solidcraft.verify import (
    Verification,
    check_rebuild,
    compare,
    driving_parameter,
    original_bodies,
)
from f3d2solidcraft.visibility import hide_what_fusion_hides


def _reference_reading(oracle: Oracle | None) -> list[dict] | None:
    """The oracle's reading of the original, or None when there is none to be had."""
    if oracle is None:
        return None
    try:
        return oracle.measured()
    except OracleUnavailable:
        return None


# How many times a conversion may be redone after learning something only the engine could tell it.
_RETRIES = 6


def _in_engine(
    design: object,
    step: Path,
    out: Path,
    out_partial: Path,
    tmp: Path,
    tools: Tools,
    strict: bool,
    step_warnings: list[str],
    unmeasurable: set[str],
    progress: Callable[[int, int, str], None] | None = None,
) -> tuple:
    """The whole engine half of a conversion, in the one engine process of the run.

    `strict` asks for the reading that enumerates placements where the design has repeats. It is
    tried first and raises `_PlacementsUnmeasurable` when this engine cannot answer it, which is the
    caller's cue to redo the run per definition.
    """
    with Engine(tools.solidcraft_cli, [tmp, out.parent]) as eng:
        eng.unmeasurable = set(unmeasurable)
        eng.call("new_design", {"name": out.stem})
        # Without the original to ask, the conversion still runs: every feature that
        # needs inference simply falls back, as it did before M1.
        oracle_warnings: list[str] = []
        try:
            oracle = Oracle.open(eng, step, strict=strict)
        except OracleUnavailable as exc:
            oracle = None
            oracle_warnings.append(f"no reference solid to infer from: {exc}")
        # The oracle closes as a context manager, not in a bare `finally`: a replay that
        # dies keeps its own error (`Oracle.__exit__` only swallows a failed delete then),
        # while on success a failed delete still aborts instead of saving the reference.
        with oracle if oracle is not None else contextlib.nullcontext():
            result, ctx = replay(design, eng, oracle, progress=progress)
            # The reference the oracle already read *is* the original, so the verification
            # takes its view of it from there instead of opening the STEP again at the end.
            original = _reference_reading(oracle)
        fallback_warnings = step_warnings + oracle_warnings + apply_fallback(ctx, result, step)
        # Right after the import, so everything below reads the names the design gives its
        # bodies rather than the STEP export's: the rebuild check and the measurement the
        # verification reports, the component reconciliation and the visibility replay.
        fallback_warnings += name_bodies(ctx, result)
        rebuild_parameter = driving_parameter(ctx, result)
        rebuild_error = check_rebuild(ctx, result)
        # The placements the measurement below stands for, counted off the component graph: one
        # `component.list`, nothing measured.
        measured_placements = eng.placements_by_body()
        converted = eng.body_readings()
        # Last, because it is the only step after the measurement: it moves bodies between
        # components without touching their geometry, and the engine drops its cached
        # measurements on any change — so a measurement taken after it pays for the whole
        # design again (24 s of `mango para lima`'s 108) and reads the same geometry.
        fallback_warnings += reconcile_components(ctx, result.imported_bodies)
        # After the moves, so every body is where it will be saved, and after the
        # measurement above, which hiding does not change but which has no reason to run
        # against a half-arranged design.
        fallback_warnings += hide_what_fusion_hides(ctx, result)
        # "Reads the same" held for each body's geometry and not for how many times it is placed:
        # moving the bodies of a part placed N times into a component placed once collapsed the N
        # copies to one, and the verification, taken above, said every one matched. Measured on a
        # board of 302 placements of 43 definitions: verified, and 43 bodies in the saved file.
        # So the placements are counted again right before the save, from the same free listing,
        # and anything lost is the verification's to report rather than the file's to hide.
        placements_lost = dict(measured_placements - eng.placements_by_body())
        eng.call("save", {"path": str(out_partial)})
        if original is None:
            # No oracle, or it stopped answering: read the original the long way. Opening
            # the STEP replaces the design in the session, so it happens after the save.
            try:
                original = original_bodies(eng, step)
            except CommandRefused as exc:
                # The third and last place the original is load-bearing, and the last one
                # that was unprotected. The design is already saved above, so what is lost
                # here is only the verification — say so instead of ending the run, which
                # is what solidcraft 0.40.0-rc.3 made it do on
                # `corpus-07` (spec section 124).
                fallback_warnings.append(f"the original could not be read to verify against: {exc.message}")
        # Read before the oracle's own numbers go out of scope: what the conversion spent of it
        # belongs in the report, so the next question about the budget is answerable from a run
        # that already happened instead of from a patch that counts by hand (spec section 131).
        budget = oracle.budget() if oracle is not None else None
        if budget is not None and budget["clock_exceeded"]:
            fallback_warnings.append(
                f"the oracle's questions took {budget['seconds']:.1f} s, over the "
                f"{budget['clock_ceiling']:.0f} s ceiling: it stopped asking with "
                f"{budget['budget'] - budget['asked']} of its {budget['budget']} questions unspent, "
                f"so what this run counted is this machine's and not the budget's"
            )
        return (
            result,
            ctx,
            original,
            converted,
            fallback_warnings,
            rebuild_parameter,
            rebuild_error,
            budget,
            placements_lost,
        )


def convert(
    f3d: Path,
    out: Path,
    report_path: Path | None,
    force: bool,
    tools: Tools,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    # Every path the engine sees is spelt out in full: on Windows it refuses an 8.3 short name, which a
    # user's own path can be as much as the temp dir below (spec of `long_path`).
    f3d = long_path(f3d)
    out = long_path(out)
    report_path = long_path(report_path) if report_path is not None else None
    for target in (out, report_path):
        if target is not None and target.exists() and not force:
            raise FileExistsError(f"{target} exists; use --force to replace it")
    out.parent.mkdir(parents=True, exist_ok=True)
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
    # Outputs land as siblings first and replace the targets only once the whole run succeeded.
    out_partial = out.with_name(out.name + ".partial")
    report_partial = report_path.with_name(report_path.name + ".partial") if report_path else None
    try:
        with tempfile.TemporaryDirectory(prefix="f3d2sc-") as tmp_name:
            tmp = long_path(tmp_name)
            design = read_design(f3d, tools.cadmpeg, tmp)
            # left by read_design; ~25x the .f3d, so it goes as soon as possible
            cadir = tmp / "design.cadir.json"
            step, step_warnings = original_step(tools.cadmpeg, cadir, tmp / "original.step")
            cadir.unlink()
            (tmp / "design.cadir.fidelity.json").unlink(missing_ok=True)  # cadmpeg dump writes it alongside
            out_partial.unlink(missing_ok=True)  # a stale one would make the engine leave a .bak
            # Two things can only be learnt by trying, and each one costs the engine: whether the
            # design's placements can be measured at all, and whether some one body cannot. A
            # call that times out leaves the session out of step, so the engine is gone either way
            # and the run is redone knowing one more thing — the import is what gets paid again.
            # Bounded by `_RETRIES` so a design that keeps producing new unmeasurable bodies ends
            # with a refusal rather than a loop (spec section 98).
            mode_warnings: list[str] = []
            strict = True
            unmeasurable: set[str] = set()
            for _attempt in range(_RETRIES):
                try:
                    (
                        result,
                        _ctx,
                        original,
                        converted,
                        fallback_warnings,
                        rebuild_parameter,
                        rebuild_error,
                        budget,
                        placements_lost,
                    ) = _in_engine(
                        design,
                        step,
                        out,
                        out_partial,
                        tmp,
                        tools,
                        strict,
                        step_warnings,
                        unmeasurable,
                        progress=progress,
                    )
                    break
                except PlacementsUnmeasurable as exc:
                    mode_warnings.append(
                        f"{exc}, so every reading counts one body per definition and the "
                        f"verification compares them that way"
                    )
                    strict = False
                except BodyUnmeasurable as exc:
                    mode_warnings.append(
                        f"{exc}, so it is left out of every reading and out of the verification: "
                        f"nothing is claimed about it either way"
                    )
                    unmeasurable.add(exc.body)
            else:
                raise EngineError(
                    f"gave up after {_RETRIES} attempts; the last reason was: {mode_warnings[-1]}"
                )
            fallback_warnings += mode_warnings
            verification = (
                compare(original, converted)
                if original is not None
                else Verification(unverified="the original could not be read")
            )
            verification.rebuild_error = rebuild_error
            verification.placements_lost = placements_lost
            verification.rebuild_parameter = rebuild_parameter
            # Said out loud, because the verification passing while a body was never looked at is
            # exactly the thing a reader has to be able to tell apart (spec section 133).
            if verification.unmeasured_original or verification.unmeasured_converted:
                fallback_warnings.append(
                    f"{len(verification.unmeasured_original)} body(ies) of the original and "
                    f"{len(verification.unmeasured_converted)} of the conversion could not be "
                    f"measured, so they are left out of the verification and nothing is claimed "
                    f"about them either way: "
                    f"{', '.join(sorted(set(verification.unmeasured_original + verification.unmeasured_converted)))}"
                )
        report = build_report(design, result, fallback_warnings, verification, budget)
        if report_partial:
            report_partial.write_text(json.dumps(report, indent=2), encoding="utf-8")
        os.replace(out_partial, out)
        if report_partial and report_path:
            os.replace(report_partial, report_path)
    finally:
        out_partial.unlink(missing_ok=True)
        if report_partial:
            report_partial.unlink(missing_ok=True)
    return report


def inspect(f3d: Path, tools: Tools) -> str:
    with tempfile.TemporaryDirectory(prefix="f3d2sc-") as tmp:
        design = read_design(f3d, tools.cadmpeg, long_path(tmp))
    tags = Counter(f.tag for f in design.features)
    lines = [f"{f3d}: {len(design.parameters)} parameters, {len(design.sketches)} sketches"]
    lines += [
        f"  {tag:<24} {n:>3}  {'mapped' if tag in MAPPERS else 'fallback'}" for tag, n in tags.most_common()
    ]
    for occ in design.occurrences:
        component = design.components.get(occ.component or "")
        if component is None or component.name is None:
            continue
        depth = 0
        parent = occ.parent
        while parent:
            depth += 1
            parent = next((o.parent for o in design.occurrences if o.id == parent), None)
        lines.append(
            f"  {'  ' * depth}component {component.name:<20} "
            f"{len(component.bodies):>2} bodies, {len(component.sketches):>2} sketches"
            f"{'' if occ.identity else '  (moved: not an identity placement)'}"
        )
    lines += [f"  ! {w}" for w in design.warnings]
    return "\n".join(lines)


def inspect_counts(f3d: Path, tools: Tools) -> dict:
    """What the file holds — parameters, sketches, features — and nothing predicted about it."""
    with tempfile.TemporaryDirectory(prefix="f3d2sc-") as tmp:
        design = read_design(f3d, tools.cadmpeg, long_path(tmp))
    return {
        "parameters": len(design.parameters),
        "sketches": len(design.sketches),
        "features": len(design.features),
    }


# A file regresses when it gets this much slower or heavier, beyond small absolute noise.
_SLOWER, _SLOWER_ABS_S = 1.5, 1.0
_HEAVIER, _HEAVIER_ABS_MB = 1.5, 50.0


def _peak_working_set(pid: int) -> int:
    """Windows' answer to `ru_maxrss`: the peak working set of a process that has exited, in bytes.

    Asked of the process object, which outlives the process while the `Popen` that started it still
    holds its handle — so `pid` cannot have been reused yet. It counts the converter's own process,
    not the engines it starts, which are processes of their own; `wait4` on Linux also folds in the
    children the converter waited for. Windows has no `wait4`: without this `bench` crashed there.
    """
    import ctypes
    from ctypes import wintypes

    class _Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
    kernel32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    process_query_limited_information = 0x1000
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), f"cannot open process {pid} to read its memory")
    try:
        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)
        if not kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise OSError(ctypes.get_last_error(), f"cannot read the memory of process {pid}")
        return int(counters.PeakWorkingSetSize)
    finally:
        kernel32.CloseHandle(handle)


def _measured_convert(f3d: Path, out_dir: Path) -> dict:
    """Convert in a child process: wall time and that process's peak memory."""
    report_path = out_dir / f"{f3d.stem}.report.json"
    log = out_dir / f"{f3d.stem}.log"
    args = [
        sys.executable,
        "-m",
        "f3d2solidcraft",
        "convert",
        str(f3d),
        "-o",
        str(out_dir / f"{f3d.stem}.solidcraft"),
        "--report",
        str(report_path),
        "--force",
    ]
    report_path.unlink(missing_ok=True)  # a stale report from an earlier run must never be read
    start = time.monotonic()
    with log.open("w") as sink:
        proc = subprocess.Popen(args, stdout=sink, stderr=subprocess.STDOUT)
        if hasattr(os, "wait4"):
            _, status, usage = os.wait4(proc.pid, 0)
            # ru_maxrss is bytes on macOS and kilobytes on Linux.
            peak_bytes = usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024)
            code = os.waitstatus_to_exitcode(status)
        else:
            code = proc.wait()
            peak_bytes = _peak_working_set(proc.pid)
    seconds = round(time.monotonic() - start, 3)
    peak_mb = round(peak_bytes / (1024 * 1024), 1)
    if code not in (0, 2) or not report_path.is_file():
        error = log.read_text(errors="replace")[-500:]
        return {"ok": False, "error": error, "seconds": seconds, "peak_mb": peak_mb}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    v = report["verification"]
    bodies = report.get("bodies") or {}
    return {
        "ok": v["ok"],
        **report["counts"],
        "native": bodies.get("native", 0),
        "imported": bodies.get("imported", 0),
        "missing": len(v["missing"]),
        "extra": len(v["extra"]),
        "placements_lost": sum((v.get("placements_lost") or {}).values()),
        "seconds": seconds,
        "peak_mb": peak_mb,
    }


def _regressions(name: str, was: dict, now: dict) -> list[str]:
    if "exact" not in was:
        return []  # only an error row has no counts, and a file that never converted cannot regress
    found = []
    if was.get("ok") and not now.get("ok"):
        found.append(f"{name}: no longer verified")
    exact_before, exact_now = was.get("exact", 0), now.get("exact", 0)
    inferred_before, inferred_now = was.get("inferred", 0), now.get("inferred", 0)
    rebuilt_before = exact_before + inferred_before
    rebuilt_now = exact_now + inferred_now
    if rebuilt_now < rebuilt_before:
        found.append(f"{name}: rebuilt features {rebuilt_before} -> {rebuilt_now}")
    # The total can hold while the file stops being read and starts being guessed: say so.
    if exact_now < exact_before and inferred_now > inferred_before:
        found.append(
            f"{name}: exact {exact_before} -> {exact_now} with inferred {inferred_before} -> {inferred_now}"
        )
    if now.get("native", 0) < was.get("native", 0):
        found.append(f"{name}: native bodies {was.get('native')} -> {now.get('native', 0)}")
    for key in ("fallback", "failed", "missing", "extra", "placements_lost"):
        if now.get(key, 0) > was.get(key, 0):
            found.append(f"{name}: {key} {was.get(key)} -> {now.get(key, 0)}")
    for key, ratio, floor in (("seconds", _SLOWER, _SLOWER_ABS_S), ("peak_mb", _HEAVIER, _HEAVIER_ABS_MB)):
        a, b = was.get(key), now.get(key)
        if a and b and b > a * ratio and b - a > floor:
            found.append(f"{name}: {key} {a} -> {b}")
    return found


def _load_baseline(path: Path) -> tuple[dict, dict]:
    """(the tool versions it was measured with, the record per file).

    A baseline written before this recorded them is still read, with empty versions: those files
    are the project's own working baselines and there was no reason to throw them away. What is not
    allowed is a silent comparison across engines, which is what `_tool_notes` is for.
    """
    try:
        before = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"baseline {path} is not valid JSON: {exc}") from None
    if not isinstance(before, dict):
        raise ValueError(f"baseline {path} is not a bench.json (expected an object per file)")  # noqa: TRY004
    if isinstance(before.get("files"), dict):
        return before.get("tools") or {}, before["files"]
    return {}, before


def _tool_notes(was: dict, now: dict) -> list[str]:
    """What to say out loud when the baseline was not measured on the same tools.

    A comparison across engine versions is exactly what the solidcore lane is for, so this is not a
    regression and does not fail the run. It is printed first, and printed every time, because the
    numbers below it mean something different then — and because an engine can move on its own: the
    `solidcraft-cli` on this machine is a symlink into an app that updates itself, which is how four
    spec sections came to be measured on 0.30.1 while a note said 0.30.0 (spec sections 123 to 125).
    """
    notes = []
    for tool in ("solidcraft_cli", "cadmpeg"):
        a, b = was.get(tool), now.get(tool)
        if not a:
            notes.append(f"the baseline does not record which {tool} measured it; this run used {b}")
        elif a != b:
            notes.append(f"measured on {tool} {b}, the baseline on {a}: the comparison crosses versions")
    return notes


def bench(
    corpus: Path, out_dir: Path, baseline: Path | None, tools: Tools
) -> tuple[dict, list[str], list[str]]:
    # A bad baseline fails before any work.
    was_tools, before = _load_baseline(baseline) if baseline else ({}, {})
    out_dir.mkdir(parents=True, exist_ok=True)
    now_tools = {
        "solidcraft_cli": tool_version(tools.solidcraft_cli),
        "cadmpeg": tool_version(tools.cadmpeg),
    }
    files = sorted([*corpus.rglob("*.f3d"), *corpus.rglob("*.f3z")])
    results = {f.name: _measured_convert(f, out_dir) for f in files}
    # Recorded with the numbers, not beside them: a baseline that does not carry the versions it was
    # measured on cannot be compared honestly, and a file is the only thing that outlives the run.
    (out_dir / "bench.json").write_text(
        json.dumps({"tools": now_tools, "files": results}, indent=2), encoding="utf-8"
    )
    regressions = [
        r for name, now in results.items() if name in before for r in _regressions(name, before[name], now)
    ]
    notes = _tool_notes(was_tools, now_tools) if baseline else []
    return results, regressions, notes


def _event(payload: dict) -> None:
    """One machine-readable progress line; flushed, because a reader is waiting on it."""
    print(json.dumps(payload), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="f3d2solidcraft")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert", help="convert a .f3d or .f3z into a .solidcraft design")
    c.add_argument("f3d", type=Path)
    c.add_argument("-o", "--output", type=Path, required=True)
    c.add_argument("--report", type=Path)
    c.add_argument("--force", action="store_true")
    c.add_argument(
        "--progress",
        action="store_true",
        help="write one JSON line per feature, and one with the exit code at the end (for the desktop app)",
    )
    i = sub.add_parser("inspect", help="show what a .f3d or .f3z holds and what would be mapped")
    i.add_argument("f3d", type=Path)
    i.add_argument("--json", action="store_true", help="print the counts as one JSON line")
    b = sub.add_parser("bench", help="convert a whole corpus and compare with a previous run")
    b.add_argument("corpus", type=Path)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--baseline", type=Path)
    sub.add_parser("mcp", help="serve the converter over MCP on stdio")
    args = parser.parse_args(argv)
    if args.cmd == "mcp":
        # Imported here so the other commands do not pay for it, and so a client that only speaks
        # MCP is the only caller that can turn this process into a protocol stream.
        from f3d2solidcraft.mcp import serve

        return serve()
    # The desktop app waits for `done` to know the run is over, so it is sent from one place on
    # every way out of a conversion: a clean end, an error turned into exit 1, and an exception
    # that escapes `main` (then reported as exit 1, and the exception still propagates).
    announce = args.cmd == "convert" and args.progress
    code = 1
    try:
        try:
            tools = find_tools()
            if args.cmd == "bench":
                results, regressions, notes = bench(args.corpus, args.out, args.baseline, tools)
                ok = sum(1 for r in results.values() if r.get("ok"))
                slowest = max((r["seconds"] for r in results.values()), default=0)
                heaviest = max((r["peak_mb"] for r in results.values()), default=0)
                print(f"{ok}/{len(results)} verified, slowest {slowest} s, peak {heaviest} MB")
                for line in notes:
                    print(f"  NOTE {line}")
                for line in regressions:
                    print(f"  REGRESSION {line}")
                return 1 if regressions else 0
            if args.cmd == "inspect":
                if args.json:
                    print(json.dumps(inspect_counts(args.f3d, tools)))
                else:
                    print(inspect(args.f3d, tools))
                return 0
            progress = None
            if announce:

                def progress(index: int, total: int, name: str) -> None:
                    _event({"event": "feature", "index": index, "total": total, "name": name})

            report = convert(args.f3d, args.output, args.report, args.force, tools, progress=progress)
        except (
            ToolMissing,
            ToolFailed,
            FileExistsError,
            FileNotFoundError,
            EngineError,
            ValueError,  # bad input data (json.JSONDecodeError is one) or a corrupt baseline
            zstandard.ZstdError,  # a damaged .f3d stream
        ) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(summary(report))
        code = 0 if report["verification"]["ok"] else 2
        return code
    finally:
        if announce:
            _event({"event": "done", "exit": code})
