"""Run the app Briefcase built, as the window runs it, before it is packaged (release.yml).

    python scripts/smoke_packaged.py BUILD_DIR SAMPLE OUT_DIR

The tests run the app from source, through a console Python. The installers run the executable
Briefcase builds, which on Windows is a GUI program: a bug in how that program writes to the pipes
the window gives it (0.3.1: «Could not read the file: exit code 0» on any file) never reached a test.
This starts the built executable itself, with pipes, the way the window's QProcess does, and reads
what comes back with the window's own code (`events.parse`, `outcome_from`):

1. `--check`: the engine lines and the worker line, exit 0.
2. `--worker inspect SAMPLE --json`: the counts line the first step shows.
3. `--worker convert SAMPLE ... --progress`: feature events, `done` with exit 0, a verified report.

On Windows it first lets go of its console: the window is a GUI program and has none, and Briefcase's
stub decides where the worker's stdout goes by whether its parent has one (`AttachConsole`). Run from
a CI shell, which has a console, the stub kept the pipes and 0.3.1 passed here. Each run gets one
pipe for stdout and stderr and `F3D2SC_WORKER_ONE_PIPE=1`, as the window starts the worker
(`f3d2solidcraft_app.stdio`); `--check` too, as any parent without a console has to.

Each fails on empty output, whatever the exit code. SolidCraft is the runner's (`F3D2SC_SOLIDCRAFT_CLI`,
as the window pins it); cadmpeg is the one inside the built app, as the window points the worker at it.
Stdlib only, plus the two pure modules of the window it imports from `src/`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from f3d2solidcraft_app.events import Done, Feature, parse
from f3d2solidcraft_app.outcome import outcome_from
from f3d2solidcraft_app.stdio import ONE_PIPE

#: What `test weird box.f3d`, the public sample the CI uses, holds.
EXPECTED = {"parameters": 32, "sketches": 8, "features": 26}
TIMEOUT = 900


class SmokeFailed(SystemExit):
    pass


def built_app(build: Path) -> tuple[Path, Path]:
    """The executable Briefcase built for this system, and the cadmpeg inside it."""
    if sys.platform == "win32":
        candidates = list(build.glob("*/windows/app/src/*.exe"))
    elif sys.platform == "darwin":
        candidates = [p for p in build.glob("*/macos/app/*.app/Contents/MacOS/*") if p.is_file()]
    else:
        # The AppImage users run, wherever this Briefcase leaves it; else the folder it is made from.
        images = [
            *build.rglob("f3d2solidcraft*.AppImage"),
            *build.parent.joinpath("dist").glob("f3d2solidcraft*.AppImage"),
        ]
        candidates = images or list(build.glob("*/linux/appimage/*.AppDir/AppRun"))
    if len(candidates) != 1:
        raise SmokeFailed(f"expected one built executable under {build}, found {candidates}")
    name = "cadmpeg.exe" if sys.platform == "win32" else "cadmpeg"
    cadmpegs = [p for p in build.rglob(name) if p.parent.parts[-2:] == ("resources", "bin")]
    if not cadmpegs:
        raise SmokeFailed(f"no bundled {name} under {build}")
    return candidates[0], cadmpegs[0]


def without_console() -> None:
    """Windows: be a parent with no console, as the window is, for every program started from here.

    Our own output is a pipe to the CI log, which `FreeConsole` leaves alone.
    """
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.FreeConsole()


def run(command: list[str], env: dict[str, str]) -> tuple[int, str, str]:
    """Exit code, the whole pipe, and its lines that are not progress events (the window's `stderr`)."""
    print("$", " ".join(command), flush=True)
    done = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**env, ONE_PIPE: "1"},
        timeout=TIMEOUT,
        check=False,
    )
    out = done.stdout.decode("utf-8", "replace")
    print(f"exit {done.returncode}\n--- stdout and stderr ---\n{out}--------------", flush=True)
    if not out.strip():
        raise SmokeFailed(f"{command[1:3]}: nothing on the pipe (exit {done.returncode})")
    text = "".join(line + "\n" for line in out.splitlines() if parse(line) is None)
    return done.returncode, out, text


def main(argv: list[str]) -> int:
    build, sample, out_dir = (Path(a).resolve() for a in argv)
    exe, cadmpeg = built_app(build)
    without_console()
    print(f"built app: {exe}\nbundled cadmpeg: {cadmpeg}", flush=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = dict(os.environ)
    base.pop("F3D2SC_CADMPEG", None)
    if sys.platform.startswith("linux"):
        base["APPIMAGE_EXTRACT_AND_RUN"] = "1"  # no FUSE on the runner

    # 1. `--check` finds the bundled cadmpeg on its own, like the window's first screen.
    code, out, _ = run([str(exe), "--check"], base)
    if code != 0 or "worker:" not in out or "cadmpeg:" not in out:
        raise SmokeFailed(f"--check did not pass (exit {code})")
    # The window starts `worker_command(..., sys.executable)`: it must be this same executable, or the
    # runs below would test a worker the window never starts. (An AppImage runs from a mount or an
    # extracted copy, so on Linux the path differs by design.)
    if not sys.platform.startswith("linux") and f"worker: {exe} --worker " not in out:
        raise SmokeFailed(f"the window would not start {exe} as its worker")

    # The worker gets what the window puts in its environment (`Wizard._new_process`).
    worker = {**base, "F3D2SC_CADMPEG": str(cadmpeg), "F3D2SC_WORKER_OWN_GROUP": "1"}

    # 2. The first step's inspection.
    code, out, _ = run([str(exe), "--worker", "inspect", str(sample), "--json"], worker)
    counts = next((json.loads(line) for line in out.splitlines() if line.startswith("{")), None)
    if code != 0 or counts != EXPECTED:
        raise SmokeFailed(f"inspect: exit {code}, counts {counts}, expected {EXPECTED}")

    # 3. The conversion, read as the window reads it.
    output = out_dir / "sample.solidcraft"
    report = out_dir / "sample.report.json"
    command = [str(exe), "--worker", "convert", str(sample), "-o", str(output)]
    code, out, err = run([*command, "--report", str(report), "--force", "--progress"], worker)
    events = [e for e in map(parse, out.splitlines()) if e is not None]
    features = [e for e in events if isinstance(e, Feature)]
    if not features or events[-1] != Done(0):
        raise SmokeFailed(f"convert: {len(features)} feature events, last event {events[-1:]}")
    data = json.loads(report.read_text(encoding="utf-8"))
    if code != 0 or not output.is_file() or data["verification"]["ok"] is not True:
        raise SmokeFailed(f"convert: exit {code}, verification {data['verification']}")
    outcome = outcome_from(code, data, err)
    if outcome.kind != "verified":
        raise SmokeFailed(f"the window would show {outcome.kind}: {outcome.message}")
    print(f"ok: {len(features)} feature events, verified, {outcome.editable}/{outcome.total} editable")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
