"""`OffsetFaces`: the largest family with a recipe and no mapper, and why it stays that way.

Twelve features across three files. All twelve state the motion — an `offset` with a real distance —
and name the faces as an opaque operand group. What the file records instead is the *input state*,
which lists every face of the bodies in scope with no geometry: that count is what makes the refusal
precise, and what rules out searching for the faces the way `edges.py` searches edges
(spec section 118).
"""

import pytest

from f3d2solidcraft.mappers import BY_DEFINITION, MAPPERS, Context, Unsupported
from f3d2solidcraft.model import Design, Feature

CAJA = "Caja parametrica v2.f3d"
MANGO = "mango para lima.f3d"


def _said(design):
    return {
        f.ordinal: (
            f.face_motion.kind,
            round(f.face_motion.distance_mm, 4),
            f.face_motion.faces_kind,
            f.face_motion.state_faces,
            f.face_motion.state_bodies,
        )
        for f in design.features
        if f.face_motion is not None
    }


def test_the_refusal_carries_the_distance_and_the_size_of_what_it_cannot_choose_from(corpus_ctx):
    """The distance is in the sentence because the file gives it; the face count because it is the
    measure of how far off a guess would be."""
    with corpus_ctx(CAJA) as ctx:
        feature = next(f for f in ctx.design.features if f.ordinal == 38)
        with pytest.raises(Unsupported) as refused:
            MAPPERS["OffsetFaces"](feature, ctx)
    message = str(refused.value)
    assert "distance (-0.3 mm)" in message
    assert "lists all 15 face(s) of its 1 body(ies) with no geometry" in message


def test_a_motion_that_is_not_an_offset_is_refused_by_the_motion():
    """Only an offset is read, and anything else says so rather than reading the distance."""
    from f3d2solidcraft.model import FaceMotion

    feature = Feature(
        ordinal=1,
        name="OffsetFaces 1",
        tag="OffsetFaces",
        definition_kind="move_face",
        suppressed=False,
        outputs=(),
        face_motion=FaceMotion(
            kind="translate", distance_mm=1.0, faces_kind="native", state_faces=4, state_bodies=1
        ),
    )
    design = Design(source="", parameters=[], sketches={}, features=[feature], bodies=[])
    with pytest.raises(Unsupported, match="is a 'translate', and only an offset is read"):
        MAPPERS["OffsetFaces"](feature, Context(None, design, {}, {}, {}, {}, {}))


def test_a_face_motion_cadmpeg_left_untyped_is_refused_for_having_nothing():
    feature = Feature(
        ordinal=1,
        name="OffsetFaces 1",
        tag="OffsetFaces",
        definition_kind="native",
        suppressed=False,
        outputs=(),
    )
    design = Design(source="", parameters=[], sketches={}, features=[feature], bodies=[])
    with pytest.raises(Unsupported, match="neither faces nor a motion"):
        MAPPERS["OffsetFaces"](feature, Context(None, design, {}, {}, {}, {}, {}))


def test_the_family_is_reachable_from_its_definition():
    """Which pile it is reported in — the point of a mapper that only refuses (§109)."""
    assert BY_DEFINITION["move_face"] is MAPPERS["OffsetFaces"]
