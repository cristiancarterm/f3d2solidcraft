import math
import re

import pytest

from f3d2solidcraft.engine import Engine
from f3d2solidcraft.mappers import JOIN_TOUCHING, MAPPERS, Context
from f3d2solidcraft.mappers.params import (
    _rewrite,
    add_parameters,
    recover_user_parameters,
    solidcraft_name,
)
from f3d2solidcraft.replay import replay


def test_a_join_keeps_pieces_that_only_touch_in_one_body(tools):
    """The engine contract the conversion relies on, asserted on either engine.

    Two 10 mm boxes that meet at a single **point** are one solid of 1500 mm³ and 12 faces for the
    engine this converter ships on, and 0.40's kernel leaves them as two bodies unless the join is
    told to keep them together. Asked with `JOIN_TOUCHING` both engines leave one body, which is what
    makes the user open what Fusion had instead of a design split in two (spec section 133).
    """
    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        eng.execute("solid.box", {"length": 10, "width": 10, "height": 10, "corner": [0, 0, 0]})
        first = eng.body_names()[0]
        eng.execute("solid.box", {"length": 10, "width": 10, "height": 5, "corner": [10, 10, 10]})
        second = next(name for name in eng.body_names() if name != first)
        eng.execute(
            "solid.combine",
            {"target": first, "tools": [second], "operation": "join", "touching": JOIN_TOUCHING},
        )
        rows = eng.body_readings()
        assert len(rows) == 1, [r["name"] for r in rows]
        assert rows[0]["volume_mm3"] == 1500.0 and rows[0]["faces"] == 12, rows[0]


def test_fusion_model_parameter_names_are_prefixed():
    assert solidcraft_name("d5") == "fusion_d5"
    assert solidcraft_name("wall_thickness") == "wall_thickness"


def test_expressions_are_rewritten_to_the_prefixed_names():
    assert _rewrite("d3 * 2", {"d3": "fusion_d3"}) == "fusion_d3 * 2"


@pytest.fixture
def ctx(design, tools):
    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        yield Context(eng, design, {}, {}, {}, {}, {})


def test_parameters_are_created(ctx):
    warnings = add_parameters(ctx)
    assert warnings == []
    listed = ctx.engine.call("execute", {"command": "parameters.list", "params": {"filter": "user"}})
    names = {p["name"] for p in listed["parameters"]}
    assert {"fusion_d1", "fusion_d19", "fusion_d32"} <= names
    # what SolidCraft got, keyed by the Fusion name; the rebuild check nudges and restores exactly this
    assert ctx.param_expressions["d5"] == "50 mm"
    assert set(ctx.param_expressions) == set(ctx.param_names)


def test_first_sketch_is_rebuilt_with_its_profiles(ctx):
    add_parameters(ctx)
    first = ctx.design.features[0]
    warnings = MAPPERS["Sketch"](first, ctx)
    sketch = ctx.design.sketches[first.sketch]
    drawn = [e for e in sketch.entities if e.kind != "point"]
    assert len(ctx.curve_ids) == len(drawn)
    assert ctx.sketch_names[first.sketch]
    # d1 and d2 are the diameters of the two 50 mm circles: both must be applied, without a warning.
    assert [w for w in warnings if (" d1 " in w or " d2 " in w) and not w.startswith("ambiguous")] == []
    # Twins of the same size: d1 (two circles), d3 and d4 (four lines each); d2 then has a single candidate.
    assert sorted(w.split()[2] for w in warnings if w.startswith("ambiguous")) == ["d1", "d3", "d4"]
    assert [w for w in warnings if "profiles" in w] == []


def _by_ordinal(ctx):
    return {f.ordinal: f for f in ctx.design.features}


