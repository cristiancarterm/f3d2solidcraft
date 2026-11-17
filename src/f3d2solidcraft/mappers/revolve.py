"""Revolve: a whole sketch turned a full circle about a world axis."""

from __future__ import annotations

import math

from f3d2solidcraft.geometry import _cross
from f3d2solidcraft.mappers import JOIN_TOUCHING, Context, Unsupported, definition, register
from f3d2solidcraft.mappers.outputs import name_outputs, no_target
from f3d2solidcraft.model import Feature, Frame, Vec3

_OPERATIONS = {"new_body": "new", "join": "join", "cut": "cut", "intersect": "intersect"}

# How far the axis may sit from the sketch's own origin and still be through it. The design
# declares its own linear tolerance — `mango para lima` says 1e-5 mm — and this is that order:
# inside it, Fusion itself cannot tell the two apart.
AXIS_ORIGIN_DUST = 1e-5
# How far the axis's direction may sit from one of the sketch's. The file writes these as exact 0.0
# and ±1.0, so this only has to be tight enough that a tilted axis never passes as a square one.
AXIS_DIRECTION_DUST = 1e-9


def sketch_axis(frame: Frame, origin: Vec3, direction: Vec3) -> tuple[str, float] | None:
    """The `solid.revolve` name for this axis in the **sketch's** own terms, or None if it has none.

    `solid.revolve` names an axis as `x` or `y` of the sketch, or `X`/`Y`/`Z` of the world. The
    world names were the obvious reading of a file that resolves its axis as an origin and a
    direction — and they are **wrong**, which the placement work found: the axis the file states is
    in the *component's* coordinates, exactly like the sketch, so naming it in the world's only
    agrees while the component sits at the identity. Once a placement moves the component, the
    engine answers `the Z axis does not lie in the sketch plane` — measured on `mango para lima`,
    where both revolves fell back the moment their component was placed (spec section 101).

    So the axis is named in the sketch's terms, which no placement can move it out of.
    `plane_spec` builds every sketch with `x` along the file's `u_axis` and `y` along
    `normal x u_axis`, so those two vectors are what the axis is compared against, through the
    sketch's own origin. The sign comes out with it: the engine turns the profile the right-hand
    way about the named axis, and the opposite direction turns the other way.
    """
    if any(abs(a - b) > AXIS_ORIGIN_DUST for a, b in zip(origin, frame.origin)):
        return None
    for name, candidate in (("x", frame.u_axis), ("y", _cross(frame.normal, frame.u_axis))):
        length = math.sqrt(sum(c * c for c in candidate))
        if length <= AXIS_DIRECTION_DUST:
            continue
        along = sum(a * b for a, b in zip(direction, candidate)) / length
        square = math.sqrt(sum(c * c for c in direction))
        if square > AXIS_DIRECTION_DUST and abs(abs(along / square) - 1.0) <= AXIS_DIRECTION_DUST:
            return name, math.copysign(1.0, along)
    return None


@register("Revolve")
@definition("revolve")
def map_revolve(feature: Feature, ctx: Context) -> list[str]:
    """Rebuild a revolve whose profile is the whole sketch and whose axis is a world axis.

    Measured over the corpus under cadmpeg 0.6.8, that is the only revolve the file resolves at all:
    2 of 5, both in `mango para lima`, both a full turn of a whole sketch into a new body about the
    Z axis. The other 3 come back `unresolved` — `Caja parametrica v2`'s two and this file's third —
    and carry no recipe to read, so they are refused by saying exactly that.

    The leverage is the cascade behind them, which was measured before the mapper was written: of
    `mango para lima`'s 18 fallback features, **two** are the file's own gaps (these revolves) and
    the other 16 read "builds on a body that is already a fallback import". Both of the design's
    bodies are born in a revolve, so the whole timeline hung off these two features.
    """
    rev = feature.revolve
    if rev is None:
        raise Unsupported(
            f"cadmpeg decoded this revolve as {feature.definition_kind!r}, so the file hands over "
            "no profile, axis or angle to rebuild it from"
        )
    if rev.construction != "resolved":
        # Before every other field, because an unresolved construction leaves them all empty and
        # any of the checks below would then blame a field the file never filled in.
        raise Unsupported(
            f"the file resolves this revolve's construction to {rev.construction!r}, so it hands "
            "over no profile, axis or angle to rebuild it from"
        )
    if not rev.solid:
        raise Unsupported("this revolve makes a surface, not a solid")
    if rev.profile_kind != "sketch":
        raise Unsupported(f"revolve profile kind {rev.profile_kind}")
    if rev.sketch is None:
        raise Unsupported("the file names no sketch for this revolve's profile")
    if rev.extent_kind != "one_sided" or rev.angle_rad is None:
        raise Unsupported(f"revolve extent {rev.extent_kind}")
    if rev.op not in _OPERATIONS:
        raise Unsupported(f"revolve operation {rev.op}")
    if rev.axis_origin is None or rev.axis_direction is None:
        raise Unsupported("the file resolves no axis for this revolve")
    sketch_name = ctx.sketch_names.get(rev.sketch)
    if sketch_name is None:
        raise Unsupported("its sketch was not rebuilt")
    frame = ctx.design.sketches[rev.sketch].frame
    if frame is None:
        raise Unsupported("the file resolves no plane for this revolve's sketch, so its axes are unknown")
    named = sketch_axis(frame, rev.axis_origin, rev.axis_direction)
    if named is None:
        origin = ", ".join(f"{c:.6g}" for c in rev.axis_origin)
        axis_direction = ", ".join(f"{c:.6g}" for c in rev.axis_direction)
        raise Unsupported(
            f"this revolve turns about the axis through ({origin}) along ({axis_direction}), which "
            "is neither axis of its sketch through the sketch's own origin, and those are the only "
            "axes a name reaches that a component's placement cannot move the profile out of"
        )
    axis, sign = named
    if rev.sketch in ctx.partial_sketches:
        # Same reason as an extrude's (spec section 87): the sketch is in the design but short an
        # entity this converter cannot draw, so turning it would turn a profile Fusion did not have.
        raise Unsupported("its sketch is missing an entity this converter cannot draw")
    operation = _OPERATIONS[rev.op]
    targets = [ctx.body_names[o] for o in feature.outputs if o in ctx.body_names]
    if operation != "new" and not targets:
        raise no_target(ctx, operation)
    params = {
        "sketch": sketch_name,
        # The whole sketch, which is what the `sketch` profile kind says and what the engine takes
        # as `profiles: "all"`. There is no index to resolve and so nothing to infer: the question
        # every other profile kind spends measurements on does not arise here. `exact`.
        "profiles": "all",
        "axis": axis,
        "angle": f"{math.degrees(rev.angle_rad) * sign:.10g} deg",
        "operation": operation,
        "name": feature.name,
        "touching": JOIN_TOUCHING,
    }
    if operation != "new":
        params["targets"] = targets
    warnings: list[str] = []
    out = ctx.engine.execute("solid.revolve", params)
    name_outputs(feature, ctx, out, warnings)
    return warnings
