"""The corpus's one trimmed region: what the file bounds it with, and what identifies it.

A `trimmed` region names no loop of the sketch — it gives an outer boundary of entity segments,
each with a parameter range. Sections 70 and 82 counted it and left it untranslated, and the
feature went to the measured path, where the pairing refused it. These tests pin the translation
that replaces that: the entities say *which* region of the engine it is, and the area the boundary
describes says the translation left nothing out (spec section 112).

What is pinned elsewhere, so nobody looks for it twice: `test_cli` and `test_verify` already hold
this sample at 2 native bodies and 1 imported. They passed before this change because the measured
path happened to finish on this Mac; they pass now by translation, on any machine.
"""

import dataclasses
import math

import pytest

from f3d2solidcraft.engine import Engine
from f3d2solidcraft.fallback import original_step
from f3d2solidcraft.geometry import UnsupportedGeometry, boundary_polygon, polygon_area, segment_points
from f3d2solidcraft.model import SketchEntity
from f3d2solidcraft.oracle import Oracle
from f3d2solidcraft.reader.cadir import _boundary
from f3d2solidcraft.replay import replay

# The circle `c102` of `Sketch 1`, and the bottom edge of the rectangle it cuts: the two numbers
# every assertion below is built from.
RADIUS = 25.0
BOTTOM_V = 24.99581456061835
EDGE_MM = 100.0


def _arc_segments(design):
    """Every (boundary, entity, segment) of this design where the segment names an arc."""
    for feature in design.features:
        extrude = feature.extrude
        if extrude is None:
            continue
        sketch = design.sketches.get(extrude.sketch)
        if sketch is None:
            continue
        by_id = {e.id: e for e in sketch.entities}
        for boundary in extrude.trimmed_boundaries or ():
            for segment in boundary:
                entity = by_id.get(segment.entity)
                if entity is not None and entity.kind == "arc":
                    yield boundary, entity, segment


def _reading(entity, start, end, how):
    """The segment's two endpoints under one reading of what an arc's parameter measures."""
    g = entity.geometry
    if entity.kind == "line":
        x0, y0 = float(g["start"]["u"]), float(g["start"]["v"])
        x1, y1 = float(g["end"]["u"]), float(g["end"]["v"])
        return [(x0 + (x1 - x0) * s, y0 + (y1 - y0) * s) for s in (start, end)]
    cx, cy = float(g["center"]["u"]), float(g["center"]["v"])
    r = float(g["radius"])
    if entity.kind == "circle" or how == "absolute":
        angles = (start, end)
    elif how == "normalized":  # 0..1 of the arc's own sweep
        a0, a1 = float(g["start_angle"]), float(g["end_angle"])
        angles = (a0 + (a1 - a0) * start, a0 + (a1 - a0) * end)
    else:  # an angle measured from the arc's start
        a0 = float(g["start_angle"])
        angles = (a0 + start, a0 + end)
    return [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in angles]


def _worst_gap(sketch, boundary, how):
    """The largest distance between the end of one segment and the start of the next, in mm."""
    by_id = {e.id: e for e in sketch.entities}
    ends = []
    for segment in boundary:
        entity = by_id[segment.entity]
        pair = _reading(entity, segment.start, segment.end, how)
        ends.append(pair[::-1] if segment.reversed else pair)
    return max(math.dist(ends[i][1], ends[(i + 1) % len(ends)][0]) for i in range(len(ends)))


def _one_boundary(design):
    """The sample's one trimmed boundary, which is the whole profile of its extrude.

    `region_count` is asserted here because the translation depends on it: a region list holding a
    trimmed region *and* a loop region would be half-translated, and the mapper refuses on exactly
    this comparison. The corpus's one case declares a single region and it is the trimmed one.
    """
    extrude = next(f.extrude for f in design.features if f.ordinal == 1)
    assert extrude.trimmed_regions == 1 == len(extrude.trimmed_boundaries) == extrude.region_count
    return extrude.trimmed_boundaries[0]