def test_an_extrude_matches_the_original_and_follows_its_parameter(ctx):
    """`Extrude 3` of the sample, with no oracle: the index the file gives resolves on its own.

    It used to be `Extrude 1`, which cadmpeg 0.6.8 types as a trimmed region — no index and no loop
    list, so the only way to build it is to measure the regions against an original this fixture
    deliberately withholds. `Extrude 3` is the same shape of case one sketch later: a bare index
    that resolves, driven by `d11`, building a cylinder of radius 25 between z=0 and z=50
    (spec section 95).

    Asserted against the cylinder's **analytic** volume, π·25²·50 = 98174.7704 mm³, because on
    SolidCraft 0.40.1 that is what the engine measures, to 1e-9. 0.30.1 tessellated the curved face
    and read 98163.693, 0.011 % under; the number this test used to carry was that tessellation, not
    the body (spec section 146).
    """
    add_parameters(ctx)
    by_ordinal = _by_ordinal(ctx)
    MAPPERS["Sketch"](by_ordinal[4], ctx)
    extrude = by_ordinal[5]
    assert extrude.name == "Extrude 3"
    assert extrude.extrude.distance_param == "d11"
    MAPPERS["Extrude"](extrude, ctx)
    body = ctx.engine.measure()["bodies"][0]
    assert body["volume_mm3"] == pytest.approx(math.pi * 25.0**2 * 50.0, rel=1e-9)
    assert body["bbox"]["min"][2] == pytest.approx(0.0, abs=0.01)
    assert body["bbox"]["max"][2] == pytest.approx(50.0, abs=0.01)
    assert ctx.body_names[extrude.outputs[0]] == body["name"]
    changed = ctx.engine.execute("parameters.change", {"name": "fusion_d11", "expression": "60 mm"})
    assert changed["errors"] == []
    # d11 at 60 mm: the same cylinder, taller, and again its analytic volume (spec section 146).
    assert ctx.engine.measure()["total"]["volume_mm3"] == pytest.approx(math.pi * 25.0**2 * 60.0, rel=1e-9)


def test_reversed_extrude_with_negative_parameter(ctx):
    add_parameters(ctx)
    extrude5 = next(f for f in ctx.design.features if f.name == "Extrude 5")
    assert extrude5.extrude.reversed is True
    assert extrude5.extrude.distance_param == "d19"
    sketch_feature = next(
        f for f in ctx.design.features if f.tag == "Sketch" and f.sketch == extrude5.extrude.sketch
    )
    MAPPERS["Sketch"](sketch_feature, ctx)
    MAPPERS["Extrude"](extrude5, ctx)
    name = ctx.body_names[extrude5.outputs[0]]
    body = next(b for b in ctx.engine.measure()["bodies"] if b["name"] == name)
    assert body["volume_mm3"] > 0
    assert body["bbox"]["min"][2] == pytest.approx(-15.0, abs=0.01)
    assert body["bbox"]["max"][2] == pytest.approx(0.0, abs=0.01)


def test_extrude_without_its_parameter_warns_it_is_not_parametric(ctx):
    # no add_parameters: d11 never reaches SolidCraft, so the literal length is used. `Extrude 3`
    # for the same reason as the test above (spec section 95).
    by_ordinal = _by_ordinal(ctx)
    MAPPERS["Sketch"](by_ordinal[4], ctx)
    warnings = MAPPERS["Extrude"](by_ordinal[5], ctx)
    assert "extrude distance is not parametric: d11 missing" in warnings
    # The same cylinder, so the same analytic volume (spec section 146).
    assert ctx.engine.measure()["total"]["volume_mm3"] == pytest.approx(math.pi * 25.0**2 * 50.0, rel=1e-9)


# `test_extrude_from_sketch_regions_with_a_hole` lived here, over `corpus-04`'s `Extruir 2`. On
# cadmpeg 0.6.7 that extrude resolves to `sketch_profiles` with explicit indices, so the design has
# no region profile left to build from — it is why the file gains three `exact` features. The
# behaviour it covered is covered by
# `test_a_declared_region_is_resolved_to_every_piece_the_engine_split_it_into`, which reads the same
# kind from `corpus-01` `Extrude 5` and still passes, so this one is retired rather than
# pointed at a second subject.


