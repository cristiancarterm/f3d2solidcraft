"""Revolve: what the file resolves, what the engine can be told, and what it builds.

The subject is `mango para lima`, the only design of the corpus whose revolves cadmpeg 0.6.8
resolves at all — 2 of the corpus's 5. The other 3 (`Caja parametrica v2`'s two and this file's
third) come back `{"definition": "unresolved", "family": "revolve"}`.
"""

import dataclasses
import math

import pytest

from f3d2solidcraft.mappers import MAPPERS, Unsupported
from f3d2solidcraft.mappers.params import add_parameters
from f3d2solidcraft.mappers.revolve import sketch_axis
from f3d2solidcraft.model import Frame

MANGO = "mango para lima.f3d"


def _revolves(design):
    return {f.ordinal: f for f in design.features if f.tag == "Revolve"}


def _sketch_of(ctx, feature):
    return next(
        f
        for f in sorted(ctx.design.features, key=lambda f: f.ordinal)
        if f.tag == "Sketch" and f.sketch == feature.revolve.sketch
    )


# The plane `mango para lima`'s two revolves are drawn on: its normal is x, its u axis is y, so
# `plane_spec` builds the sketch with `x` along y of the world and `y` along z — the axis both
# revolves turn about.
YZ = Frame(origin=(0.0, 0.0, 0.0), normal=(1.0, 0.0, 0.0), u_axis=(0.0, 1.0, 0.0))


def test_an_axis_is_named_in_the_sketch_own_terms():
    """The axis is named `x` or `y` of the sketch, which no component placement can move it out of.

    Naming it in the world's terms is what the placement work found to be wrong: the axis the file
    resolves is in the component's coordinates, like the sketch, so a world name only agrees while
    the component sits at the identity. The sign comes out with the name, because the engine turns
    the profile the right-hand way about the axis it is given.
    """
    assert sketch_axis(YZ, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)) == ("y", 1.0)
    assert sketch_axis(YZ, (0.0, 0.0, 0.0), (0.0, 0.0, -1.0)) == ("y", -1.0)
    assert sketch_axis(YZ, (0.0, 0.0, 0.0), (0.0, 1.0, 0.0)) == ("x", 1.0)
    # In the sketch's plane and through its origin, but neither of its axes: 45° between the two.
    assert sketch_axis(YZ, (0.0, 0.0, 0.0), (0.0, 0.7071067811865476, 0.7071067811865476)) is None
    # Along an axis of the sketch but 5 mm off its origin: a different line, and no name for it.
    assert sketch_axis(YZ, (5.0, 0.0, 0.0), (0.0, 0.0, 1.0)) is None
    # Out of the sketch's plane altogether.
    assert sketch_axis(YZ, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)) is None


def test_a_recipe_can_arrive_without_the_axis_it_turns_about(corpus_design):
    """Re-measured on cadmpeg 0.6.11: a revolve can resolve and still not say where its axis is.

    Under 0.6.8 both revolves of `Caja parametrica v2` came back `unresolved` with no construction at
    all, and this test pinned that. **0.6.11 resolves both**, and corpus-wide there is no longer a
    single `unresolved` revolve — `Caja parametrica v2` reads 2 of 2 and `mango para lima` 3 of 3
    (spec section 136).

    What the file gives is not uniform, and that is what is pinned now. Ordinal 21 is whole: a full
    turn about (-65.4, 30.3, 22.0) along +y, joined. Ordinal 32 carries half a turn and **no axis at
    all** — `axis_origin` and `axis_direction` are both `None`. So the reason a revolve is refused
    moved from "the file resolves nothing" to the sharper "it resolves a recipe with no axis", which
    is the one thing a `solid.revolve` cannot be given a default for.
    """
    revolves = [f for f in corpus_design("Caja parametrica v2.f3d").features if f.tag == "Revolve"]
    assert [f.ordinal for f in revolves] == [21, 32]
    assert [f.definition_kind for f in revolves] == ["revolve", "revolve"]
    whole, axisless = revolves
    assert whole.revolve.profile_kind == "sketch" and whole.revolve.sketch is not None
    assert whole.revolve.op == "join"
    assert whole.revolve.angle_rad == pytest.approx(2 * math.pi)
    assert whole.revolve.axis_origin == pytest.approx((-65.4, 30.3, 22.0))
    assert whole.revolve.axis_direction == pytest.approx((0.0, 1.0, 0.0), abs=1e-9)
    # Half a turn, a profile, and nothing to turn it about.
    assert axisless.revolve.angle_rad == pytest.approx(math.pi)
    assert axisless.revolve.axis_origin is None
    assert axisless.revolve.axis_direction is None


