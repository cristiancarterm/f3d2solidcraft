"""The corpus's one pattern: half a recipe, and a seed that is out of reach as well.

`Caja parametrica v2`'s `Mirror 4` (ordinal 33) resolves its **seed** and leaves its **pattern**
unresolved: the file says it is a mirror and does not say about what plane. These tests pin both
halves, and the measurement that says the seed is no better off than the plane — the body id it
names is produced thirteen ordinals later and is already condemned by then (spec section 116).
"""

import dataclasses

import pytest

from f3d2solidcraft.engine import Engine
from f3d2solidcraft.mappers import BY_DEFINITION, MAPPERS, Context, Unsupported
from f3d2solidcraft.model import Design
from f3d2solidcraft.replay import replay

CAJA = "Caja parametrica v2.f3d"
SEED = "f3d:brep/ebf3be60-0ec9-4813-9845-66152ba3b85c.smbh/brep:entity#4"


def _mirror(design):
    return next(f for f in design.features if f.ordinal == 33)


def test_the_reader_reads_the_one_pattern_of_the_corpus_whole(corpus_design):
    """Half resolved and half not, which is the whole character of this family."""
    pattern = _mirror(corpus_design(CAJA)).pattern
    assert pattern.form == "mirror"
    assert pattern.pattern_kind == "unresolved"
    assert pattern.seed_kind == "bodies"
    assert pattern.seed_bodies == (SEED,)


def test_the_refusal_blames_the_pattern_and_not_the_seed(corpus_ctx):
    """The plane is what the **file** leaves out, so it is what the refusal names (section 104)."""
    with corpus_ctx(CAJA) as ctx:
        feature = _mirror(ctx.design)
        with pytest.raises(Unsupported, match="resolves nothing of the pattern itself"):
            MAPPERS["Mirror"](feature, ctx)


def test_a_pattern_whose_seed_is_not_rebuilt_says_so_once_the_plane_is_there(corpus_design):
    """The second refusal, reached by giving the pattern the one thing the file withholds.

    Not reachable on the corpus — its one pattern never gets past the check above — so the plane is
    handed over here to prove the next sentence is the right one.
    """
    feature = _mirror(corpus_design(CAJA))
    resolved = dataclasses.replace(
        feature, pattern=dataclasses.replace(feature.pattern, pattern_kind="resolved")
    )
    design = Design(source="", parameters=[], sketches={}, features=[resolved], bodies=[])
    with pytest.raises(Unsupported, match="1 seed body\\(ies\\) of this mirror pattern were not rebuilt"):
        MAPPERS["Mirror"](resolved, Context(None, design, {}, {}, {}, {}, {}))


def test_the_seed_the_file_resolves_is_not_a_body_the_replay_can_reach(corpus_design, tools, tmp_path):
    """The measurement: `resolved` in the file is not the same as reachable in the run.

    The timeline is cut at ordinal 32, right before the mirror, and the design is replayed. By then
    **nine features have fallen** — eleven under cadmpeg 0.6.8, and 0.6.11 rebuilds two of them: the
    file's two revolves at ordinals 21 and 32, which it resolves where 0.6.8 left them `unresolved`
    (spec section 136). Nine is still enough that not one of the file's body ids is bound —
    `body_names` is empty although four bodies stand in the engine — and the mirror's own seed is
    among the bodies already condemned for the fallback import, which is what this measures.

    The file also places that id thirteen ordinals **later**: `Combine 3` at ordinal 46 is what
    declares `…brep:entity#4` as its output. So the id names the design's finished third body, not
    the body the mirror operated on.
    """
    whole = corpus_design(CAJA)
    before = dataclasses.replace(whole, features=[f for f in whole.features if f.ordinal <= 32])
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        result, ctx = replay(before, eng, None)
        standing = eng.body_names()
    # Four bodies stand in the engine and not one of them answers to an id of the file.
    assert len(standing) == 4
    assert ctx.body_names == {}
    assert sum(1 for r in result.features if r.status in ("fallback", "failed")) == 9
    assert SEED in result.fallback_bodies
    # And who declares that id, in the file's own records: thirteen ordinals after the mirror.
    producer = [f.ordinal for f in whole.features if SEED in f.outputs]
    assert producer == [46]


def test_the_report_no_longer_files_mirror_as_a_mapper_still_to_write():
    """Which pile the family is reported in — the point of a mapper that only refuses (§109)."""
    assert BY_DEFINITION["pattern"] is MAPPERS["Mirror"]
