"""`scripts/fetch_cadmpeg.py`, which puts cadmpeg inside the app before Briefcase packages it."""

import importlib.util
import platform
import socket
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_cadmpeg.py"


@pytest.fixture(scope="module")
def fetch():
    spec = importlib.util.spec_from_file_location("fetch_cadmpeg", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("machine", ["aarch64", "arm64", "armv7l", "riscv64", ""])
def test_a_linux_machine_that_is_not_x86_64_is_refused_before_anything_is_written(fetch, tmp_path, machine):
    dest = tmp_path / "bin"
    with pytest.raises(SystemExit) as stop:
        fetch.main(["--dest", str(dest)], system="Linux", machine=machine)
    message = str(stop.value.code)
    assert "x86_64 only" in message and (machine or "unknown") in message, message
    assert not dest.exists()


@pytest.mark.parametrize("machine", ["x86_64", "AMD64"])
def test_an_x86_64_linux_machine_gets_the_linux_build(fetch, machine):
    assert fetch.assets_for("Linux", machine) == ["cadmpeg-x86_64-unknown-linux-gnu.tar.xz"]


def test_macos_gets_both_architectures_and_windows_the_x86_64_build(fetch):
    assert fetch.assets_for("Darwin", "arm64") == fetch.assets_for("Darwin", "x86_64")
    assert len(fetch.assets_for("Darwin", "arm64")) == 2
    assert fetch.assets_for("Windows", "ARM64") == ["cadmpeg-x86_64-pc-windows-msvc.zip"]
    assert set(fetch.SHA256) == {
        *fetch.assets_for("Windows", "AMD64"),
        *fetch.assets_for("Darwin", "arm64"),
        *fetch.assets_for("Linux", "x86_64"),
    }


def test_a_server_that_never_answers_stops_the_download_with_a_clear_error(fetch, tmp_path):
    # A real socket that takes the connection (the kernel completes the handshake from the backlog)
    # and never sends a byte, as a stalled server does.
    with socket.create_server(("127.0.0.1", 0)) as silent:
        port = silent.getsockname()[1]
        with pytest.raises(SystemExit) as stop:
            fetch.download(f"http://127.0.0.1:{port}/cadmpeg.tar.xz", tmp_path / "x", timeout=0.5)
    message = str(stop.value.code)
    assert "timed out" in message and "0.5 s" in message, message


def test_a_server_that_is_not_there_is_named_in_the_error(fetch, tmp_path):
    with socket.create_server(("127.0.0.1", 0)) as gone:
        port = gone.getsockname()[1]
    # Closed: nothing listens on `port` any more.
    with pytest.raises(SystemExit) as stop:
        fetch.download(f"http://127.0.0.1:{port}/cadmpeg.tar.xz", tmp_path / "x", timeout=5)
    message = str(stop.value.code)
    assert message.startswith(f"could not download http://127.0.0.1:{port}/cadmpeg.tar.xz"), message


def test_this_machine_gets_a_cadmpeg_that_runs(fetch, tmp_path):
    """The real download, checked against the pinned sha256, installed and run, as the release does."""
    if platform.system() == "Linux" and platform.machine().lower() not in fetch.X86_64:
        pytest.skip("cadmpeg has no Linux build for this machine (the refusal is tested above)")
    dest = tmp_path / "bin"
    assert fetch.main(["--dest", str(dest)]) == 0
    (installed,) = list(dest.iterdir())
    done = subprocess.run([str(installed), "--version"], capture_output=True, text=True, check=True)
    assert fetch.VERSION in done.stdout