def test_the_reader_reads_all_three_revolves_the_file_resolves(corpus_design):
    """Re-measured on cadmpeg 0.6.11: all three of mango's revolves carry a recipe now.

    Under 0.6.8 the third resolved to nothing and this test pinned "two and one empty". 0.6.11 reads
    it whole, and it differs from the other two in exactly one field: it **cuts** instead of making a
    body (spec section 136).
    """
    revolves = _revolves(corpus_design(MANGO))
    assert sorted(revolves) == [3, 6, 9]
    first, second, third = revolves[3], revolves[6], revolves[9]
    for feature in (first, second, third):
        assert feature.definition_kind == "revolve"
        assert feature.revolve.profile_kind == "sketch"  # the whole sketch, no index to resolve
        assert feature.revolve.sketch is not None
        assert feature.revolve.extent_kind == "one_sided"
        assert feature.revolve.angle_rad == pytest.approx(2 * math.pi)  # a full turn
        assert feature.revolve.axis_origin == (0.0, 0.0, 0.0)
        assert feature.revolve.solid is True
    # The first two differ in which way the axis points, which the mapper has to carry.
    assert first.revolve.axis_direction == (0.0, 0.0, 1.0)
    assert second.revolve.axis_direction == (0.0, 0.0, -1.0)
    assert [f.revolve.op for f in (first, second)] == ["new_body", "new_body"]
    # The third turns the same way as the first and takes material away instead of adding it.
    assert third.revolve.axis_direction == (0.0, 0.0, 1.0)
    assert third.revolve.op == "cut"


def test_a_revolve_of_the_whole_sketch_builds_the_body_it_declares(corpus_ctx):
    """`Revolve 2` at ordinal 3, measured: the solid of revolution the file asks for.

    17835.322 mm³ turned about Z between z=0 and z=73, symmetric about the axis to 11.4258 mm — a
    body of revolution and not a fragment of one. No warning: the profile is the whole sketch, so
    there is no index to resolve and nothing to infer, which is why this is reported `exact` without
    an oracle. Its output id is given the body's name, so the rest of the timeline can find it.

    17835.322 on SolidCraft 0.40.1 against 17834.253 on 0.30.1: 0.006 % more, the same direction and
    the same order as the sample's cylinder, whose analytic volume 0.40.1 hits to 1e-9 while 0.30.1
    tessellated it 0.011 % under. For this profile no analytic value was worked out, so the claim
    here is only that it moved the way a tessellation removed does (spec section 146).
    """
    with corpus_ctx(MANGO) as ctx:
        add_parameters(ctx)
        revolve = _revolves(ctx.design)[3]
        MAPPERS["Sketch"](_sketch_of(ctx, revolve), ctx)
        assert MAPPERS["Revolve"](revolve, ctx) == []
        name = ctx.body_names[revolve.outputs[0]]
        body = next(b for b in ctx.engine.measure()["bodies"] if b["name"] == name)
        assert body["volume_mm3"] == pytest.approx(17835.322, rel=1e-5)
        # 22 on SolidCraft 0.40.1, 6 on 0.30.1 — and the 6 was the engine disagreeing with itself:
        # on 0.30.1 `measure()` said 6 while `model.faces` listed 62, areas repeating in threes,
        # tessellation patches. On 0.40.1 both say 22, all distinct. So 22 is this engine's B-rep
        # face count; whether Fusion would split the surface of revolution the same way is not
        # what this measures (spec section 146).
        assert body["faces"] == 22
        assert body["bbox"]["min"][2] == pytest.approx(0.0, abs=0.01)
        assert body["bbox"]["max"][2] == pytest.approx(73.0, abs=0.01)
        for axis in (0, 1):
            assert body["bbox"]["min"][axis] == pytest.approx(-11.4258, abs=0.001)
            assert body["bbox"]["max"][axis] == pytest.approx(11.4258, abs=0.001)


