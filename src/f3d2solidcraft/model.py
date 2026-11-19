"""Neutral model of a Fusion design. Knows nothing about SolidCraft."""

from __future__ import annotations

from dataclasses import dataclass, field

Vec3 = tuple[float, float, float]


@dataclass(frozen=True)
class Frame:
    origin: Vec3
    normal: Vec3
    u_axis: Vec3


@dataclass(frozen=True)
class Parameter:
    name: str
    expression: str
    role: str
    unit: str
    value_mm: float | None  # lengths only, from ezf3d's internal centimetres


@dataclass(frozen=True)
class SketchEntity:
    id: str
    kind: str  # point | line | circle | arc | other cadmpeg kinds
    construction: bool
    geometry: dict  # cadmpeg geometry, millimetres, sketch coordinates u/v


@dataclass(frozen=True)
class ProfileEdge:
    entity: str
    reversed: bool


@dataclass(frozen=True)
class TrimmedSegment:
    """One piece of a trimmed region's boundary: an entity and the part of it the boundary uses.

    `start` and `end` are the entity's own parameter, which is not the same quantity for every
    kind. Measured on the corpus's one trimmed region (spec section 112): a line's parameter runs
    0 to 1 along start -> end, and a circle's is the angle in radians from the +u axis. The two
    were not assumed — the segment the file gives for the line is `[0.009148867715291616, 1.0]` and
    0.009148867715 is exactly the chord the circle cuts off that 100 mm edge, and the circle's
    `[1.5524975701750943, 1.5890950834146989]` is pi/2 give or take the half-angle of that same
    chord. Any other kind is refused rather than guessed, because nothing in the corpus says what
    its parameter means.
    """

    entity: str
    start: float
    end: float
    reversed: bool = False


@dataclass(frozen=True)
class SketchConstraint:
    kind: str
    definition: dict


@dataclass(frozen=True)
class Sketch:
    id: str
    frame: Frame | None
    entities: tuple[SketchEntity, ...]
    profiles: tuple[tuple[ProfileEdge, ...], ...]
    constraints: tuple[SketchConstraint, ...]
    dimensions: tuple[Parameter, ...] = ()
    # Fusion hides a sketch once it has been used, which is nearly always: measured, 86 of the
    # corpus's 88 sketches are hidden. `True` when the file says nothing.
    visible: bool = True


@dataclass(frozen=True)
class Extrude:
    profile_kind: str  # sketch_profiles | sketch_regions | sketch_selection | historical_faces | ...
    sketch: str | None
    profiles: tuple[int, ...]
    op: str  # new_body | join | cut | intersect
    reversed: bool
    extent_kind: str  # one_sided | ...
    length_mm: float | None
    # Where the extrude stops, which is a field of the extent's side and not the extent itself.
    # Measured over the corpus: 81 `blind` (a length, which `length_mm` holds), 7 `through_all` and
    # 4 `to_face`. Read apart from the extent because refusing by the extent blamed the wrong thing
    # — a `through_all` cut was refused as "extrude extent one_sided", which is the one part of it
    # that was fine, and 24 features fell behind that message in `corpus-08`.
    termination: str = "unknown"
    # A `to_face` extrude stops at a face the file names through the kernel history. The face itself
    # has no geometry in the file, but the edges of that history state that bound it do
    # (`feature_input_topologies.edge_geometry`): these are their interior points, which is enough
    # to recover the face's plane. Empty when the face is not historical or has no bounding edge
    # with geometry (spec section 150).
    to_face_points: tuple[Vec3, ...] = ()
    distance_param: str | None = None  # Fusion parameter name driving the distance
    regions: tuple[tuple[int, tuple[int, ...]], ...] = ()  # sketch_regions: (outer loop, hole loops)
    # sketch_selection: how many members the opaque selection group holds. **Not** how many
    # profiles or regions Fusion picked: measured on the corpus, `Extrude 1` of `corpus-05`
    # holds 1 member where the inference needs 7 regions, and a 2-loop sketch came back with 4
    # members. Parsed and reported, never used to choose candidates (the oracle does that); three
    # reader tests pin the parsing, so the field stays as the record of what the file really says.
    selection_count: int = 0
    # How many regions the file describes as `trimmed` — a boundary of entity segments with
    # parameter ranges, not loop indexes (cadmpeg 0.6.8's IR 10). No loop index names such a
    # region: the corpus's one case bounds its region with parts of two loops. It is translated
    # all the same, by the entities and parameter ranges it does name (spec section 112), and the
    # count stays because it is what a refusal reports when the boundary cannot be read.
    trimmed_regions: int = 0
    # One entry per trimmed region, in the file's order, and empty where its boundary could not be
    # read — so `len(trimmed_boundaries) == trimmed_regions` always holds and an empty entry says
    # "the file describes a region here that this reader could not parse".
    trimmed_boundaries: tuple[tuple[TrimmedSegment, ...], ...] = ()
    # How many regions the file declares in all, of any kind. `regions` is all-or-nothing — one
    # region of a kind it cannot read leaves it empty — so without this count a list holding one
    # loop region and one trimmed region would translate the trimmed one and drop the loop one
    # silently, and the feature would be reported `exact` short a profile. The corpus holds no such
    # list; this is the guard that keeps one from passing (spec section 112).
    region_count: int = 0


