"""Sketch: plane, curves, constraints and driving dimensions."""

from __future__ import annotations

import math
from itertools import pairwise

from f3d2solidcraft.engine import CommandRefused
from f3d2solidcraft.geometry import UnsupportedGeometry, bezier_spans, nurbs_curve, plane_spec
from f3d2solidcraft.mappers import Context, Unsupported, definition, register
from f3d2solidcraft.mappers.components import holder_of, placed_frame
from f3d2solidcraft.model import Feature, Sketch, SketchEntity


def _uv(p: dict) -> list[float]:
    return [float(p["u"]), float(p["v"])]


def _draw(e: SketchEntity) -> list[dict]:
    """The SolidCraft commands that draw one cadmpeg sketch entity.

    A list, not one command, because a single entity is not always a single curve: a spline whose
    interior knots are unevenly spaced is drawn as the chain of Bézier segments it is made of
    (`_spline`). Every other kind is one command, and `build_sketch` keeps each entity's curves
    together whichever it is.
    """
    g = e.geometry
    if e.kind == "line":
        return [
            {
                "command": "sketch.line",
                "params": {"points": [_uv(g["start"]), _uv(g["end"])], "construction": e.construction},
            }
        ]
    if e.kind == "circle":
        return [
            {
                "command": "sketch.circle.center",
                "params": {
                    "center": _uv(g["center"]),
                    "radius": float(g["radius"]),
                    "construction": e.construction,
                },
            }
        ]
    if e.kind == "arc":
        cx, cy = _uv(g["center"])
        r = float(g["radius"])
        a0, a1 = float(g["start_angle"]), float(g["end_angle"])
        return [
            {
                "command": "sketch.arc.center_point",
                "params": {
                    "center": [cx, cy],
                    "start": [cx + r * math.cos(a0), cy + r * math.sin(a0)],
                    "end": [cx + r * math.cos(a1), cy + r * math.sin(a1)],
                    "construction": e.construction,
                },
            }
        ]
    if e.kind == "nurbs":
        return _spline(e)
    raise Unsupported(f"sketch entity kind {e.kind}")


# The degrees SolidCraft has a control point spline for, and the command for each.
_SPLINES = {3: "sketch.spline.control_point", 5: "sketch.spline.control_point_5"}

# How far a weight may sit from 1, and a knot step from its neighbours, and still be the same curve.
SPLINE_TOLERANCE = 1e-9


def _conic(poles: list, weights: list[float]) -> dict:
    """A degree 2 nurbs curve as `sketch.conic`, exactly, or refused with what stops it.

    A quadratic is not a spline to draw with control points — it is a **conic arc**, and SolidCraft
    has a command for exactly that: `sketch.conic` takes the two ends, the apex where the end
    tangents meet, and `rho`. Those are the three poles of a rational quadratic Bézier and its
    middle weight, so this is a translation rather than a fit.

    Measured on a real design of the user's, `corpus-07.f3d`, whose sketch was
    lost whole to `a degree 2 spline (SolidCraft has control points for 3 and 5)`. Its four curves
    are rational quadratics of three poles each, with weights `(1.404…, 1.513…, 1.513…)` — not the
    `(1, w, 1)` form `rho` wants. They are **not** circular arcs: a centre fitted through their
    samples leaves the radius spread over 3.1e-02 mm, so drawing them as arcs would be a fit, not a
    translation.

    The normal form is reached exactly, not approximately: a rational Bézier is unchanged in shape
    by a Möbius reparameterization, and `w = w1 / sqrt(w0 · w2)` is the one that takes any weights
    to `(1, w, 1)`. Verified on those four curves against the parameter the reparameterization
    predicts, `t = s·a / (1 - s + s·a)` with `a = sqrt(w0 / w2)`: the two curves give the same point
    to **1e-14 mm**, machine precision. Then `rho = w / (1 + w)`, which puts a parabola at 0.5 where
    the command's own default does (spec section 86).

    Refused by name: a quadratic of more than three poles (several conics in one curve, which needs
    weight-aware knot insertion this does not do) and a non-positive weight (no conic).
    """
    if len(poles) != 3 or len(weights) != 3:
        raise Unsupported(
            f"a degree 2 curve of {len(poles)} poles is more than one conic arc, which is not split yet"
        )
    if any(w <= 0.0 for w in weights):
        raise Unsupported("a degree 2 curve with a non-positive weight is no conic")
    w = weights[1] / math.sqrt(weights[0] * weights[2])
    rho = w / (1.0 + w)
    return {
        "command": "sketch.conic",
        "params": {
            "start": [poles[0][0], poles[0][1]],
            "apex": [poles[1][0], poles[1][1]],
            "end": [poles[2][0], poles[2][1]],
            "rho": rho,
        },
    }


