"""The Combine family: what the file says of it, and the measurement that refuses the arithmetic.

A Combine's target is resolved and its tools are an opaque `native_set`, so which bodies they are
cannot be read. The census of section 108 noticed that for two of the corpus's four the *count*
closes against the bodies of the feature's input state — 1 target and 2 tools against a state of 3 —
and that looked like a reading rather than a guess. These tests pin what the file says, and then
measure the thing that reading rests on (spec section 109).
"""

import dataclasses
import pathlib

import pytest

from f3d2solidcraft.engine import Engine
from f3d2solidcraft.fallback import original_step
from f3d2solidcraft.mappers import MAPPERS, Context, Unsupported
from f3d2solidcraft.mappers.params import add_parameters
from f3d2solidcraft.oracle import Oracle
from f3d2solidcraft.pairing import declared_reference
from f3d2solidcraft.replay import replay

CAJA = "Caja parametrica v2.f3d"


def _combines(design):
    return {f.ordinal: f.combine for f in design.features if f.combine is not None}


def test_a_combine_whose_target_is_a_state_id_is_refused_by_the_target(corpus_ctx):
    """Ordinal 46: refused by the kind of the target, which is the thing that is missing.

    Not by the arithmetic, although that does not close for it either — section 104's lesson is
    that refusing by the wrong field sends the reader looking in the wrong place.
    """
    with corpus_ctx(CAJA) as ctx:
        add_parameters(ctx)
        feature = next(f for f in ctx.design.features if f.ordinal == 46)
        with pytest.raises(Unsupported, match="names its target as 'historical'"):
            MAPPERS["Combine"](feature, ctx)


def test_a_combine_whose_count_closes_is_refused_all_the_same(corpus_ctx):
    """Ordinals 25 and 27, the two the census called reachable: refused, citing the measurement.

    This is the test that would have to change for the family to be rebuilt, so it says plainly
    what the refusal rests on rather than just matching the words.
    """
    with corpus_ctx(CAJA) as ctx:
        add_parameters(ctx)
        for ordinal in (25, 27):
            feature = next(f for f in ctx.design.features if f.ordinal == ordinal)
            assert feature.combine.target_count + feature.combine.tool_count == feature.combine.state_bodies
            with pytest.raises(Unsupported, match="joining them leaves exactly as much"):
                MAPPERS["Combine"](feature, ctx)


def test_the_other_bodies_of_the_state_are_measurably_not_the_tools(corpus_design, tools, tmp_path):
    """The measurement the refusal above rests on, taken on the real design.

    The timeline is cut after ordinal 25 — the first Combine whose count closes — and the arithmetic
    is then carried out by hand: the target is the one body the file resolves, the tools are the
    other two bodies standing, and a join is asked for. `shortfall` says how much of the reference
    body the target stands in for is still uncovered, before and after.

    **It does not move.** The two bodies the count leaves over contribute nothing to that body, so
    they are not its tools and the count does not name them. A join only ever adds material, which
    is what makes this reading sound: tools that belonged to this body would cover part of what it
    is missing, and these cover none of it.

    `replay_bodies` is spelled out here rather than imported, because this is also where the trap
    behind it was measured: with the oracle open its reference bodies stand in the same design, so
    `engine.body_names()` answers 6 where the design has 3, and the first version of this gate
    counted them and refused both Combines it was written for.
    """
    whole = corpus_design(CAJA)
    design = dataclasses.replace(whole, features=[f for f in whole.features if f.ordinal <= 25])
    step, _ = original_step(tools.cadmpeg, pathlib.Path(whole.source), tmp_path / "original.step")
    feature = next(f for f in design.features if f.ordinal == 25)
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        with Oracle.open(eng, step) as oracle:
            replay(design, eng, oracle)
            mine = [n for n in eng.body_names() if n not in set(oracle.bodies)]
            assert len(mine) == feature.combine.state_bodies == 3
            assert len(eng.body_names()) == 6  # the design's three and the reference's three
            target, tool_names = mine[0], mine[1:]
            reference = oracle.standing_for(target, declared_reference(design, feature, oracle.bodies))
            before = oracle.shortfall(target, reference)
            eng.execute("solid.combine", {"target": target, "tools": tool_names, "operation": "join"})
            after = oracle.shortfall(target, reference)
    # 2801.398 on cadmpeg 0.6.8 and 2801.357 on 0.6.11, both on SolidCraft 0.30.1; 2966.142 on
    # SolidCraft 0.40.1. The last is an exact boolean and the first two were sampled — but the gap,
    # 164.8 mm³ or 5.9 %, is three times the widest spread two samplings of a correct 0.30.1 reading
    # were measured to have (2.0 %, spec section 134), so it is not only sampling: the bodies at
    # ordinal 25 may differ between the engines too, and this does not separate the two. What the
    # test is for holds on either: the join covers none of what is missing (spec section 146).
    assert before[0] == pytest.approx(2966.142, abs=0.01)
    # Not "close enough": the same number. Joining those two bodies covered nothing of what the
    # reference was missing.
    assert after[0] == pytest.approx(before[0], abs=0.01)
    # And it did add material, so the join itself ran: this is not a no-op that measured the same
    # body twice.
    assert after[1] > before[1]


def test_the_report_no_longer_files_combine_as_a_mapper_still_to_write(corpus_ctx):
    """The point of a mapper that only refuses: which pile the family is reported in.

    `replay._unmapped` says "no mapper for Combine (cadmpeg decoded it as combine)" for a family
    with no mapper, which reads as this converter's gap still to fill. It is not one — the recipe is
    read in full, and what is missing is in the file.
    """
    from f3d2solidcraft.mappers import BY_DEFINITION

    assert "Combine" in MAPPERS
    assert BY_DEFINITION["combine"] is MAPPERS["Combine"]


def test_a_combine_cadmpeg_left_untyped_is_refused_for_having_no_operands():
    """A feature routed here whose definition carries nothing: the refusal names the decoder."""
    from f3d2solidcraft.model import Design, Feature

    feature = Feature(
        ordinal=1, name="Combine 1", tag="Combine", definition_kind="native", suppressed=False, outputs=()
    )
    design = Design(source="", parameters=[], sketches={}, features=[feature], bodies=[])
    ctx = Context(None, design, {}, {}, {}, {}, {})
    with pytest.raises(Unsupported, match="hands over no operands"):
        MAPPERS["Combine"](feature, ctx)