@dataclass(frozen=True)
class FaceMotion:
    """An `OffsetFaces`: the motion is stated and the faces are not.

    The corpus's twelve, measured whole (spec section 118): **every one** is an `offset` with a real
    distance, and **every one** names its faces as an opaque operand group — eleven `native`, one
    `historical`. No `face_geometry` appears anywhere in any file of the corpus.

    `state_faces` is what makes the refusal precise rather than vague: the input state the file
    records for the feature lists **all** the faces of the bodies in scope, with no geometry — 15 for
    the smallest, 206 for the largest, 871 in all. So the file does say how many faces the feature
    could have moved and never which ones, and that is also what rules out searching for them the
    way `edges.py` searches edge subsets: a fillet's input topology names its own edges, while this
    one names a whole body's faces.
    """

    kind: str  # offset — the only motion the corpus holds
    distance_mm: float | None
    faces_kind: str  # native | historical: how the file names the faces it moved
    state_faces: int  # faces the input state lists (the bodies' own, not the ones that moved)
    state_bodies: int


@dataclass(frozen=True)
class BodyOp:
    """A feature whose operands are whole bodies: a delete, a split, or a paste of bodies.

    Three families cadmpeg types and this converter cannot build, kept in one record because they
    fail the same way: the *operation* is stated and the *bodies* are not. Re-measured on cadmpeg
    0.6.11 over the corpus's **four** (spec sections 117 and 136):

    | feature | operando | lo que trae | lo que falta |
    | --- | --- | --- | --- |
    | `RemoveBody 1` / `2` (corpus-03 51, 53) | `native` | `mode: delete_selected` | qué cuerpos |
    | `Split 1` (corpus-03 55) | `historical` | tool `faces`, también `historical` | el cuerpo y la cara |
    | `Split 1` (corpus-07 4) | `historical` | **tool `plane`: un WorkPlane que sí construimos** | el cuerpo |

    The fifth was `CopyPasteBodies 3` (corpus-03 56), `resolved` with one `native` id and nothing else.
    cadmpeg 0.6.11 decodes that feature as a base feature, so `insert_bodies` no longer occurs in the
    corpus at all; the mapper's guard for it stays, tested against an operand built in the test.

    The corpus-07 split is the one worth a second look: `solid.split_body` takes a plane by name and the
    file names a `WorkPlane` feature this converter builds `exact`. What it has no name for is the
    body — and at that ordinal there is no body at all, because the `Base Feature` before it carries
    B-rep and no recipe (measured: `body_names` empty, nothing standing, both ids condemned).

    `operand_bodies` counts the ids the file really lists, which is **not** the same as ids anything
    maps: a `historical` entry lists one and it names a state of the kernel (spec section 116's
    lesson, a third time).
    """

    kind: str  # delete_body | split_body | insert_bodies
    operand_kind: str  # native | historical | resolved — how the file names the bodies
    operand_bodies: int  # how many ids it lists under that kind
    mode: str = ""  # a delete's mode, e.g. delete_selected
    tool_kind: str = ""  # a split's tool: plane | faces | …
    tool_plane: str = ""  # the plane feature id, when the tool is a plane


