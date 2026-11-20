"""Sketch: plane, curves, constraints and driving dimensions."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from itertools import pairwise

from f3d2solidcraft.engine import CommandRefused
from f3d2solidcraft.geometry import UnsupportedGeometry, bezier_spans, nurbs_curve, plane_spec
from f3d2solidcraft.mappers import Context, Unsupported, definition, register
from f3d2solidcraft.mappers.components import holder_of, placed_frame
from f3d2solidcraft.mappers.params import negated
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


# --- dimensions ---------------------------------------------------------------------------------------
#
# A dimension is applied by what it measures, the way Fusion recorded it: cadmpeg types each one with
# its entities (`SketchDimension`), and `sketch.dimension` takes exactly those. Before 0.4.2 a
# dimension was only matched by value to one line or circle of the same size, and on the corpus's 13
# `.f3d` that brought 81 of 157 dimensions into the saved design; most of the rest are horizontal and
# vertical distances between two points, which no curve is as long as.
#
# A sketch must never move, and four rules keep it so. Every call passes `if_redundant: "driven"`, so
# a dimension the sketch already determines is placed as a reference instead of refused. A dimension
# drives only where the drawn sketch already measures the parameter's value: one this converter cannot
# check — its value is unknown, or `_measures` does not compute what it measures — is skipped, never
# sent unchecked. A point of the file is one of SolidCraft's only where the engine has a point at that
# place. And a dimension placed by matching its value rather than by the entities it measures, where
# more than one place could be it, is placed as a reference without its parameter (`_reference`): its
# value is right, but a parameter change would move whichever place was guessed.

# How near a point of the file must sit to one of SolidCraft's to be that point, in sketch millimetres.
POINT_TOLERANCE = 1e-6
# Points closer than this are one point to the engine: it joins every point it draws onto one already
# there with a coincident constraint (measured on 0.43.0: a line end on a circle's centre, a line end on
# the origin, an arc's centre on a line end, each answered with a `Coincident`).
JOINED = 1e-9
# How far the drawn sketch may measure from the parameter's value, in millimetres or degrees.
MEASURE_TOLERANCE = 1e-5
# How far from parallel two lines may be for a distance between them, as the sine of their angle.
PARALLEL_TOLERANCE = 1e-9

# cadmpeg's kind of dimension -> the `type` `sketch.dimension` takes for it.
_TYPES = {
    "horizontal_distance": "horizontal",
    "vertical_distance": "vertical",
    "distance_loci": "distance",
    "distance": "distance",
    "diameter": "diameter",
    "radius": "radius",
    "angle": "angle",
    "repeated_length": "length",
    "repeated_diameter": "diameter",
}

_Point = tuple[str, tuple[float, float]]


class _Skipped(Exception):
    """A dimension that cannot be applied: `reason` is what the report counts, `detail` what it says."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or reason


@dataclass
class DimensionTally:
    """What became of one sketch's Fusion dimensions, as the report counts them.

    `applied` drive the sketch with their parameter, placed by the entities they measure; `reference`
    were placed so but the sketch already determined them, so they measure without a parameter;
    `ambiguous` were placed by matching their value where more than one place could be theirs, as a
    reference without a parameter, so a guess never drives; `skipped` did not arrive, counted by
    reason. A sketch pattern's count and angle are parameters of the sketch and not dimensions, so
    they are counted apart and never as a lost dimension.
    """

    applied: int = 0
    reference: int = 0
    ambiguous: int = 0
    skipped: Counter = field(default_factory=Counter)
    pattern_parameters: int = 0

    @property
    def fusion(self) -> int:
        return self.applied + self.reference + self.ambiguous + sum(self.skipped.values())


@dataclass
class _Placed:
    """What this sketch's dimensions already hold: so no curve or pair of points gets two."""

    curves: set[str] = field(default_factory=set)
    pairs: set[tuple[str, frozenset[str]]] = field(default_factory=set)