def test_the_reader_reads_the_one_trimmed_boundary_the_corpus_has(design):
    """Five segments: four whole edges of a rectangle and a 0.0366 rad arc of a circle.

    The shape of the record, which is what the translation rests on: the entity, the part of it the
    boundary uses, and whether the boundary runs along it backwards. The bottom edge starts at
    0.009148867715 of its length and the arc is the piece of the circle that cuts it off — the two
    numbers the parameter conventions were read off, below.
    """
    said = [(s.entity.rsplit("@200", 1)[-1], (s.start, s.end), s.reversed) for s in _one_boundary(design)]
    assert said == [
        ("c109:0", (0.009148867715291616, 0.9999999999999986), False),
        ("c110:0", (0.0, 1.0), False),
        ("c111:0", (0.0, 1.0), False),
        ("c112:0", (0.0, 1.0), False),
        ("c102:0", (1.5524975701750943, 1.5890950834146989), True),
    ]


def test_the_parameters_the_file_gives_are_the_chord_the_circle_cuts(design):
    """Why a line's parameter is a fraction of its length and a circle's is an angle in radians.

    Neither was assumed. The circle of radius 25 centred 25.0042 mm below the rectangle's bottom
    edge crosses that edge over a chord of 2*sqrt(r^2 - v^2) = 0.914887 mm, and subtends
    2*asin(a/r) = 0.0365975 rad doing it. The file's own two ranges are that chord as a fraction of
    the 100 mm edge, and that angle centred on pi/2 — to twelve figures. That agreement is the
    measurement: a wrong convention would not fail loudly, it would bound a different region.
    """
    half_chord = math.sqrt(RADIUS**2 - BOTTOM_V**2)
    half_angle = math.asin(half_chord / RADIUS)
    line, arc = _one_boundary(design)[0], _one_boundary(design)[-1]
    assert line.start == pytest.approx(2 * half_chord / EDGE_MM, abs=1e-12)
    assert arc.start == pytest.approx(math.pi / 2 - half_angle, abs=1e-12)
    assert arc.end == pytest.approx(math.pi / 2 + half_angle, abs=1e-12)


def test_the_boundary_describes_the_rectangle_less_the_circle_s_bite(design):
    """1499.997447 mm2: the 100x15 rectangle minus the circular segment, from the file alone.

    The area is computed the way the loop path computes its own — polygon, shoelace — and it is
    what says the translation is complete rather than partial. Here it is checked against the
    closed form of a circular segment, so the number is not merely reproduced from the code that
    produced it.
    """
    sketch = next(s for sid, s in design.sketches.items() if sid.endswith("@200"))
    half_chord = math.sqrt(RADIUS**2 - BOTTOM_V**2)
    segment = RADIUS**2 * math.asin(half_chord / RADIUS) - half_chord * BOTTOM_V
    described = polygon_area(boundary_polygon(sketch, _one_boundary(design)))
    assert segment == pytest.approx(0.002552845, abs=1e-9)
    assert described == pytest.approx(1500.0 - segment, rel=1e-9)


def test_a_parameter_range_on_a_kind_the_corpus_does_not_settle_is_refused():
    """A nurbs has a parameter too, and nothing measured says what this one's is. Refused.

    This was the arc's test until cadmpeg 0.6.11 put trimmed boundaries over arcs in the corpus and
    the question became measurable — see the two tests below (spec section 137). The nurbs takes its
    place: the refusal is not gone, it moved to the kind that is still unmeasured.
    """
    nurbs = SketchEntity(
        id="nurbs",
        kind="nurbs",
        construction=False,
        geometry={"degree": 3, "control_points": [], "knots": []},
    )
    with pytest.raises(UnsupportedGeometry, match="not settled by anything in the corpus"):
        segment_points(nurbs, 0.1, 0.9)


