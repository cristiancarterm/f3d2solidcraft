"""Shell: hollow a body out, removing the face the file names.

The one family of the §138 census that had a complete recipe and no mapper. What makes it reachable
where `OffsetFaces` and `delete_face` are not: its removed face is named through the **kernel
history**, not as an opaque `design-construction-operand-group`, and `feature_input_topologies`
gives geometry for the edges that bound it (spec sections 141 and 142).

The face is not named by a point, so it is **found** rather than read: the bounding edges of the
corpus's two shells are coplanar, so the face is planar, and the body has exactly one face in that
plane. `model.faces` then gives a point on it — `solid.shell` takes either that point or the face's
name, so nothing has to be derived by hand.
"""

from __future__ import annotations

from f3d2solidcraft.engine import CommandRefused
from f3d2solidcraft.mappers import Context, Unsupported, definition, register
from f3d2solidcraft.mappers.components import placed_point
from f3d2solidcraft.model import Feature

#: How far from the common plane a bounding point may sit and still count as on it, in mm.
#
# The corpus's two shells are exact: all 24 of their points report the same z to the last digit, so
# this is not covering a spread that was measured — it is covering the kernel's own repeatability on
# a coordinate, which elsewhere in this project shows up at 1e-8 mm on numbers of this size
# (spec section 137's closure). 1e-6 is two orders above that and still four below the 4.5 mm
# thickness this feature works at, so nothing of the geometry can hide inside it.
PLANE_TOLERANCE = 1e-6

#: How far the face's own point may sit from the bounding points' plane. Looser than the tolerance
#: above on purpose: this one is read off the engine's answer for a face it computed, not off the
#: file's own coordinates, and `model.faces` says its `centroid` is "not always on the face".
FACE_TOLERANCE = 0.01


def _plane(points: tuple[tuple[float, float, float], ...]) -> tuple[int, float]:
    """The axis the bounding points share a coordinate on, and that coordinate.

    Only an axis-aligned plane is read. A removed face that is not axis-aligned would need the
    points' own best-fit plane, and the corpus holds none — so rather than fit a plane to three
    points and trust it, this refuses and says which case it is (spec section 142).
    """
    for axis in (2, 0, 1):  # z first: both of the corpus's shells are planes of constant z
        values = [p[axis] for p in points]
        if max(values) - min(values) <= PLANE_TOLERANCE:
            return axis, sum(values) / len(values)
    raise Unsupported(
        "the edges that bound the removed face do not share a plane along x, y or z, so which face "
        "they bound is not settled by them"
    )


def _body_for(feature: Feature, ctx: Context) -> str:
    """The body this shell hollows: the one its outputs name, or the single orphan before it."""
    for output in feature.outputs:
        name = ctx.body_names.get(output)
        if name is not None:
            return name
    if len(ctx.orphan_bodies) == 1:
        return ctx.orphan_bodies[0]
    raise Unsupported("its body was not rebuilt natively")


def _faces(ctx: Context, body: str) -> list[dict]:
    answer = ctx.engine.call("execute", {"command": "model.faces", "params": {"body": body}})
    if isinstance(answer, list):
        return [f for f in answer if isinstance(f, dict)]
    if isinstance(answer, dict):
        return [f for f in (answer.get("faces") or []) if isinstance(f, dict)]
    raise Unsupported(f"model.faces answered {answer!r} for {body!r}")


def _coordinate(value, axis: int) -> float | None:
    if isinstance(value, dict):
        got = value.get("xyz"[axis])
        return float(got) if isinstance(got, int | float) else None
    if isinstance(value, list | tuple) and len(value) > axis:
        got = value[axis]
        return float(got) if isinstance(got, int | float) else None
    return None


