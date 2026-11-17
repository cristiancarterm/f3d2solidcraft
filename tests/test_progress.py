"""`convert --progress`: one JSON event per feature, then the exit code, on the real sample."""

import json
import os
import sys

import pytest

from f3d2solidcraft.cli import main


def _events(out: str) -> list[dict]:
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


def test_progress_reports_every_feature_and_the_exit_code(tools, sample_f3d, tmp_path, capsys):
    code = main(
        [
            "convert",
            str(sample_f3d),
            "-o",
            str(tmp_path / "box.solidcraft"),
            "--report",
            str(tmp_path / "r.json"),
            "--progress",
        ]
    )
    events = _events(capsys.readouterr().out)
    features = [e for e in events if e["event"] == "feature"]
    total = features[0]["total"]
    report = json.loads((tmp_path / "r.json").read_text())
    assert total == len(report["features"])
    # A conversion may be redone after learning something only the engine can tell it, so the
    # count can start over; what holds is that the last pass reaches every feature, in order.
    last_pass = features[-total:]
    assert [e["index"] for e in last_pass] == list(range(1, total + 1))
    assert [e["name"] for e in last_pass] == [f["name"] for f in report["features"]]
    assert events[-1] == {"event": "done", "exit": code}
    assert code == 0


def test_progress_says_done_with_the_exit_code_on_an_error(tools, tmp_path, capsys):
    code = main(["convert", str(tmp_path / "nope.f3d"), "-o", str(tmp_path / "x.solidcraft"), "--progress"])
    assert code == 1
    assert _events(capsys.readouterr().out)[-1] == {"event": "done", "exit": 1}


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="a read-only directory does not stop mkdir on Windows or for root",
)
def test_progress_says_done_even_when_an_unexpected_error_escapes(tools, sample_f3d, tmp_path, capsys):
    # A real condition `main` does not translate into an exit code: the output folder cannot be
    # created inside a read-only one, so `mkdir` raises PermissionError, which escapes `main`.
    # The desktop app still has to hear that the run is over.
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(PermissionError):
            main(["convert", str(sample_f3d), "-o", str(locked / "sub" / "x.solidcraft"), "--progress"])
    finally:
        locked.chmod(0o700)
    assert _events(capsys.readouterr().out)[-1] == {"event": "done", "exit": 1}


def test_without_progress_there_are_no_events(tools, sample_f3d, tmp_path, capsys):
    main(["convert", str(sample_f3d), "-o", str(tmp_path / "box.solidcraft")])
    assert _events(capsys.readouterr().out) == []
