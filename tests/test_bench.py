import json

from f3d2solidcraft.cli import _load_baseline, _regressions, _tool_notes, main
from f3d2solidcraft.tools import tool_version


def test_bench_records_and_detects_no_regression(sample_f3d, tools, tmp_path):
    corpus = sample_f3d.parent
    first = tmp_path / "run1"
    assert main(["bench", str(corpus), "--out", str(first)]) == 0
    written = json.loads((first / "bench.json").read_text())
    # The versions live with the numbers, because a baseline that does not say what measured it
    # cannot be compared honestly (spec section 126).
    assert written["tools"] == {
        "solidcraft_cli": tool_version(tools.solidcraft_cli),
        "cadmpeg": tool_version(tools.cadmpeg),
    }
    results = written["files"]
    row = results[sample_f3d.name]
    assert row["ok"] is True
    assert row["seconds"] > 0 and row["peak_mb"] > 0
    # Two of this sample's three bodies are the replay's own under cadmpeg 0.6.8 (spec section 95).
    assert (row["native"], row["imported"]) == (2, 1)
    second = tmp_path / "run2"
    assert main(["bench", str(corpus), "--out", str(second), "--baseline", str(first / "bench.json")]) == 0


def test_regressions_compare_two_runs():
    was = {"ok": True, "exact": 4, "native": 2, "imported": 1, "seconds": 2.0, "peak_mb": 240.0}
    assert _regressions("a.f3d", was, dict(was)) == []
    assert _regressions("a.f3d", was, {**was, "native": 1, "imported": 2}) == ["a.f3d: native bodies 2 -> 1"]
    # slower by more than the ratio but under the absolute floor: noise, not a regression
    assert _regressions("a.f3d", was, {**was, "seconds": 2.9}) == []
    assert _regressions("a.f3d", was, {**was, "ok": False}) == ["a.f3d: no longer verified"]


def test_bench_rejects_a_corrupt_baseline(sample_f3d, tools, tmp_path, capsys):
    baseline = tmp_path / "bench.json"
    baseline.write_text("{not json")
    assert (
        main(["bench", str(sample_f3d.parent), "--out", str(tmp_path / "run"), "--baseline", str(baseline)])
        == 1
    )
    assert "is not valid JSON" in capsys.readouterr().err


def test_a_baseline_says_what_measured_it_and_an_older_one_says_it_cannot(tmp_path):
    """Both shapes are read: the one that carries its versions, and the flat one that predates them.

    The project's own working baselines are flat files in `out/`, and throwing them away would have
    cost the comparisons four spec sections rest on. So they are still read — and the run says, every
    time, that they do not state what measured them.
    """
    new_shape = tmp_path / "new.json"
    new_shape.write_text(
        json.dumps({"tools": {"solidcraft_cli": "0.30.1", "cadmpeg": "0.6.8"}, "files": {"a.f3d": {}}})
    )
    assert _load_baseline(new_shape) == ({"solidcraft_cli": "0.30.1", "cadmpeg": "0.6.8"}, {"a.f3d": {}})
    flat = tmp_path / "flat.json"
    flat.write_text(json.dumps({"a.f3d": {"exact": 1}}))
    assert _load_baseline(flat) == ({}, {"a.f3d": {"exact": 1}})


def test_a_comparison_across_engine_versions_says_so_and_is_not_a_regression():
    """It is the solidcore lane's whole point, so it is a NOTE and not a failure — but never silent.

    Four spec sections were measured on solidcraft 0.30.1 while a note in hand said 0.30.0, because
    the `solidcraft-cli` on this machine is a symlink into an app that updates itself (spec section
    123). This is the line that would have said so.
    """
    same = {"solidcraft_cli": "0.30.1", "cadmpeg": "0.6.8"}
    assert _tool_notes(same, dict(same)) == []
    crossed = _tool_notes(same, {**same, "solidcraft_cli": "0.40.0-rc.3"})
    assert crossed == [
        "measured on solidcraft_cli 0.40.0-rc.3, the baseline on 0.30.1: the comparison crosses versions"
    ]
    unknown = _tool_notes({}, same)
    assert unknown == [
        "the baseline does not record which solidcraft_cli measured it; this run used 0.30.1",
        "the baseline does not record which cadmpeg measured it; this run used 0.6.8",
    ]