# This test lived here over `corpus-01` `Extrude 3`, which cadmpeg 0.6.7 resolves to the
# profile indices the file names, so it comes back `exact` with nothing to admit on a balance. It was
# retired then on the finding that no design reached `_added_inside` any more — and that finding was
# wrong. `Extrude 4` of the same design is a `sketch_selection` **join**, so it is answered by the
# balance, not by coverage: its twelve overshoot warnings come from `_added_inside` itself. The test
# is back, over that feature.


def test_a_datum_plane_under_a_spanish_label_is_routed_by_its_definition(tools, corpus_design, tmp_path):
    """`soporte controles`' two `PlanoDeObra`: no mapper under that label, one under what it is.

    The label is Spanish and has no same-length English alias, so it reaches cadmpeg untranslated,
    and on 0.6.11 cadmpeg types it `datum_plane` anyway, with a resolved frame. Until spec section
    148 the plane mapper was registered under `WorkPlane` only, so both came back "no mapper" while
    the recipe sat there whole. Both are parallel to XY, so they build exactly; the first sits off
    the z axis and says so.
    """

    design = corpus_design("soporte controles.f3d")
    planes = [f for f in design.features if f.tag == "PlanoDeObra"]
    assert [f.ordinal for f in planes] == [2, 9]
    assert all(f.frame is not None for f in planes)
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        result, _ctx = replay(design, eng)
    reports = {r.ordinal: r for r in result.features}
    for plane in planes:
        report = reports[plane.ordinal]
        assert report.status == "exact", (report.name, report.detail)
        assert "routed by the definition cadmpeg decoded for it (datum_plane)" in report.warnings[0]
    assert "the plane's own origin within XY is not carried over" in " ".join(reports[2].warnings)


def test_a_canvas_is_recorded_as_making_no_geometry(tools, corpus_design, tmp_path):
    """`soporte controles` `Lienzo 1`: a reference image on a plane, with no body of its own.

    It was reported `failed`, which reads as something that should have worked. Nothing is wrong
    with it: a canvas makes no geometry, and it condemns no body either (it has no outputs). It is
    recorded as skipped, with the reason, so the file's failures are only real ones.
    """

    design = corpus_design("soporte controles.f3d")
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        result, _ctx = replay(design, eng)
    canvas = {r.ordinal: r for r in result.features}[0]
    assert (canvas.name, canvas.tag) == ("Lienzo 1", "Lienzo")
    assert canvas.status == "skipped", (canvas.status, canvas.detail)
    assert "makes no geometry" in canvas.detail, canvas.detail


def test_a_nurbs_sketch_is_rebuilt_as_a_control_point_spline(tools, corpus_design, tmp_path):
    """The two corpus files with a spline: both sketches used to fail on `sketch entity kind nurbs`.

    cadmpeg gives the curve whole — degree, knot vector and per-pole weights — and the corpus's are
    degree 5, every weight 1, interior knots evenly spaced, which is exactly what SolidCraft's
    degree-5 control point spline is. So the poles rebuild it rather than approximate it, and a curve
    that would not agree (rational, unclamped, another degree) is refused by name. An unevenly
    knotted one is not refused any more: it comes as a chain of Bézier segments (spec section 77).

    `mango para lima` was stopped at ordinal 1 by this, ahead of ten features of its own.
    """

    for filename, ordinal, name in (
        ("mango para lima.f3d", 1, "Sketch 1"),
        ("soporte controles.f3d", 1, "Boceto 3"),
    ):
        design = corpus_design(filename)
        with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
            eng.call("new_design", {})
            result, ctx = replay(design, eng)
            sketches = eng.call("inspect_design", {"measure": False})["sketches"]
        sketch = {r.ordinal: r for r in result.features}[ordinal]
        assert sketch.name == name, (filename, sketch.name)
        assert sketch.status == "exact", (filename, sketch.status, sketch.detail)
        # The spline is a real curve in the rebuilt sketch, not a feature reported without geometry.
        feature = {f.ordinal: f for f in design.features}[ordinal]
        spline = next((s for s in sketches if s["name"] == ctx.sketch_names[feature.sketch]), None)
        assert spline is not None, (filename, [s["name"] for s in sketches])
        assert len(spline["curves"]) >= 1, (filename, spline)


