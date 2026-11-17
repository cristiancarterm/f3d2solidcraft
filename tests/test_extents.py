"""Where an extrude stops: a blind length, through everything, or a face we cannot name.

The termination is a field of the extent's *side*, not of the extent, and reading it as the extent
was a real defect: a `through_all` cut came back refused as "extrude extent one_sided" — the one
part of it that was fine — and in `corpus-08.f3z` 24 features fell behind that
message. Measured over the corpus: 81 `blind`, 7 `through_all`, 4 `to_face` (spec section 104).
"""

import pytest

from f3d2solidcraft.engine import Engine


def _terminations(design):
    return sorted(f.extrude.termination for f in design.features if f.extrude is not None)


def test_the_engine_cuts_through_all_only_the_way_it_is_pointed(tools):
    """The measurement the mapper rests on: `through_all` honours `direction`.

    Every `through_all` in the corpus is a reversed cut, so a `through_all` that ignored the
    direction and went both ways would quietly cut twice as much — and all 7 would still report
    `exact`, which is the false zero this converter is built not to produce.

    A 40x40x20 slab, a 5 mm circle sketched halfway up it, cut `through_all` upwards: 785.27 mm³,
    which is the half above the sketch (pi*25*10 = 785.40 to the tessellation) and not the 1570.8
    of both halves.
    """
    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        made = eng.execute("solid.box", {"length": 40, "width": 40, "height": 20})
        slab = made["created_bodies"][0]
        before = next(b for b in eng.body_readings([slab]) if b["name"] == slab)["volume_mm3"]
        eng.execute("sketch.create", {"plane": "XY", "offset": "10 mm", "name": "middle"})
        eng.execute("sketch.circle.center", {"center": [20, 20], "radius": 5})
        eng.execute("sketch.finish", {})
        eng.execute(
            "solid.extrude",
            {
                "sketch": "middle",
                "profiles": "all",
                "through_all": True,
                "direction": "positive",
                "operation": "cut",
                "targets": [slab],
            },
        )
        after = next(b for b in eng.body_readings([slab]) if b["name"] == slab)["volume_mm3"]
    assert before == pytest.approx(32000.0, rel=1e-9)
    assert before - after == pytest.approx(785.4, rel=1e-3)
