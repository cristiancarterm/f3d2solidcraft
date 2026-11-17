"""Plane frames and 2D sketch geometry helpers."""

from __future__ import annotations

import math

from f3d2solidcraft.model import Frame, Sketch, SketchEntity, TrimmedSegment

Point2 = tuple[float, float]


class UnsupportedGeometry(ValueError):
    """A sketch entity kind this version cannot turn into points."""


def _cross(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def plane_spec(frame: Frame) -> dict:
    """SolidCraft sketch.create plane: x along Fusion's u axis, y = normal x u."""
    y = _cross(frame.normal, frame.u_axis)
    clean = [0.0 if abs(c) < 1e-12 else c for c in y]
    return {"origin": list(frame.origin), "x_dir": list(frame.u_axis), "y_dir": clean}


def _uv(p: dict) -> Point2:
    return (float(p["u"]), float(p["v"]))


def entity_points(entity: SketchEntity, reversed: bool = False, segments: int = 48) -> list[Point2]:
    g = entity.geometry
    if entity.kind == "line":
        pts = [_uv(g["start"]), _uv(g["end"])]
    elif entity.kind == "circle":
        cx, cy = _uv(g["center"])
        r = float(g["radius"])
        pts = [
            (cx + r * math.cos(2 * math.pi * i / segments), cy + r * math.sin(2 * math.pi * i / segments))
            for i in range(segments)
        ]
    elif entity.kind == "arc":
        cx, cy = _uv(g["center"])
        r = float(g["radius"])
        a0, a1 = float(g["start_angle"]), float(g["end_angle"])
        if a1 <= a0:
            a1 += 2 * math.pi
        pts = [
            (
                cx + r * math.cos(a0 + (a1 - a0) * i / segments),
                cy + r * math.sin(a0 + (a1 - a0) * i / segments),
            )
            for i in range(segments + 1)
        ]
    elif entity.kind == "nurbs":
        pts = nurbs_points(*nurbs_curve(g), segments=segments)
    else:
        raise UnsupportedGeometry(f"sketch entity kind {entity.kind}")
    return pts[::-1] if reversed else pts


def segment_points(
    entity: SketchEntity, start: float, end: float, reversed: bool = False, segments: int = 48
) -> list[Point2]:
    """The part of one entity a trimmed boundary names, as points.

    The parameter is the entity's own, and what it measures depends on the kind — so only the kinds
    the corpus settles are translated and the rest are refused. Measured on `test weird box`'s one
    trimmed region (spec section 112):

    - **line**: 0 to 1 along start -> end. The file's start of `0.009148867715291616` on a 100 mm
      edge is 0.9148867715 mm, and the chord the circle cuts off that edge is 0.914886771 mm. (Its
      end is `0.9999999999999986`, which is the file's way of writing the far end.)
    - **circle**: the angle in radians from the +u axis. The file's
      `[1.5524975701750943, 1.5890950834146989]` is pi/2 +/- 0.0182987, and the half-angle of that
      same chord is asin(0.457443/25) = 0.01829873.

    - **arc**: the same quantity as a circle, the angle in radians from the +u axis — measured on
      cadmpeg 0.6.11, which declares trimmed boundaries over arcs where 0.6.8 declared none
      (spec section 137). Two independent readings say so. Where a boundary uses the whole arc, its
      `start` and `end` are the arc's own `start_angle` and `end_angle` **to the last digit**: the
      corpus-05's four arcs give `[1.5707963267948966, 4.71238898038469]` against an arc whose
      angles are exactly those. Where it trims one, the values fall strictly inside that span:
      `corpus-01`'s `[-2.3317388986423015, -0.809853754947492]` inside `[-2.3412531376361687,
      -0.8003395159536246]`.

      And the check that decides it rather than suggesting it: read as absolute angles, all four of
      the corpus's arc boundaries **close to 0.000000 mm** between consecutive endpoints. Read as a
      normalized 0..1 of the arc's own sweep they leave gaps of 2.34 to 21.25 mm, and read as an
      angle measured from `start_angle`, 2.12 to 19.89 mm. The right reading is not close to the
      others; it is exact.

    A nurbs has a parameter too and the corpus holds no trimmed segment of one, so which quantity it
    is there is not measured — and a wrong guess would not fail loudly, it would quietly bound a
    different region. Refused.
    """
    # `start` to `end` in the order the file gives them, with no sorting: the direction a boundary
    # runs is part of what it says, and `reversed` is the file's own word for flipping it. Sorting
    # the pair would quietly turn a segment that runs backwards into one that runs forwards.
    g = entity.geometry
    if entity.kind == "line":
        (x0, y0), (x1, y1) = _uv(g["start"]), _uv(g["end"])
        pts = [(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t) for t in (start, end)]
    elif entity.kind in ("circle", "arc"):
        # One branch for both, because the parameter is the same quantity: the angle in radians
        # from the +u axis. An arc's `start_angle` and `end_angle` are not read here on purpose —
        # the boundary's own range is what it uses, and where it uses the whole arc that range IS
        # those two angles.
        cx, cy = _uv(g["center"])
        r = float(g["radius"])
        pts = [
            (
                cx + r * math.cos(start + (end - start) * i / segments),
                cy + r * math.sin(start + (end - start) * i / segments),
            )
            for i in range(segments + 1)
        ]
    else:
        raise UnsupportedGeometry(
            f"a parameter range on a sketch entity of kind {entity.kind}: what its parameter "
            "measures is not settled by anything in the corpus"
        )
    return pts[::-1] if reversed else pts


def boundary_polygon(sketch: Sketch, boundary: tuple[TrimmedSegment, ...]) -> list[Point2]:
    """A trimmed region's boundary as one closed polygon, in the order the file lists it."""
    by_id = {e.id: e for e in sketch.entities}
    poly: list[Point2] = []
    for segment in boundary:
        entity = by_id.get(segment.entity)
        if entity is None:
            raise UnsupportedGeometry("a trimmed boundary names an entity that is not in the sketch")
        poly.extend(segment_points(entity, segment.start, segment.end, segment.reversed))
    return poly


def profile_polygon(sketch: Sketch, index: int) -> list[Point2]:
    by_id = {e.id: e for e in sketch.entities}
    poly: list[Point2] = []
    for edge in sketch.profiles[index]:
        poly.extend(entity_points(by_id[edge.entity], edge.reversed))
    return poly


def point_in_polygon(pt: Point2, poly: list[Point2]) -> bool:
    x, y = pt
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _pole(point: dict) -> dict:
    """The u/v of one pole, whichever of the two shapes cadmpeg wrote it in."""
    inner = point.get("point")
    return inner if isinstance(inner, dict) else point


def nurbs_curve(geometry: dict) -> tuple[int, list[float], list[Point2], list[float]]:
    """degree, knots, poles and weights of a cadmpeg nurbs curve, in sketch u/v."""
    curve = geometry.get("curve") or {}
    points = (curve.get("poles") or {}).get("points") or []
    try:
        degree = int(curve["degree"])
        knots = [float(k) for k in curve["knots"]]
        # A rational curve's poles come as {point: {u, v}, weight}; a polynomial one's as bare
        # {u, v} with no weight around them (measured on `mango para lima`'s two degree 3 curves).
        poles = [(float(_pole(p)["u"]), float(_pole(p)["v"])) for p in points]
        weights = [float(p.get("weight", 1.0)) for p in points]
    except (KeyError, TypeError, ValueError) as exc:
        raise UnsupportedGeometry(f"a nurbs curve this reader cannot read: {exc}") from None
    if degree < 1 or not poles:
        raise UnsupportedGeometry(f"a nurbs curve of degree {degree} with {len(poles)} poles")
    return degree, knots, poles, weights


def _insert_knot(
    degree: int, knots: list[float], poles: list[Point2], u: float
) -> tuple[list[float], list[Point2]]:
    """Böhm's knot insertion: the same curve, with `u` once more in its knot vector.

    Exact, and that is the whole point: inserting a knot rewrites the poles so the curve does not
    move at all. Only the poles that carry the span around `u` change, each to a point on the
    segment between two of the old ones.
    """
    # The last span whose start is at or before u: with u already present, that is its last
    # occurrence, which is what keeps the denominators below away from zero.
    k = max(i for i in range(degree, len(knots) - degree - 1) if knots[i] <= u)
    out: list[Point2] = []
    for i in range(len(poles) + 1):
        if i <= k - degree:
            out.append(poles[i])
        elif i <= k:
            lo, hi = knots[i], knots[i + degree]
            a = (u - lo) / (hi - lo)
            before, now = poles[i - 1], poles[i]
            out.append(((1 - a) * before[0] + a * now[0], (1 - a) * before[1] + a * now[1]))
        else:
            out.append(poles[i - 1])
    return sorted([*knots, u]), out


def bezier_spans(degree: int, knots: list[float], poles: list[Point2]) -> list[list[Point2]]:
    """The same curve as a chain of Bézier segments of the same degree, exactly.

    A clamped B-spline becomes a chain of Bézier curves by raising every interior knot to
    multiplicity `degree`: the poles then fall into groups of `degree + 1` that share an end, and
    each group is a Bézier of that degree over its own span. Nothing is approximated — knot
    insertion is an exact rewrite of the poles (`_insert_knot`), so the chain *is* the curve.

    What it buys: SolidCraft's control point splines are clamped and uniform, so a curve whose
    interior knots are unevenly spaced cannot be handed over as it stands. Each Bézier segment has
    no interior knots at all, which makes it both — so every segment is drawable as it is. Measured
    on the corpus's four such curves (spec section 76), all degree 3 with every interior knot
    doubled: 5 to 8 segments each.

    The caller must have checked that the curve is clamped and non-rational; this only needs the
    knot vector to be the right length for the poles.
    """
    knots, poles = list(knots), list(poles)
    if len(knots) != len(poles) + degree + 1:
        raise UnsupportedGeometry(
            f"a degree {degree} spline with {len(poles)} poles needs {len(poles) + degree + 1} knots, "
            f"got {len(knots)}"
        )
    while True:
        interior = knots[degree + 1 : len(knots) - degree - 1]
        short = next((u for u in sorted(set(interior)) if interior.count(u) < degree), None)
        if short is None:
            break
        knots, poles = _insert_knot(degree, knots, poles, short)
    return [poles[j : j + degree + 1] for j in range(0, len(poles) - 1, degree)]


def _span(degree: int, knots: list[float], t: float) -> int:
    """The knot span t falls in, clamped so the last span owns the end of the curve."""
    last = len(knots) - degree - 2
    for index in range(degree, last + 1):
        if t < knots[index + 1]:
            return index
    return last


def nurbs_points(
    degree: int, knots: list[float], poles: list[Point2], weights: list[float], segments: int = 48
) -> list[Point2]:
    """A NURBS curve sampled into `segments` straight pieces, by de Boor's algorithm.

    Rational all through — the weights are carried in homogeneous coordinates and divided out at the
    end — so a curve with weights other than 1 samples correctly even where it cannot be rebuilt
    exactly. Sampling is uniform in the parameter, not in arc length: it is for area and
    point-in-profile work, where what matters is that the polygon follows the curve closely.
    """
    if len(poles) != len(weights) or len(knots) != len(poles) + degree + 1:
        raise UnsupportedGeometry(
            f"a degree {degree} spline with {len(poles)} poles needs {len(poles) + degree + 1} knots, "
            f"got {len(knots)}"
        )
    start, end = knots[degree], knots[len(knots) - degree - 1]
    out: list[Point2] = []
    for step in range(segments + 1):
        t = start + (end - start) * step / segments
        span = _span(degree, knots, t)
        # de Boor on (x·w, y·w, w), so the division at the end is the rational part
        working = [
            (poles[span - degree + i][0] * w, poles[span - degree + i][1] * w, w)
            for i, w in enumerate(weights[span - degree : span + 1])
        ]
        for level in range(1, degree + 1):
            for i in range(degree, level - 1, -1):
                lo = knots[span - degree + i]
                hi = knots[span + i - level + 1]
                alpha = 0.0 if hi == lo else (t - lo) / (hi - lo)
                before, now = working[i - 1], working[i]
                working[i] = tuple(b * (1.0 - alpha) + n * alpha for b, n in zip(before, now))
        x, y, w = working[degree]
        out.append((x / w, y / w) if w else (x, y))
    return out


def polygon_area(poly: list[Point2]) -> float:
    """Unsigned area of a closed polygon, whichever way it winds."""
    total = 0.0
    for i, (x1, y1) in enumerate(poly):
        x2, y2 = poly[(i + 1) % len(poly)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def _encloses(outer: list[Point2], inner: list[Point2]) -> bool:
    """Whether every point of inner lies in outer.

    Strictly every one, because a loop that merely overlaps another is not inside it and its area is
    not a hole in it. Read as a majority, `corpus-04`'s first sketch had one loop's overlapping
    neighbours taken off it as holes and the profile came out at -32.166 mm², a negative area.
    """
    return all(point_in_polygon(p, outer) for p in inner)


def direct_children(loops: list[list[Point2]], outer: int) -> list[int]:
    """The loops directly inside `loops[outer]`: the ones whose area is not part of its profile.

    A loop two levels down is material again — the island in the middle of a hole belongs to the
    plate — so only direct children count.

    Shared by the two things that have to agree about one profile: the area it covers
    (`profile_area`) and a point inside it (`interior_point`'s `excluded`). They disagreed, and
    `corpus-03`'s `Extrude 8` is what that cost — spec section 78.
    """
    return [
        j
        for j in range(len(loops))
        if j != outer
        and _encloses(loops[outer], loops[j])
        and not any(
            k not in (outer, j) and _encloses(loops[outer], loops[k]) and _encloses(loops[k], loops[j])
            for k in range(len(loops))
        )
    ]


def profile_area(loops: list[list[Point2]], outer: int, holes: tuple[int, ...]) -> float:
    """The area the profile at `outer` covers: its loop, less the loops directly inside it.

    `holes` are the ones the file named; they are direct children already, and naming them cannot
    take the same area off twice.
    """
    taken = set(direct_children(loops, outer)) | {h for h in holes if h != outer}
    return polygon_area(loops[outer]) - sum(polygon_area(loops[j]) for j in taken)


def _inside_the_edges(target: list[Point2]) -> list[Point2]:
    """Points a little way in from each edge of `target`, for a band a grid steps over.

    A grid finds a point in the middle of an area, and that is the right answer whenever there is a
    middle. What it cannot find is a thin band — a profile that is one loop less the loop nested
    just inside it. Measured on `corpus-03`'s `Extrude 8`: a 463 mm² ring around a 12165 mm²
    island, barely over a millimetre wide, where the 25-step grid over a 115 mm extent lands no
    point at all (spec section 78).

    These candidates come off the loop's own edges, so a band has them wherever it is: the midpoint
    of each edge, stepped in along its normal by a ladder of distances. Both signs of the normal are
    offered rather than working out the winding — the caller tests every candidate against the
    target anyway, so the one that points outwards is simply rejected.
    """
    xs = [p[0] for p in target]
    ys = [p[1] for p in target]
    diagonal = math.dist((min(xs), min(ys)), (max(xs), max(ys)))
    out: list[Point2] = []
    for a, b in zip(target, [*target[1:], target[0]]):
        length = math.dist(a, b)
        if not length:
            continue
        nx, ny = -(b[1] - a[1]) / length, (b[0] - a[0]) / length
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        for scale in (1e-1, 1e-2, 1e-3, 1e-4):
            step = diagonal * scale
            out.append((mx + nx * step, my + ny * step))
            out.append((mx - nx * step, my - ny * step))
    return out


def interior_point(
    target: list[Point2], others: list[list[Point2]], grid: int = 25, excluded: list[list[Point2]] = ()
) -> Point2 | None:
    """A point inside target and outside every excluded loop, in as few others as possible, nearest the centre.

    Candidates are a grid over the loop's box plus points just inside its edges
    (`_inside_the_edges`), which is what makes a thin band reachable. The scoring is unchanged, so a
    grid point still wins any tie: an edge candidate only comes out on top when it lies in strictly
    fewer of the other loops, which is precisely the band case.
    """
    xs = [p[0] for p in target]
    ys = [p[1] for p in target]
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    candidates = [
        (
            min(xs) + (max(xs) - min(xs)) * i / grid,
            min(ys) + (max(ys) - min(ys)) * j / grid,
        )
        for i in range(1, grid)
        for j in range(1, grid)
    ]
    best: tuple[int, float, Point2] | None = None
    for pt in [*candidates, *_inside_the_edges(target)]:
        if not point_in_polygon(pt, target) or any(point_in_polygon(pt, e) for e in excluded):
            continue
        score = (sum(point_in_polygon(pt, o) for o in others), math.dist(pt, (cx, cy)), pt)
        if best is None or score[:2] < best[:2]:
            best = score
    return best[2] if best else None