# `test_a_candidate_that_cannot_be_measured_does_not_end_the_feature` lived here, over `corpus-04`
# `Extruir 3`. On cadmpeg 0.6.7 that feature resolves to profile indices the file names and comes
# back `exact` with no detail at all, so there is no unmeasurable candidate to survive: that test
# stays retired. The test below asks the oracle directly about the warned reading on that design's
# real geometry, which on 0.40.1 is no longer a warned reading at all.


def test_the_user_parameters_the_file_leans_on_are_read_off_what_it_resolved(corpus_design):
    """The fallback for a design whose user parameter records do not read.

    The reader now reads that table itself, so on `Caja parametrica v2` this rule has nothing left
    to do — which is why the design handed to it here is that file **without** its user parameters,
    exactly the shape it had before the reader could read them. The rule is what still answers for
    a document whose records fail the reader's checks: a model parameter whose whole expression is
    an undefined name states that name's value.

    Twelve of the fourteen named that way are recovered. The two that are not are the honest half:
    the file resolves no length for them (both are angles, so `value_mm` is None), and they are
    reported instead of being given a value nobody measured.
    """
    whole = corpus_design("Caja parametrica v2.f3d")
    model_only = [p for p in whole.parameters if re.fullmatch(r"d\d+(?:_\d+)?", p.name)]
    assert len(model_only) == 90 and len(whole.parameters) == 109
    recovered, warnings = recover_user_parameters(model_only)
    assert {p.name: p.value_mm for p in recovered} == {
        "CaseExteriorHeight": 22.0,
        "CaseExteriorLength": 60.0,
        "CaseExteriorWidth": 150.0,
        "CaseOverlap": 3.0000000000000004,
        "CaseThickness": 4.5,
        "LatchDiameter": 6.000000000000001,
        "LatchFrameThickness": 5.0,
        "LatchWidth": 10.0,
        "Tolerance": 0.3,
        "X_ComputedCornerRadius": 4.6000000000000005,
        "X_ComputedEdgeChamfer": 2.0,
        "X_ComputedHingeThickness": 5.0,
    }
    assert [p.expression for p in recovered if p.name == "CaseExteriorWidth"] == ["150 mm"]
    assert sorted(w.split()[2] for w in warnings) == ["LatchCutoutAngle", "LatchFrameOverhang"]
    assert all("resolves no length" in w for w in warnings)


def test_every_parameter_of_a_parametric_design_is_created(tools, corpus_design):
    """End to end on the real engine: `Caja parametrica v2` arrives whole.

    With the table read there is nothing left to refuse — all 109, the user's own among them, with
    the expressions the file holds; the model parameters that only restate them are valued and not
    created. Nothing is accepted on trust either: `parameters.add` answers
    with the value it computed, and none came out anywhere but where the file resolved it.
    """
    design = corpus_design("Caja parametrica v2.f3d")
    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        ctx = Context(eng, design, {}, {}, {}, {}, {})
        warnings = add_parameters(ctx)
        listed = eng.execute("parameters.list", {"filter": "user"})["parameters"]
    assert warnings == []
    assert len(ctx.param_names) == len(design.parameters) == 109
    names = {p["name"] for p in listed}
    # `d7 = CaseExteriorWidth` restates the name, so it is not a parameter of its own (0.4.1);
    # `d10 = 0.0 deg` is a plain number and keeps one.
    assert "CaseExteriorWidth" in names and "fusion_d10" in names and "fusion_d7" not in names
    assert {p["value"] for p in listed if p["name"] == "CaseExteriorWidth"} == {150.0}
    # the formula the designer wrote, not a number standing in for it
    formula = next(p for p in listed if p["name"] == "X_ComputedEdgeChamfer")
    assert formula["expression"].startswith("max(min(min(CaseExteriorHeight")