def _engine_points(ctx: Context, sketch: Sketch) -> list[_Point]:
    """Every point SolidCraft has in this sketch, with where it is: the origin first, then in drawing order.

    Named the way `sketch.dimension` takes them (`origin`, `l3.start`, `a1.center`). A curve drawn as
    several Bézier segments has no single point to name and is left out, as `_one_curve` leaves it.
    """
    points: list[_Point] = [("origin", (0.0, 0.0))]
    for e in sketch.entities:
        cid, _why = _one_curve(ctx, e.id)
        if cid is None:
            continue
        g = e.geometry
        if e.kind == "line":
            points += [(f"{cid}.start", tuple(_uv(g["start"]))), (f"{cid}.end", tuple(_uv(g["end"])))]
        elif e.kind == "circle":
            points.append((f"{cid}.center", tuple(_uv(g["center"]))))
        elif e.kind == "arc":
            (cx, cy), r = _uv(g["center"]), float(g["radius"])
            a0, a1 = float(g["start_angle"]), float(g["end_angle"])
            points += [
                (f"{cid}.center", (cx, cy)),
                (f"{cid}.start", (cx + r * math.cos(a0), cy + r * math.sin(a0))),
                (f"{cid}.end", (cx + r * math.cos(a1), cy + r * math.sin(a1))),
            ]
    return points


@dataclass
class _Ref:
    """One entity a dimension measures, as SolidCraft names it: a point and where, or a curve."""

    id: str
    at: tuple[float, float] | None = None  # a point's position
    entity: SketchEntity | None = None  # a curve's record
    note: str = ""  # why the point chosen was a choice, when it was one


def _point_at(points: list[_Point], at: tuple[float, float]) -> _Ref | None:
    """The engine point at this place, chosen the same way every time, or None when there is none.

    Several of SolidCraft's points share a place wherever curves meet; the engine has joined them
    (`JOINED`), so any is the same point and the first is taken — the origin before any curve's, then
    in drawing order. Points that are near but not joined are a real choice, and `note` says so.
    """
    near = [(name, p) for name, p in points if math.dist(p, at) <= POINT_TOLERANCE]
    if not near:
        return None
    name, where = near[0]
    apart = [other for other, p in near[1:] if math.dist(p, where) > JOINED]
    note = (
        f"{len(apart) + 1} points of the drawn sketch near ({at[0]:.4g}, {at[1]:.4g}), took {name}"
        if apart
        else ""
    )
    return _Ref(name, where, note=note)


def _resolve(ctx: Context, by_id: dict[str, SketchEntity], points: list[_Point], ref: object) -> _Ref:
    """The SolidCraft point or curve a dimension's entity is, or `_Skipped` saying why there is none."""
    if isinstance(ref, dict):
        if ref.get("kind") not in (None, "entity"):
            raise _Skipped("it measures a locus this converter does not read", f"a {ref.get('kind')} locus")
        ref = ref.get("entity")
    entity = by_id.get(ref) if isinstance(ref, str) else None
    if entity is None:
        raise _Skipped("an entity it measures is not in the sketch")
    if entity.kind == "point":
        position = entity.geometry.get("position") if isinstance(entity.geometry, dict) else None
        u = _number(position.get("u")) if isinstance(position, dict) else None
        v = _number(position.get("v")) if isinstance(position, dict) else None
        found = _point_at(points, (u, v)) if u is not None and v is not None else None
        if found is None:
            raise _Skipped(
                "a point it measures is not one SolidCraft drew",
                f"no point of the drawn sketch at ({u}, {v})" if u is not None and v is not None else "",
            )
        return found
    cid, why = _one_curve(ctx, entity.id)
    if cid is None:
        raise _Skipped("an entity it measures is not drawn as one curve", f"its {entity.kind} {why}")
    return _Ref(cid, entity=entity)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _line_angle(e: SketchEntity) -> float:
    (x0, y0), (x1, y1) = _uv(e.geometry["start"]), _uv(e.geometry["end"])
    return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0


