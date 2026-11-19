"""Feature mappers: Fusion feature -> SolidCraft commands."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from f3d2solidcraft.engine import Engine
from f3d2solidcraft.model import Design, Feature

if TYPE_CHECKING:  # pragma: no cover - typing only
    from f3d2solidcraft.oracle import Oracle


class Unsupported(Exception):
    """This mapper cannot rebuild the feature; the replay falls back.

    `bodies` are Fusion body ids the replay should condemn when the feature itself names none. A
    feature that records no output body has nothing to attribute a fallback to, so refusing it would
    simply lose its material — the mapper says instead which bodies it could have changed, and those
    come back from the original.
    """

    def __init__(self, *args: object, bodies: tuple[str, ...] = ()) -> None:
        super().__init__(*args)
        self.bodies = bodies


class NoGeometry(Exception):
    """This feature makes no geometry, so there is nothing to rebuild and nothing failed.

    The replay records it as `skipped` with this reason. It is not `Unsupported`: a canvas or a
    comment is not a gap in the converter, and counting it as a failure hides the real ones.
    """


@dataclass
class Context:
    engine: Engine
    design: Design
    # Fusion parameter -> what a feature is handed for it: a SolidCraft parameter's name, or for one
    # in `param_inlined` the name or the formula it restates (`HingeDiameter / 2`).
    param_names: dict[str, str]
    sketch_names: dict[str, str]  # cadmpeg sketch id -> SolidCraft sketch name
    # cadmpeg sketch entity id -> every SolidCraft curve it was drawn as. Nearly always one;
    # a spline whose interior knots are uneven comes as a chain of Bézier segments (spec 76),
    # so anything that needs a single curve asks `sketch._one_curve` rather than indexing.
    curve_ids: dict[str, tuple[str, ...]]
    body_names: dict[str, str]  # cadmpeg body id -> SolidCraft body name
    param_expressions: dict[str, str]  # Fusion parameter -> expression as given to SolidCraft
    oracle: Oracle | None = None  # the original solid, when it is available to ask
    component_ids: dict[str, object] = field(default_factory=dict)  # cadmpeg component id -> SolidCraft id
    active_component: object = None  # what the engine is authoring into, so activating is not repeated
    # The placement each component really carries in the design, by cadmpeg component id, as a
    # 3x4. Only the ones `place` applied and read back: a placement that was refused, scaled or
    # mirrored is not here, so the frames of its sketches are left alone too and the component and
    # its geometry stay consistent with each other (spec section 102).
    placements: dict[str, tuple[tuple[float, ...], ...]] = field(default_factory=dict)
    # The bodies the feature that last built anything left with no output of the file to name them.
    # `name_outputs` already warns about these: the file declares fewer output ids than the bodies
    # built, so `body_names` has no key for them and nothing downstream can ask for them by id. The
    # body is in the engine all the same, and the feature right after it is usually the one that
    # meant to use it — measured on `Caja parametrica v2`, whose extrudes at ordinals 2 and 8
    # declare no output at all and whose fillets at 3 and 9 were refused for "its body was not
    # rebuilt natively" (spec section 107). Set on every build, so it is always the last one's.
    orphan_bodies: tuple[str, ...] = ()
    # The sheet metal rule made for each thickness the file states, in millimetres as the rule's
    # name spells it. A rule is a design-wide object, not a feature's parameter: `sheet.flange`
    # refuses a thickness passed to it and takes the rule's, so one rule per distinct thickness is
    # made on first use and named here (spec section 103).
    sheet_rules: dict[str, str] = field(default_factory=dict)
    # Sketches drawn without one of their entities, because this converter cannot draw its kind.
    # The geometry is there for whoever opens the design; what must not happen is a feature
    # building a body out of a profile that is missing a piece (spec section 87).
    partial_sketches: set[str] = field(default_factory=set)
    # Fusion parameter -> (the value the engine computed for it, its unit), as `parameters.add`
    # answered. What a mapper checks before it lets a feature follow a parameter (`params.follows`).
    param_values: dict[str, tuple[float, str]] = field(default_factory=dict)
    # Fusion parameters that are not in the design because they only restate the user's names: for
    # these `param_names` holds the name or the formula a feature is handed, not a parameter to
    # change (`params.driver` finds the one that is).
    param_inlined: set[str] = field(default_factory=set)


Mapper = Callable[[Feature, Context], list[str] | tuple[str, list[str]]]
MAPPERS: dict[str, Mapper] = {}


def register(tag: str) -> Callable[[Mapper], Mapper]:
    def wrap(fn: Mapper) -> Mapper:
        MAPPERS[tag] = fn
        return fn

    return wrap


# Mappers by the definition cadmpeg decoded, for a feature whose Fusion label this converter has no
# mapper under. A label is the word the designer's Fusion wrote, in the designer's language, and
# there are as many spellings as there are localizations; the definition is what the decoder made of
# the recipe, and there is one of those per family. So the label is the fast path and the definition
# is the one that generalizes — spec section 82.
BY_DEFINITION: dict[str, Mapper] = {}


def definition(kind: str) -> Callable[[Mapper], Mapper]:
    """Also register this mapper under the definition kind it rebuilds."""

    def wrap(fn: Mapper) -> Mapper:
        BY_DEFINITION[kind] = fn
        return fn

    return wrap


# What a join is told to do with pieces that end up touching each other only along an edge or at a
# point: keep them in **one body**, which is what the engine this converter ships on does on its own
# and what Fusion had. SolidCraft 0.40's kernel does not fuse them unless asked — its release says
# new property tests verify exactly that — so without this a conversion can hand the user two bodies
# where the file has one.
#
# Measured on `corpus-04`, replay with no oracle, the case spec section 128 found: 0.30.1 leaves
# **one** body of 1092.116 mm³ with **42 faces**; 0.40.0-rc.3 leaves **two**, 969.804 (23 faces) and
# 122.330 (19 faces), which share no volume at all — 0.000000 from both sides, they touch — and whose
# faces add up to the same 42. With this asked for, rc.3 leaves **one** body of 1092.134 mm³ and
# **42 faces**: the same body count and the same face count as the engine we ship.
#
# Harmless on 0.30.1, where `solid.extrude` has no such parameter: measured, both engines ignore a
# parameter they do not know (a deliberate `nonsense_param` is accepted by each), and the same replay
# on 0.30.1 is unchanged to the last digit with it passed.
#
# It only ever changes an edge or point contact: measured on two boxes joined at a shared **face**,
# every mode leaves one body of 1500 mm³ and 6 faces, while at an **edge** or a **point** the default
# leaves two bodies and this leaves one of 1500 mm³ and 12 faces (spec section 133).
JOIN_TOUCHING = "one_body"

from f3d2solidcraft.mappers import bodies as _bodies  # noqa: F401  (registers "RemoveBody", "Split", "CopyPasteBodies")
from f3d2solidcraft.mappers import combine as _combine  # noqa: F401  (registers "Combine")
from f3d2solidcraft.mappers import edges as _edges  # noqa: F401  (registers "Fillet" and "Chamfer")
from f3d2solidcraft.mappers import extrude as _extrude  # noqa: F401  (registers "Extrude")
from f3d2solidcraft.mappers import faces as _faces  # noqa: F401  (registers "OffsetFaces")
from f3d2solidcraft.mappers import loft as _loft  # noqa: F401  (registers "Loft")
from f3d2solidcraft.mappers import pattern as _pattern  # noqa: F401  (registers "Mirror")
from f3d2solidcraft.mappers import planes as _planes  # noqa: F401  (registers "WorkPlane")
from f3d2solidcraft.mappers import primitives as _primitives  # noqa: F401  (registers "CylinderPrimitive")
from f3d2solidcraft.mappers import records as _records  # noqa: F401  (registers "Base Feature", "Canvas")
from f3d2solidcraft.mappers import revolve as _revolve  # noqa: F401  (registers "Revolve")
from f3d2solidcraft.mappers import sheet as _sheet  # noqa: F401  (registers "BaseFlange")
from f3d2solidcraft.mappers import shell as _shell  # noqa: F401  (registers "Shell")
from f3d2solidcraft.mappers import sketch as _sketch  # noqa: F401  (registers "Sketch")
