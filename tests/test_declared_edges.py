"""The edges a fillet used, read from the file instead of searched for.

Since IR 10 the file carries a point inside each edge a round or a bevel used, and only for those
edges. Where it gives one for every edge of the group the feature needs no oracle, no subsets and no
budget: the size is declared, the edges are declared, and the engine takes exactly a point on each
edge. That is `exact`, by the same rule as an extrude of a whole sketch (spec section 107).

The subject is `Caja parametrica v2`, which carries the two things this needed together: fillets
whose edges the file resolves, and the one placed component of the corpus.
"""

import dataclasses

import pytest

from f3d2solidcraft.mappers import MAPPERS, Unsupported
from f3d2solidcraft.mappers.params import add_parameters

CAJA = "Caja parametrica v2.f3d"


def _edge_ops(design):
    return {f.ordinal: f for f in design.features if f.edge_op is not None}


def _build_body(ctx, extrude_ordinal: int):
    """Draw the sketch an extrude uses and run it, which is how its body comes to exist."""
    extrude = next(f for f in ctx.design.features if f.ordinal == extrude_ordinal)
    sketch = next(f for f in ctx.design.features if f.tag == "Sketch" and f.sketch == extrude.extrude.sketch)
    MAPPERS["Sketch"](sketch, ctx)
    MAPPERS["Extrude"](extrude, ctx)
    return extrude


def test_the_reader_takes_the_points_only_when_the_file_gives_them_all(corpus_design):
    """What the file says, pinned: five edge operations, every one with all of its points.

    Re-measured on cadmpeg 0.6.11. Under 0.6.8 the two chamfers were `historical_partial` — the file
    resolved some of their edges and not the rest — and carried **no** points, because a partial
    reading is no reading: rounding 2 of 4 edges and reporting it as read from the file is the false
    zero this converter is built not to produce. 0.6.11 resolves them whole: each declares 8 edges
    and gives 8 points, so there is no partial case left in the corpus (spec section 136). This is
    where `Caja parametrica v2`'s two new `exact` features come from.

    The rule the test guards has not changed — points are taken only when the file gives them all —
    only the file no longer holds an example of the half-given case.
    """
    ops = _edge_ops(corpus_design(CAJA))
    assert {o: len(f.edge_op.edge_points) for o, f in sorted(ops.items())} == {
        3: 4,
        4: 8,
        9: 4,
        10: 8,
        50: 1,
    }
    # The count the file states and the points it gives are the same edges, where it gives any.
    for feature in ops.values():
        points = feature.edge_op.edge_points
        assert not points or len(points) == feature.edge_op.edge_count


def test_a_fillet_whose_edges_the_file_names_is_built_from_them(corpus_ctx):
    """`Fillet 3` at ordinal 3: four edges, read and applied, with no oracle in the context.

    `corpus_ctx` has no oracle at all, which is the point — this path asks for none. The body comes
    from the extrude at ordinal 2, which declares no output of its own, so nothing maps it to a
    Fusion id and this used to end with "its body was not rebuilt natively".
    """
    with corpus_ctx(CAJA) as ctx:
        assert ctx.oracle is None
        add_parameters(ctx)
        ops = _edge_ops(ctx.design)
        extrude = _build_body(ctx, 2)
        # The extrude declares no output, so its body is an orphan the context keeps.
        assert extrude.outputs == ()
        assert len(ctx.orphan_bodies) == 1
        before = next(b for b in ctx.engine.measure()["bodies"] if b["name"] == ctx.orphan_bodies[0])
        said = MAPPERS["Fillet"](ops[3], ctx)
        assert said == ["4 edges read from the file, with a point inside each one"], said
        after = next(b for b in ctx.engine.measure()["bodies"] if b["name"] == before["name"])
        # A round takes material off a convex corner and adds the cylinders back: fewer edges here,
        # and less volume. What matters is that it ran at all on the edges the file named.
        assert after["volume_mm3"] < before["volume_mm3"]
        assert after["faces"] > before["faces"]


