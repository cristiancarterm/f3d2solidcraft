import dataclasses
import os
from pathlib import Path

import pytest

from f3d2solidcraft.engine import Engine
from f3d2solidcraft.fallback import apply_fallback, original_step, same_body
from f3d2solidcraft.replay import replay
from f3d2solidcraft.verify import (
    Verification,
    check_rebuild,
    compare,
    driving_parameter,
    original_bodies,
)


def test_fallback_brings_back_every_missing_visible_body(design, tools, sample_f3d, tmp_path):
    step, check_warnings = original_step(tools.cadmpeg, sample_f3d, tmp_path / "original.step")
    assert check_warnings == []
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        result, ctx = replay(design, eng)
        warnings = apply_fallback(ctx, result, step)
        converted = eng.measure()["bodies"]
        eng.call("open", {"path": str(step)})
        original = eng.measure()["bodies"]
    # Four of this sample's bodies are hidden in Fusion and all four are condemned, not just the two
    # a feature of their own fails on: `Extrude 6` records no output body, so which of the bodies
    # standing in the design it changes is not known and every one is suspect. None of that is warned
    # about any more: `cadmpeg convert` exports hidden bodies too, so the import brings them back
    # like any other, and the warning that said otherwise was false.
    # Two of the four under cadmpeg 0.6.8, not four: the replay rebuilds two of this sample's bodies
    # now — 0.6.8 types its first extrude as a trimmed region, which the mapper translates from the
    # entity segments it names — so only the two that are still condemned come back from the import
    # (spec sections 95 and 112).
    assert sum(1 for b in design.bodies if not b.visible and b.id in result.fallback_bodies) == 2
    assert warnings == []
    # One of this sample's three bodies ends up imported and two are the replay's own: the imported
    # one is still the body every unmapped feature behind it touches, and the other two are built by
    # extrudes 0.6.8 lets us rebuild (spec section 95).
    assert sorted(result.imported_bodies) == ["cadmpeg_model (3)"]
    names = sorted(b["name"] for b in converted)
    # Two of the three are the replay's own. **Which number the engine gave them is not pinned**:
    # it is an allocation counter, and translating the trimmed region moved it from `Body1`/`Body2`
    # to `Body2`/`Body3` without changing one fact about the design (spec section 112).
    natives = [n for n in names if n not in result.imported_bodies]
    assert len(natives) == 2 and all(n.startswith("Body") for n in natives), names
    assert len(converted) == len(original)
    for o in original:
        assert sum(same_body(o, c) for c in converted) == 1, o["name"]


def test_converted_sample_matches_the_original(design, tools, sample_f3d, tmp_path):
    step, _ = original_step(tools.cadmpeg, sample_f3d, tmp_path / "original.step")
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        result, ctx = replay(design, eng)
        apply_fallback(ctx, result, step)
        rebuild = check_rebuild(ctx, result)
        parameter = driving_parameter(ctx, result)
        converted = eng.measure()["bodies"]
        original = original_bodies(eng, step)
    verification = compare(original, converted)
    verification.rebuild_parameter = parameter
    # Two native bodies survive on this sample under cadmpeg 0.6.8, so the rebuild check has
    # something honest to nudge and it runs: `fusion_d11` is moved and restored without error. On
    # 0.6.7 every body here was imported and there was nothing to try (spec section 95).
    assert rebuild is None
    assert verification.rebuild_parameter == "fusion_d11"
    assert len(result.imported_bodies) == 1
    assert len(verification.matched) == 3
    assert verification.missing == []
    assert verification.extra == []
    assert all(m.faces_equal for m in verification.matched)
    assert verification.ok


def _parameter_value(eng, name):
    return next(p["value"] for p in eng.execute("parameters.list", {})["parameters"] if p["name"] == name)


def test_rebuild_check_moves_a_native_body_and_restores_the_parameter(design, tools):
    """`Sketch 3` + `Extrude 3`, with no oracle and nothing measured.

    It used to be the sample's first sketch and extrude. cadmpeg 0.6.8 types that extrude as a
    trimmed region — no loop to translate and no index to resolve — so the only way to build it is
    to measure the regions against the original, and whether that **finishes** depends on the
    machine: the oracle spent a 60 s budget and a trial cost about 2 s. On this Mac the feature
    came back `inferred`; on the CI runner the budget ran out first and it fell back, so a test
    asserting either status was really asserting how fast the machine is (spec section 97). The
    budget is counted in questions since section 131 and that no longer happens, but the feature
    this check uses is still the one that needs no measurement at all.

    `Extrude 3` is a bare index that resolves on its own, driven by `d11`: nothing is measured, no
    budget is spent, and the native body this check nudges is there on any machine.
    """
    by_ordinal = {f.ordinal: f for f in design.features}
    two = dataclasses.replace(design, features=[by_ordinal[4], by_ordinal[5]])
    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        result, ctx = replay(two, eng)
        assert [r.status for r in result.features] == ["exact", "exact"]
        before = _parameter_value(eng, "fusion_d11")
        volume = eng.measure()["total"]["volume_mm3"]
        assert driving_parameter(ctx, result) == "fusion_d11"
        assert check_rebuild(ctx, result) is None
        assert _parameter_value(eng, "fusion_d11") == before
        assert eng.measure()["total"]["volume_mm3"] == volume


def test_compare_reports_face_mismatch_as_a_match():
    # Measured values of the sample's 75000 mm3 body; the converted copy differs only in face count.
    original = {
        "name": "cadmpeg_model (2)",
        "volume_mm3": 75000.0,
        "bbox": {
            "min": [-49.8684830325249, -40.32416836501319, 0.0],
            "max": [50.1315169674751, -25.324168365013193, 50.0],
        },
        "faces": 6,
    }
    converted = {**original, "faces": 7}
    verification = compare([original], [converted])
    assert len(verification.matched) == 1
    assert verification.matched[0].faces_equal is False
    assert verification.missing == []
    assert verification.extra == []
    assert verification.ok is False


