"""A degree 2 nurbs curve is a conic arc, not a spline to draw with control points.

`_spline` refused one as `a degree 2 spline (SolidCraft has control points for 3 and 5)` and the
sketch went with it. SolidCraft has `sketch.conic`, which takes the two ends, the apex where the end
tangents meet and `rho` — the three poles of a rational quadratic Bézier and its middle weight. So
it is a translation (spec section 86).

Measured on a real design, `corpus-07.f3d`: four rational quadratics of three
poles, weights `(1.404…, 1.513…, 1.513…)`, none of them a circular arc.
"""

import os
from pathlib import Path

import pytest

from f3d2solidcraft.geometry import nurbs_curve
from f3d2solidcraft.mappers import Unsupported
from f3d2solidcraft.mappers.sketch import _conic

CORPUS = Path(os.environ.get("F3D2SC_CORPUS", "~/.f3d2solidcraft/corpus/propios")).expanduser()


def _at(poles, weights, t):
    """A rational quadratic Bézier at t, straight from its definition."""
    basis = [(1 - t) ** 2, 2 * t * (1 - t), t * t]
    divisor = sum(b * w for b, w in zip(basis, weights))
    return (
        sum(b * w * p[0] for b, w, p in zip(basis, weights, poles)) / divisor,
        sum(b * w * p[1] for b, w, p in zip(basis, weights, poles)) / divisor,
    )


def _quadratics(design):
    out = []
    for sketch in design.sketches.values():
        for entity in sketch.entities:
            if entity.kind != "nurbs":
                continue
            degree, _knots, poles, weights = nurbs_curve(entity.geometry)
            if degree == 2:
                out.append((poles, weights))
    return out


def test_what_it_refuses_rather_than_fit():
    """A quadratic of more than one span, and a weight that makes no conic."""
    with pytest.raises(Unsupported, match="more than one conic arc"):
        _conic([(0.0, 0.0), (1.0, 1.0), (2.0, 1.0), (3.0, 0.0)], [1.0, 1.0, 1.0, 1.0])
    with pytest.raises(Unsupported, match="non-positive weight"):
        _conic([(0.0, 0.0), (1.0, 2.0), (2.0, 0.0)], [1.0, 0.0, 1.0])
