"""The colours Fusion gives the bodies, on the bodies this converter rebuilt (spec 0.4, item 3)."""

from __future__ import annotations

from f3d2solidcraft.engine import CommandRefused
from f3d2solidcraft.mappers import Context
from f3d2solidcraft.replay import ReplayResult


def hex_colour(rgba: tuple[float, float, float, float]) -> str:
    """`#rrggbb` for a colour whose channels run 0..1, as `appearance.assign` takes it."""
    return "#" + "".join(f"{round(min(max(c, 0.0), 1.0) * 255):02x}" for c in rgba[:3])


def paint_native(ctx: Context, result: ReplayResult) -> list[str]:
    """Give each body the replay rebuilt the colour its Fusion body binding names; returns warnings.

    Imported bodies are left alone: cadmpeg's STEP carries a `COLOUR_RGB` per face and the import
    keeps it as the body's paint. A rebuilt body is the one `ctx.body_names` names for its Fusion id,
    still standing and not imported — which is also the body `name_bodies` already renamed.

    A face's own colour is not applied: `appearance.assign` picks a face by a point on it and the
    file names the face only. Measured on `corpus-03`, the one corpus design with face colours, a
    point worked out from a flat face's edges picked the face with the bound colour in 7 of 15
    cases, so it is counted and reported instead. No rebuilt body of the corpus has one.
    """
    imported = set(result.imported_bodies)
    standing = set(ctx.engine.body_names())
    warnings: list[str] = []
    faces = 0
    for paint in ctx.design.paints:
        name = ctx.body_names.get(paint.body)
        if name is None or name not in standing or name in imported:
            continue
        if paint.face is not None:
            faces += 1
            continue
        params: dict = {"bodies": [name], "color": hex_colour(paint.rgba)}
        if paint.rgba[3] < 1.0:
            params["opacity"] = paint.rgba[3]
        try:
            ctx.engine.execute("appearance.assign", params)
        except CommandRefused as exc:
            warnings.append(f"the colour Fusion gives {name!r} was not applied: {exc.message}")
    if faces:
        warnings.append(
            f"{faces} face colour(s) of rebuilt bodies were not applied: the engine picks a face by a "
            "point and the file names the face only"
        )
    return warnings