def test_a_fillet_in_a_placed_component_needs_its_points_carried_into_the_world(corpus_ctx):
    """The same feature in the `Top`, which is placed: its points are in the component's frame.

    `Caja parametrica v2`'s `Top` is the one placed component of the corpus, and the point its
    `Fillet 3` names came back from the engine as "no edge at [...]" until `placed_point` was
    applied. The `Bottom` sits at the identity, which is why ordinal 3 worked without it — the third
    time one coordinate frame hid behind another in this corpus.

    This test needs the components built, because the correction is the component's placement.
    """
    from f3d2solidcraft.mappers.components import create_components

    with corpus_ctx(CAJA) as ctx:
        add_parameters(ctx)
        assert create_components(ctx) == []
        ops = _edge_ops(ctx.design)
        from f3d2solidcraft.mappers.components import activate_owner

        for ordinal in (2, 8):
            extrude = next(f for f in ctx.design.features if f.ordinal == ordinal)
            sketch = next(
                f for f in ctx.design.features if f.tag == "Sketch" and f.sketch == extrude.extrude.sketch
            )
            activate_owner(sketch, ctx)
            MAPPERS["Sketch"](sketch, ctx)
            activate_owner(extrude, ctx)
            MAPPERS["Extrude"](extrude, ctx)
            fillet = ops[ordinal + 1]
            said = MAPPERS["Fillet"](fillet, ctx)
            assert said == ["4 edges read from the file, with a point inside each one"], (ordinal, said)


def test_the_chamfer_the_file_now_resolves_whole_is_built_from_its_own_points(corpus_ctx):
    """Ordinal 4, re-measured on cadmpeg 0.6.11: the partial case became a complete one.

    Under 0.6.8 this chamfer was `historical_partial` with no points at all, and this test asserted
    that the path declined and left the search to answer — with no oracle, that was a refusal. 0.6.11
    resolves all 8 of its edges and gives a point inside each, so the chamfer is built from what the
    file says and needs no search and no oracle (spec section 136).

    The body has to be the one Fusion rounded, which is the extrude **after its fillet** and inside
    its own component: asked against the bare extrude the engine places 4 of the 8 points and refuses
    4, and says so — "this is not the body Fusion rounded". That refusal is the measurement that
    fixes the setup below.
    """
    from f3d2solidcraft.mappers.components import activate_owner, create_components

    with corpus_ctx(CAJA) as ctx:
        add_parameters(ctx)
        assert create_components(ctx) == []
        ops = _edge_ops(ctx.design)
        chamfer = ops[4]
        assert chamfer.edge_op.edge_count == 8
        assert len(chamfer.edge_op.edge_points) == 8
        extrude = next(f for f in ctx.design.features if f.ordinal == 2)
        sketch = next(
            f for f in ctx.design.features if f.tag == "Sketch" and f.sketch == extrude.extrude.sketch
        )
        activate_owner(sketch, ctx)
        MAPPERS["Sketch"](sketch, ctx)
        activate_owner(extrude, ctx)
        MAPPERS["Extrude"](extrude, ctx)
        activate_owner(ops[3], ctx)
        MAPPERS["Fillet"](ops[3], ctx)
        activate_owner(chamfer, ctx)
        said = MAPPERS["Chamfer"](chamfer, ctx)
        assert said == ["8 edges read from the file, with a point inside each one"], said


def test_the_orphan_body_is_used_only_when_there_is_exactly_one(corpus_ctx):
    """Two bodies with no output to name them is nothing to choose between, so it is refused.

    The orphan is the body the feature right before built and the file had no id for. One is an
    answer; more than one is the same open question `no_target` refuses for a join.
    """
    with corpus_ctx(CAJA) as ctx:
        add_parameters(ctx)
        fillet = _edge_ops(ctx.design)[3]
        ctx.orphan_bodies = ("Body1", "Body2")
        with pytest.raises(Unsupported, match="its body was not rebuilt natively"):
            MAPPERS["Fillet"](fillet, ctx)


def test_points_the_body_has_no_edge_at_are_refused_and_said_so(corpus_ctx):
    """The engine's refusal is the check on all of this, and it reaches the feature's reason.

    Nothing proves the orphan body is the one the file meant, so the points are the proof: a body
    that is not this feature's has no edge where the file says. Moved 50 mm, the same four points
    land nowhere, the search takes over, and its refusal carries both halves.
    """
    with corpus_ctx(CAJA) as ctx:
        add_parameters(ctx)
        ops = _edge_ops(ctx.design)
        _build_body(ctx, 2)
        moved = dataclasses.replace(
            ops[3],
            edge_op=dataclasses.replace(
                ops[3].edge_op,
                edge_points=tuple((x + 50.0, y, z) for x, y, z in ops[3].edge_op.edge_points),
            ),
        )
        with pytest.raises(Unsupported, match="the edges the file names were refused: .*no edge at"):
            MAPPERS["Fillet"](moved, ctx)