def _parallel(a: SketchEntity, b: SketchEntity) -> bool | None:
    """Whether two drawn lines are parallel (`PARALLEL_TOLERANCE`), or None when one has no length."""
    (ax0, ay0), (ax1, ay1) = _uv(a.geometry["start"]), _uv(a.geometry["end"])
    (bx0, by0), (bx1, by1) = _uv(b.geometry["start"]), _uv(b.geometry["end"])
    la, lb = math.dist((ax0, ay0), (ax1, ay1)), math.dist((bx0, by0), (bx1, by1))
    if la == 0.0 or lb == 0.0:
        return None
    sine = abs((ax1 - ax0) * (by1 - by0) - (ay1 - ay0) * (bx1 - bx0)) / (la * lb)
    return sine <= PARALLEL_TOLERANCE


def _off_line(line: SketchEntity, at: tuple[float, float] | list[float]) -> float | None:
    """How far a point sits from the infinite line through this drawn line, or None if it has no length."""
    (x0, y0), (x1, y1) = _uv(line.geometry["start"]), _uv(line.geometry["end"])
    length = math.dist((x0, y0), (x1, y1))
    if length == 0.0:
        return None
    px, py = at
    return abs((x1 - x0) * (y0 - py) - (x0 - px) * (y1 - y0)) / length


def _measures(kind: str, refs: list[_Ref]) -> list[float] | None:
    """What the drawn sketch measures for this dimension, or None when this does not compute it.

    A list because an angle between two lines reads two ways, the acute and its supplement, and the
    engine takes whichever the value is nearer. A distance between two lines is the gap between them
    only when they are parallel; two lines that are not have no one distance, and this says None.
    """
    points = [r.at for r in refs if r.at is not None]
    curves = [r.entity for r in refs if r.entity is not None]
    if kind in ("horizontal", "vertical") and len(points) == 2:
        axis = 0 if kind == "horizontal" else 1
        return [abs(points[1][axis] - points[0][axis])]
    if kind == "distance" and len(points) == 2:
        return [math.dist(*points)]
    if kind == "distance" and len(points) == 1 and len(curves) == 1 and curves[0].kind == "line":
        off = _off_line(curves[0], points[0])
        return None if off is None else [off]
    if kind == "distance" and len(curves) == 2 and all(c.kind == "line" for c in curves):
        if not _parallel(*curves):
            return None
        off = _off_line(curves[0], _uv(curves[1].geometry["start"]))
        return None if off is None else [off]
    if kind in ("diameter", "radius") and len(curves) == 1 and curves[0].kind in ("circle", "arc"):
        radius = float(curves[0].geometry["radius"])
        return [2.0 * radius if kind == "diameter" else radius]
    if kind == "length" and len(curves) == 1 and curves[0].kind == "line":
        return [_size(curves[0])]
    if kind == "angle" and len(curves) == 2 and all(c.kind == "line" for c in curves):
        between = abs(_line_angle(curves[0]) - _line_angle(curves[1])) % 180.0
        return [between, 180.0 - between]
    return None


