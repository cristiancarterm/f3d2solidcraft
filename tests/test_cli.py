import json
import os
from pathlib import Path

import pytest

from f3d2solidcraft.cli import main
from f3d2solidcraft.engine import Engine
from f3d2solidcraft.mappers import MAPPERS, Unsupported

REFERENCE_NAME = "f3d2solidcraft reference"


def test_convert_writes_design_and_report(sample_f3d, tools, tmp_path):
    out = tmp_path / "box.solidcraft"
    rep = tmp_path / "box.report.json"
    code = main(["convert", str(sample_f3d), "-o", str(out), "--report", str(rep)])
    assert code == 0
    assert out.is_file() and out.stat().st_size > 0
    report = json.loads(rep.read_text())
    assert report["verification"]["ok"] is True
    assert report["counts"]["exact"] >= 2
    assert len(report["features"]) == 26
    # The truth for this sample under cadmpeg 0.6.8: two of its three bodies are the replay's own.
    # 0.6.7 typed the first extrude `sketch_profiles` with an index no region matched, so it fell
    # back and took the bodies with it; 0.6.8 types it `sketch_regions` with a trimmed region, which
    # the mapper measures and rebuilds — so the rebuild check is exercised now and runs on the
    # parameter `fusion_d11` without error (spec section 95).
    assert report["bodies"] == {"native": 2, "imported": 1}
    assert report["verification"]["rebuild_parameter"] == "fusion_d11"
    assert report["verification"]["rebuild_error"] is None
    # What the run spent of the oracle travels with the counts: this sample asks it nothing, and a
    # run that crossed the clock ceiling would say so here instead of in nobody's notes (§131).
    assert report["oracle"] == {
        "asked": 0,
        "budget": 60,
        "seconds": 0.0,
        "clock_ceiling": 400.0,
        "clock_exceeded": False,
    }


def test_convert_takes_paths_spelt_in_8_3_short_names(sample_f3d, tools, tmp_path, short_name):
    """The user's own paths can carry 8.3 short names too, not just the temp dir, and SolidCraft
    refuses any path spelt that way: the input and the output's folder here are both given short.
    """
    folder = tmp_path / "a folder with a long name"
    folder.mkdir()
    short = short_name(folder)
    f3d = short_name(sample_f3d)
    code = main(
        ["convert", str(f3d), "-o", str(short / "box.solidcraft"), "--report", str(short / "box.json")]
    )
    assert code == 0
    assert (folder / "box.solidcraft").is_file()
    assert json.loads((folder / "box.json").read_text(encoding="utf-8"))["verification"]["ok"] is True


def test_convert_summary_says_where_bodies_come_from(sample_f3d, tools, tmp_path, capsys):
    assert main(["convert", str(sample_f3d), "-o", str(tmp_path / "box.solidcraft")]) == 0
    text = capsys.readouterr().out
    assert "  bodies: 2 native, 1 imported" in text
    assert "  rebuild: fusion_d11 ok" in text
    assert "  verified: yes" in text


def test_convert_refuses_to_overwrite(sample_f3d, tools, tmp_path):
    out = tmp_path / "box.solidcraft"
    out.write_text("keep me")
    assert main(["convert", str(sample_f3d), "-o", str(out)]) == 1
    assert out.read_text() == "keep me"


def test_inspect_lists_the_timeline(sample_f3d, tools, capsys):
    assert main(["inspect", str(sample_f3d)]) == 0
    text = capsys.readouterr().out
    assert "Extrude" in text and "SurfaceDeleteFace" in text


def test_convert_with_force_replaces_output(sample_f3d, tools, tmp_path):
    out = tmp_path / "box.solidcraft"
    rep = tmp_path / "box.report.json"
    out.write_text("old")
    rep.write_text("old")
    code = main(["convert", str(sample_f3d), "-o", str(out), "--report", str(rep), "--force"])
    assert code == 0
    assert out.stat().st_size > 100 and out.read_bytes() != b"old"
    assert json.loads(rep.read_text())["verification"]["ok"] is True
    assert sorted(p.name for p in tmp_path.iterdir()) == ["box.report.json", "box.solidcraft"]


