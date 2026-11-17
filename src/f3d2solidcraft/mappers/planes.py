"""Datum plane: a construction plane where the file already resolved one."""

from __future__ import annotations

import math

from f3d2solidcraft.mappers import Context, Unsupported, definition, register
from f3d2solidcraft.model import Feature

# How square a normal has to be to an origin plane's own to count as parallel to it. Frames come out
# of the file as exact axes (0.0, 0.0, -1.0), so this only absorbs float noise.
SQUARE = 1e-9

# Each origin plane, and the axis its offset runs along. Measured on the engine: `XY` offset d puts
# the plane at (0, 0, d) with x_dir (1,0,0) and y_dir (0,1,0); `XZ` at (0, d, 0) with y_dir (0,0,-1);
# `YZ` at (d, 0, 0) with x_dir (0,1,0). So in all three the offset is the origin's own component on
# the normal's axis, counted along the positive direction.
_BASES = (("YZ", 0), ("XZ", 1), ("XY", 2))

_NAMES = ("x", "y", "z")


def _axis(normal: tuple[float, float, float]) -> int | None:
    """Which axis this normal lies along, or None when the plane is tilted."""
    for index in range(3):
        if abs(abs(normal[index]) - 1.0) <= SQUARE and all(
            abs(normal[other]) <= SQUARE for other in range(3) if other != index
        ):
            return index
    return None


# A normal with exactly one zero component lies in a coordinate plane, so one rotation about that
# component's axis reaches it. Per zero component: the origin plane to turn, the axis to turn it
# about, the angle that takes that plane's own normal to this one, and the u axis the result comes
# out with. All four measured on the engine, six tilts over the three axes: the normal comes back
# with a dot product of 1.000000000000 against the one asked for, and the plane passes through the
# point asked for to within 1.1e-15 mm (spec section 81).
_TILTS = {
    0: ("XY", "X", lambda n: math.atan2(-n[1], n[2]), lambda t: (1.0, 0.0, 0.0)),
    1: ("XY", "Y", lambda n: math.atan2(n[0], n[2]), lambda t: (math.cos(t), 0.0, -math.sin(t))),
    2: ("YZ", "Z", lambda n: math.atan2(n[1], n[0]), lambda t: (-math.sin(t), math.cos(t), 0.0)),
}


def _free_name(feature: Feature, ctx: Context) -> tuple[str, list[str]]:
    """The name to build this plane under, and what the mapper says when it is not the file's.

    Fusion lets two features share a name (`corpus-03` has a `WorkPlane 1` at ordinals 13 and 60,
    in different components), and SolidCraft does not: a second `construct.plane.*` under a name the
    timeline already holds is refused with "another feature is called …". The plane holds no solid,
    so the name is only what a reader sees; it gets the ordinal appended, and the warning says so.
    """
    taken = {entry.get("name") for entry in ctx.engine.call("inspect_design", {}).get("timeline") or []}
    if feature.name not in taken and f"{feature.name} (tilt)" not in taken:
        return feature.name, []
    name = f"{feature.name} (ord {feature.ordinal})"
    return name, [f"another feature is already called {feature.name!r}, so this one is built as {name!r}"]


def _tilted(feature: Feature, ctx: Context, flat: int, name: str) -> list[str]:
    """Build a plane tilted about one coordinate axis, exactly where the file puts it.

    Two steps, because neither alone can do it. `construct.plane.at_angle` turns an origin plane
    about an axis, and the result always contains that axis — so it has the right normal but sits
    wherever the axis is, never through an arbitrary point. Measured: handing it the plane's own
    origin as `axis: {origin, dir}` still left the file's point 7.277 mm off the result. So the
    rotation is taken about the origin axis, which puts the plane through the world origin, and then
    `construct.plane.offset` moves it along its own normal by the distance to the file's plane.
    Positive offset follows that normal — measured, the other sign lands 20.8 mm away.

    The offset needs the turned plane's own normal, and the engine does not report a construction
    plane's frame. What it does report is the frame of a *sketch*, so the normal comes from the
    file's own `frame.normal`, which the rotation reproduces exactly: the distance is
    `normal · (origin - 0)`, the component of the plane's origin along its normal.

    The cost is a helper plane the original does not have, named after the feature so it reads as
    one. It is reported rather than left to be discovered.
    """
    frame = feature.frame
    base, axis, angle_of, u_of = _TILTS[flat]
    angle = angle_of(frame.normal)
    helper = f"{name} (tilt)"
    # The rotation about the origin axis puts the plane through (0, 0, 0), so the distance from it to
    # the file's plane, along the shared normal, is that plane's own origin projected on the normal.
    step = sum(a * b for a, b in zip(frame.normal, frame.origin))
    ctx.engine.batch(
        [
            {
                "command": "construct.plane.at_angle",
                "params": {"base": base, "axis": axis, "angle": f"{math.degrees(angle)} deg", "name": helper},
            },
            {
                "command": "construct.plane.offset",
                "params": {"base": helper, "offset": f"{step} mm", "name": name},
            },
        ]
    )
    said = (
        f"a plane tilted {math.degrees(angle):.6g}° about {_NAMES[flat]} is built as a rotation and "
        f"an offset, so the design also holds the helper plane {helper!r}, which Fusion does not"
    )
    warnings = [said]
    natural = u_of(angle)
    if any(abs(a - b) > SQUARE for a, b in zip(frame.u_axis, natural)):
        warnings.append(
            f"the plane's u_axis {frame.u_axis} is not carried over; the rotation gives {natural}"
        )
    return warnings


