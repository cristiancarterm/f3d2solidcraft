"""Fusion's primitive solids: a shape the file describes but does not place."""

from __future__ import annotations

import math

from f3d2solidcraft.mappers import Context, Unsupported, definition, register
from f3d2solidcraft.model import Feature
from f3d2solidcraft.oracle import OracleUnavailable, noise
from f3d2solidcraft.pairing import declared_reference

# A full revolution, which is the only sweep a `solid.cylinder` makes. Anything less is a wedge of a
# cylinder and SolidCraft has no primitive for it, so it is refused by name rather than drawn whole.
FULL_TURN = 2.0 * math.pi
ROUND = 1e-9

# How far the reference body's box may sit from the shape the file describes and still be it. The
# engine measures a curved body's box from a tessellation, so this is the same relative band the
# rest of the module reads volumes with, floored for a small radius.
BOX_BAND = 1e-3

_AXES = ("X", "Y", "Z")


def _extent(box: dict, axis: int) -> float:
    return float(box["max"][axis]) - float(box["min"][axis])


def _placement(box: dict, radius: float, height: float) -> tuple[str, list[float]] | None:
    """The axis a cylinder of this size runs along in this box, and the centre of its bottom.

    The file says how big the cylinder is and never where it is: `solid.cylinder` wants a `base` and
    an `axis`, and the definition carries neither. The box of the body the file says this feature
    becomes carries both, and reading them is a measurement rather than a guess — a cylinder's box
    is `2r` by `2r` by `height`, so the axis is the one extent that matches the height and the other
    two have to match the diameter. A box that does not have that shape is not this cylinder
    standing on its own, and `None` says so.
    """
    extents = [_extent(box, i) for i in range(3)]
    for axis in range(3):
        others = [extents[i] for i in range(3) if i != axis]
        band = max(BOX_BAND * height, BOX_BAND)
        if abs(extents[axis] - height) > band:
            continue
        if any(abs(e - 2.0 * radius) > max(BOX_BAND * 2.0 * radius, BOX_BAND) for e in others):
            continue
        base = [
            (float(box["min"][i]) + float(box["max"][i])) / 2.0 if i != axis else float(box["min"][i])
            for i in range(3)
        ]
        return _AXES[axis], base
    return None


@register("CylinderPrimitive")
@definition("primitive")
def map_primitive(feature: Feature, ctx: Context) -> tuple[str, list[str]]:
    """Rebuild a Fusion primitive cylinder where the original says it stands.

    Measured on a real design of the user's, `corpus-06.f3d`, whose first
    feature is one: cadmpeg types it `primitive` and gives the shape whole — `{"kind": "cylinder",
    "radius": 2.25, "height": 59.0, "angle": 6.283…}` with `op: join` — and says **nothing about
    where it is**. π·2.25²·59 is 938.3 mm³ and the finished body the file names for it measures
    938.258, so that body is this cylinder and nothing later touches it (spec section 85).

    So the size is read and the placement is measured: `_placement` takes the axis and the base from
    the box of the reference body the file names, and the result is checked against that body's
    volume before it is kept. `inferred`, never `exact`: the shape is the file's, the position is
    the original's.

    Refused by name, rather than approximated: a sweep short of a full turn (SolidCraft's cylinder
    has no wedge), a primitive that is not a cylinder, a feature whose output the file does not name
    unambiguously, and a reference box that is not this cylinder's. Without an oracle there is
    nothing to place it by at all.
    """
    solid = feature.primitive or {}
    kind = solid.get("kind")
    if kind != "cylinder":
        raise Unsupported(f"a {kind or 'nameless'} primitive is not mapped; only a cylinder is")
    radius, height, angle = solid.get("radius"), solid.get("height"), solid.get("angle")
    if radius is None or height is None or radius <= 0.0 or height <= 0.0:
        raise Unsupported("the primitive carries no usable radius and height")
    if angle is not None and abs(angle - FULL_TURN) > ROUND:
        raise Unsupported(
            f"a cylinder swept {math.degrees(angle):.6g}° instead of a full turn has no primitive here"
        )
    if ctx.oracle is None:
        raise Unsupported(
            "the file does not say where the primitive stands and there is no original to measure"
        )
    reference = declared_reference(ctx.design, feature, ctx.oracle.bodies)
    if reference is None:
        raise Unsupported("the file does not name which body this primitive becomes, so it cannot be placed")
    try:
        measured = {b["name"]: b for b in ctx.oracle.measured()}
    except OracleUnavailable as exc:
        raise Unsupported(f"the original could not be measured: {exc}") from None
    body = measured.get(reference)
    if body is None or not body.get("bbox"):
        raise Unsupported(f"the original gives no box for {reference}")
    placed = _placement(body["bbox"], float(radius), float(height))
    if placed is None:
        raise Unsupported(
            f"the box of {reference} is not a cylinder of radius {radius:.6g} and height "
            f"{height:.6g} standing on an axis, so this primitive is not what that body is"
        )
    axis, base = placed
    out = ctx.engine.execute(
        "solid.cylinder",
        {
            "radius": f"{radius} mm",
            "height": f"{height} mm",
            "base": base,
            "axis": axis,
            "name": feature.name,
        },
    )
    built = (out.get("created_bodies") or [None])[0]
    if built is None:
        raise Unsupported("the engine built no body for this primitive")
    # `b.get`, not `b["volume_mm3"]`: the engine reports no volume for an open body, and indexing it
    # would crash here instead of reaching the `volume is None` the next line already answers for
    # (spec section 93).
    volume = next(
        (
            float(b["volume_mm3"])
            for b in ctx.engine.body_readings([built])
            if b["name"] == built and b.get("volume_mm3") is not None
        ),
        None,
    )
    wanted = ctx.oracle.volume_of(reference)
    if volume is None or abs(volume - wanted) > noise(wanted) + BOX_BAND * wanted:
        raise Unsupported(
            f"the cylinder built measures {volume} mm³ where {reference} measures {wanted:.3f}",
            bodies=tuple(feature.outputs),
        )
    for key in feature.outputs:
        ctx.body_names[key] = built
    said = (
        f"the file gives this cylinder its size and not its place; it stands on {axis} at "
        f"{tuple(round(v, 6) for v in base)}, read from the box of {reference}"
    )
    return "inferred", [said]
