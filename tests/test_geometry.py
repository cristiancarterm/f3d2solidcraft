import math

import pytest

from f3d2solidcraft.geometry import (
    interior_point,
    nurbs_curve,
    nurbs_points,
    plane_spec,
    point_in_polygon,
    profile_area,
    profile_polygon,
)
from f3d2solidcraft.model import Frame


def test_plane_spec_completes_a_right_handed_frame():
    spec = plane_spec(Frame((0.0, 0.0, 0.0), (0.0, 0.0, -1.0), (1.0, 0.0, 0.0)))
    assert spec == {"origin": [0.0, 0.0, 0.0], "x_dir": [1.0, 0.0, 0.0], "y_dir": [0.0, -1.0, 0.0]}


def test_point_in_polygon():
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    assert point_in_polygon((5.0, 5.0), square)
    assert not point_in_polygon((15.0, 5.0), square)


def test_interior_point_avoids_the_circles_of_the_real_sample(design):
    sketch = design.sketches[design.features[0].sketch]
    loops = [profile_polygon(sketch, i) for i in range(len(sketch.profiles))]
    pt = interior_point(loops[2], [loops[0], loops[1]])
    assert pt is not None
    assert point_in_polygon(pt, loops[2])
    assert math.dist(pt, (-50.0, 0.0)) > 25.0
    assert math.dist(pt, (50.0, 0.0)) > 25.0


def test_profile_polygon_of_a_circle_has_its_radius(design):
    sketch = design.sketches[design.features[0].sketch]
    ring = profile_polygon(sketch, 0)
    assert all(math.dist(p, (-50.0, 0.0)) == pytest.approx(25.0) for p in ring)


def test_interior_point_never_lands_in_an_excluded_hole():
    outer = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    hole = [(3.0, 3.0), (7.0, 3.0), (7.0, 7.0), (3.0, 7.0)]
    # overlaps everything around the hole (slit polygon), so containment scoring alone ties and picks the centre
    around = [(-1.0, -1.0), (11.0, -1.0), (11.0, 11.0), (-1.0, 11.0), (-1.0, 5.0), (3.0, 5.0)]
    around += [(3.0, 3.0), (7.0, 3.0), (7.0, 7.0), (3.0, 7.0), (3.0, 5.0), (-1.0, 5.0)]
    assert point_in_polygon(interior_point(outer, [hole, around]), hole)
    pt = interior_point(outer, [around], excluded=[hole])
    assert point_in_polygon(pt, outer)
    assert not point_in_polygon(pt, hole)


def test_interior_point_none_when_everything_is_excluded():
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    assert interior_point(square, [], excluded=[square]) is None


PLATE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
HOLE = [(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0)]
INNER = [(1.5, 1.5), (2.5, 1.5), (2.5, 2.5), (1.5, 2.5)]


def test_profile_area_takes_off_the_loops_inside_it():
    assert profile_area([PLATE, HOLE], 0, ()) == pytest.approx(96.0)
    assert profile_area([PLATE, HOLE], 1, ()) == pytest.approx(4.0)


def test_profile_area_only_takes_off_the_loops_directly_inside():
    # The island inside the hole is the hole's child, not the plate's: it is material of the plate.
    assert profile_area([PLATE, HOLE, INNER], 0, ()) == pytest.approx(96.0)
    assert profile_area([PLATE, HOLE, INNER], 1, ()) == pytest.approx(3.0)
    assert profile_area([PLATE, HOLE, INNER], 2, ()) == pytest.approx(1.0)


def test_profile_area_takes_off_a_declared_hole_once():
    assert profile_area([PLATE, HOLE], 0, (1,)) == pytest.approx(96.0)


def test_profile_area_does_not_take_off_a_loop_that_only_overlaps():
    """An overlapping neighbour is not a hole. Read as a majority this went negative on real data."""
    straddles = [(8.0, 8.0), (14.0, 8.0), (14.0, 14.0), (8.0, 14.0)]
    assert profile_area([PLATE, straddles], 0, ()) == pytest.approx(100.0)
    assert profile_area([PLATE, straddles], 1, ()) == pytest.approx(36.0)


def test_profile_area_of_a_polygonized_circle_is_within_a_percent_of_the_circle(design):
    sketch = design.sketches[design.features[0].sketch]
    loops = [profile_polygon(sketch, i) for i in range(len(sketch.profiles))]
    assert profile_area(loops, 0, ()) == pytest.approx(math.pi * 25.0**2, rel=0.01)


def test_a_spline_with_collinear_poles_evaluates_on_that_line():
    """A B-spline lies in the convex hull of its poles, so collinear poles give the segment itself."""
    poles = [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]
    knots = [0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0]
    points = nurbs_points(3, knots, poles, [1.0] * 4, segments=8)
    assert points[0] == pytest.approx((0.0, 0.0))
    assert points[-1] == pytest.approx((3.0, 3.0))
    assert all(x == pytest.approx(y) for x, y in points), points


def test_a_spline_starts_and_ends_at_its_end_poles():
    """Clamped knots pin the curve to the first and last pole, whatever the ones between do."""
    poles = [(0.0, 0.0), (0.0, 10.0), (10.0, 10.0), (10.0, 0.0), (20.0, 5.0), (25.0, 0.0)]
    knots = [0.0] * 6 + [1.0] * 6
    points = nurbs_points(5, knots, poles, [1.0] * 6, segments=16)
    assert points[0] == pytest.approx(poles[0])
    assert points[-1] == pytest.approx(poles[-1])
    # and it never leaves the box its poles span
    assert all(-1e-9 <= x <= 25.0 + 1e-9 and -1e-9 <= y <= 10.0 + 1e-9 for x, y in points), points


def test_a_polynomial_spline_writes_its_poles_without_a_weight():
    """cadmpeg gives a non-rational curve's poles as bare {u, v}, with no `point` or `weight` around.

    Measured on `mango para lima`: its two degree-3 curves come as `form: "polynomial"` and were read
    as unreadable, which the sketch mapper then reported as an internal error of its own.
    """
    rational = {
        "curve": {
            "degree": 1,
            "knots": [0.0, 0.0, 1.0, 1.0],
            "poles": {
                "form": "rational",
                "points": [
                    {"point": {"u": 0.0, "v": 0.0}, "weight": 1.0},
                    {"point": {"u": 2.0, "v": 4.0}, "weight": 1.0},
                ],
            },
        }
    }
    polynomial = {
        "curve": {
            "degree": 1,
            "knots": [0.0, 0.0, 1.0, 1.0],
            "poles": {"form": "polynomial", "points": [{"u": 0.0, "v": 0.0}, {"u": 2.0, "v": 4.0}]},
        }
    }
    assert nurbs_curve(polynomial) == nurbs_curve(rational)
    assert nurbs_curve(polynomial) == (1, [0.0, 0.0, 1.0, 1.0], [(0.0, 0.0), (2.0, 4.0)], [1.0, 1.0])