def _spline(e: SketchEntity) -> list[dict]:
    """A nurbs curve as control point splines, or refused with what about it cannot be rebuilt.

    SolidCraft's control point splines are clamped, uniform and non-rational, at degree 3 or 5. A
    cadmpeg nurbs curve carries its own degree, knot vector and per-pole weights, so each of those
    things is checked against the curve rather than assumed: the poles only reproduce it when they
    agree.

    An unevenly knotted curve used to be refused here too, and that cost the corpus two whole
    sketches — one of `corpus-11`'s and `mango para lima`'s `Sketch 5` — because the refusal
    takes the sketch with it. It no longer has to: `bezier_spans` rewrites such a curve as the chain
    of Bézier segments it is made of, exactly, and a Bézier has no interior knots at all, so every
    segment is clamped and uniform by construction. The curve comes out as several curves instead of
    one, which is why the caller is told how many (spec section 76).

    A rational curve and a degree SolidCraft has no command for are still refused by name, instead
    of being approximated into a shape that would be reported as if it were the original. The
    polygonizer in `geometry` reads all of them, so area and profile work is unaffected by this.
    """
    degree, knots, poles, weights = nurbs_curve(e.geometry)
    if degree == 2:
        return [_conic(poles, weights)]
    command = _SPLINES.get(degree)
    if command is None:
        raise Unsupported(f"a degree {degree} spline (SolidCraft has control points for 3 and 5)")
    if any(abs(w - 1.0) > SPLINE_TOLERANCE for w in weights):
        raise Unsupported("a rational spline: its poles carry weights other than 1")
    head, tail = knots[: degree + 1], knots[len(knots) - degree - 1 :]
    if any(abs(k - head[0]) > SPLINE_TOLERANCE for k in head) or any(
        abs(k - tail[0]) > SPLINE_TOLERANCE for k in tail
    ):
        raise Unsupported("a spline whose knots are not clamped at both ends")
    interior = knots[degree : len(knots) - degree]
    steps = [b - a for a, b in pairwise(interior)]
    if steps and any(abs(s - steps[0]) > SPLINE_TOLERANCE * max(1.0, abs(steps[0])) for s in steps):
        runs = bezier_spans(degree, knots, poles)
    else:
        runs = [list(poles)]
    return [{"command": command, "params": {"points": [[u, v] for u, v in run]}} for run in runs]


# The constraints Fusion records between two curves, and the command for each. Every one of them
# takes the same `a, b` pair of curve ids, and the record carries them as `first` and `second` — so
# this is a translation, not an inference. Measured over the corpus: perpendicular 44, parallel 24,
# tangent 21, collinear 19, concentric 9, equal 4, of 187 records that were reported unhandled.
#
# Deliberately not here: `midpoint` (its record pairs a sketch *point* with a line, and points are not
# in `curve_ids`), `coincident_loci` (a list of loci, not a pair), `symmetry`, and `native` (cadmpeg
# did not type it). Those keep saying so.
_PAIRED = {
    "perpendicular": "sketch.constraint.perpendicular",
    "parallel": "sketch.constraint.parallel",
    "tangent": "sketch.constraint.tangent",
    "collinear": "sketch.constraint.collinear",
    "concentric": "sketch.constraint.concentric",
    "equal": "sketch.constraint.equal",
}


def _coincident_loci(sketch: Sketch, c) -> str:
    """Why a `coincident_loci` is not applied, with what the file actually says.

    Measured over the corpus: every one of its 55 records pairs exactly two `point` entities, and in
    **every** one the two sit at the same position — distance 0.00e+00. What they pin is therefore not
    a position but an identity, and the file names it with point-entity ids this converter has no way
    to match to SolidCraft's own points, which are named for what they belong to (`origin`,
    `c1.center`, `l2.start`).

    Matching by position was measured and does not carry it: of the 51 reachable records, 6 land where
    exactly one engine point is — and those 6 resolve to **one and the same** point (`origin` five
    times, `c1.center` once), so there is nothing left to constrain, as with a plain `coincident`. The
    other 45 land where 2 to 12 engine points share the spot, and picking the wrong pair of those
    would **move geometry**, which is the one outcome worth less than a warning. So this says what the
    file says and stops there.
    """
    loci = c.definition.get("loci") or []
    by_id = {e.id: e for e in sketch.entities}
    places = []
    for locus in loci:
        entity = by_id.get(locus.get("entity", "")) if isinstance(locus, dict) else None
        position = (entity.geometry.get("position") if entity else None) or {}
        places.append(
            f"({float(position['u']):.4g}, {float(position['v']):.4g})"
            if "u" in position and "v" in position
            else "an entity this sketch does not hold"
        )
    where = " and ".join(places) if places else "nothing"
    return (
        f"constraint coincident_loci skipped: it pins {len(loci)} point entities of the file, at "
        f"{where}, and nothing in the file says which of SolidCraft's points there each one is"
    )


