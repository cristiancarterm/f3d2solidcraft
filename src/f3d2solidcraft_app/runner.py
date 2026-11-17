"""How the window starts the converter in a process of its own (spec, Arquitectura 2)."""

import os
import signal
import subprocess
import sys
from pathlib import Path

from f3d2solidcraft_app.stdio import ONE_PIPE

#: Set by the window in the worker's environment: the worker then leads a process group of its own,
#: so cancelling can reach the engines it starts (`kill_tree`). A worker run by hand from a terminal
#: does not get it and stays in the terminal's group, where Ctrl-C reaches it.
OWN_GROUP = "F3D2SC_WORKER_OWN_GROUP"


def worker_command(args: list[str], executable: str = sys.executable) -> list[str]:
    """In development `executable` is Python; packaged, it is the app itself, which is its own worker."""
    if Path(executable).name.lower().startswith("python"):
        return [executable, "-m", "f3d2solidcraft_app", "--worker", *args]
    return [executable, "--worker", *args]


def check_worker(executable: str = sys.executable) -> tuple[list[str], str | None]:
    """Start a worker exactly as the window would and ask it for the converter's help.

    `(command, problem)`, problem None when the worker answered as the converter. Packaged, this is
    what shows the app really is its own worker: `--check`'s engine lines never start one.
    """
    command = worker_command(["--help"], executable)
    try:
        # One pipe for both streams, as the window starts it (`stdio`: that is what lets a packaged
        # Windows worker keep its stdout).
        done = subprocess.run(
            command,
            env={**os.environ, ONE_PIPE: "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return command, f"does not run: {exc}"
    if done.returncode != 0 or "convert" not in done.stdout:
        return command, f"did not answer as the converter (exit {done.returncode}): {done.stdout[-300:]}"
    return command, None


def partial_paths(output: Path, report: Path) -> list[Path]:
    return [output.with_name(output.name + ".partial"), report.with_name(report.name + ".partial")]


def lead_own_group() -> None:
    """Called first thing in `--worker`: become a session (and group) leader when the window asked.

    On Windows the window holds the worker in a Job object instead (`winjob`): the worker joins it
    unless the window has put it there already, so no engine it starts is born outside the job.
    """
    if os.environ.pop(OWN_GROUP, None) and hasattr(os, "setsid"):
        try:
            os.setsid()
        except OSError:
            pass  # already a group leader: its group is its own anyway
    if os.name == "nt":
        from f3d2solidcraft_app.winjob import JOB_VARIABLE, join

        job = os.environ.pop(JOB_VARIABLE, None)
        if job:
            join(job)


def kill_tree(pid: int) -> None:
    """Kill a running worker and every process it started (the engines, which write `.partial`s).

    Call it only while the worker is still running: the fallbacks kill `pid` itself, which after the
    worker is reaped may be someone else.
    """
    if pid <= 0:
        return
    if os.name == "nt":
        # Only when the window could not hold the worker in a Job object (`winjob`), which is what
        # kills the tree on Windows. /T takes the children still linked to `pid`, /F does not ask; an
        # engine whose worker already died is out of its reach. No test reaches this path.
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        # No group `pid` yet: the worker has not reached `setsid`, so it has started nothing either.
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    except PermissionError:
        pass


def kill_group(pgid: int) -> None:
    """Sweep what is left of a worker's group after the worker itself is gone (Unix only).

    The engines keep the group id when the worker dies, so this still reaches them. On Windows the
    worker's Job object does this instead (`winjob.ProcessTree.close`).
    """
    if pgid <= 0 or os.name == "nt":
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
