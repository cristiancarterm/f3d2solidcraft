"""`stdio.attach_std_streams`: the worker's stdout back on the window's pipe (Windows, packaged).

What the packaged Windows app does to its stdout (Briefcase's stub reopens it onto a log file before
Python starts) is reproduced here with real processes: the child moves its own fd 1 onto a file with
`os.dup2`, the same C runtime call path as the stub's `freopen`. The stub itself, started by a parent
with no console, is only reached by the packaged smoke in release.yml (scripts/smoke_packaged.py).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from f3d2solidcraft_app.stdio import ONE_PIPE, attach_std_streams

windows = pytest.mark.skipif(sys.platform != "win32", reason="the stub's redirect is a Windows launcher's")

# The child: stdout taken off the pipe onto `log` (as the stub does), then the worker's first call.
HIJACKED = """
import os, sys
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
os.dup2(fd, 1)
from f3d2solidcraft_app.stdio import attach_std_streams
attach_std_streams()
print('{"parameters": 1}')
print("error: on stderr", file=sys.stderr)
print("flag left:", os.environ.get("F3D2SC_WORKER_ONE_PIPE"), file=sys.stderr)
"""


def _run_hijacked(log: Path, one_pipe: bool) -> str:
    env = {k: v for k, v in os.environ.items() if k != ONE_PIPE}
    if one_pipe:
        env[ONE_PIPE] = "1"
    done = subprocess.run(
        [sys.executable, "-c", HIJACKED, str(log)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        timeout=60,
        check=True,
    )
    return done.stdout.decode("utf-8")


@pytest.mark.skipif(sys.platform == "win32", reason="on Windows it would re-point pytest's own streams")
def test_elsewhere_the_flag_is_taken_out_and_nothing_else_changes():
    environ = {ONE_PIPE: "1", "OTHER": "x"}
    before = sys.stdout, sys.stderr
    attach_std_streams(environ)
    assert (sys.stdout, sys.stderr) == before
    assert environ == {"OTHER": "x"}


@windows
def test_without_the_flag_the_redirect_loses_stdout(tmp_path):
    # The control: this is 0.3.1's packaged worker, the counts line in a log and not on the pipe.
    log = tmp_path / "stub.log"
    out = _run_hijacked(log, one_pipe=False)
    assert '{"parameters": 1}' not in out
    assert '{"parameters": 1}' in log.read_text(encoding="utf-8")
    assert "error: on stderr" in out


@windows
def test_with_one_pipe_stdout_comes_back_to_the_pipe(tmp_path):
    log = tmp_path / "stub.log"
    out = _run_hijacked(log, one_pipe=True)
    assert '{"parameters": 1}' in out.splitlines()[0]
    assert "error: on stderr" in out
    assert "flag left: None" in out  # the engines the worker starts do not inherit it
    assert log.read_text(encoding="utf-8") == ""


@windows
def test_a_gui_python_without_streams_gets_some_and_can_print(tmp_path):
    """pythonw, detached: whatever streams it starts with, after the call a print works."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        pytest.skip(f"no pythonw.exe beside {sys.executable}")
    result = tmp_path / "result.json"
    script = f"""
import json, sys
before = [sys.stdout is None, sys.stderr is None]
from f3d2solidcraft_app.stdio import attach_std_streams
attach_std_streams()
print("printed")
print("printed", file=sys.stderr)
json.dump({{"before": before, "after": [sys.stdout is None, sys.stderr is None]}}, open({str(result)!r}, "w"))
"""
    subprocess.run(
        [str(pythonw), "-c", script],
        creationflags=subprocess.DETACHED_PROCESS,
        timeout=60,
        check=True,
    )
    data = json.loads(result.read_text())
    assert data["after"] == [False, False], data