def _pick(ctx: Context, body: str, axis: int, where: float) -> dict:
    """The body's one face in that plane, refusing when there is not exactly one.

    Both ends matter. None means the plane the file's edges describe is not a face of this body —
    the body is wrong or the points were not carried into its frame. More than one means the plane
    does not pick a face on its own, and taking either would be a guess.
    """
    on_plane = []
    for face in _faces(ctx, body):
        for key in ("point", "centroid"):
            got = _coordinate(face.get(key), axis)
            if got is not None and abs(got - where) <= FACE_TOLERANCE:
                on_plane.append(face)
                break
    if len(on_plane) == 1:
        return on_plane[0]
    said = f"{'xyz'[axis]} = {where:.4f}"
    if not on_plane:
        raise Unsupported(f"no face of {body!r} lies in the plane the removed face's edges describe ({said})")
    raise Unsupported(
        f"{len(on_plane)} faces of {body!r} lie in the plane the removed face's edges describe "
        f"({said}), so they do not say which one was removed"
    )


@register("Shell")
@definition("shell")
def map_shell(feature: Feature, ctx: Context) -> tuple[str, list[str]] | list[str]:
    spec = feature.shell
    if spec is None:
        raise Unsupported("the file does not give this shell's thickness")
    if not spec.bounding_points:
        raise Unsupported(
            "the file names this shell's removed faces as an opaque operand group, so which faces "
            "they are is not settled by anything it gives"
        )
    body = _body_for(feature, ctx)
    # The file states these in the component's coordinates, like every other point it hands over.
    points = tuple(placed_point(ctx, point) for point in spec.bounding_points)
    axis, where = _plane(points)
    face = _pick(ctx, body, axis, where)
    name = face.get("name")
    if not isinstance(name, str) or not name:
        raise Unsupported(
            f"the face of {body!r} in {'xyz'[axis]} = {where:.4f} is the removed one and the engine "
            f"gives it no name to pass to solid.shell"
        )
    found = (
        f"the removed face was found as the one face of the body in {'xyz'[axis]} = {where:.4f}, "
        f"the plane its {len(spec.bounding_points)} bounding edges share: {name}"
    )
    # Everything the feature needs is in hand — and the wall still cannot be built. Measured on both
    # of the corpus's shells (spec section 143):
    #
    # - The wall belongs **inside**: the original's `Bottom` keeps x at -75..75, and shelling outward
    #   grows the body to -79.5..79.5. Built that way the three extrudes at ordinals 17, 30 and 41 go
    #   from `exact` to overlapping the body by 87.817, 169.192 and 169.196 mm³ — they are measured
    #   against the original, so a body that makes them collide is not the body Fusion built.
    # - And `direction: inside` is refused by the kernel at this thickness, in its own words:
    #   "offset: not supported yet: the move is larger than a wall or step is thick (faces would
    #   cross)".
    #
    # On SolidCraft 0.40.1 neither direction builds (spec section 146). Inward: "the offset of face 10
    # leaves a gap in its loops, and the straight segment that would bridge it could not be
    # established within tolerance". Outward: "how far the surfaces there must be extended to meet
    # has no certified bound". So the outward body measured wrong above no longer exists to be wrong,
    # and the refusal says only that the flag is not used — which holds on either engine.
    #
    # So the honest answer is the refusal, with the engine's reason and the work that got here. A
    # body built the only way the engine accepts would be 4.5 mm too big on every side, reported as
    # rebuilt — which is the false `exact` this converter exists not to produce.
    try:
        ctx.engine.execute(
            "solid.shell",
            {
                "faces": [name],
                "thickness": f"{spec.thickness_mm} mm",
                # `inside`, and NOT what `outward` literally maps to. Said out loud because this is a
                # field the reader records and this mapper does not trust: the measurement above
                # says the wall is inward, the file says `outward: true`, and **what `outward` means
                # was not established** — only that building outward is wrong here. Mapping it
                # literally would build the wrong body; mapping it backwards to make a number come
                # out would be fitting the flag to the answer. So the measurement decides and the
                # disagreement is reported, not resolved.
                "direction": "inside",
                "body": body,
            },
        )
    except CommandRefused as exc:
        raise Unsupported(
            f"{found}, and the engine cannot build the wall inward: {exc.message}"
            + ("; the file says `outward`, which this mapper does not use" if spec.outward else "")
        ) from None
    unused = (
        "the wall was built inward; the file's `outward` flag was not used, and what it means is "
        "not settled (spec section 143)"
    )
    return [found, unused] if spec.outward else [found]