CORPUS = Path(os.environ.get("F3D2SC_CORPUS", "~/.f3d2solidcraft/corpus/propios")).expanduser()


def test_same_body_tells_two_shapes_apart_by_where_their_mass_sits(tools):
    """Volume, bbox and face count do not determine a shape, and the net was built on those three.

    A 10 mm cube with a 4 × 4 notch at corner (0, 0) and the same cube notched at (6, 6) agree on
    every number the check used to read: 840.000 mm³, the same 0..10 box, 8 faces, 18 edges, 12
    vertices, 568.000 mm² of area. They are not the same solid, and a conversion that produced one
    where the original has the other used to verify. What separates them is the centre of mass,
    5.571 against 4.429 on two axes.

    Built with the real engine, since the point is what the kernel actually reports.
    """
    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        eng.execute("solid.box", {"length": 10, "width": 10, "height": 10, "body_name": "A"})
        eng.execute(
            "solid.box",
            {
                "length": 4,
                "width": 4,
                "height": 10,
                "corner": [0, 0, 0],
                "operation": "cut",
                "targets": ["A"],
            },
        )
        eng.execute("solid.box", {"length": 10, "width": 10, "height": 10, "body_name": "B"})
        eng.execute(
            "solid.box",
            {
                "length": 4,
                "width": 4,
                "height": 10,
                "corner": [6, 6, 0],
                "operation": "cut",
                "targets": ["B"],
            },
        )
        bodies = {b["name"]: b for b in eng.measure()["bodies"]}
    a, b = bodies["A"], bodies["B"]
    # Everything the check used to compare agrees, which is why it passed.
    assert a["volume_mm3"] == pytest.approx(b["volume_mm3"])
    assert (a["faces"], a["edges"], a["vertices"]) == (b["faces"], b["edges"], b["vertices"])
    assert a["bbox"] == b["bbox"]
    # And the two solids are not the same one.
    assert a["center_of_mass"] != pytest.approx(b["center_of_mass"])
    assert not same_body(a, b), "same_body still cannot tell these two apart"
    # A body still matches itself, which is what the fallback's dedup and the verification rely on.
    assert same_body(a, dict(a))


# The two measurements below are the engine's own, read off the conversion of a real package of the
# user's, `corpus-09.f3z`: 494 bodies of which exactly **one** is open. The engine reports
# `volume_mm3` and `center_of_mass` only for a closed, consistently oriented body, so that one
# carries neither — and reading the volume straight crashed the whole conversion (spec section 89).
OPEN_BODY = {
    "name": "3214j (3)",
    "faces": 1,
    "area_mm2": 0.3691269147743991,
    "volume_mm3": None,
    "center_of_mass": None,
    "closed": False,
    "bbox": {
        "min": [16.731, 0.5040000288832625, 2.420548670862],
        "max": [16.731, 1.5123917846647694, 3.309999773499924],
    },
}
CLOSED_BODY = {
    "name": "cadmpeg_model",
    "faces": 56,
    "area_mm2": 46.33968630829502,
    "volume_mm3": 20.60745990014442,
    "center_of_mass": [0.00014142360689089328, -2.446976012496058e-16, 1.4494259115139807],
    "closed": True,
    "bbox": {
        "min": [-1.5298871994024499, -1.275000015895204, 0.04692597190513299],
        "max": [1.5301127433783026, 1.2750000158950268, 2.851925849915],
    },
}


def test_an_open_body_is_compared_by_area_and_not_by_a_volume_it_has_not_got():
    """One open body in 494 crashed the conversion; now it compares, and only with its own kind.

    `TypeError: unsupported operand type(s) for -: 'NoneType' and 'float'`, from
    `verify.compare` reaching `same_body` on a surface. The fix is not a default of zero: a body
    with no volume and one with a volume are not the same body, and between two without one the
    surface area takes volume's place — the box and the face count alone do not determine a shape,
    which is the measurement this function's own docstring rests on.
    """
    assert same_body(OPEN_BODY, OPEN_BODY)
    assert not same_body(OPEN_BODY, CLOSED_BODY), "a surface is not the solid beside it"
    assert not same_body(CLOSED_BODY, OPEN_BODY)
    # Same box, same single face, a different surface: the area is what tells them apart.
    stretched = {**OPEN_BODY, "area_mm2": OPEN_BODY["area_mm2"] * 1.5}
    assert not same_body(OPEN_BODY, stretched)
    # And a closed body still compares as it did, which is the half that was never broken.
    assert same_body(CLOSED_BODY, CLOSED_BODY)


def test_a_verification_that_could_not_be_made_is_not_a_verification_that_passed():
    """`unverified` is the third of the three places the original is load-bearing.

    The oracle tolerated a reference it could not import, `apply_fallback` now does too, and the
    verification's own `original_bodies` was the last unprotected one: on solidcraft 0.40.0-rc.3 it
    ended the run after the design had already been saved (spec section 124).

    What it must not do is read as a pass, and it must not call the design's own bodies `extra`
    either: that would say the original lacks them, which is exactly what is not known. So every
    list stays empty and `ok` is false on its own.
    """
    unread = Verification(unverified="the original could not be read")
    assert not unread.ok
    assert (unread.matched, unread.missing, unread.extra) == ([], [], [])
    # And an empty verification with nothing wrong is still a pass, so `ok` did not simply become
    # false for every empty one.
    assert Verification().ok