def _one_curve(ctx: Context, entity: str) -> tuple[str | None, str]:
    """The one SolidCraft curve this entity is, or `None` and why there is not exactly one.

    A constraint or a dimension names a curve, so it needs exactly one. An entity drawn as a chain
    of Bézier segments (`_spline`) is several, and there is no honest way to pick one of them: the
    constraint belongs to the whole curve, and applying it to the first segment alone would hold
    that segment and leave the rest free — which **moves geometry** on a guess, the one outcome
    worth less than a warning. So it is skipped, saying which case it was.
    """
    drawn = ctx.curve_ids.get(entity)
    if drawn is None:
        return None, "was not drawn"
    if len(drawn) != 1:
        return None, f"is drawn as {len(drawn)} Bézier segments, so there is no single curve to name"
    return drawn[0], ""


def _paired(ctx: Context, c) -> list[str]:
    """Apply a two-curve constraint, or say why it could not be.

    A constraint cannot move geometry that is already where it belongs, so a refusal here costs
    nothing but the constraint itself: it is reported and the sketch goes on. What it buys is a
    sketch that behaves like Fusion's when a parameter moves.
    """
    first, first_why = _one_curve(ctx, c.definition.get("first", ""))
    second, second_why = _one_curve(ctx, c.definition.get("second", ""))
    if first is None or second is None:
        missing = ", ".join(
            f"its {side} entity {why}"
            for side, cid, why in (("first", first, first_why), ("second", second, second_why))
            if cid is None
        )
        return [f"constraint {c.kind} skipped: {missing}"]
    try:
        ctx.engine.execute(_PAIRED[c.kind], {"a": first, "b": second})
    except CommandRefused as exc:
        return [f"constraint {c.kind} on {first}/{second} refused: {exc.message}"]
    return []


def _size(e: SketchEntity) -> float | None:
    g = e.geometry
    if e.kind == "line":
        return math.dist(_uv(g["start"]), _uv(g["end"]))
    if e.kind in ("circle", "arc"):
        return float(g["radius"])
    return None


def _dimension(ctx: Context, sketch_entities: list[SketchEntity], used: set[str], p) -> list[str]:
    if p.value_mm is None or p.name not in ctx.param_names:
        return [f"dimension {p.name} ({p.role}) skipped: no length value or parameter"]
    role = p.role.lower()
    if role.startswith("diameter"):
        kinds, want, kind = ("circle", "arc"), abs(p.value_mm) / 2.0, "diameter"
    elif role.startswith("radius"):
        kinds, want, kind = ("circle", "arc"), abs(p.value_mm), "radius"
    elif role.startswith("linear"):
        kinds, want, kind = ("line",), abs(p.value_mm), "length"
    else:
        return [f"dimension {p.name} ({p.role}) skipped: this kind of dimension is not supported yet"]
    matches = [
        e
        for e in sketch_entities
        if e.id not in used
        and e.kind in kinds
        and _one_curve(ctx, e.id)[0] is not None
        and (size := _size(e)) is not None
        and abs(size - want) < 1e-6
    ]
    if not matches:
        return [f"unmatched dimension {p.name} ({p.role} = {p.expression})"]
    e = matches[0]
    curve_id, _why = _one_curve(ctx, e.id)
    try:
        ctx.engine.execute(
            "sketch.dimension", {"entities": [curve_id], "type": kind, "value": ctx.param_names[p.name]}
        )
    except CommandRefused as exc:
        return [f"dimension {p.name} refused: {exc.message}"]
    used.add(e.id)
    if len(matches) > 1:
        # Twins of the same size: the value is right, but it may drive a different entity than in Fusion.
        note = f"{len(matches)} entities of that size, applied to {curve_id}"
        return [f"ambiguous dimension {p.name} ({p.role} = {p.expression}): {note}"]
    return []