def _checked(ctx: Context, name: str, kind: str, refs: list[_Ref]) -> str:
    """The expression a dimension is given, once the drawn sketch is seen to measure its parameter's value.

    Nothing is sent without this: a driving dimension that disagrees with the drawing would move it,
    and a parameter of the file holds what Fusion solved, so a disagreement says the entities were read
    wrong. A dimension it cannot check is skipped, never sent on trust: its parameter has no computed
    value (as `params.follows` keeps the number then), or `_measures` does not compute what it measures
    — two lines that are not parallel among them, which have no one distance. A negative value is
    handed with its sign turned (`params.negated`), as a feature's distance is: what is measured is a
    size.
    """
    ids = [r.id for r in refs]
    if len(set(ids)) != len(ids):
        raise _Skipped("both ends are the same point of the drawn sketch", f"both ends are {ids[0]}")
    computed = ctx.param_values.get(name)
    unit = "deg" if kind == "angle" else "mm"
    if computed is None:
        raise _Skipped(
            "its parameter has no value to check the drawn sketch against",
            f"the engine computed no value for {name}, so {'/'.join(ids)} cannot be checked against it",
        )
    value, got = computed
    if got != unit:
        raise _Skipped(
            "its parameter is not the quantity it measures",
            f"the parameter is in {got or 'no unit'}, not {unit}",
        )
    expected = _measures(kind, refs)
    if expected is None:
        lines = [r.entity for r in refs if r.entity is not None and r.entity.kind == "line"]
        if kind == "distance" and len(lines) == 2 and len(refs) == 2 and _parallel(*lines) is False:
            raise _Skipped(
                "the two lines it measures between are not parallel",
                f"{'/'.join(ids)} are not parallel in the drawn sketch, so no one distance between them "
                "can be checked against the parameter",
            )
        kinds = "/".join(r.entity.kind if r.entity is not None else "point" for r in refs)
        raise _Skipped(
            "the converter cannot check what the drawn sketch measures for it",
            f"a {kind} between {kinds} is not measured here, so it is not sent unchecked",
        )
    if not any(abs(abs(value) - x) <= MEASURE_TOLERANCE * max(1.0, x) for x in expected):
        raise _Skipped(
            "the drawn sketch does not measure the parameter's value",
            f"{'/'.join(ids)} measure {expected[0]:.6g} {unit}, the parameter is {value:.6g} {unit}",
        )
    expression = ctx.param_names[name]
    return negated(expression) if value < 0 else expression


def _refused(kind: str, refs: list[_Ref], exc: CommandRefused) -> _Skipped:
    """Why the engine refused a dimension this converter checked, said plainly where it is known."""
    lines = [r.entity for r in refs if r.entity is not None and r.entity.kind == "line"]
    if kind == "distance" and len(lines) == 2 and "expected an angle" in exc.message:
        # Measured on `corpus-08.f3z`'s `d58`: two lines parallel in the file
        # (u = 27.5 and u = 10, both vertical), but the engine's own copy of one of them had an end
        # nudged 1.5e-7 mm by its solver, so to the engine they are not parallel and a distance
        # between them is an angle. Refused, so nothing moves; said so rather than as a bare refusal.
        return _Skipped(
            "SolidCraft does not read the two lines as parallel",
            f"{'/'.join(r.id for r in refs)} are parallel in the file, but SolidCraft's drawing of them "
            f"is not exactly, so it takes a distance between them for an angle: {exc.message}",
        )
    return _Skipped("refused by SolidCraft", exc.message)


def _place(placed: _Placed, kind: str, refs: list[_Ref]) -> None:
    """Record what a dimension now holds, so no curve or pair of points gets a second one."""
    ids = [r.id for r in refs]
    for r in refs:
        if r.entity is not None and len(refs) == 1:
            placed.curves.add(r.id)
    if len(refs) == 2:
        placed.pairs.add((kind, frozenset(ids)))


def _apply(
    ctx: Context, name: str, kind: str, refs: list[_Ref], placed: _Placed, tally: DimensionTally
) -> list[str]:
    """Apply one dimension to these entities with its parameter, once `_checked` passes it."""
    ids = [r.id for r in refs]
    expression = _checked(ctx, name, kind, refs)
    params = {"entities": ids, "type": kind, "value": expression, "if_redundant": "driven"}
    try:
        out = ctx.engine.execute("sketch.dimension", params)
    except CommandRefused as exc:
        raise _refused(kind, refs, exc) from None
    _place(placed, kind, refs)
    if isinstance(out, dict) and (out.get("redundant") or out.get("driven")):
        tally.reference += 1
        by = ", ".join(out.get("determined_by") or []) or "its constraints"
        return [
            f"dimension {name} placed as a reference on {'/'.join(ids)}: the sketch already determines it ({by})"
        ]
    tally.applied += 1
    return []