def test_a_revolve_about_the_negative_axis_builds_its_body_too(corpus_ctx):
    """`Revolve 2` at ordinal 6, whose axis points down `-Z`: 17432.904 mm³, z=-25 to z=51.

    17431.609 on SolidCraft 0.30.1; 17432.904 on 0.40.1, 0.007 % more — the tessellation gone, as in
    the revolve above (spec section 146).

    A full turn closes the same body whichever way it goes round, so this measures that the
    negative direction is carried without upsetting the body — the turn's *direction* is what
    `test_the_engine_turns_the_other_way_for_a_negative_angle` measures, since the corpus has no
    partial revolve to read it from.
    """
    with corpus_ctx(MANGO) as ctx:
        add_parameters(ctx)
        revolve = _revolves(ctx.design)[6]
        MAPPERS["Sketch"](_sketch_of(ctx, revolve), ctx)
        assert MAPPERS["Revolve"](revolve, ctx) == []
        name = ctx.body_names[revolve.outputs[0]]
        body = next(b for b in ctx.engine.measure()["bodies"] if b["name"] == name)
        assert body["volume_mm3"] == pytest.approx(17432.904, rel=1e-5)
        assert body["bbox"]["min"][2] == pytest.approx(-25.0, abs=0.01)
        assert body["bbox"]["max"][2] == pytest.approx(51.0, abs=0.01)


def test_a_revolve_whose_sketch_was_not_rebuilt_is_refused_for_that(corpus_ctx):
    """Ordinal 9 of the same file, re-measured on cadmpeg 0.6.11: the gap moved one step back.

    Under 0.6.8 this revolve carried no profile, no axis and no angle, and the refusal said so —
    `cadmpeg decoded this revolve as 'unresolved'`. 0.6.11 reads it whole (a full turn about +z,
    cutting), so that refusal has nothing left to report. What stops it now is upstream of the
    revolve: its sketch is not in the design the mapper can reach, so the message is **"its sketch
    was not rebuilt"** — a cascade, not a gap in the decoder and not one of ours (spec section 136).

    Which is why `mango para lima` gained nothing from 0.6.11 in the bench even though all three of
    its revolves now resolve: the recipe arrived, the thing it needs did not.
    """
    with corpus_ctx(MANGO) as ctx:
        revolve = _revolves(ctx.design)[9]
        with pytest.raises(Unsupported, match="its sketch was not rebuilt"):
            MAPPERS["Revolve"](revolve, ctx)


