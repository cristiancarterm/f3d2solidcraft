"""The corpus's two Shells: what the file gives, and why the wall still cannot be built.

The one family of the section 138 census with a complete recipe and no mapper. The recipe is read
here and the face it names is found; what stops it is the kernel, in its own words (spec sections 141
to 143).
"""

import math

import pytest

from f3d2solidcraft.mappers import MAPPERS, Unsupported
from f3d2solidcraft.mappers.shell import PLANE_TOLERANCE, _plane

CAJA = "Caja parametrica v2.f3d"
SHELLS = (5, 11)


def _shells(design):
    return {f.ordinal: f for f in design.features if f.shell is not None}


def test_the_reader_reads_both_shells_whole(corpus_design):
    """What the file says, pinned: thickness, direction, and the removed face's boundary.

    Both of `Caja parametrica v2`'s shells are 4.5 mm and `outward`, and both name **one** removed
    face through the kernel history — not as the opaque operand group that makes `delete_face`
    unreachable (spec section 141). `feature_input_topologies` gives geometry for all 8 edges that
    bound it, which is what makes the face findable at all.
    """
    shells = _shells(corpus_design(CAJA))
    assert sorted(shells) == list(SHELLS)
    for ordinal in SHELLS:
        spec = shells[ordinal].shell
        assert spec.thickness_mm == pytest.approx(4.5)
        assert spec.outward is True
        assert len(spec.bounding_points) == 8, (ordinal, spec.bounding_points)


def test_the_bounding_edges_of_each_shell_share_one_plane(corpus_design):
    """Why the face can be found without the file naming a point on it.

    All 8 points of each shell lie in **z = 22.0**, so the removed face is planar and the body has
    exactly one face in that plane. The spread is asserted against the tolerance the mapper uses, so
    the two cannot drift apart (spec section 142).
    """
    shells = _shells(corpus_design(CAJA))
    for ordinal in SHELLS:
        points = shells[ordinal].shell.bounding_points
        axis, where = _plane(points)
        assert axis == 2, (ordinal, axis)
        assert where == pytest.approx(22.0, abs=1e-9), (ordinal, where)
        spread = max(p[2] for p in points) - min(p[2] for p in points)
        assert spread <= PLANE_TOLERANCE, (ordinal, spread)
        # x and y do vary, which is what makes z the plane rather than an accident of a flat corpus.
        assert max(p[0] for p in points) - min(p[0] for p in points) > 100.0
        assert max(p[1] for p in points) - min(p[1] for p in points) > 50.0


def test_the_face_is_found_and_the_engine_refuses_the_wall(corpus_design, tools, tmp_path):
    """The measured end of it: the face is identified, and the kernel cannot build the wall.

    The refusal carries both halves, because each is a different fact. The face **was** found — the
    one face of the body in the plane the 8 edges share, by the name `model.faces` gives it — and
    `solid.shell` then refuses the inward wall at 4.5 mm. On SolidCraft 0.43.0 the reason is *"a gap
    in its offset's loops cannot be bridged within tolerance"*; 0.40.1 said the offset of face 10
    "leaves a gap in its loops", and 0.30.1 *"the move is larger than a wall or step is thick (faces
    would cross)"*.

    Since 0.40.1 the **outward** wall is refused too — on 0.43.0, *"how far its neighbours extend to
    meet it has no certified bound"* — so the outward body 0.30.1 built and the measurement found
    wrong does not exist on these engines; the refusal says only that `outward` is not used (spec
    sections 146 and 151).

    Inward is what the measurement supports and not what `outward: true` maps to: built outward the
    body grows 4.5 mm on every side, and the three extrudes at ordinals 17, 30 and 41 go from
    `exact` to overlapping it by 87.817, 169.192 and 169.196 mm³ — they are measured against the
    original, so a body that makes them collide is not the one Fusion built. What `outward` does mean
    was not established, and the mapper says so rather than mapping it to make a count come out.

    ⚠️ The timeline is replayed up to the feature before the shell rather than assembled by hand,
    because **whether the kernel can build the wall depends on the body**: on the body as it stands
    after ordinal 2 the same inward shell succeeds, and after ordinal 4 it does not. Reproducing the
    refusal means reproducing the body (spec section 143).
    """
    import dataclasses

    from f3d2solidcraft.engine import Engine
    from f3d2solidcraft.mappers.components import activate_owner
    from f3d2solidcraft.replay import replay

    whole = corpus_design(CAJA)
    before = dataclasses.replace(whole, features=[f for f in whole.features if f.ordinal <= 4])
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        _result, ctx = replay(before, eng, None)
        shell = _shells(whole)[SHELLS[0]]
        activate_owner(shell, ctx)
        with pytest.raises(Unsupported) as refused:
            MAPPERS["Shell"](shell, ctx)
    said = str(refused.value)
    assert "the one face of the body in z = 22.0000" in said, said
    assert "8 bounding edges share" in said, said
    assert "the engine cannot build the wall inward" in said, said
    # The kernel refuses the offset, and *why* it says depends on the platform as well as the
    # version. SolidCraft 0.43.0 on macOS: "a gap in its offset's loops cannot be bridged within
    # tolerance"; the same 0.43.0 on Linux (measured on the public CI's steps, spec section 153): "an
    # edge of its offset closes to nothing: a vertex moves onto or past the edge's other end". 0.40.1
    # said "the offset of face 10 leaves a gap in its loops" and 0.30.1 "the move is larger than a
    # wall or step is thick (faces would cross)". Many wordings, one wall (spec sections 143 to 151),
    # so what is asserted is the refusal, not its words.
    assert "SMLib shell: operation: the offset is refused" in said, said
    assert "the file says `outward`, which this mapper does not use" in said, said


def test_a_plane_the_points_do_not_share_is_refused_rather_than_fitted(corpus_design):
    """A boundary whose points share no axis is refused, with the real points tilted to make one.

    Nothing synthetic: these are `Caja parametrica v2`'s own 8 bounding points, rotated 30 degrees
    about x so they no longer share a coordinate. The mapper fits no plane to them — a plane through
    three of eight points would pick a face by a number nothing measured (spec section 142).
    """
    points = _shells(corpus_design(CAJA))[SHELLS[0]].shell.bounding_points
    angle = math.radians(30.0)
    tilted = tuple(
        (x, y * math.cos(angle) - z * math.sin(angle), y * math.sin(angle) + z * math.cos(angle))
        for x, y, z in points
    )
    with pytest.raises(Unsupported, match="do not share a plane along x, y or z"):
        _plane(tilted)