@register("WorkPlane")
@definition("datum_plane")
def map_work_plane(feature: Feature, ctx: Context) -> list[str]:
    """Rebuild a datum plane as an offset from the origin plane it is parallel to.

    cadmpeg gives a datum plane as a frame that is already resolved — origin, normal and u axis — so
    there is nothing to infer. What SolidCraft offers for it is `construct.plane.offset`, which
    places a plane parallel to `XY`, `XZ` or `YZ`; a tilted plane is refused rather than approximated.

    **No sketch needs this.** Every sketch in the corpus carries its own resolved frame, so a plane
    this mapper refuses costs no geometry — `corpus-01` places its sketches correctly with
    both of its `WorkPlane` features unmapped. That was written here before it was true: the replay
    condemned every standing body for a feature that named none of its own, so refusing `corpus-01`'s
    plane at ordinal 11 threw away the body its eleven exact features had just built, and six
    features behind it read "builds on a body that is already a fallback import". A plane holds no
    solid and cannot change one, so it now condemns nothing (`replay.CONSTRUCTION`, spec section
    119). They are rebuilt so the design holds what Fusion held, for whoever opens it next.

    And an offset plane carries position and normal only, so the mapper says what it drops: the frame
    may put the plane's own origin off the axis and may point its normal or u axis the other way, and
    each of those is a warning rather than something left unsaid. They matter for anything later
    placed on the plane, which in these designs is nothing.
    """
    frame = feature.frame
    if frame is None:
        raise Unsupported("the file gives no resolved frame for this plane")
    name, renamed = _free_name(feature, ctx)
    axis = _axis(frame.normal)
    if axis is None:
        flat = [i for i in range(3) if abs(frame.normal[i]) <= SQUARE]
        if len(flat) != 1:
            raise Unsupported(
                "a datum plane tilted about two axes at once is not mapped "
                f"(normal {frame.normal} lies along none of the coordinate planes)"
            )
        return renamed + _tilted(feature, ctx, flat[0], name)
    base, _ = next(entry for entry in _BASES if entry[1] == axis)
    offset = frame.origin[axis]
    ctx.engine.execute("construct.plane.offset", {"base": base, "offset": f"{offset} mm", "name": name})
    warnings = list(renamed)
    in_plane = [
        f"{_NAMES[other]}={frame.origin[other]:.6g}"
        for other in range(3)
        if other != axis and abs(frame.origin[other]) > SQUARE
    ]
    if in_plane:
        warnings.append(f"the plane's own origin within {base} is not carried over ({', '.join(in_plane)})")
    if frame.normal[axis] < 0:
        warnings.append(f"the plane's normal points along -{_NAMES[axis]}; an offset from {base} points +")
    natural = {0: (0.0, 1.0, 0.0), 1: (1.0, 0.0, 0.0), 2: (1.0, 0.0, 0.0)}[axis]
    if any(abs(a - b) > SQUARE for a, b in zip(frame.u_axis, natural)):
        warnings.append(f"the plane's u_axis {frame.u_axis} is not carried over; {base} gives {natural}")
    return warnings


@register("WorkAxis")
@definition("datum_axis")
def map_work_axis(feature: Feature, ctx: Context) -> list[str]:
    """Rebuild a datum axis as the line where two planes parallel to the origin planes meet.

    cadmpeg resolves a datum axis as a point and a direction. SolidCraft has no command that takes
    those, but `construct.axis.two_planes` takes two planes, and an axis along a coordinate direction
    is exactly where two planes square to it cross: one through the point's first other coordinate,
    one through its second. Measured on `corpus-07`'s `WorkAxis 1`, the corpus's
    only one: through (0, 0, 15.519) along -z, so `YZ` and `XZ` themselves, with no helper plane
    (spec section 152).

    A plane the axis needs off the origin is built as an offset plane and reported, as the tilted
    datum plane's helper is. An axis along no coordinate direction is refused rather than
    approximated. And like a plane, an axis holds no solid, so a refusal here condemns nothing
    (`replay.CONSTRUCTION`).
    """
    if feature.datum_axis is None:
        raise Unsupported("the file gives no resolved point and direction for this axis")
    origin, direction = feature.datum_axis
    # The file states the axis in its component's coordinates and the origin planes are the world's,
    # so it is carried through that component's placement first, the way `placed_frame` carries a
    # sketch plane. `corpus-07` places one of its three components (-90 mm along x) and this axis is in
    # another, at the identity, so here the carry changes nothing — but it is not assumed.
    if feature.component is None and ctx.placements:
        raise Unsupported("the design places a component and the file does not say which one holds this axis")
    if feature.component is not None:
        from f3d2solidcraft.mappers.components import placed_frame
        from f3d2solidcraft.model import Frame

        line = placed_frame(ctx, feature.component, Frame(origin, direction, direction))
        origin, direction = line.origin, line.normal
    along = _axis(direction)
    if along is None:
        raise Unsupported(f"a datum axis along {direction} lies along no coordinate direction")
    name, renamed = _free_name(feature, ctx)
    warnings = list(renamed)
    planes = []
    for base, index in _BASES:
        if index == along:
            continue
        offset = origin[index]
        if abs(offset) <= SQUARE:
            planes.append(base)
            continue
        helper = f"{name} ({_NAMES[index]})"
        ctx.engine.execute("construct.plane.offset", {"base": base, "offset": f"{offset} mm", "name": helper})
        planes.append(helper)
        warnings.append(
            f"the axis is built where two planes cross, so the design also holds the helper plane "
            f"{helper!r}, which Fusion does not"
        )
    ctx.engine.execute("construct.axis.two_planes", {"a": planes[0], "b": planes[1], "name": name})
    if direction[along] < 0:
        warnings.append(f"the axis points along -{_NAMES[along]}; two planes give it no direction of its own")
    return warnings