def _reference(
    ctx: Context, name: str, kind: str, refs: list[_Ref], placed: _Placed, tally: DimensionTally, why: str
) -> list[str]:
    """Place a dimension whose place was guessed as a reference, without its parameter: `ambiguous`.

    A place found by its value, not by the entities the file says it measures, is a coincidence of
    value: right today, but a parameter change would move whichever place was guessed, not the one
    Fusion dimensioned. So it measures and never drives (`driven: true`, no `value`), and the report
    counts it apart from the dimensions that arrived by what they measure.
    """
    ids = [r.id for r in refs]
    if len(set(ids)) != len(ids):
        raise _Skipped("both ends are the same point of the drawn sketch", f"both ends are {ids[0]}")
    try:
        ctx.engine.execute("sketch.dimension", {"entities": ids, "type": kind, "driven": True})
    except CommandRefused as exc:
        raise _refused(kind, refs, exc) from None
    _place(placed, kind, refs)
    tally.ambiguous += 1
    said = (
        f"ambiguous dimension {name}: {why}; placed on {'/'.join(ids)} as a reference without its "
        "parameter, so it measures and does not drive"
    )
    return [said]


def _typed(
    ctx: Context, sketch: Sketch, dims: list, points: list[_Point], placed: _Placed, tally
) -> list[str]:
    """Apply one parameter's typed dimension: its records in order, until one is applied.

    A parameter can have more than one record — an annotation and a pair for the same distance, an
    angle typed beside an untyped `native` — and is applied once. The supported records are tried
    first; what the warning says, when none applies, is why the first of them did not.

    A `repeated_*` record lists every entity of that size and does not say which one Fusion's
    dimension is on. With one of them left undimensioned that one is it, and it drives; with more,
    picking one is picking by drawing order, so it is placed as a reference on the first
    (`_reference`, counted `ambiguous`) — measured on `corpus-05`, whose `d3` and `d5` list
    four lines each and used to drive whichever came first.
    """
    name = dims[0].parameter
    by_id = {e.id: e for e in sketch.entities}
    ordered = sorted(dims, key=lambda d: d.kind not in _TYPES)
    reasons: list[_Skipped] = []
    for d in ordered:
        try:
            kind = _TYPES.get(d.kind)
            if kind is None:
                raise _Skipped(f"{d.kind} dimension is not rebuilt yet")
            defn = d.definition
            note = ""
            if d.kind.startswith("repeated_"):
                listed = defn.get("entities") if isinstance(defn.get("entities"), list) else []
                refs = [_resolve(ctx, by_id, points, x) for x in listed]
                free = [r for r in refs if r.id not in placed.curves]
                if not free:
                    raise _Skipped("every entity it lists already carries a dimension")
                if len(free) > 1:
                    # Checked all the same: a reference that does not measure the value is no
                    # place of this dimension at all.
                    _checked(ctx, name, kind, free[:1])
                    why = (
                        f"the file lists {len(listed)} entities of that size for it ({d.kind}) and "
                        f"{len(free)} carry no dimension yet, so which one Fusion dimensioned is not known"
                    )
                    return _reference(ctx, name, kind, free[:1], placed, tally, why)
                refs = free
            else:
                if d.kind in ("diameter", "radius"):
                    listed = [defn.get("entity")]
                elif d.kind == "distance":
                    listed = defn.get("entities") if isinstance(defn.get("entities"), list) else []
                else:
                    listed = [defn.get("first"), defn.get("second")]
                if len(listed) != (1 if d.kind in ("diameter", "radius") else 2):
                    raise _Skipped("the file does not say what it measures")
                refs = [_resolve(ctx, by_id, points, x) for x in listed]
                note = "; ".join(r.note for r in refs if r.note)
            said = _apply(ctx, name, kind, refs, placed, tally)
        except _Skipped as why:
            reasons.append(why)
            continue
        if note:
            said = [*said, f"ambiguous dimension {name} ({d.kind}): {note}"]
        return said
    first = next((r for r, d in zip(reasons, ordered) if d.kind in _TYPES), reasons[0])
    tally.skipped[first.reason] += 1
    return [f"dimension {name} ({ordered[0].kind}) skipped: {first.detail}"]


