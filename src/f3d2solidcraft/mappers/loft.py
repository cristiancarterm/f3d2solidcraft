"""Loft: a body through two or more sketch sections, one of which may be an apex point."""

from __future__ import annotations

from f3d2solidcraft.mappers import JOIN_TOUCHING, Context, Unsupported, definition, register
from f3d2solidcraft.mappers.outputs import name_outputs, no_target
from f3d2solidcraft.model import Feature, Sketch

_OPERATIONS = {"new_body": "new", "join": "join", "cut": "cut", "intersect": "intersect"}

# How close to the sketch's own (0, 0) a point entity is the origin marker rather than a point of
# the design. Measured over the corpus: **all 101 sketches** carry at least one point entity at
# exactly 0.0 / 0.0 — 63 carry exactly one and the other 38 carry two to ten of them — so the
# marker is something cadmpeg emits per sketch and not a point anybody drew. Any tolerance would do;
# this one is tight on purpose, so an apex a designer really put at the sketch origin is refused for
# having no point rather than built at the wrong place (spec section 113).
ORIGIN_DUST = 1e-9
# How close two point entities are the same apex. The corpus's one apex sketch lists its point
# **twice**, the two differing at the 14th decimal, and the design's own linear tolerance is
# 1e-5 mm — this is that order, like `revolve.AXIS_ORIGIN_DUST`.
APEX_DUST = 1e-6


def _apex(sketch: Sketch) -> tuple[float, float]:
    """The one point an apex section names, or a refusal saying how many it found.

    An apex is a sketch the file gives no loop and only points. Which point is the apex is not
    stated, so it is read: the origin marker is discounted and the rest are deduped. Exactly one
    distinct position is an answer; anything else is refused rather than picked from.
    """
    points = [e for e in sketch.entities if e.kind == "point"]
    away = [
        (float(e.geometry["position"]["u"]), float(e.geometry["position"]["v"]))
        for e in points
        if abs(float(e.geometry["position"]["u"])) > ORIGIN_DUST
        or abs(float(e.geometry["position"]["v"])) > ORIGIN_DUST
    ]
    distinct: list[tuple[float, float]] = []
    for p in away:
        if not any(abs(p[0] - q[0]) < APEX_DUST and abs(p[1] - q[1]) < APEX_DUST for q in distinct):
            distinct.append(p)
    if len(distinct) != 1:
        raise Unsupported(
            f"a section of this loft is a sketch with no loop, and its {len(points)} point(s) leave "
            f"{len(distinct)} distinct position(s) once the origin marker is discounted, so which "
            "one the apex is is not in the file"
        )
    return distinct[0]


@register("Loft")
@definition("loft")
def map_loft(feature: Feature, ctx: Context) -> list[str]:
    """Rebuild a loft from the sections the file names, and report what the engine answers.

    The corpus's one loft resolves **whole** — two sketch sections, no guides, `new_body`, open,
    solid — which makes this family unlike every wall of section 108: there is nothing opaque to
    work around. What stops it is the engine, and the engine says so itself. Measured on it
    (spec section 113):

    | sections | `solid.loft` |
    | --- | --- |
    | a rectangle to a point | builds a pyramid |
    | a circle to a circle | builds a frustum |
    | **a circle to a point** | *"not supported yet: lofting curved sections to a point"* |

    The corpus's loft is a degree 5 nurbs to a point, so it lands on the one combination the engine
    refuses, in either section order. **No limit of its own is written here for that**: the recipe
    is handed over and `CommandRefused` carries the engine's own sentence into the report, where it
    reads "refused by SolidCraft" rather than "no mapper for Solevación" — which is what the report
    said before this mapper, and which was false (section 109 made the same point for `Combine`).

    The leverage, for whoever reads this when the engine gains the case: the loft heads **6
    cascades** in `soporte controles` — a shell, four chamfers and a sphere primitive — so its
    ceiling is 7 features. A ceiling, not a promise: section 112 measured why.
    """
    loft = feature.loft
    if loft is None:
        raise Unsupported(
            f"cadmpeg decoded this loft as {feature.definition_kind!r}, so the file hands over no "
            "sections to rebuild it from"
        )
    if not loft.solid:
        raise Unsupported("this loft makes a surface, not a solid")
    if loft.guides:
        # `solid.loft` takes sections and nothing else, so a loft the file shapes with guide curves
        # would come out a different body. None of the corpus's does: its one loft names
        # `guidance {kind: "guides", path: []}`.
        raise Unsupported(
            f"this loft is shaped by {loft.guides} guide curve(s) ({loft.guidance_kind}), which "
            "`solid.loft` does not take, so building it from the sections alone would be a "
            "different body"
        )
    if loft.closed:
        raise Unsupported("this loft closes back on its first section, which `solid.loft` does not take")
    if loft.ruled or loft.linearize:
        switches = ", ".join(n for n, on in (("ruled", loft.ruled), ("linearize", loft.linearize)) if on)
        raise Unsupported(f"this loft is {switches}, which `solid.loft` has no switch for")
    if len(loft.sections) < 2:
        raise Unsupported(f"this loft names {len(loft.sections)} section(s) and a loft needs two")
    if any(s.kind != "sketch" for s in loft.sections):
        kinds = ", ".join(sorted({s.kind for s in loft.sections if s.kind != "sketch"}))
        raise Unsupported(f"a section of this loft is a {kinds}, not a sketch of the design")
    if loft.op not in _OPERATIONS:
        raise Unsupported(f"loft operation {loft.op}")

    sections, apexes = [], []
    for section in loft.sections:
        sketch = ctx.design.sketches.get(section.value)
        name = ctx.sketch_names.get(section.value)
        if sketch is None or name is None:
            raise Unsupported("a section of this loft names a sketch that was not rebuilt")
        if section.value in ctx.partial_sketches:
            # Same reason as an extrude's (spec section 87): the sketch is in the design but short
            # an entity this converter cannot draw, so a body through it is a body through a
            # section Fusion did not have.
            raise Unsupported("a section of this loft is missing an entity this converter cannot draw")
        if sketch.profiles:
            sections.append({"sketch": name})
            continue
        # No loop: an apex. The point has to be in the engine's sketch to be named, and `map_sketch`
        # draws no point entity at all — so it is drawn here, for the one sketch a loft asks it of,
        # with an id of this feature's own. A loft that then fails is rolled back by the replay with
        # the point in it, so a refusal leaves the sketch as it was.
        point_id = f"loft{feature.ordinal}apex"
        apexes.append((name, point_id, _apex(sketch)))
        sections.append({"sketch": name, "point": point_id})

    operation = _OPERATIONS[loft.op]
    targets = [ctx.body_names[o] for o in feature.outputs if o in ctx.body_names]
    if operation != "new" and not targets:
        raise no_target(ctx, operation)
    warnings: list[str] = []
    for name, point_id, (u, v) in apexes:
        ctx.engine.execute("sketch.edit", {"sketch": name})
        ctx.engine.execute("sketch.point", {"point": [u, v], "id": point_id})
        ctx.engine.execute("sketch.finish", {})
        warnings.append(f"the apex of this loft was drawn into '{name}' at ({u:.3f}, {v:.3f})")
    params = {
        "sections": sections,
        "operation": operation,
        "name": feature.name,
        "touching": JOIN_TOUCHING,
    }
    if operation != "new":
        params["targets"] = targets
    out = ctx.engine.execute("solid.loft", params)
    name_outputs(feature, ctx, out, warnings)
    return warnings