def test_convert_refuses_to_overwrite_report(sample_f3d, tools, tmp_path):
    out = tmp_path / "box.solidcraft"
    rep = tmp_path / "box.report.json"
    rep.write_text("keep me")
    assert main(["convert", str(sample_f3d), "-o", str(out), "--report", str(rep)]) == 1
    assert rep.read_text() == "keep me"
    assert not out.exists()


CORPUS = Path(os.environ.get("F3D2SC_CORPUS", "~/.f3d2solidcraft/corpus/propios")).expanduser()


def _corpus_file(name: str) -> Path:
    path = CORPUS / name
    if not path.exists():
        pytest.skip(f"private corpus file not found: {path}")
    return path


PACKAGES = Path(os.environ.get("F3D2SC_PACKAGES", "~/.f3d2solidcraft/corpus/propios-f3z")).expanduser()


def test_the_saved_design_keeps_nothing_of_the_reference(sample_f3d, tools, tmp_path):
    """The oracle imports the original into the same session, so the output must not carry it."""
    out = tmp_path / "box.solidcraft"
    assert main(["convert", str(sample_f3d), "-o", str(out)]) == 0
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("open", {"path": str(out)})
        opened = eng.call("inspect_design", {"measure": False, "sketches": False})
    assert opened["bodies"], "nothing was read back; this proves nothing about the reference"
    names = [t.get("name") for t in opened["timeline"]]
    assert REFERENCE_NAME not in names, f"the reference import survived in the timeline: {names}"
    # A body the delete missed would point at a feature that is no longer in the timeline.
    timeline_ids = {t["id"] for t in opened["timeline"]}
    orphans = [b["name"] for b in opened["bodies"] if b["feature"] not in timeline_ids]
    assert orphans == [], f"bodies left behind by a deleted feature: {orphans}"


def test_a_mapper_is_handed_the_oracle_during_a_real_convert(sample_f3d, tools, tmp_path):
    """Context.oracle really reaches a mapper mid-conversion, not just in a unit test."""
    seen: list[bool] = []

    def peek(feature, ctx):
        seen.append(ctx.oracle is not None)
        raise Unsupported("registered only to look at the oracle")

    rep = tmp_path / "box.report.json"
    # Unmapped until now, so the feature falls back either way and the conversion is unchanged.
    MAPPERS["SurfaceDeleteFace"] = peek
    try:
        out = tmp_path / "box.solidcraft"
        assert main(["convert", str(sample_f3d), "-o", str(out), "--report", str(rep)]) == 0
    finally:
        del MAPPERS["SurfaceDeleteFace"]
    report = json.loads(rep.read_text())
    assert seen, "the mapper never ran, so nothing was checked"
    assert all(seen), "a mapper ran without the reference solid to ask"
    assert [w for w in report["warnings"] if w.startswith("no reference solid")] == []
    assert report["verification"]["ok"] is True


def test_regressions_ignore_a_baseline_that_failed():
    from f3d2solidcraft.cli import _regressions

    was = {"ok": False, "error": "boom", "seconds": 0.1, "peak_mb": 40.0}
    now = {
        "ok": True,
        "exact": 4,
        "inferred": 2,
        "fallback": 1,
        "failed": 0,
        "native": 1,
        "imported": 0,
        "missing": 0,
        "extra": 0,
        "seconds": 9.0,
        "peak_mb": 700.0,
    }
    assert _regressions("x.f3d", was, now) == []


def test_regressions_flag_fewer_rebuilt_features():
    from f3d2solidcraft.cli import _regressions

    was = {
        "ok": True,
        "exact": 8,
        "inferred": 2,
        "fallback": 1,
        "failed": 0,
        "native": 1,
        "imported": 1,
        "missing": 0,
        "extra": 0,
        "seconds": 3.0,
        "peak_mb": 600.0,
    }
    now = {**was, "exact": 6, "inferred": 2, "native": 0}
    assert _regressions("x.f3d", was, now) == [
        "x.f3d: rebuilt features 10 -> 8",
        "x.f3d: native bodies 1 -> 0",
    ]


