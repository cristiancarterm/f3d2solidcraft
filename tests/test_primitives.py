"""A Fusion primitive solid: the file says how big it is and never where it stands.

cadmpeg types one `primitive` and gives the shape whole — `{"kind": "cylinder", "radius": 2.25,
"height": 59.0, "angle": 6.283…}` — with no origin and no axis, while `solid.cylinder` wants a
`base` and an `axis`. So the size is read from the file and the placement is measured off the box of
the body the file says the feature becomes (spec section 85).

Measured on a real design: `corpus-06.f3d`, whose first feature is one of these
and whose body `RielArriba` is that cylinder untouched by anything later.
"""

import os
from pathlib import Path

from f3d2solidcraft.mappers.primitives import _placement

CORPUS = Path(os.environ.get("F3D2SC_CORPUS", "~/.f3d2solidcraft/corpus/propios")).expanduser()


def test_the_axis_and_the_base_come_off_the_box_and_nothing_else_does():
    """The reading itself: a cylinder's box is 2r by 2r by height, so the axis is the odd extent.

    No engine and no file — this is the arithmetic the placement rests on, including its refusals.
    A box that is not that shape is not this cylinder standing on its own, and the mapper needs
    that `None` to refuse rather than put a cylinder somewhere the original does not have one.
    """
    box = {"min": [-2.25, -2.25, 0.0], "max": [2.25, 2.25, 59.0]}
    assert _placement(box, 2.25, 59.0) == ("Z", [0.0, 0.0, 0.0])
    moved = {"min": [10.0, -2.25, -2.25], "max": [69.0, 2.25, 2.25]}
    assert _placement(moved, 2.25, 59.0) == ("X", [10.0, 0.0, 0.0])
    assert _placement(box, 2.25, 10.0) is None, "a height no extent matches is not this cylinder"
    assert _placement(box, 1.0, 59.0) is None, "a diameter the other two do not match either"
