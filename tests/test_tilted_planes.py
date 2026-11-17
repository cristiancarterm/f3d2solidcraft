"""A datum plane tilted about one coordinate axis, built where the file puts it.

`construct.plane.offset` only ever makes a plane parallel to an origin plane, so a tilted one was
refused: `corpus-03`'s two `WorkPlane` features carry a normal of (0, -0.5, -0.866) — 150° about
x — and were lost. A rotation plus an offset reaches them exactly (spec section 81).

The frame is read back the only way the engine offers: `inspect_design` does not report a
construction plane, but it does report the plane of a *sketch*, so a sketch drawn on the built plane
is what the measurements here go through.
"""

import math

import pytest

from f3d2solidcraft.engine import Engine
from f3d2solidcraft.mappers.planes import _TILTS

TILTED = (13, 14)  # `WorkPlane 1` and `WorkPlane 2`, the corpus's only tilted datum planes


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _frame_of(eng, plane: str, label: str):
    """The origin and normal of a plane, read through a sketch drawn on it."""
    eng.batch(
        [
            {"command": "sketch.create", "params": {"plane": plane, "name": label}},
            {"command": "sketch.finish", "params": {}},
        ]
    )
    drawn = next(s for s in eng.call("inspect_design", {})["sketches"] if s["name"] == label)["plane"]
    return tuple(drawn["origin"]), _cross(drawn["x_dir"], drawn["y_dir"]), tuple(drawn["x_dir"])


@pytest.mark.parametrize("flat", sorted(_TILTS))
@pytest.mark.parametrize("degrees", [30.0, -70.0, 115.0])
def test_the_rule_holds_for_every_axis_and_not_only_the_corpus_s(flat, degrees, tools, tmp_path):
    """The base, axis and angle of `_TILTS`, measured against the engine for all three axes.

    The corpus only tilts about x, so the other two rows of the table would otherwise go untested.
    The normals here are built from an angle rather than read from a file — which is the point: what
    is being measured is the engine's own rotation convention, not any design's data. Each case asks
    for a normal and a point and checks the plane that comes back carries both.
    """
    turn = math.radians(degrees)
    normal = {
        0: (0.0, -math.sin(turn), math.cos(turn)),
        1: (math.sin(turn), 0.0, math.cos(turn)),
        2: (math.cos(turn), math.sin(turn), 0.0),
    }[flat]
    origin = (3.0, -8.25, 4.5)
    base, axis, angle_of, u_of = _TILTS[flat]
    angle = angle_of(normal)
    assert math.isclose(math.degrees(angle), degrees, abs_tol=1e-9)
    step = sum(a * b for a, b in zip(normal, origin))
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        eng.batch(
            [
                {
                    "command": "construct.plane.at_angle",
                    "params": {"base": base, "axis": axis, "angle": f"{degrees} deg", "name": "T"},
                },
                {
                    "command": "construct.plane.offset",
                    "params": {"base": "T", "offset": f"{step} mm", "name": "P"},
                },
            ]
        )
        built, got, u = _frame_of(eng, "P", "probe")
    assert abs(sum(a * b for a, b in zip(got, normal)) - 1.0) < 1e-9, (got, normal)
    off = sum(a * b for a, b in zip(got, [origin[i] - built[i] for i in range(3)]))
    assert abs(off) < 1e-9, f"the point asked for is {off} mm off the plane"
    # And the u axis the table predicts is the one the rotation really gives, which is what lets the
    # mapper warn only when the file's own u axis differs from it.
    assert all(abs(a - b) < 1e-9 for a, b in zip(u, u_of(angle))), (u, u_of(angle))