def test_regressions_still_hold_a_failed_baseline_that_has_counts():
    """Only a count-less error row is exempt: a row that converted but failed to verify is not."""
    from f3d2solidcraft.cli import _regressions

    was = {
        "ok": False,
        "exact": 8,
        "inferred": 0,
        "fallback": 1,
        "failed": 0,
        "native": 3,
        "imported": 1,
        "missing": 1,
        "extra": 0,
        "seconds": 3.0,
        "peak_mb": 600.0,
    }
    now = {**was, "native": 0}
    assert _regressions("x.f3d", was, now) == ["x.f3d: native bodies 3 -> 0"]


def test_regressions_flag_exact_sliding_into_inferred():
    """The total of rebuilt features holds, but the file is now guessed where it used to be read."""
    from f3d2solidcraft.cli import _regressions

    was = {
        "ok": True,
        "exact": 8,
        "inferred": 0,
        "fallback": 1,
        "failed": 0,
        "native": 1,
        "imported": 1,
        "missing": 0,
        "extra": 0,
        "seconds": 3.0,
        "peak_mb": 600.0,
    }
    now = {**was, "exact": 5, "inferred": 3}
    assert _regressions("x.f3d", was, now) == ["x.f3d: exact 8 -> 5 with inferred 0 -> 3"]


def test_the_containment_survey_compares_every_reference_body(tools, tmp_path):
    """`standing_for` surveys every reference body before refusing, and the message proves it.

    "Contains 0 of the 3 reference bodies" is a sentence only a completed survey can say: it is the
    count of what every comparison showed, not of the first one. A survey that stopped early — on a
    pairing whose comparison contradicts itself, say — would have one comparison to report, and if
    one of the ones it never reached were the contained body, a sound answer would have become a
    refusal.

    This used to be asserted on `Extrude 1` at ordinal 36, whose pairing with `Bottom` really is
    unreadable (5.032 mm³ of shared volume from one side and -1476.043 from the other, spec section
    95). That feature no longer reaches the survey at all: since section 107 the features before it
    are rebuilt and measured, and they spend the oracle's budget, so it now refuses for the budget
    instead — the `F3D2SC_TRACE` lesson of section 97, that a result which needs measurement depends
    on what was measured before it. The `Fillet` at ordinal 50 is what reaches the survey now, and
    the invariant asserted here is the survey's completeness, which is what that test was for.
    """
    f3d = _corpus_file("Caja parametrica v2.f3d")
    rep = tmp_path / "caja.report.json"
    code = main(["convert", str(f3d), "-o", str(tmp_path / "caja.solidcraft"), "--report", str(rep)])
    assert code == 0
    report = json.loads(rep.read_text())
    detail = next(f["detail"] for f in report["features"] if f["ordinal"] == 50)
    assert "contains 0 of the 3 reference bodies" in detail, detail
    # And what the declaration contributed, so a refusal never reads as the file saying nothing.
    assert "the file declares Latch" in detail, detail
    # And the file still converts to the same thing: this is about which refusal, not about geometry.
    assert report["verification"]["ok"] is True
    # 15 exact until the `Top` component's geometry was placed where the file has it: two of its
    # extrudes were refused for overlapping a body already built, by 198000.000 mm³ and
    # 141.362 mm³, and that overlap was an artefact of the whole component sitting at the origin
    # on top of everything else (spec section 102).
    #
    # Then 17 until the edges a fillet used were read from the file instead of searched for. Both
    # fillets here declare no output body of their own, so nothing mapped their body to a Fusion id
    # and both were refused for "its body was not rebuilt natively" — with the body standing in the
    # engine. With the orphan body found and the file's points carried into the `Top`'s placement,
    # the two are `exact` and one chamfer behind them reaches the search and comes back `inferred`
    # (spec section 107).
    #
    # Then 21 exact, 0 inferred and 14 failed on cadmpeg 0.6.11, which resolves both of this file's
    # revolves and both of its chamfers' edge sets (spec section 136).
    assert report["counts"] == {"exact": 21, "inferred": 0, "fallback": 2, "failed": 14, "skipped": 0}