def _name(feature: Feature, ctx: Context) -> str:
    """The name to create this sketch under: the one Fusion shows, made unique.

    What the designer sees in Fusion's browser is the **feature's** label (`Boceto 1`, `Sketch 3`),
    not the sketch record's `name`, which is an internal id (`0_230861`) — spec section 72 measured
    that and ruled it out. Without a name the engine numbers its own `Sketch1`, `Sketch2`, so the
    file's own names were simply lost.

    Unique because the engine does not insist: asked for `Sketch 1` twice it answers `Sketch 1`
    twice, and `_engine_profiles` finds a sketch **by name**, so a repeat would have a later feature
    read the wrong sketch's regions. Fusion does repeat them — `Caja parametrica v2` has `Sketch 1`
    and `Sketch 3` twice over, one per configuration — so the second gets ` (2)`, which is how
    cadmpeg numbers a repeated body name too.
    """
    taken = set(ctx.sketch_names.values())
    if feature.name not in taken:
        return feature.name
    return next(
        f"{feature.name} ({n})" for n in range(2, len(taken) + 3) if f"{feature.name} ({n})" not in taken
    )


def build_sketch(sketch: Sketch, name: str | None, ctx: Context) -> list[str]:
    """Draw one sketch record under `name`, register it, and return its warnings.

    Split out of `map_sketch` because a sketch does not need a feature to be buildable: the record
    carries the plane, the curves and the constraints, and the feature only ever supplied the
    *name*. The 22 sketches of the corpus that no feature names (`replay._unnamed_sketches`) are
    built through here with `name=None`.

    `name=None` leaves the key out of `sketch.create` rather than sending a null, so the engine
    applies its own numbering — `Sketch1`, no space. That is the honest outcome where the file
    offers no name at all: spec section 72 measured that the sketch record's own `name` is an
    internal id (`0_1139`), and section 74 made the space the mark of a name the file really gave.
    """
    if sketch.frame is None:
        raise Unsupported("sketch has no resolved placement")
    warnings: list[str] = []
    wanted = [e for e in sketch.entities if e.kind != "point"]
    # An entity whose kind this converter cannot draw used to take the whole sketch with it, and on
    # the corpus's 101 sketches exactly one is lost that way: a `text` entity costs the other ten of
    # its sketch. So each entity is asked on its own and the ones that answer are drawn. What is left
    # out is named, one warning each, and the sketch goes into `ctx.partial_sketches` so no feature
    # builds a body from a profile with a piece missing (spec section 87).
    drawable, per_entity = [], []
    for entity in wanted:
        try:
            per_entity.append(_draw(entity))
        except (Unsupported, UnsupportedGeometry) as exc:
            # Geometry this reader cannot turn into commands is a gap in the converter, not a bug in
            # it: reported as an internal mapper error it read as if the mapper had crashed.
            warnings.append(f"{entity.kind} {entity.id.rsplit('/', 1)[-1]} is not in this sketch: {exc}")
            continue
        drawable.append(entity)
    if wanted and not drawable:
        # Every entity refused, so there is no sketch to draw — as against a sketch that simply has
        # no curves, which several of the corpus's do (points only) and which builds empty and
        # `exact` as it always did. Guarding on `not drawable` alone took three of those down.
        raise Unsupported(f"none of the {len(wanted)} entities of this sketch can be drawn: {warnings[0]}")
    # The plane where the design really has it: the file states it in the holding component's
    # coordinates and an explicit plane reaches the engine in the world's (spec section 102).
    holder = holder_of(ctx, sketch.id)
    frame = placed_frame(ctx, holder, sketch.frame) if holder is not None else sketch.frame
    params = {"plane": plane_spec(frame)}
    if name is not None:
        params["name"] = name
    # One round trip for the plane and every curve; the engine rolls it all back if one fails.
    commands = [cmd for cmds in per_entity for cmd in cmds]
    results = ctx.engine.batch([{"command": "sketch.create", "params": params}] + commands)
    created = results[0]
    at = 1
    for e, cmds in zip(drawable, per_entity):
        outs, at = results[at : at + len(cmds)], at + len(cmds)
        ctx.curve_ids[e.id] = tuple(out["created"]["curves"][0] for out in outs)
        if len(cmds) > 1:
            warnings.append(
                f"{e.kind} {e.id.rsplit('/', 1)[-1]} drawn as {len(cmds)} Bézier segments "
                f"({', '.join(ctx.curve_ids[e.id])}): its interior knots are not evenly spaced, "
                f"which is the same curve but not one curve"
            )
        # A construction curve the command can carry the flag for is drawn as one; the warning is
        # for the kinds whose command takes no `construction`, which is the splines and the conic.
        # Measured over the corpus and the files sent so far: **63 construction lines** and **2
        # construction circles** — one in `corpus-03` and one in `corpus-13.f3d` — and no
        # construction arc. A construction curve drawn as normal geometry is not cosmetic: it takes
        # part in the regions the engine finds, which is what an extrude's profile is matched against
        # (spec section 94).
        if e.construction and e.kind not in ("line", "circle", "arc"):
            drawn = ", ".join(ctx.curve_ids[e.id])
            warnings.append(f"construction {e.kind} {drawn} drawn as normal geometry")
    for c in sketch.constraints:
        if c.kind in ("horizontal", "vertical"):
            cid, why = _one_curve(ctx, c.definition.get("entity", ""))
            if cid is None:
                warnings.append(f"constraint {c.kind} skipped: entity {why}")
                continue
            try:
                ctx.engine.execute("sketch.constraint.horizontal_vertical", {"line": cid, "mode": c.kind})
            except CommandRefused as exc:
                warnings.append(f"constraint {c.kind} on {cid} refused: {exc.message}")
        elif c.kind in _PAIRED:
            warnings += _paired(ctx, c)
        elif c.kind == "coincident_loci":
            warnings.append(_coincident_loci(sketch, c))
        elif c.kind != "coincident":  # coincident is implicit: sketch.line connects shared points
            warnings.append(f"constraint {c.kind} not supported yet")
    used: set[str] = set()
    for p in sketch.dimensions:
        warnings += _dimension(ctx, drawable, used, p)
    finished = ctx.engine.execute("sketch.finish", {})
    found = finished["profiles"]
    # SolidCraft counts closed regions, Fusion lists loops; crossing curves give more regions, fewer means a loop broke.
    if found < len(sketch.profiles):
        warnings.append(
            f"sketch {created['name']}: SolidCraft found {found} closed regions, "
            f"Fusion {len(sketch.profiles)} profiles; some loops may not have closed"
        )
    ctx.sketch_names[sketch.id] = created["name"]
    if len(drawable) != len(wanted):
        # Registered all the same: the sketch is in the design, `_hide_sketches` can reach it and the
        # report can name it. What this records is that a feature must not build on it.
        ctx.partial_sketches.add(sketch.id)
    return warnings