@dataclass(frozen=True)
class Pattern:
    """A Fusion pattern — rectangular, circular, along a path, or a mirror — as the file states it.

    The corpus holds one, `Caja parametrica v2`'s `Mirror 4` (ordinal 33), and it is the clearest
    case of a *half* recipe: the seed is `resolved` and names a body id, and the pattern itself is
    `{"kind": "unresolved", "form": "mirror"}` — the file says it is a mirror and does not say about
    what plane. A plane is not guessable, so `form` is reported and never acted on.

    `seed_bodies` are the ids the file resolves, which is not the same as bodies the replay can
    reach: measured on that very feature (spec section 116), the one id it names is produced
    **thirteen ordinals later** by the `Combine 3` of ordinal 46, and by ordinal 33 it is already
    condemned. An operand the file calls `resolved` can still be out of reach.
    """

    form: str  # mirror | rectangular | circular | path — what the file calls the pattern
    pattern_kind: str  # resolved | unresolved: whether the file states the pattern's own geometry
    seed_kind: str  # bodies | features | faces — what the seeds are
    seed_bodies: tuple[str, ...]  # the body ids the file resolves for the seed, if any


@dataclass(frozen=True)
class LoftSection:
    """One section of a loft, as the file names it.

    `kind` is `sketch` for the corpus's one loft, and the value is the sketch's id. A sketch is a
    *profile* section when the file gives it loops and an *apex* when it gives it none and only
    points — which is how `solid.loft` splits them too, into `{sketch, profiles?}` and
    `{sketch, point}` (spec section 113).
    """

    kind: str
    value: str


@dataclass(frozen=True)
class Loft:
    """A Fusion loft: a body through two or more sections, with optional guides.

    The corpus holds exactly one — `soporte controles`'s `Solevación 2` — and the file resolves it
    **whole**: two sketch sections, no guides, `new_body`, open, solid, not ruled, not linearized.
    Nothing here is opaque, which is what sets this family apart from every wall of section 108.

    What stops it is the engine, and it says so itself: `solid.loft` answers *"not supported yet:
    lofting curved sections to a point"*. Measured on the engine (spec section 113): a section of
    straight edges lofts to a point, two curved sections loft to each other, and a curved section
    to a point is the one combination it refuses — and this loft's profile section is a degree 5
    nurbs. So the mapper hands the recipe over and reports whatever the engine answers, rather than
    predicting a limit of its own.
    """

    sections: tuple[LoftSection, ...]
    guidance_kind: str  # guides | rail | centerline — what the file calls its guidance
    guides: int  # how many guide curves it names under that guidance; 0 for the corpus's one
    op: str  # new_body | join | cut | intersect
    closed: bool
    solid: bool
    ruled: bool
    linearize: bool


@dataclass(frozen=True)
class Combine:
    """A Fusion Combine: a boolean between the design's own bodies.

    The file resolves the target and leaves the tools opaque — a `native_set` of design-records that
    nothing else in the model resolves. So which bodies the tools are is not readable, and that is
    the whole difficulty of the family.

    What **is** readable is how many there are, and against the bodies of the kernel state the
    feature took as input that count can close. Measured on the corpus's four Combines (spec
    section 109):

    | | target | tools | bodies of the state | |
    | --- | --- | --- | --- | --- |
    | `Combine 5` Caja ord 25 | 1 | 2 | 3 | adds up |
    | `Combine 2` Caja ord 27 | 1 | 2 | 3 | adds up |
    | `Combine 3` Caja ord 46 | 1 | 2 | 5 | 2 left over |
    | `Combine 3` corpus-03 ord 59 | 1 | 1 | 8 | 6 left over |

    Where it adds up there is nothing to choose: the state holds 3 bodies, the file says one of them
    is the target and that there are two tools, and there is one way to take 2 of 2. Where it does
    not, which of the others are the tools is not in the file, and the two that do add up are no
    licence to guess at the two that do not — that is the false zero this converter exists not to
    produce.

    `state_bodies` is None when no `feature_input_topologies` entry names this feature as its input,
    so the arithmetic has nothing to close against.
    """

    op: str  # join | cut | intersect
    # How the file names the target, and the body ids where it resolves them. A `resolved_set` gives
    # `f3d:brep/…` ids, the same shape as a feature's outputs; a `historical` target names
    # history-state ids instead, which no part of this converter maps to a body.
    target_kind: str
    target_bodies: tuple[str, ...]
    # How many bodies the file says the target is, which it states even where it names none of
    # them: a `historical` target has a count and no id this converter maps. Kept apart from
    # `target_bodies` so a refusal can blame the kind and not read as "no target" (spec 104's
    # lesson: refusing by the wrong field sends the reader to the wrong place).
    target_count: int
    tool_count: int
    state_bodies: int | None = None


