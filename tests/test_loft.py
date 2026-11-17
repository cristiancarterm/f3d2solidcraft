"""The corpus's one loft: a recipe the file resolves whole, and an engine that cannot build it yet.

`soporte controles`'s `Solevación 2` is the only loft in the corpus, and unlike every wall of
section 108 there is nothing opaque in it: two sketch sections, no guides, `new_body`, open, solid.
What stops it is `solid.loft`, which answers *"not supported yet: lofting curved sections to a
point"* — so these tests pin the recipe, the rule that finds the apex, the engine's limit measured
on the engine itself, and the sentence the report carries because of it (spec section 113).

`test_the_engine_lofts_to_a_point_only_from_straight_sections` **fails when upstream fixes this**,
which is the point: it is the signal to come back and measure what the loft then buys.
"""

import dataclasses
import math

import pytest

from f3d2solidcraft.engine import Engine
from f3d2solidcraft.mappers import BY_DEFINITION, MAPPERS, Unsupported
from f3d2solidcraft.mappers.loft import _apex
from f3d2solidcraft.model import Sketch, SketchEntity
from f3d2solidcraft.replay import replay

SOPORTE = "soporte controles.f3d"
APEX, SECTION = "@2460", "@2248"
# What SolidCraft 0.30.1 said to a curved section lofted to a point, kept for the record: 0.40.1
# builds it (spec section 146). "not supported yet: lofting curved sections to a point"


def _loft(design):
    return next(f for f in design.features if f.ordinal == 5)


def _point(u: float, v: float, name: str = "p") -> SketchEntity:
    return SketchEntity(id=name, kind="point", construction=False, geometry={"position": {"u": u, "v": v}})


def _sketch(*points: SketchEntity) -> Sketch:
    return Sketch(id="s", frame=None, entities=points, profiles=(), constraints=())


def test_the_reader_reads_the_one_loft_of_the_corpus_whole(corpus_design):
    """Every field, and the point of the test is that none of them is missing.

    This is what makes the family different from the walls of section 108: the file hands over the
    whole recipe. The sections are the apex first and the profile second, in the file's order.
    """
    loft = _loft(corpus_design(SOPORTE)).loft
    assert [(s.kind, s.value.split("@")[-1]) for s in loft.sections] == [
        ("sketch", APEX.lstrip("@")),
        ("sketch", SECTION.lstrip("@")),
    ]
    assert (loft.guidance_kind, loft.guides) == ("guides", 0)
    assert loft.op == "new_body"
    assert (loft.closed, loft.solid, loft.ruled, loft.linearize) == (False, True, False, False)


def test_the_apex_is_the_one_point_left_once_the_origin_marker_is_discounted(corpus_design):
    """Three point entities in the file, one apex: the origin marker and a duplicate.

    The rule rests on a census: **all 101 sketches of the corpus** carry a point entity at exactly
    (0, 0) — 63 exactly one, the other 38 between two and ten — so a point there is something
    cadmpeg emits per sketch and not one anybody drew. This sketch's other two points are the same
    position written twice, differing at the 14th decimal.
    """
    design = corpus_design(SOPORTE)
    sketch = next(s for sid, s in design.sketches.items() if sid.endswith(APEX))
    points = [e for e in sketch.entities if e.kind == "point"]
    assert len(points) == 3
    assert _apex(sketch) == pytest.approx((-105.8825300823465, 136.24481707642244))


def test_an_apex_that_is_not_one_point_is_refused_rather_than_picked_from():
    """Nothing in the file says which point an apex is, so none and two are both refusals."""
    with pytest.raises(Unsupported, match="0 distinct position"):
        _apex(_sketch(_point(0.0, 0.0)))
    with pytest.raises(Unsupported, match="2 distinct position"):
        _apex(_sketch(_point(0.0, 0.0), _point(3.0, 4.0, "a"), _point(-3.0, 4.0, "b")))
    # Two writings of the same place are one apex, which is the corpus's own case.
    assert _apex(_sketch(_point(0.0, 0.0), _point(3.0, 4.0, "a"), _point(3.0, 4.0 + 1e-14, "b"))) == (
        3.0,
        4.0,
    )