def test_the_engine_turns_the_other_way_for_a_negative_angle(tools):
    """The engine measurement the sign in `world_axis` rests on, kept as a tripwire.

    `solid.revolve` takes no axis direction, only an axis name, so a `-Z` axis is said by negating
    the angle. Nothing in the corpus revolves part of a turn, so there is no file to read this off:
    it is measured here, on a 10x10 profile 10 mm out from the axis. +90° lands the body in the
    first quadrant (x and y both positive) and -90° in the fourth (y negative) — if that ever stops
    being true, the sign this mapper applies is wrong and this test is how we find out.
    """
    from f3d2solidcraft.engine import Engine

    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        quadrants = {}
        for angle in ("90 deg", "-90 deg"):
            eng.execute("sketch.create", {"plane": "XZ"})
            eng.execute("sketch.rectangle.two_point", {"p0": [10, 0], "p1": [20, 10]})
            eng.execute("sketch.finish", {})
            out = eng.execute(
                "solid.revolve", {"axis": "Z", "angle": angle, "profiles": "all", "operation": "new"}
            )
            name = out["created_bodies"][0]
            body = next(b for b in eng.body_readings([name]) if b["name"] == name)
            quadrants[angle] = body["center_of_mass"][1]
        assert quadrants["90 deg"] > 0
        assert quadrants["-90 deg"] < 0
        assert quadrants["90 deg"] == pytest.approx(-quadrants["-90 deg"], rel=1e-9)


def test_the_engine_cuts_with_a_revolve_when_it_is_given_a_target(tools):
    """The other engine measurement this mapper rests on: `operation: cut` with a named target.

    No revolve in the corpus does anything but make a new body, so there is no file to read this
    off either — and a cut reported `exact` on an unmeasured path is exactly the false zero this
    converter is built not to produce. So it is measured here: a 20 mm cube at the origin, then a
    ring profile 5 mm out turned a full circle as a cut, naming the cube. The cube must come back
    smaller and still be one body, which is what says the operation and the target both landed.
    """
    from f3d2solidcraft.engine import Engine

    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        made = eng.execute("solid.box", {"length": 20, "width": 20, "height": 20})
        cube = made["created_bodies"][0]
        before = next(b for b in eng.body_readings([cube]) if b["name"] == cube)["volume_mm3"]
        eng.execute("sketch.create", {"plane": "XZ"})
        eng.execute("sketch.rectangle.two_point", {"p0": [5, -5], "p1": [8, 5]})
        eng.execute("sketch.finish", {})
        out = eng.execute(
            "solid.revolve",
            {"axis": "Z", "angle": "360 deg", "profiles": "all", "operation": "cut", "targets": [cube]},
        )
        assert out["created_bodies"] == []
        assert out["changed_bodies"] == [cube]
        after = next(b for b in eng.body_readings([cube]) if b["name"] == cube)["volume_mm3"]
        assert after < before
        assert len(eng.body_names()) == 1


def test_a_revolve_still_builds_when_its_component_is_placed(tools, corpus_design, tmp_path):
    """The defect the placement work found: both revolves fell back the moment the component moved.

    `mango para lima`'s `File Handle` is placed at (-4.448, -2.309, 2.139) mm. While that placement
    was only reported, the revolves could be told to turn about `Z` of the world and the engine
    agreed, because the component sat at the identity. Applied, the engine answers `the Z axis does
    not lie in the sketch plane` and both revolves fall back — measured, 8 exact back down to 6.

    So this replays the real timeline with the real placement and asserts the two revolves are
    `exact`. It is the guard that the axis stays named in terms a placement cannot move.
    """
    from f3d2solidcraft.engine import Engine
    from f3d2solidcraft.replay import replay

    whole = corpus_design(MANGO)
    # The two revolves and the sketches they turn, which is all this needs: the features after them
    # build on bodies a fallback import condemns, and that is another test's subject.
    kept = [f for f in whole.features if f.ordinal in (1, 3, 5, 6)]
    design = dataclasses.replace(whole, features=kept)
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        result, _ctx = replay(design, eng)
        placed = {
            component["name"]: (component.get("occurrences") or [{}])[0].get("transform")
            for component in eng.execute("component.list", {"compact": True})["components"]
        }
    assert result.component_warnings == []
    assert placed["File Handle"][3][:3] == pytest.approx(
        [-4.447834227105019, -2.3086530688813123, 2.1391811582237086]
    )
    assert [(r.ordinal, r.status) for r in result.features] == [
        (1, "exact"),
        (3, "exact"),
        (5, "exact"),
        (6, "exact"),
    ]
