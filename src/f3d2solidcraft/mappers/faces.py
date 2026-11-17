"""OffsetFaces: the motion is in the file and which faces it moves is not."""

from __future__ import annotations

from f3d2solidcraft.mappers import Context, Unsupported, definition, register
from f3d2solidcraft.model import Feature


@register("OffsetFaces")
@definition("move_face")
def map_offset_faces(feature: Feature, ctx: Context) -> list[str]:
    """Refuse an offset of faces by the faces, with the distance it would have used in the sentence.

    Twelve in the corpus — the largest family with a recipe and no mapper — and all twelve say the
    same thing: `motion` is an `offset` with a real distance, and `faces` is an opaque operand group
    (eleven `native`, one `historical`). `solid.offset_face` takes **points on the faces**, and no
    point, no face id and no `face_geometry` for them appears anywhere in the corpus.

    What the file *does* record is the input state, and it is worth saying in the refusal because it
    is the measure of how far off a guess would be: the state lists **every** face of the bodies in
    scope, 15 for the smallest of the twelve and 206 for the largest, 871 in all (spec section 118).

    | file | ordinals | distance | faces in the state |
    | --- | --- | --- | --- |
    | `Caja parametrica v2` | 23, 38 | -0.3 | 43, 15 |
    | `corpus-03` | 11, 12, 20, 47, 48 | 0.5 … -4.475 | 90 … 206 |
    | `mango para lima` | 17, 18, 20, 21, 22 | -0.2 | 41, 13 |

    **And that is also what rules out searching for them**, which would otherwise be the obvious next
    move: `edges.py` searches edge subsets against the original because a fillet's input topology
    names *its own* edges — 71 subsets at the worst, against a limit of 32. Here the file names a
    whole body's faces instead, so the subsets of a 15-face state are already 32768 and of a 206-face
    one are not worth writing down. The measurement says no, rather than a hunch.
    """
    motion = feature.face_motion
    if motion is None:
        raise Unsupported(
            f"cadmpeg decoded this face motion as {feature.definition_kind!r}, so the file hands "
            "over neither faces nor a motion"
        )
    if motion.kind != "offset":
        raise Unsupported(f"this face motion is a {motion.kind!r}, and only an offset is read")
    if motion.distance_mm is None:
        raise Unsupported("the file gives this offset of faces no distance")
    raise Unsupported(
        f"the file states this offset's distance ({motion.distance_mm:.4g} mm) and names the faces "
        f"it moves as a {motion.faces_kind!r} operand group; the input state it records lists all "
        f"{motion.state_faces} face(s) of its {motion.state_bodies} body(ies) with no geometry, so "
        "which of them moved is not in the file"
    )