@dataclass(frozen=True)
class Revolve:
    """A Fusion revolve: a profile turned about an axis.

    Measured over the corpus, cadmpeg 0.6.8 resolves 2 of its 5 revolves — both in
    `mango para lima` — and the two it resolves are the simplest shape the family has: the whole
    sketch as the profile, a full turn, a new body, and an axis along a world axis through the
    origin. The other three come back `{"definition": "unresolved", "family": "revolve"}`, so
    there is nothing in the file to read for them and this holds nothing for them either.
    """

    # What the file says of the construction itself: "resolved", or whatever else it holds. Every
    # other field of a construction that is not resolved is empty, and read without this the
    # refusal picked the first empty field it met and blamed that — a revolve with no recipe was
    # refused for "making a surface", which the file never said.
    construction: str
    profile_kind: str  # sketch | sketch_profiles | sketch_regions | ...
    sketch: str | None
    profiles: tuple[int, ...]
    op: str  # new_body | join | cut | intersect
    extent_kind: str  # one_sided | ...
    angle_rad: float | None  # the turn, as the file gives it; None when it says anything else
    # The axis as the file resolves it, in the design's millimetres. `solid.revolve` takes an axis
    # by name (`X`/`Y`/`Z` of the world, or a line of the sketch) and not as an origin and a
    # direction, so the mapper can only answer for an axis those names reach; it is kept whole here
    # so the refusal can say what the axis actually was.
    axis_origin: Vec3 | None
    axis_direction: Vec3 | None
    # Fusion revolves surfaces too. `False` means a surface revolve, which `solid.revolve` is not.
    solid: bool = True


@dataclass(frozen=True)
class BaseFlange:
    """A Fusion sheet-metal base flange: a sketch given a thickness, and the sheet's first body.

    Measured on `corpus-12.f3z`, the corpus's only file with sheet metal: all 3 of its
    base flanges resolve, all 3 the same shape — the whole sketch as the profile, 2.5 mm thick,
    `side: "forward"`. The file says nothing of the bend radius or the K-factor here, and for a
    base flange nothing it builds depends on them: the body is a flat plate, measured at exactly
    length x width x thickness. They matter to the flanges that bend off it, and those are a
    different feature (section 103).

    Its 19 `EdgeFlange` siblings are not here because none of them is reachable: measured on cadmpeg
    0.6.11, 7 carry no definition at all and the 12 it types all stand behind an EdgeFlange of the
    first kind, a Mirror, an R-Pattern or a Move that this converter cannot rebuild either. (Under
    0.6.8 the same file read 14, as 5 and 9 — spec section 136.)
    """

    profile_kind: str  # sketch | ...
    sketch: str | None
    thickness: float | None  # millimetres, as the file states it
    # Which way the material goes from the sketch's plane. Measured: `forward` is along the plane's
    # normal, which is what `sheet.flange` builds with no `flip` — the file's own body centre for
    # each of the 3 sits at z = thickness/2 above a sketch at z = 0. No other value appears in the
    # corpus, so the mapper refuses anything else by name rather than guessing its opposite.
    side: str