def _fallback_refs(
    ctx: Context, drawable: list[SketchEntity], points: list[_Point], placed: _Placed, kind: str, want: float
) -> tuple[list[list[_Ref]], str]:
    """Where a dimension only ezf3d sees could go: every place of the drawn sketch that measures `want`.

    The value is all ezf3d gives, so this is matching, not reading. A linear dimension is first a line
    of that length, as it always was; with no such line, the pairs of points that far apart
    horizontally or vertically — the dimensions Fusion draws most — the ones on one horizontal or
    vertical line first. The caller places a pair as a reference only (`_reference`), however many fit.
    """
    curves = [e for e in drawable if _one_curve(ctx, e.id)[0] is not None]
    if kind in ("diameter", "radius", "length"):
        kinds = ("line",) if kind == "length" else ("circle", "arc")
        size = want / 2.0 if kind == "diameter" else want
        found = [
            [_Ref(cid, entity=e)]
            for e in curves
            if e.kind in kinds
            and (cid := _one_curve(ctx, e.id)[0]) not in placed.curves
            and (got := _size(e)) is not None
            and abs(got - size) < POINT_TOLERANCE
        ]
        if found or kind != "length":
            return found, kind
        unique: list[_Point] = []
        for name, at in points:
            if all(math.dist(at, p) > JOINED for _, p in unique):
                unique.append((name, at))
        pairs: list[tuple[int, str, list[_Ref]]] = []
        for i, (a, pa) in enumerate(unique):
            for b, pb in unique[i + 1 :]:
                for axis, along in ((0, "horizontal"), (1, "vertical")):
                    if (
                        abs(abs(pb[axis] - pa[axis]) - want) <= POINT_TOLERANCE
                        and (along, frozenset((a, b))) not in placed.pairs
                    ):
                        level = 0 if abs(pb[1 - axis] - pa[1 - axis]) <= JOINED else 1
                        pairs.append((level, along, [_Ref(a, pa), _Ref(b, pb)]))
        pairs.sort(key=lambda x: x[0])  # stable: drawing order within a level
        if not pairs:
            return [], kind
        along = pairs[0][1]
        return [refs for _level, how, refs in pairs if how == along], along
    if kind == "angle":
        lines = [e for e in curves if e.kind == "line" and e.geometry.get("start") != e.geometry.get("end")]
        found = []
        for i, a in enumerate(lines):
            for b in lines[i + 1 :]:
                refs = [_Ref(_one_curve(ctx, a.id)[0], entity=a), _Ref(_one_curve(ctx, b.id)[0], entity=b)]
                if ("angle", frozenset(r.id for r in refs)) in placed.pairs:
                    continue
                if any(
                    abs(want - x) <= MEASURE_TOLERANCE * max(1.0, x) for x in _measures("angle", refs) or []
                ):
                    found.append(refs)
        return found, kind
    return [], kind


