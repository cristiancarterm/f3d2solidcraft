"""The two readings the window makes of a conversion, on a real conversion of the public sample."""

import json
import subprocess
import sys

import pytest

from f3d2solidcraft_app.events import Done, Feature, parse
from f3d2solidcraft_app.outcome import outcome_from


@pytest.fixture(scope="module")
def sample_run(tools, sample_f3d, tmp_path_factory):
    out = tmp_path_factory.mktemp("run")
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "f3d2solidcraft",
            "convert",
            str(sample_f3d),
            "-o",
            str(out / "box.solidcraft"),
            "--report",
            str(out / "r.json"),
            "--progress",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return done, json.loads((out / "r.json").read_text())


def test_every_line_of_a_real_run_is_read(sample_run):
    done, report = sample_run
    events = [parse(line) for line in done.stdout.splitlines()]
    features = [e for e in events if isinstance(e, Feature)]
    assert features and features[-1].index == features[-1].total == len(report["features"])
    assert [e for e in events if isinstance(e, Done)] == [Done(done.returncode)]
    assert any(e is None for e in events)  # the summary lines `convert` also prints are not events


def test_a_verified_run_reads_as_verified_with_its_counts(sample_run):
    done, report = sample_run
    result = outcome_from(done.returncode, report, done.stderr)
    counts = report["counts"]
    assert result.kind == "verified"
    assert result.editable == counts["exact"] + counts["inferred"]
    assert result.imported == counts["fallback"]
    assert result.total == len(report["features"])
    assert result.features == report["features"]


def test_exit_two_is_differs_and_says_what_does_not_match(sample_run):
    # The real sample report with one field changed to reach exit 2; the change is the only made-up
    # part, and it is said here (no synthetic data otherwise).
    _done, report = sample_run
    broken = json.loads(json.dumps(report))
    broken["verification"].update(ok=False, missing=["Body1"])
    result = outcome_from(2, broken, "")
    assert result.kind == "differs"
    assert "Body1" in result.message


def test_an_error_carries_what_the_converter_said(tools, tmp_path):
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "f3d2solidcraft",
            "convert",
            str(tmp_path / "nope.f3d"),
            "-o",
            str(tmp_path / "x.solidcraft"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 1
    result = outcome_from(done.returncode, None, done.stderr)
    assert result.kind == "error"
    assert "nope.f3d" in result.message


def test_lines_that_are_not_events_are_none():
    assert parse("") is None
    assert parse("features: 14 exact") is None
    assert parse("{not json") is None
    assert parse('{"event": "other"}') is None


def test_malformed_event_lines_are_none_not_an_exception():
    assert parse('{"event":"feature"}') is None
    assert parse('{"event":"done","exit":null}') is None
    assert parse('{"event":"done","exit":"x"}') is None


def test_differs_names_the_body_whose_faces_do_not_match(sample_run):
    _done, report = sample_run
    broken = json.loads(json.dumps(report))
    body = broken["verification"]["matched"][0]
    # The only made-up part: one real matched body declared with a different face count, and `ok`.
    body["faces_equal"], broken["verification"]["ok"] = False, False
    result = outcome_from(2, broken, "")
    assert result.kind == "differs"
    assert body["original"] in result.message