@dataclass(frozen=True)
class EdgeOp:
    """A Fillet or Chamfer: what to apply, how big, and how many edges Fusion used."""

    kind: str  # fillet | chamfer
    value: float  # radius (fillet) or distance (chamfer), millimetres
    # two_distances chamfer: the second value is an ANGLE in radians, never a distance
    angle_rad: float | None = None
    edge_count: int | None = None  # historical groups say how many edges; native ones do not
    # A point inside each edge the file names, where the file gives one for every edge of the group.
    # Since IR 10 `feature_input_topologies` carries an `edge_geometry` for a kernel history state —
    # interior point, tangent, start and end — and only for the edges that feature really used: in
    # `corpus-01`, `Fillet 1` names 8 edges and the state has 101, and the 8 are there.
    # `solid.fillet` takes exactly a point on each edge, so where this is filled there is nothing to
    # search for (spec section 107). Empty when the file resolves no edge, or only some of them.
    edge_points: tuple[Vec3, ...] = ()


@dataclass(frozen=True)
class Shell:
    """A Shell: hollow the body out, removing the faces the file names.

    Measured on the corpus's two, both in `Caja parametrica v2` (spec sections 141 and 142). What the
    file gives is complete: `thickness` as a number, `outward` as a flag, and the removed face named
    through the kernel history — not as the opaque `design-construction-operand-group` that makes
    `OffsetFaces` and `delete_face` unreachable.

    `bounding_points` is what makes the face findable: `feature_input_topologies` carries an
    `edge_geometry` for the edges of that state, and the ones that name this face are its boundary.
    All 24 points of the corpus's two shells lie in one plane (z = 22.0), so the face is planar and
    the body has exactly one face in that plane — which is how the mapper picks it without the file
    naming a point on the face itself. Empty when the file does not give geometry for every bounding
    edge, because a partial boundary names a different plane.
    """

    thickness_mm: float
    outward: bool
    bounding_points: tuple[tuple[float, float, float], ...] = ()


@dataclass(frozen=True)
class Feature:
    ordinal: int
    name: str
    tag: str  # cadmpeg source_tag: Sketch, Extrude, SurfaceDeleteFace, ...
    # Which definition cadmpeg decoded for it: "sketch", "extrude", "fillet", "move_face", ... or
    # "native" when it typed nothing and handed the feature back opaque. Reported, never used to
    # choose a mapper (the tag does that), so it says which side an unmapped feature's gap is on.
    definition_kind: str
    suppressed: bool
    outputs: tuple[str, ...]
    sketch: str | None = None
    # True when `sketch` was not read from the file but worked out by `cadir`, because cadmpeg left
    # this feature untyped and so named no sketch for it. The mapper reports `inferred` for it.
    sketch_derived: bool = False
    frame: Frame | None = None  # datum plane: the plane the file already resolved
    # datum axis: a point on it and its direction, as the file resolves them (spec section 152)
    datum_axis: tuple[Vec3, Vec3] | None = None
    # The component the file says this feature belongs to (`product_definition`), when it says.
    component: str | None = None
    extrude: Extrude | None = None
    edge_op: EdgeOp | None = None
    revolve: Revolve | None = None
    base_flange: BaseFlange | None = None
    combine: Combine | None = None
    loft: Loft | None = None
    pattern: Pattern | None = None
    body_op: BodyOp | None = None
    face_motion: FaceMotion | None = None
    # The shape of a Fusion primitive, as cadmpeg types it: `{"kind": "cylinder", "radius": …,
    # "height": …, "angle": …}`. The size and nothing else — the definition says nowhere where the
    # solid stands, which is why `map_primitive` measures that off the original (spec section 85).
    shell: Shell | None = None
    primitive: dict | None = None
    parameters: tuple[str, ...] = ()
    # The object id of the timeline item that places this feature, read off the end of its cadmpeg
    # id (`…:Extrude3:1234`). It is what pairs the feature with the record ezf3d reads its parameters
    # from: that record is the object right before the item. None for a feature of a merged `.f3z`
    # member, whose id is an `f3d:xref/…` and whose item lives in another member's stream.
    item: int | None = None