def _dimension(
    ctx: Context,
    drawable: list[SketchEntity],
    points: list[_Point],
    placed: _Placed,
    p,
    tally: DimensionTally,
) -> list[str]:
    """Apply a dimension only ezf3d sees, by its value: the fallback for what cadmpeg does not type.

    Their parameter is not in cadmpeg's list or has no typed record, so the value and the role are all
    there is to go on: `corpus-03`'s `docklength` and `dockphoneheight`, `corpus-04`'s `d5` and
    `d6`, among others. A line or circle of that size drives with the parameter, as in 0.4.1. A pair —
    two points that far apart, two lines at that angle — is a coincidence of value that names no
    entity: `dockphoneheight` fits three pairs of points, and the first in drawing order used to drive.
    So a pair is placed as a reference without the parameter, counted `ambiguous`, one fit or many.
    """
    role = p.role.lower()
    if p.name not in ctx.param_names:
        tally.skipped["its parameter is not in the design"] += 1
        return [f"dimension {p.name} ({p.role}) skipped: its parameter is not in the design"]
    if role.startswith("angular"):
        computed = ctx.param_values.get(p.name)
        if computed is None or computed[1] != "deg":
            tally.skipped["its parameter is not the quantity it measures"] += 1
            return [f"dimension {p.name} ({p.role}) skipped: no angle value"]
        kind, want = "angle", abs(computed[0])
    elif role.startswith(("diameter", "radius", "radial", "linear")):
        if p.value_mm is None:
            tally.skipped["its parameter is not the quantity it measures"] += 1
            return [f"dimension {p.name} ({p.role}) skipped: no length value"]
        kind = (
            "diameter"
            if role.startswith("diameter")
            else "radius"
            if role.startswith(("radius", "radial"))
            else "length"
        )
        want = abs(p.value_mm)
    else:
        tally.skipped["this kind of dimension is not rebuilt yet"] += 1
        return [f"dimension {p.name} ({p.role}) skipped: this kind of dimension is not supported yet"]
    candidates, kind = _fallback_refs(ctx, drawable, points, placed, kind, want)
    if not candidates:
        tally.skipped["unmatched: nothing in the drawn sketch measures its value"] += 1
        return [f"unmatched dimension {p.name} ({p.role} = {p.expression})"]
    refs = candidates[0]
    try:
        if len(refs) == 2:
            places = f"{len(candidates)} places" if len(candidates) > 1 else "one place"
            why = f"{p.role} = {p.expression} names no entity and matches {places} of that {kind} by value"
            return _reference(ctx, p.name, kind, refs, placed, tally, why)
        said = _apply(ctx, p.name, kind, refs, placed, tally)
    except _Skipped as why:
        tally.skipped[why.reason] += 1
        return [f"dimension {p.name} ({p.role}) skipped: {why.detail}"]
    if len(candidates) > 1:
        # Twins of the same size: the value is right, but it may drive a different place than in Fusion.
        note = f"{len(candidates)} places of that {kind}, applied to {'/'.join(r.id for r in refs)}"
        said = [*said, f"ambiguous dimension {p.name} ({p.role} = {p.expression}): {note}"]
    return said


def _dimensions(
    ctx: Context, sketch: Sketch, drawable: list[SketchEntity]
) -> tuple[list[str], DimensionTally]:
    """Apply this sketch's Fusion dimensions: the typed ones by entity, then the ones only ezf3d sees.

    No parameter is applied twice: one cadmpeg types anywhere in the design is never matched by value,
    whether its typed record applied or not.
    """
    tally = DimensionTally()
    warnings: list[str] = []
    points = _engine_points(ctx, sketch)
    placed = _Placed()
    groups: dict[str, list] = {}
    for d in sketch.typed_dimensions:
        if not d.parameter:
            tally.skipped["it names a parameter the file does not list"] += 1
            warnings.append(f"dimension ({d.kind}) skipped: it names a parameter the file does not list")
            continue
        if d.foreign:
            # The member's own table is not read, and the root's parameter of the same name is another one.
            tally.skipped[
                "its parameter belongs to a referenced document, whose parameters are not read"
            ] += 1
            warnings.append(
                f"dimension {d.parameter} ({d.kind}) skipped: its parameter belongs to a referenced "
                "document, whose parameters are not read"
            )
            continue
        groups.setdefault(d.parameter, []).append(d)
    for name, dims in groups.items():
        if name not in ctx.param_names:
            tally.skipped["its parameter is not in the design"] += 1
            warnings.append(f"dimension {name} ({dims[0].kind}) skipped: its parameter is not in the design")
            continue
        warnings += _typed(ctx, sketch, dims, points, placed, tally)
    typed = {d.parameter for s in ctx.design.sketches.values() for d in s.typed_dimensions if not d.foreign}
    for p in sketch.dimensions:
        if p.name in typed:
            continue
        if p.role.startswith("C-Pattern"):
            # A sketch pattern's count or angle: a parameter of the sketch, not a dimension of it.
            tally.pattern_parameters += 1
            warnings.append(f"sketch pattern parameter {p.name} ({p.role} = {p.expression}) not rebuilt")
            continue
        warnings += _dimension(ctx, drawable, points, placed, p, tally)
    return warnings, tally


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
    said, tally = _dimensions(ctx, sketch, drawable)
    warnings += said
    finished = ctx.engine.execute("sketch.finish", {})
    found = finished["profiles"]
    # SolidCraft counts closed regions, Fusion lists loops; crossing curves give more regions, fewer means a loop broke.
    if found < len(sketch.profiles):
        warnings.append(
            f"sketch {created['name']}: SolidCraft found {found} closed regions, "
            f"Fusion {len(sketch.profiles)} profiles; some loops may not have closed"
        )
    ctx.sketch_names[sketch.id] = created["name"]
    ctx.dimensions[sketch.id] = {"sketch": created["name"], "tally": tally}
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