def test_a_trimmed_region_whose_boundary_is_malformed_reads_as_no_boundary():
    """Empty, not partial: a boundary short a segment bounds a different region.

    The count stays at 1 either way, so the mapper can say a region is there and that its segments
    could not be read — which is a different sentence from "the file names nothing".
    """
    good = {"region": "trimmed", "outer_boundary": [{"entity": "e", "parameter_range": [0.0, 1.0]}]}
    assert _boundary(good)[0].entity == "e"
    assert _boundary({"region": "trimmed"}) == ()
    assert _boundary({"region": "trimmed", "outer_boundary": []}) == ()
    # One good segment and one malformed: the whole boundary, not the readable half of it.
    assert _boundary({"region": "trimmed", "outer_boundary": [*good["outer_boundary"], {}]}) == ()
    assert _boundary({"region": "trimmed", "outer_boundary": [{"entity": "e"}]}) == ()
    assert (
        _boundary({"region": "trimmed", "outer_boundary": [{"entity": "e", "parameter_range": [0.0]}]}) == ()
    )
    nan = {"entity": "e", "parameter_range": [0.0, float("nan")]}
    assert _boundary({"region": "trimmed", "outer_boundary": [nan]}) == ()


def test_the_curve_ids_are_what_tells_this_sketch_s_two_rectangles_apart(design, tools, tmp_path):
    """The measurement the order of the two checks rests on, taken on the engine.

    `Sketch 1` holds two 100x15 rectangles, and only one of them is cut by the circle. So the
    engine's regions come back at **1500.000 mm2** and **1499.997 mm2** — a relative difference of
    1.7e-6, under any area tolerance worth having and far under the 1 % the loop path uses. An area
    match alone would have two answers here and no way to choose.

    The curve ids have one: the boundary's five entities map to exactly the five curves of one
    region and match no other. Hence ids to identify, area to confirm, in that order.
    """
    one_sketch = dataclasses.replace(design, features=[f for f in design.features if f.ordinal <= 0])
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        _result, ctx = replay(one_sketch, eng, None)
        finished = eng.call("inspect_design", {"measure": False})
    regions = next(s["profiles"] for s in finished["sketches"] if s["name"] == "Sketch 1")
    areas = sorted(float(p["area_mm2"]) for p in regions)
    wanted = frozenset(c for s in _one_boundary(design) for c in ctx.curve_ids.get(s.entity, ()))
    matched = [i for i, p in enumerate(regions) if frozenset(p["curves"]) == wanted]

    assert len(wanted) == 5
    assert matched == [2]
    assert float(regions[2]["area_mm2"]) == pytest.approx(1499.9974471551764, abs=1e-9)
    # The other rectangle, uncut: the same shape, 0.0026 mm2 bigger, and 1.7e-6 away in relative
    # terms. Both of these sit inside the loop path's own 1 % tolerance of each other.
    assert 1500.0 in areas
    assert abs(1500.0 - float(regions[2]["area_mm2"])) / 1500.0 < 1e-5
    # And the bite the circle takes is in there as a region of its own, which is the other reason
    # the ids are needed: it is bounded by the same two entities the boundary names.
    assert min(areas) == pytest.approx(0.002552845, abs=1e-9)


def test_building_from_the_boundary_rebuilds_two_features_that_were_behind_it(
    design, tools, sample_f3d, tmp_path
):
    """The gain, on the whole timeline with an oracle, as a conversion always runs.

    Three features, where the dependency graph of the file predicted two: `Extrude 1` itself, the
    `Extrude 2` that joins into its body, and `Extrude 6` at ordinal 21 — which the file says
    depends on the last `SurfaceDeleteFace`, still a fallback, but which *touches* the body
    ordinal 1 makes. What blocks a feature is the body it builds on, not everything the file lists
    as its dependency (spec section 112).

    And ordinal 22 then shows a wall of its own, `historical_faces`, that was hidden behind the
    cascade: the three behind it stay fallback for a reason that is now its own.
    """
    step, _ = original_step(tools.cadmpeg, sample_f3d, tmp_path / "original.step")
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        with Oracle.open(eng, step) as oracle:
            result, _ctx = replay(design, eng, oracle)
    by_ordinal = {r.ordinal: r for r in result.features}
    assert [by_ordinal[o].status for o in (1, 3, 21)] == ["exact", "exact", "exact"]
    assert "a trimmed boundary of 5 entity segments translated to region 2" in " ".join(
        by_ordinal[1].warnings
    )
    assert "historical_faces" in by_ordinal[22].detail
    assert [by_ordinal[o].status for o in (23, 24, 25)] == ["fallback"] * 3