@dataclass(frozen=True)
class Body:
    id: str
    visible: bool
    # The name the designer gave the body in Fusion, where the file carries one. Measured over the
    # corpus's 90 bodies, two do — both in `corpus-03`, and both say something the designer meant:
    # `corpus-03 (dont print)` and `corpus-03 (dont print)`. They used to be dropped here
    # and the converted file showed the import's own `cadmpeg_model` instead (spec section 84).
    name: str | None = None


@dataclass(frozen=True)
class Paint:
    """A colour Fusion binds to a body, or to one face of it (spec 0.4, item 3).

    `rgba` is the appearance's `base_color`, each channel 0..1. A face's own colour carries the face
    id: `appearance.assign` picks a face by a point, and the file gives none for a face, so a face
    colour is read and reported but not applied (see `paint.paint_native`).
    """

    body: str  # the Fusion body id
    rgba: tuple[float, float, float, float]
    face: str | None = None  # the Fusion face id, for a face's own colour


@dataclass(frozen=True)
class Component:
    """A Fusion component: the design's own parts, each owning bodies and sketches.

    The root of the design is a component too, and the file leaves its `name` empty. Measured on
    the three corpus files that have components, every body and every sketch belongs to exactly
    one of them, so the lists partition the design.
    """

    id: str
    name: str | None  # Fusion's component name; None for the root holder, which the file leaves unnamed
    bodies: tuple[str, ...]
    sketches: tuple[str, ...]


IDENTITY: tuple[tuple[float, ...], ...] = ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0))

# How far a resolved placement may sit from the identity and still be the identity: a nanometre, far
# under any tolerance a kernel or a machine works to and far over the float dust a resolved
# transform carries (`Occurrence.identity` holds the measurement).
PLACEMENT_DUST = 1e-9


@dataclass(frozen=True)
class Occurrence:
    """Where a component sits: its placement in the parent, as the file resolved it."""

    id: str
    component: str | None  # local prototype: the component id. None when the prototype is a document
    document: str | None  # external prototype (.f3z): the member path cadmpeg resolved
    parent: str | None  # parent occurrence id; None is the root of the design
    ordinal: int
    transform: tuple[tuple[float, ...], ...]  # 3x4 rows, millimetres
    scale: Vec3

    @property
    def identity(self) -> bool:
        """Placed where its component was built, at its own size: nothing to move.

        It is the only placement the converter can prove; anything else is reported and left alone
        rather than approximated.

        Compared **within a tolerance**, because an exact comparison counted float dust as a
        placement. Measured on `mango para lima` under cadmpeg 0.6.8: `Collet`'s resolved transform
        is the identity to 2.6e-16 mm of translation and about 1e-32 in the rotation terms, and an
        exact `==` read that as a component moved — by 0.26 femtometres. A nanometre is far under
        any tolerance a kernel or a machine works to and far over the dust, so it separates the two
        without admitting a placement that matters. `Caja parametrica v2`'s `Top` (180° about z and
        65.6 mm), `corpus-07`'s pipe fitting (-90 mm) and `corpus-08`'s `corpus-08` (a quarter
        turn and three offsets) stay what they are (spec section 95).
        """
        flat = [(a, b) for row, wanted in zip(self.transform, IDENTITY) for a, b in zip(row, wanted)]
        return all(abs(a - b) <= PLACEMENT_DUST for a, b in flat) and all(
            abs(s - 1.0) <= PLACEMENT_DUST for s in self.scale
        )


@dataclass
class Design:
    source: str
    parameters: list[Parameter]
    sketches: dict[str, Sketch]
    features: list[Feature]
    bodies: list[Body]
    warnings: list[str] = field(default_factory=list)
    components: dict[str, Component] = field(default_factory=dict)
    occurrences: list[Occurrence] = field(default_factory=list)
    # The features no parameter record could be paired with, by name: they carry no parameters.
    unpaired: list[str] = field(default_factory=list)
    # The colours the file binds to bodies and faces.
    paints: list[Paint] = field(default_factory=list)