# Why a dimension of a sketch that is not in the saved design did not arrive.
NOT_BUILT = "sketch not built"


def _unbuilt(ctx: Context, sketch: Sketch) -> DimensionTally:
    """A sketch that failed or was rolled back: its dimensions, counted as `_dimensions` counts them.

    It leaves no entry in `ctx.dimensions`, so without this its dimensions vanished from the count
    instead of being reported as lost.
    """
    tally = DimensionTally()
    lost = 0
    names: set[str] = set()
    for d in sketch.typed_dimensions:
        if not d.parameter or d.foreign:
            lost += 1
        else:
            names.add(d.parameter)
    lost += len(names)
    typed = {d.parameter for s in ctx.design.sketches.values() for d in s.typed_dimensions if not d.foreign}
    for p in sketch.dimensions:
        if p.name in typed:
            continue
        if p.role.startswith("C-Pattern"):
            tally.pattern_parameters += 1
        else:
            lost += 1
    if lost:
        tally.skipped[NOT_BUILT] = lost
    return tally


def dimension_report(ctx: Context) -> dict:
    """The report's account of the sketches' Fusion dimensions: how many arrived and why the rest did not.

    `fusion` is every dimension the file gives its sketches; `applied` drive their sketch with a
    parameter, placed by the entities they measure; `reference` were placed so but the sketch already
    determined them; `ambiguous` were placed by matching their value where the place is a guess, as a
    reference without a parameter; `skipped` did not arrive, and `reasons` counts them by why — a
    sketch that was not built counts every one of its dimensions as `sketch not built`. A sketch
    pattern's count and angle are counted apart as `pattern_parameters`, since they are not
    dimensions. One row per sketch that has any, by the name the engine gave it (None for a sketch not
    built) and the file's id for it.
    """
    totals = DimensionTally()
    rows = []
    done = [(sid, d["sketch"], d["tally"]) for sid, d in ctx.dimensions.items()]
    done += [
        (sid, None, _unbuilt(ctx, sketch))
        for sid, sketch in ctx.design.sketches.items()
        if sid not in ctx.dimensions
    ]
    for sid, name, tally in done:
        totals.applied += tally.applied
        totals.reference += tally.reference
        totals.ambiguous += tally.ambiguous
        totals.skipped.update(tally.skipped)
        totals.pattern_parameters += tally.pattern_parameters
        if tally.fusion or tally.pattern_parameters:
            rows.append(
                {
                    "sketch": name,
                    "id": sid,
                    "fusion": tally.fusion,
                    "applied": tally.applied,
                    "reference": tally.reference,
                    "ambiguous": tally.ambiguous,
                    "skipped": dict(tally.skipped),
                }
            )
    return {
        "fusion": totals.fusion,
        "applied": totals.applied,
        "reference": totals.reference,
        "ambiguous": totals.ambiguous,
        "skipped": sum(totals.skipped.values()),
        "reasons": dict(totals.skipped.most_common()),
        "pattern_parameters": totals.pattern_parameters,
        "sketches": rows,
    }