@register("スケッチ")  # cadmpeg carries no Japanese spelling: `cadir` works the link out (§49)
@register("Sketch")
@definition("sketch")
def map_sketch(feature: Feature, ctx: Context) -> list[str] | tuple[str, list[str]]:
    sketch = ctx.design.sketches.get(feature.sketch or "")
    if sketch is None:
        if feature.definition_kind == "spatial_sketch":
            # Not a missing record: a 3D sketch, whose curves do not lie in one plane. cadmpeg 0.6.8
            # types it apart from a planar sketch and so records no planar sketch for it, and the
            # two extrudes that use it arrive as `spatial_sketch_profiles`. Measured on `corpus-02`'s `Boceto 12`, where 0.6.7 typed all eight of its sketch features `native` and
            # the derived link paired eight features onto seven records (spec section 95).
            raise Unsupported(
                "a spatial (3D) sketch: its curves are not in one plane and this converter draws "
                "planar sketches only, so the file records no planar sketch for it"
            )
        raise Unsupported("sketch record not found")
    warnings = build_sketch(sketch, _name(feature, ctx), ctx)
    if sketch.id in ctx.partial_sketches:
        # Not `exact`, because the sketch is not what the file holds — an entity of a kind this
        # converter cannot draw was left out, and the warnings name it. `inferred` is the only status
        # that says "rebuilt, but not verbatim" without claiming the feature failed; nothing was
        # measured here, and spec section 87 says so plainly rather than stretching the word quietly.
        return "inferred", warnings
    if feature.sketch_derived:
        # The curves and constraints are the file's own; which sketch record this feature holds is
        # not — cadmpeg left the feature untyped and named none, so `cadir` worked it out. Rebuilt
        # natively all the same, but on a link that was derived rather than read: `inferred`.
        derived = (
            "cadmpeg left this feature untyped, so its sketch record was worked out from the "
            "file's record order rather than read"
        )
        return "inferred", [derived, *warnings]
    return warnings
