"""A sketch short one entity is still a sketch, and still not something to build a body on.

An entity whose kind this converter cannot draw used to take the whole sketch with it. Measured over
the corpus's 101 sketches, exactly one is lost that way — a `text` entity costs the other ten of its
sketch in `corpus-07.f3d` — so each entity is asked on its own now and what is
left out is named (spec section 87).

What must not follow is a body built from a profile with a piece missing: an engraving dropped and
the result reported as a rebuild. So the sketch is drawn and registered, and `ctx.partial_sketches`
keeps every feature off it.
"""

import os
from pathlib import Path

import pytest

from f3d2solidcraft.engine import Engine
from f3d2solidcraft.mappers import Context, Unsupported
from f3d2solidcraft.mappers.extrude import map_extrude
from f3d2solidcraft.mappers.sketch import build_sketch

CORPUS = Path(os.environ.get("F3D2SC_CORPUS", "~/.f3d2solidcraft/corpus/propios")).expanduser()


def test_a_feature_will_not_build_on_it(design, tools, tmp_path):
    """The point of recording it: an extrude over such a sketch refuses instead of building short.

    No extrude of the file with the text names that sketch, and no sketch of a file with a supported
    extrude has an entity this converter cannot draw — so the pair does not occur in the corpus and
    this is the guard with no file, the way `test_components` exercises a placement the corpus does
    not have. The design, its sketch and its extrude are the sample's own; the one thing perturbed is
    the converter's own note that the sketch came out short.
    """
    ordered = sorted(design.features, key=lambda f: f.ordinal)
    sketch_feature = next(f for f in ordered if f.tag == "Sketch")
    extrude = next(f for f in ordered if f.extrude is not None and f.extrude.sketch)
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        ctx = Context(
            engine=eng,
            design=design,
            param_names={},
            sketch_names={},
            curve_ids={},
            body_names={},
            param_expressions={},
        )
        build_sketch(design.sketches[sketch_feature.sketch], "probe", ctx)
        assert ctx.partial_sketches == set(), "the sample draws whole; nothing is short here"
        ctx.sketch_names[extrude.extrude.sketch] = "probe"
        ctx.partial_sketches.add(extrude.extrude.sketch)
        with pytest.raises(Unsupported, match="missing an entity this converter cannot draw"):
            map_extrude(extrude, ctx)


def test_a_sketch_with_no_curves_at_all_still_builds(design, tools, tmp_path):
    """Nothing to draw is not the same as nothing drawable, and the bench is what caught it.

    Three corpus sketches hold points and no curves — `test weird box`'s ordinals 6 and 7 and
    `soporte controles`'s ordinal 3 — and they build empty and `exact` as they always have. Guarding
    the all-refused case on `not drawable` alone took all three down to `this sketch has nothing to
    draw`, which the suite did not notice and the bench did: `rebuilt features 11 -> 9`.
    """
    empty = next(
        s for s in design.sketches.values() if s.entities and all(e.kind == "point" for e in s.entities)
    )
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        ctx = Context(
            engine=eng,
            design=design,
            param_names={},
            sketch_names={},
            curve_ids={},
            body_names={},
            param_expressions={},
        )
        assert build_sketch(empty, "empty", ctx) == []
    assert ctx.sketch_names[empty.id] == "empty"
    assert empty.id not in ctx.partial_sketches
