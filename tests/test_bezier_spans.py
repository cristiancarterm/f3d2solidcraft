"""The unevenly knotted splines, drawn as the Bézier chains they are made of.

Spec section 76 measured what this recovers: two whole sketches of the corpus — one of
`corpus-11`'s and `mango para lima`'s `Sketch 5` — were lost because `_spline`
refused a curve whose interior knots are not evenly spaced, and that refusal takes the sketch with
it. The four curves behind it are all degree 3, clamped, non-rational, with every interior knot
doubled: Fusion's fit point spline.

The exactness claim is checked **without** the engine, against `nurbs_points` — the de Boor
sampler the polygonizer already uses. The engine's volume of a curved body carries a ~6.2e-05
relative floor (measured on a circle in spec section 76), which is far too coarse to tell an exact
rewrite from a good approximation.
"""

import math
from itertools import pairwise

from f3d2solidcraft.geometry import bezier_spans, nurbs_curve

# The two corpus files whose sketches this recovers.

SAMPLES = 512  # enough that every span of the longest chain (8) gets dozens of its own


def _uneven(design):
    """Every nurbs curve of the design whose interior knots are not evenly spaced."""
    out = []
    for sketch in design.sketches.values():
        for e in sketch.entities:
            if e.kind != "nurbs":
                continue
            degree, knots, poles, _weights = nurbs_curve(e.geometry)
            interior = knots[degree : len(knots) - degree]
            steps = [b - a for a, b in pairwise(interior)]
            if steps and any(abs(s - steps[0]) > 1e-9 * max(1.0, abs(steps[0])) for s in steps):
                out.append((degree, knots, poles))
    return out


def _bernstein(poles, t):
    """A Bézier segment at t, by the Bernstein basis its poles stand for."""
    n = len(poles) - 1
    weights = [math.comb(n, i) * t**i * (1 - t) ** (n - i) for i in range(n + 1)]
    return (
        sum(w * p[0] for w, p in zip(weights, poles)),
        sum(w * p[1] for w, p in zip(weights, poles)),
    )


def test_a_curve_that_is_already_one_bezier_is_left_alone():
    """Four poles and no interior knots: nothing to insert, so the poles come back untouched."""
    poles = [(0.0, 0.0), (10.0, 20.0), (30.0, 20.0), (40.0, 0.0)]
    assert bezier_spans(3, [0, 0, 0, 0, 1, 1, 1, 1], poles) == [poles]


def _warnings(result):
    return [w for row in (*result.features, *result.sketches) for w in row.warnings]