def test_the_engine_lofts_a_curved_section_to_a_point_now(tools, tmp_path):
    """The limit this file was built around, gone in SolidCraft 0.40.1 — and measured, not assumed.

    Under 0.30.1 a section of straight edges lofted to a point and a curved section lofted to
    another curved one, but a curved section to a point was refused in either order — the one
    combination the corpus's loft needs, a degree 5 nurbs to a point (solidcraft#13072). This test
    was written to fail the day that changed: *"it is the signal to build the loft for real and
    measure what it buys"* (spec section 113).

    It failed on 0.40.1. The loft a circle of radius 10 makes to a point 30 mm away is a **cone of
    the analytic volume**, π·10²·30/3 = 3141.592654 mm³, to 1e-9 and in **both** orders — so it is the
    solid, not a body of roughly the right size (spec section 146).
    """
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        eng.execute("sketch.create", {"plane": "XZ", "name": "Circulo"})
        eng.execute("sketch.circle.center", {"center": [60, 0], "radius": 10})
        eng.execute("sketch.finish", {})
        eng.execute("sketch.create", {"plane": "XZ", "offset": "30 mm", "name": "Punta"})
        eng.execute("sketch.point", {"point": [60, 0], "id": "tip"})
        eng.execute("sketch.finish", {})
        cone = math.pi * 10.0**2 * 30.0 / 3.0
        for name, sections in (
            ("hacia la punta", [{"sketch": "Circulo"}, {"sketch": "Punta", "point": "tip"}]),
            ("desde la punta", [{"sketch": "Punta", "point": "tip"}, {"sketch": "Circulo"}]),
        ):
            out = eng.execute("solid.loft", {"sections": sections, "operation": "new", "body_name": name})
            body = next(b for b in eng.measure()["bodies"] if b["name"] == out["created_bodies"][0])
            assert body["volume_mm3"] == pytest.approx(cone, rel=1e-9), (name, body["volume_mm3"])
            assert body["bbox"]["min"][1] == pytest.approx(0.0, abs=1e-9)
            assert body["bbox"]["max"][1] == pytest.approx(30.0, abs=1e-9)


def test_the_corpus_loft_is_built_and_what_stands_behind_it_is_its_own_wall(corpus_design, tools, tmp_path):
    """`soporte controles`'s loft, built on SolidCraft 0.40.1 without a line of ours changing.

    Under 0.30.1 this asserted the refusal: the recipe read in full, handed over, and the engine's
    own sentence back. The docstring said what would happen next — *"no limit of ours is written into
    the mapper for it: if the engine stops refusing, the body gets built"* — and that is what
    happened. Ordinal 5 is `exact`, and the apex point the mapper draws now stays in its sketch
    instead of being rolled back with a refusal: no curves and two points, cadmpeg's origin marker
    and the apex.

    **And the six features behind it stay `fallback`**, so the loft bought one feature where the
    cascade map said seven. What stops them is the very next feature, ordinal 6 `Vaciado` —
    Spanish for *Shell* — which cadmpeg types as nothing at all, the way it does `Empalme`: no
    same-length English alias, so the label reaches the decoder untranslated and comes back opaque.
    A cascade hides its own wall, measured once more (spec section 146).
    """
    design = corpus_design(SOPORTE)
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        result, ctx = replay(design, eng)
        after = eng.call("inspect_design", {"measure": False})
    by_ordinal = {r.ordinal: r for r in result.features}
    loft = by_ordinal[5]
    assert loft.status == "exact", (loft.status, loft.detail)
    assert loft.detail == ""
    assert [w for w in loft.warnings if "the apex of this loft was drawn into" in w], loft.warnings
    # The wall behind it, and the cascade it holds.
    assert by_ordinal[6].status == "fallback"
    assert "cadmpeg typed no definition for Vaciado" in by_ordinal[6].detail
    assert [by_ordinal[o].status for o in (7, 8, 10, 11, 12)] == ["fallback"] * 5

    apex_name = ctx.sketch_names[next(s for s in ctx.sketch_names if s.endswith(APEX))]
    drawn = next(s for s in after["sketches"] if s["name"] == apex_name)
    assert (len(drawn.get("curves") or []), len(drawn.get("points") or [])) == (0, 2)


def test_a_loft_cadmpeg_left_untyped_is_refused_for_having_no_sections():
    """A feature routed here whose definition carries nothing: the refusal names the decoder."""
    from f3d2solidcraft.mappers import Context
    from f3d2solidcraft.model import Design, Feature

    feature = Feature(
        ordinal=1, name="Loft 1", tag="Loft", definition_kind="native", suppressed=False, outputs=()
    )
    design = Design(source="", parameters=[], sketches={}, features=[feature], bodies=[])
    with pytest.raises(Unsupported, match="hands over no sections"):
        MAPPERS["Loft"](feature, Context(None, design, {}, {}, {}, {}, {}))


def test_a_loft_shaped_by_guides_is_refused_by_the_guides(corpus_design):
    """`solid.loft` takes sections and nothing else, so a guided loft would be another body.

    Reached by giving the corpus's loft a guide it does not have, because refusing by the wrong
    field sends the reader to the wrong place (section 104's lesson).
    """
    loft = _loft(corpus_design(SOPORTE))
    guided = dataclasses.replace(loft, loft=dataclasses.replace(loft.loft, guides=2))
    from f3d2solidcraft.mappers import Context
    from f3d2solidcraft.model import Design

    design = Design(source="", parameters=[], sketches={}, features=[guided], bodies=[])
    with pytest.raises(Unsupported, match="2 guide curve"):
        MAPPERS["Loft"](guided, Context(None, design, {}, {}, {}, {}, {}))


def test_the_loft_is_reachable_from_the_definition_and_not_only_from_the_english_tag():
    """`Solevación` never matches the tag table, so the definition is the route that matters (§82)."""
    assert BY_DEFINITION["loft"] is MAPPERS["Loft"]
