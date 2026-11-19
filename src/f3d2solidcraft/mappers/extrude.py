"""Extrude from sketch profiles or loop regions, one side, to a distance or through all."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from f3d2solidcraft.engine import CommandRefused, roll_back, rolled_back
from f3d2solidcraft.geometry import (
    UnsupportedGeometry,
    boundary_polygon,
    direct_children,
    interior_point,
    polygon_area,
    profile_area,
    profile_polygon,
)
from f3d2solidcraft.mappers import JOIN_TOUCHING, Context, Unsupported, definition, register
from f3d2solidcraft.mappers.outputs import name_outputs, no_target
from f3d2solidcraft.mappers.params import negated
from f3d2solidcraft.model import Feature
from f3d2solidcraft.oracle import (
    OracleUnavailable,
    interference_incomplete,
    noise,
    out_of_budget,
)
from f3d2solidcraft.pairing import declared_reference

_OPERATIONS = {"new_body": "new", "join": "join", "cut": "cut", "intersect": "intersect"}
# The extrude terminations this converter builds. `blind` goes to a length; `through_all` to
# `solid.extrude`'s own `through_all`. `to_face` stops at a face the file names by a kernel identity
# (spec section 104); the face has no geometry of its own in the file, but the edges that bound it
# do, and where they lie in one plane parallel to the sketch the extrude is exactly a blind one to
# that plane (spec section 150).
_TERMINATIONS = ("blind", "through_all", "to_face")
# How far the points bounding a `to_face` target may sit from one plane and still be that plane.
# The file writes these coordinates to full precision, so this is the design's own linear tolerance.
TO_FACE_DUST = 1e-5


def _to_face_distance(ctx: Context, feature: Feature) -> float:
    """How far a `to_face` extrude goes, read off the plane of the face it stops at.

    The face is named through the kernel history, and that state's `edge_geometry` gives a point
    inside every edge bounding it (`Extrude.to_face_points`). Measured on `corpus-06
    metro`: both of its `to_face` extrudes stop at face 880, whose bounding edges all lie at
    x = 20.324818, and both sketches sit at x = 1.324818 with normal +x — so each is an extrude of 19 mm.

    Only that case is built: three points or more, not on one line, every one at the same distance
    along the sketch's normal (the face is a plane parallel to the sketch), and on the side the file's
    direction points to. A face at an angle would need `solid.extrude`'s `to`, and a point on it the
    file does not give; anything short of the above is refused rather than approximated.
    """
    ex = feature.extrude
    points = ex.to_face_points
    if len(points) < 3:
        raise Unsupported("extrude termination to_face: the file gives no geometry for the face it stops at")
    frame = ctx.design.sketches[ex.sketch].frame
    if frame is None:
        raise Unsupported("extrude termination to_face: the file resolves no plane for its sketch")
    norm = math.sqrt(sum(c * c for c in frame.normal))
    normal = tuple(c / norm for c in frame.normal)
    along = [sum((p[i] - frame.origin[i]) * normal[i] for i in range(3)) for p in points]
    if max(along) - min(along) > TO_FACE_DUST:
        raise Unsupported(
            "extrude termination to_face: the face it stops at is not parallel to its sketch "
            f"(its bounding points sit {min(along):.4g} to {max(along):.4g} mm along the sketch normal)"
        )
    first = points[0]
    spread = max(math.dist(first, p) for p in points)
    far = max(points, key=lambda p: math.dist(first, p))
    off_line = (
        max(
            math.dist(p, tuple(first[i] + (far[i] - first[i]) * _t(first, far, p) for i in range(3)))
            for p in points
        )
        if spread > TO_FACE_DUST
        else 0.0
    )
    if off_line <= TO_FACE_DUST:
        raise Unsupported("extrude termination to_face: the points bounding its face lie on one line")
    distance = sum(along) / len(along)
    if abs(distance) <= TO_FACE_DUST or (distance < 0) != ex.reversed:
        raise Unsupported(
            f"extrude termination to_face: the face it stops at is {distance:.4g} mm along the sketch "
            f"normal, which is not the side the file extrudes toward"
        )
    return abs(distance)


def _t(a, b, p) -> float:
    """Where `p` projects on the line from `a` to `b`, as a fraction of it."""
    ab = [b[i] - a[i] for i in range(3)]
    return sum((p[i] - a[i]) * ab[i] for i in range(3)) / sum(c * c for c in ab)


# Regions a sketch may hold before measuring them one by one is not worth what it costs. It was 64
# and the real cap was somewhere else: the predictive gate below compared `total + 1` trials against
# a 60 s budget at a 2 s floor, so no search of more than 29 regions ever started. With the budget
# counted in questions and large enough for the corpus's heaviest conversion (47 of them), that gate
# no longer refuses them, and the cost is measured: `corpus-04`'s `Extrude` at ordinal 11 is a
# sketch of 38 regions whose candidates the kernel mostly fails to build, and letting it run takes
# the file from 7.0 s to **360.8 s, 51x**, with the feature refused by the engine at the end either
# way — the same 11/0/3 counts. Spec section 110 measured the same search at the 300 s budget: 251 s
# and not one feature gained. So the cap is explicit, and it is the number the old arithmetic
# implied: 29 (spec section 131).
#
# Raising it was measured again on SolidCraft 0.43.0, where that feature does build: 11/0/3 became
# 13/1/0 with the body native — and **wrong**: 4.738 mm³ of the original uncovered (0.38 %, about one
# of the 32 half-teeth), so the file stopped verifying, and a parameter change then lost the
# chamfer's reference. The `inferred` was mistaken and two `exact` features stood on it. So it stays
# at 29 (spec section 152).
MAX_REGIONS = 29

# How far a loop's area may sit from the region it names. Arcs reach the engine as polygons, so a
# circle drawn with 48 segments is 0.29 % under the circle itself; a fragment of a profile is out
# by far more than that.
PROFILE_TOLERANCE = 0.01

# What a trial reports: (original not covered, material the original lacks), or None when that
# candidate could not be read at all — the engine would not build it, or nobody could measure it.
Measure = Callable[[list[int]], "tuple[float, float] | None"]


@dataclass
class _Scope:
    """The reference body a search measures every candidate against, fixed by its first reading.

    Resolved once and then reused. Re-deriving it per candidate is what made the old multi-body
    reading unsound: a candidate that drops a reference body entirely would stop being asked about
    it and read as covering everything it was asked about. On a single-body original this is that
    body and costs nothing.

    `declared` is the reference body the file names for this feature's output, which the pairing is
    asked to confirm rather than to find.
    """

    oracle: object
    declared: str | None = None
    name: str | None = None
    # Candidates whose comparison came back unmeasurable, and the first reason given. A reading that
    # carries a warning is not a volume anyone should read, but refusing one candidate is no reason
    # to end the feature: measured on `corpus-04`, the third comparison of `Extruir 3` warned and
    # that threw away the eight exact features before it.
    unmeasured: int = 0
    unmeasured_reason: str = ""

    def not_measured(self, reason: str) -> None:
        self.unmeasured += 1
        self.unmeasured_reason = self.unmeasured_reason or reason

    def note(self) -> list[str]:
        """The warning that says candidates went unread, so an answer never reads as the only one."""
        if not self.unmeasured:
            return []
        note = (
            f"{self.unmeasured} candidate(s) could not be measured and were not considered "
            f"({self.unmeasured_reason})"
        )
        return [note]

    def of(self, body: str) -> str:
        if self.name is None:
            self.name = self.oracle.standing_for(body, self.declared)
        return self.name


def _distance(ctx: Context, feature: Feature) -> tuple[str, list[str]]:
    """The extrude distance expression, plus a warning when it cannot follow its parameter."""
    ex = feature.extrude
    name = ex.distance_param
    param = next((p for p in ctx.design.parameters if p.name == name), None) if name else None
    if name and (param is None or name not in ctx.param_names):
        return f"{ex.length_mm} mm", [f"extrude distance is not parametric: {name} missing"]
    if param is None:
        return f"{ex.length_mm} mm", []
    sc = ctx.param_names[name]
    # Fusion stores a reversed extrude as a negative distance; direction carries the sign here.
    return (negated(sc) if (param.value_mm or 0.0) < 0 else sc), []


def _extent(ctx: Context, feature: Feature) -> tuple[dict, list[str]]:
    """How far `solid.extrude` is told to go: a distance, or through everything.

    Both come out as the extent half of the command's parameters, so the real extrude and the
    oracle's trials ask for the same thing without either having to know which kind it is.
    `through_all` honours `direction` — measured: a circle sketched halfway up a 40x40x20 slab cut
    `through_all` upwards takes 785.27 mm³, which is the half above it (pi*25*10 = 785.40) and not
    the 1570.8 of both halves, so a reversed extrude still cuts only its own way (section 104).
    """
    if feature.extrude.termination == "through_all":
        return {"through_all": True}, []
    if feature.extrude.termination == "to_face":
        distance = _to_face_distance(ctx, feature)
        return {"distance": f"{distance:.10g} mm"}, [
            f"to_face read as the plane its bounding edges share, {distance:.6g} mm along the sketch normal"
        ]
    distance, warnings = _distance(ctx, feature)
    return {"distance": distance}, warnings


def _picks_for(loops: list, targets) -> list[dict] | None:
    """Interior points for (outer, holes) targets, or None when one has no interior point.

    A profile is one loop less the loops nested directly inside it, which is exactly what
    `profile_area` measures — so the point this picks must be outside those loops too, not only
    outside the holes the file happened to name. It was only outside the named ones, and the point
    then landed on the island in the middle instead of in the profile: measured on `corpus-03`'s
    `Extrude 8`, whose three loops are nested one in the next, two of its targets picked the same
    innermost region and SolidCraft refused the extrude for it (spec section 78).
    """
    picks = []
    for outer, holes in targets:
        if outer >= len(loops) or any(h >= len(loops) for h in holes):
            return None
        off = {h for h in holes if h != outer} | set(direct_children(loops, outer))
        others = [loop for j, loop in enumerate(loops) if j != outer and j not in off]
        pt = interior_point(loops[outer], others, excluded=[loops[j] for j in off])
        if pt is None:
            return None
        picks.append({"point": [pt[0], pt[1]]})
    return picks


def _trial(
    ctx: Context,
    params: dict,
    targets: list[str],
    scope: _Scope,
    then: Callable[[], None] | None = None,
) -> tuple[float, float] | None:
    """Run the feature with this candidate, measure the body it affected, and undo it.

    The candidate is applied with the feature's own operation, so what gets measured is the design
    after the feature, not a stand-in body: a cut's tool is not the cut's result, and a join only
    adds to a body that is already there.

    **One unit of budget per trial**, charged by `shortfall` when the oracle answers and here when
    the engine refused the candidate and the oracle was asked nothing. It used to be charged the
    trial's whole wall clock by a wrapper here, which is what made a conversion's counts depend on
    how loaded the machine was (spec section 130). A refused candidate still costs the engine the
    build and the undo, which is why it is not free: measured on `corpus-04`, a search of 38
    regions whose candidates the engine mostly refuses takes 360 s and puts 13 questions, so a
    budget that only counted questions would not bound it at all (spec section 131).
    """
    depth = ctx.engine.undo_depth()
    asked = ctx.oracle.asked
    try:
        with rolled_back(ctx.engine, depth):
            try:
                out = ctx.engine.execute("solid.extrude", {**params, "name": "f3d2solidcraft trial"})
                if then is not None:
                    # What a later feature does to the result before it is measured (the later cuts
                    # of `_added_inside_past_cuts`). Inside the same rollback, so it is undone with
                    # the candidate.
                    then()
                # A join or a cut reshapes its target; a new body is the one it created. Only the
                # target is measured: a cut that splits it leaves the other pieces out of the
                # reading, so coverage would be understated for a feature whose cut breaks its body
                # apart.
                affected = targets[0] if targets else (out.get("created_bodies") or [None])[0]
                if affected is None:
                    return None
                # Names, not measurements: a `measure` recomputes every body's volume, area, faces
                # and centre of mass and only names are wanted here. On a 74.8 MB package of the
                # user's, 1440 bodies, that is **52 s** against the **0.0 s** `body_names` takes —
                # and this runs per feature, which is where its 81 minutes went (spec section 92).
                # Every name asked about is one the replay built, a definition in the design, so the
                # definition listing answers it; the placements `measure` adds beyond it are an
                # import's instances, which are never the replay's.
                if affected not in set(ctx.engine.body_names()):
                    # A cut that consumed the body it worked on leaves nothing to cover the original:
                    # that is a measurement of a complete failure to cover, not a question the oracle
                    # cannot answer. What it failed to cover is the one reference body it stood for,
                    # or the whole original when the scope was never resolved.
                    volumes = ctx.oracle.volumes()
                    original = (
                        volumes.get(scope.name, sum(volumes.values()))
                        if scope.name
                        else sum(volumes.values())
                    )
                    return max(original, noise(original) * 2), 0.0
                # The pairing is resolved outside the catch below: a reference body this candidate
                # cannot be paired with is a fact about the whole search, not about this candidate,
                # and its own message says far more than "could not be measured" would.
                reference = scope.of(affected)
                try:
                    return ctx.oracle.shortfall(affected, reference)
                except OracleUnavailable as exc:
                    # Unreadable is not an answer either, and not a reason to end the feature:
                    # skipped like a candidate the engine will not build, and counted.
                    scope.not_measured(str(exc))
                    return None
            except CommandRefused:
                return None  # the engine would not build this set: it is not the answer
    finally:
        if ctx.oracle.asked == asked:
            ctx.oracle.charge()


def _engine_profiles(ctx: Context, sketch_name: str) -> list[dict]:
    """Every region SolidCraft finds in this sketch, in its own order, as the engine reports them.

    Each one carries its area, the curve ids of its outer boundary and one curve-id list per hole,
    which is what lets a region the file declares be matched against them.
    """
    finished = ctx.engine.call("inspect_design", {"measure": False})
    for sketch in finished.get("sketches") or []:
        if sketch.get("name") == sketch_name:
            return list(sketch.get("profiles") or [])
    return []


def _engine_regions(ctx: Context, sketch_name: str) -> list[float]:
    """The area of every region SolidCraft finds in this sketch, in its own order."""
    return [float(p.get("area_mm2") or 0.0) for p in _engine_profiles(ctx, sketch_name)]


def _loop_curves(ctx: Context, sketch, index: int) -> frozenset[str]:
    """The SolidCraft curve ids of one cadmpeg loop, as a set.

    Every curve of every edge, because an entity is not always one curve: a spline drawn as a chain
    of Bézier segments contributes all of them, which is what the engine's own region boundary
    lists. Taking one of them would make the loop match no region at all.
    """
    return frozenset(curve for edge in sketch.profiles[index] for curve in ctx.curve_ids.get(edge.entity, ()))


def _declared_region(
    ctx: Context, sketch, profiles: list[dict], outer: int, holes: tuple[int, ...]
) -> tuple[list[int], float] | None:
    """The engine regions that make up the region the file declares, and their total area.

    A `sketch_regions` extrude names its profile as loops, so unlike a bare profile index there is
    nothing to guess — only to translate. SolidCraft may split that profile, and when it does it says
    so itself: the region whose outer boundary is the declared outer loop carries one hole per piece
    it was split off from, each as the very curve-id list that is another region's own boundary. So
    the declared profile is that region plus the regions filling every hole it has that the file did
    not declare.

    `None` when the outer loop matches no region or a hole matches none, which leaves the caller to
    refuse rather than extrude part of a profile.

    More than one piece is a **reassembly**, not a translation, and the caller does not take it on
    trust: filling the holes is a decision the file does not make, and its only check — the area —
    cannot see it. Spec section 121 has the measurement on `corpus-01`'s ordinal 10.
    """
    wanted_outer = _loop_curves(ctx, sketch, outer)
    declared_holes = {_loop_curves(ctx, sketch, h) for h in holes}
    if not wanted_outer:
        return None
    by_boundary: dict[frozenset[str], int] = {}
    for index, profile in enumerate(profiles):
        by_boundary.setdefault(frozenset(profile.get("curves") or []), index)
    root = by_boundary.get(wanted_outer)
    if root is None:
        return None
    keep = [root]
    for hole in profiles[root].get("holes") or []:
        curves = frozenset(hole)
        if curves in declared_holes:
            continue  # the file asked for this one to stay a hole
        piece = by_boundary.get(curves)
        if piece is None:
            return None  # a hole no region accounts for: the profile cannot be put back together
        keep.append(piece)
    return keep, sum(float(profiles[i].get("area_mm2") or 0.0) for i in keep)


def _shared_with(ctx: Context, body: str, others: list[str]) -> tuple[str, float] | None:
    """The body `body` shares most material with and how much, or None when nothing was measured.

    Between native bodies, which is the one pair `inspect.interference` answers reliably: the kernel
    fails on it when one side is the imported original (`edges.py` carries that measurement), and the
    reference bodies are deliberately not in `others` — the new body is *supposed* to sit inside the
    original.

    None for "not measured", never 0.0: an engine that refused the question or could not check every
    pair says nothing about whether these bodies overlap, and reading that as "they do not" would be
    the false zero the oracle refuses to produce.
    """
    rest = [name for name in others if name != body]
    if not rest:
        return None
    asked = [body, *rest]
    try:
        out = ctx.engine.call("execute", {"command": "inspect.interference", "params": {"bodies": asked}})
    except CommandRefused:
        return None
    if not isinstance(out, dict) or interference_incomplete(out, len(set(asked)), about=body):
        return None
    worst: tuple[str, float] | None = None
    for hit in out.get("interferences") or []:
        pair = {hit.get("a"), hit.get("b")}
        if body not in pair:
            continue
        other = next(iter(pair - {body}), body)
        volume = float(hit.get("volume_mm3") or 0.0)
        if worst is None or volume > worst[1]:
            worst = (str(other), volume)
    return worst


def _trimmed_picks(
    ctx: Context, sketch, boundaries: tuple, profiles: list[dict], warnings: list[str]
) -> tuple[list[int], list[str]]:
    """Region indexes for the regions the file describes as a trimmed boundary.

    A trimmed region names no loop, so there is no loop to translate — but it does name entities,
    and parts of entities, and the engine's own regions carry the curve ids of their boundaries. So
    the region is found the same way a declared loop is: by the ids, with the area as the check
    that nothing was left out.

    **The ids are what identifies it and the area is what confirms it, in that order**, and the
    measurement that settles the order is in this very sketch (spec section 112): `test weird box`
    holds two rectangles of the same 100x15, so its regions come back at 1500.000 mm2 and
    1499.997 mm2 and no area tolerance worth having tells those apart. The curve ids do, exactly —
    the trimmed boundary's five entities map to the five curves of one region and to nothing else.

    Raises `Unsupported` when the entities match no region or more than one, when the boundary
    cannot be turned into a polygon, or when the region found does not cover the area the boundary
    describes. The caller takes that to the measured path, which is where this feature was before.
    """
    if not boundaries:
        # Unreachable through `map_extrude` as it stands — a `sketch_regions` extrude with no loop
        # has at least one trimmed region, and the reader keeps one entry per region. It is here
        # because the alternative is not a wrong message, it is an extrude built from no profile at
        # all, reported `exact`.
        raise Unsupported("this extrude names no loop of the sketch and no trimmed boundary either")
    picks: list[int] = []
    for boundary in boundaries:
        if not boundary:
            raise Unsupported(
                "the file describes a region as a trimmed boundary and this reader could not read "
                "its segments"
            )
        wanted = frozenset(curve for s in boundary for curve in ctx.curve_ids.get(s.entity, ()))
        if not wanted:
            raise Unsupported(
                f"the trimmed boundary's {len(boundary)} segment(s) name no curve this converter drew"
            )
        try:
            described = polygon_area(boundary_polygon(sketch, boundary))
        except UnsupportedGeometry as exc:
            raise Unsupported(str(exc)) from None
        matches = [i for i, p in enumerate(profiles) if frozenset(p.get("curves") or ()) == wanted]
        if len(matches) != 1:
            raise Unsupported(
                f"the trimmed boundary's {len(wanted)} curve(s) match {len(matches)} of "
                f"SolidCraft's {len(profiles)} regions, so which one it bounds is not decided; "
                f"the boundary describes {described:.3f} mm2"
            )
        index = matches[0]
        area = float(profiles[index].get("area_mm2") or 0.0)
        if not math.isclose(described, area, rel_tol=PROFILE_TOLERANCE):
            raise Unsupported(
                f"the trimmed boundary describes {described:.3f} mm2 and the region its curves "
                f"match covers {area:.3f} mm2"
            )
        warnings.append(
            f"a trimmed boundary of {len(boundary)} entity segments translated to region {index}, "
            f"{area:.3f} mm2"
        )
        picks.append(index)
    return picks, warnings


def _declared_picks(
    ctx: Context, sketch, loops: list, targets, profiles: list[dict], warnings: list[str]
) -> tuple[list[int], list[str]]:
    """Region indexes for the profiles the file declares, checked against the area it describes.

    Raises `Unsupported` when a declared profile cannot be put back together from the engine's
    regions, when the pieces found do not add up to the area the file's loops describe, or when it
    took more than one of them — and that last one is new.

    **The area check cannot verify a reassembly, because for a reassembly it is a tautology.** What
    it compares is the file's outer loop less the hole the file declared, against the root region
    (already net of every hole) plus the region filling each hole the file did not declare. Those
    two are the same set by construction, so they agree up to the chord error of `profile_area`'s
    polygons — which is why the tolerance has to be as loose as 1%. It says nothing about whether
    filling those holes was right, and filling them is the one decision the file does not make.

    Measured on `corpus-01`'s ordinal 10 (spec section 121). The file declares the annulus
    between its circle `c1` (r 14.85) and `c2` (r 9.40); the engine splits that sketch into 12
    regions and the reassembly — root plus 6 of its 7 holes — came to 415.201 mm² against the
    414.016 mm² the file's polygons describe, 0.286% apart, and passed. Extruded 4 mm it put
    **1480.254 mm³ where the original has none**, against 177.307 mm³ of the original it newly
    covered: 8.4 times more outside than in, and it was reported **`exact`**. Fusion's own band
    there is two 23.480 mm² lobes, which is what the measuring road picks — and with the body right,
    the fillet that follows places all 8 of the points the file names and is `exact` too.

    So a reassembly takes the road an untranslatable declaration already takes: the regions are
    measured against the original, and the feature is `inferred`, never `exact`. The cost in
    principle is that a reassembly that *was* right is now labelled `inferred` rather than `exact`;
    that label is still the truth about how it was decided, and the measurement is strictly more
    evidence than the area ever was. The corpus has exactly one reassembly, and it is this one.
    """
    picks: list[int] = []
    for outer, holes in targets:
        described = profile_area(loops, outer, holes)
        found = _declared_region(ctx, sketch, profiles, outer, holes)
        if found is None:
            raise Unsupported(
                f"profile {outer} of the file does not match any region of the sketch: its "
                f"{described:.3f} mm² cannot be put together from SolidCraft's {len(profiles)} regions"
            )
        pieces, area = found
        if not math.isclose(described, area, rel_tol=PROFILE_TOLERANCE):
            raise Unsupported(
                f"profile {outer} covers {described:.3f} mm² and the {len(pieces)} region(s) it "
                f"matches cover {area:.3f} mm²"
            )
        if len(pieces) > 1:
            raise Unsupported(
                f"profile {outer} had to be put back together from {len(pieces)} of SolidCraft's "
                f"{len(profiles)} regions, and the {area:.3f} mm² they come to cannot say that "
                "filling those holes was right — the file does not declare it"
            )
        picks.extend(pieces)
    return picks, warnings


def _unresolvable(loops: list, targets, regions: list[float]) -> str | None:
    """Why a profile index cannot be resolved in this sketch, or None when it can.

    A `sketch_profiles` or `sketch_regions` extrude points at its profiles by index, and the file
    records no geometry for them: the mapper turns the index into a point inside that loop and lets
    SolidCraft resolve whichever region holds it. That is only sound while the file and the engine
    agree on what the sketch's profiles are, and they need not:

    - `corpus-05`: a line crosses the outer loop without closing a loop of its own, so the
      engine splits the 195.72 mm² the file describes into 168.04 and 27.68 and a pick inside the
      loop lands in one piece of the profile. Cut 4 mm deep, that piece took 501.59 mm³ the original
      has — reported `exact`.
    - `corpus-02`: the file lists 2 loops where the engine finds 18 regions. Index 1 is the
      1024.000 mm² loop and the engine does hold a region of exactly that area, so checking the
      targeted profile alone passed both extrudes that use it — building two bodies from one region,
      10 mm and 26 mm deep, in the same place. One area agreeing inside a decomposition that does
      not agree is a coincidence, not a resolution.

    So the area of the targeted profile is the check: it has to be a region here. A fragment is short
    by far more than polygonization explains, and when nothing matches, the index cannot be resolved
    at all and the feature falls back.

    **This is not enough to make an index trustworthy, and `corpus-02` is the measured proof.**
    Requiring the whole decomposition to agree instead would catch it, and was measured too: it also
    refuses three mappings that are verified correct against the original (the public sample's first
    extrude rebuilds its 90000 mm³ exactly from a sketch whose 4 loops the engine splits into 6
    regions). Neither test separates the two cases, because the file has no region list to resolve an
    index against; what would is a measurement of the result, which §14 of the spec sets out.
    """
    for outer, holes in targets:
        wanted = profile_area(loops, outer, holes)
        if not any(math.isclose(wanted, area, rel_tol=PROFILE_TOLERANCE) for area in regions):
            return (
                f"profile {outer} covers {wanted:.3f} mm² and no region of the sketch does: the "
                f"file's {len(loops)} loops and SolidCraft's {len(regions)} regions describe this "
                "sketch differently"
            )
    return None


def _infer_regions(
    ctx: Context,
    feature: Feature,
    sketch_name: str,
    extent: dict,
    operation: str,
    targets: list[str],
) -> tuple[list[int], int, list[str]]:
    """The regions Fusion's opaque selection must have held, how many the sketch has, and warnings.

    Each operation stands in a different relation to the original, so each gets its own criterion:

    - a **new body** is everything the later features carve from, so it must cover the whole original:
      the answer is the smallest region set that still does;
    - a **cut** may only take material the original does not have, so the answer is the largest set
      that can be removed while the body still covers the original;
    - a **join** only adds, and mid-timeline the body it leaves is legitimately missing what later
      features still add, so coverage says nothing: a region belongs when the material it adds lies
      inside the original.

    Every candidate is run as the feature itself, with its own operation and targets. Each criterion
    also returns the warnings that say how far its answer is from a measurement that chose.
    """
    reversed_ = feature.extrude.reversed
    # Which reference body the file says this feature's output becomes: a proposal for the pairing
    # to confirm by measuring, never an answer on its own.
    declared = declared_reference(ctx.design, feature, ctx.oracle.bodies)
    total = len(_engine_regions(ctx, sketch_name))
    if total <= 0:
        raise Unsupported("the sketch has no region to extrude")
    if total > MAX_REGIONS:
        raise Unsupported(f"{total} regions in one sketch (limit {MAX_REGIONS})")
    # Every criterion here measures the whole set once and then each region in turn, so the search
    # is `total + 1` questions and the loop below will not start one it cannot pay for. A search
    # that cannot reach its last question cannot answer, and running it anyway spends the budget for
    # nothing: measured on `corpus-04`, 38 regions against the budget — 39 questions — burned the
    # whole of it and then refused, taking the file from 6.0 s to 66.0 s with every count unchanged.
    # This gate was written in trials from the start and only multiplied them by a floor in seconds
    # to compare them against a clock; in questions it says what it means (spec section 130).
    needed = total + 1
    if needed > ctx.oracle.questions_left():
        raise Unsupported(
            f"measuring {total} regions needs {needed} questions and the oracle has "
            f"{ctx.oracle.questions_left()} of budget left, so the search could not finish"
        )
    if operation != "new" and not targets:
        # Without named targets the trial would reach for whatever bodies it found, the imported
        # original among them.
        raise Unsupported(f"the body this {operation} works on was not rebuilt")

    # Which reference body this search measures against, fixed before the first candidate: for a cut
    # or a join the body it works on is already there to be paired, and a new body is paired by its
    # own first trial, inside it. On a single-body original both cost nothing.
    scope = _Scope(ctx.oracle, declared)
    if operation != "new":
        scope.of(targets[0])
    elif declared is not None:
        # A trial body is not evidence about which finished body this feature's output is: the first
        # candidate may be the wrong region entirely, and the pairing it fixes is then whatever that
        # region happened to touch. Measured on `corpus-02`'s `Extruir 12`, region 0 is the
        # first trial and lies in neither finished body, so the search died on its own first guess.
        # The file's answer does not depend on the candidate, and `feature.outputs` is already what
        # names the body a cut or a join works on, so where it gives one it is the scope. What checks
        # it is what follows: the criterion the chosen set has to pass, and the overlap guard after.
        scope.name = declared
    # What a shortfall is read against: the paired reference body's volume once it is known, the
    # whole original's otherwise.
    scale = ctx.oracle.volume_of(scope.name) if scope.name else ctx.oracle.volume()

    def measure(indexes: list[int]) -> tuple[float, float] | None:
        if ctx.oracle.questions_left() < 1:
            raise Unsupported(out_of_budget(ctx.oracle))
        params = {
            "sketch": sketch_name,
            "profiles": indexes,
            **extent,
            "direction": "negative" if reversed_ else "positive",
            "operation": operation,
            # The same as the apply below, so what a trial measures is the body the feature would
            # leave: a trial that splits where the apply fuses measures a different solid.
            "touching": JOIN_TOUCHING,
        }
        if operation != "new":
            params["targets"] = targets
        return _trial(ctx, params, targets if operation != "new" else [], scope)

    if operation == "new" and (joins := _joined_later(ctx, feature)):
        picks, warnings = _mostly_inside(ctx, measure, total, scope)
        why = (
            f"{len(joins)} later feature(s) join onto this body ({', '.join(joins)}), so it does not "
            "have to cover the finished one: each region was judged by what it puts inside it"
        )
        warnings = [why, *warnings]
    elif operation == "new":
        picks, warnings = _smallest_covering(measure, total, scale)
    elif operation == "cut":
        picks, warnings = _largest_cut(ctx, measure, targets[0], total, scale, scope)
    elif operation == "join" and (past := _past_cuts(ctx, feature, targets[0])) is not None:
        names, apply_cuts = past

        def measure_past(indexes: list[int]) -> tuple[float, float] | None:
            if ctx.oracle.questions_left() < 1:
                raise Unsupported(out_of_budget(ctx.oracle))
            params = {
                "sketch": sketch_name,
                "profiles": indexes,
                **extent,
                "direction": "negative" if reversed_ else "positive",
                "operation": operation,
                "touching": JOIN_TOUCHING,
                "targets": targets,
            }
            return _trial(ctx, params, targets, scope, then=apply_cuts)

        picks, warnings = _added_inside_past_cuts(
            ctx, measure, measure_past, names, apply_cuts, targets[0], total, scope
        )
    elif operation == "join":
        picks, warnings = _added_inside(ctx, measure, targets[0], total, scope)
    else:
        raise Unsupported(f"no inference criterion for a {operation} extrude")
    return picks, total, warnings + scope.note()


def _smallest_covering(measure: Measure, total: int, scale: float) -> tuple[list[int], list[str]]:
    """New body: the fewest regions whose extrude still covers the whole original.

    **This is a lower bound on Fusion's selection, not the selection itself.** Covering does not
    discriminate: on `corpus-05` all 13 regions (1489.57 mm³) and these 7 (1040.49 mm³) both
    cover the original with nothing missing, and the measured truth is the full set — 1489.57 −
    911.556 = 578 mm³ is exactly what its three later cuts remove, where the 7-region answer leaves
    only 128.8 mm³ for them. Dropping a region can only lower the leftover, so the search walks away
    from the larger true answer by construction. What it returns is the smallest profile consistent
    with the finished solid, which rebuilds a valid body but need not be the one Fusion recorded.

    So the warning returned alongside it says that, in the same voice `edges.py` uses for a tied edge
    subset: whenever a region was dropped, the full set covered the original too and so does every
    superset of the answer, and the answer is a lower bound rather than a reading that chose.
    """
    tol = noise(scale)
    keep = list(range(total))
    whole = measure(keep)
    if whole is None:
        raise Unsupported("the engine would not extrude this sketch's regions")
    if whole[0] > tol:
        raise Unsupported(f"even every region leaves {whole[0]:.3f} mm³ of the original uncovered")
    leftover = whole[1]
    for index in list(keep):
        candidate = [i for i in keep if i != index]
        if not candidate:
            continue
        measured = measure(candidate)
        if measured is None:
            continue
        if measured[0] <= tol and measured[1] <= leftover + noise(leftover):
            keep, leftover = candidate, measured[1]
    if len(keep) == total:
        return keep, []
    ambiguous = (
        f"ambiguous: coverage does not choose — every superset of these {len(keep)} regions covers "
        f"the original too, all {total} of them included; took the smallest, so this is a lower bound"
    )
    return keep, [ambiguous]


def _largest_cut(
    ctx: Context, measure: Measure, target: str, total: int, scale: float, scope: _Scope
) -> tuple[list[int], list[str]]:
    """Cut: the most regions that can be removed without the cut taking anything the original has.

    Grown from nothing rather than shrunk from everything, because cutting with more regions leaves
    less material: every region is a chance to lose part of the original, so the whole sketch is the
    worst possible starting point, not the best.

    Read **against the body as it stands**, not against full coverage. The absolute test — the body
    must still cover the whole original — assumed everything after this cut only removes material,
    and that is not how a Fusion timeline is built: on `corpus-02` `Extruir 13` cuts a body
    that four later joins still add to, so it is legitimately missing 6578 mm³ of the original and
    the absolute test refused the feature outright. What a cut must not do is take material the
    finished part has, which is the same thing said relatively: the uncovered volume may not grow.
    Where the body does already cover the original the two tests are the same measurement.

    **Largest-removable is not what Fusion selected either**, for the mirror of the reason given on
    `_smallest_covering`: every subset of the true cut also leaves the original intact, so this
    cannot tell the real profile from a smaller one, and the search settles on the most that can be
    removed rather than on what Fusion removed. The warning returned alongside the answer says so,
    and says whether the full set of regions qualified too.
    """
    if ctx.oracle.questions_left() < 1:
        raise Unsupported(out_of_budget(ctx.oracle))
    tol = noise(scale)
    bare, leftover = ctx.oracle.shortfall(target, scope.of(target))
    keep: list[int] = []
    for index in range(total):
        candidate = [*keep, index]
        measured = measure(candidate)
        if measured is None:
            continue
        if measured[0] <= bare + tol and measured[1] <= leftover + noise(leftover):
            keep, leftover = candidate, measured[1]
    if not keep:
        raise Unsupported(f"every one of the {total} regions cuts into the original")
    also = (
        f"all {total} regions can be removed"
        if len(keep) == total
        else f"the full set of {total} cannot, but every subset of these {len(keep)} can"
    )
    bare_note = f", measured against the {bare:.3f} mm³ the body was already missing" if bare > tol else ""
    ambiguous = (
        f"ambiguous: the measurement does not choose — {also}{bare_note}; took the largest, so this "
        "is an upper bound"
    )
    return keep, [ambiguous]


def _later_cuts(ctx: Context, feature: Feature) -> list[Feature]:
    """The later extrude cuts on a body this feature outputs, as the file names them."""
    mine = set(feature.outputs)
    return [
        f
        for f in ctx.design.features
        if f.ordinal > feature.ordinal
        and not f.suppressed
        and mine.intersection(f.outputs)
        and f.extrude is not None
        and f.extrude.op == "cut"
    ]


def _sketch_feature(ctx: Context, sketch: str) -> Feature | None:
    """The timeline feature that draws this sketch."""
    return next(
        (f for f in ctx.design.features if f.sketch == sketch and f.definition_kind == "sketch"), None
    )


def _past_cuts(ctx: Context, feature: Feature, target: str) -> tuple[list[str], Callable[[], None]] | None:
    """The later cuts on this join's body, applied as far as they can reach, or None.

    "As far as they can reach" is every region of each cut's sketch, to the extent the file gives
    it: the most a cut can take away, because which of its regions Fusion used is not in the file
    (they are the cuts' own opaque selections). None when any later cut cannot be built that way —
    no extent this converter reads, no sketch in the file, or a sketch in another component — so the
    caller keeps the criterion it had.

    The sketches those cuts are drawn on usually come after this join in the timeline, so they are
    drawn here, inside the trial's rollback, and the context's record of them is put back after.
    """
    cuts = _later_cuts(ctx, feature)
    if not cuts:
        return None
    from f3d2solidcraft.mappers import BY_DEFINITION, MAPPERS
    from f3d2solidcraft.mappers.components import holder_of

    holder = holder_of(ctx, feature.extrude.sketch)
    plan = []
    for cut in cuts:
        ex = cut.extrude
        if ex.sketch is None or holder_of(ctx, ex.sketch) != holder or ex.extent_kind != "one_sided":
            return None
        drawer = _sketch_feature(ctx, ex.sketch) if ex.sketch not in ctx.sketch_names else None
        if ex.sketch not in ctx.sketch_names and drawer is None:
            return None
        try:
            extent, _ = _extent(ctx, cut)
        except Unsupported:
            return None
        plan.append((cut, drawer, extent))

    def apply() -> None:
        saved = (dict(ctx.sketch_names), dict(ctx.curve_ids), set(ctx.partial_sketches))
        try:
            for cut, drawer, extent in plan:
                if drawer is not None and cut.extrude.sketch not in ctx.sketch_names:
                    mapper = MAPPERS.get(drawer.tag) or BY_DEFINITION.get(drawer.definition_kind)
                    if mapper is None:
                        raise CommandRefused("no mapper for the later cut's sketch")
                    mapper(drawer, ctx)
                ctx.engine.execute(
                    "solid.extrude",
                    {
                        "sketch": ctx.sketch_names[cut.extrude.sketch],
                        "profiles": "all",
                        **extent,
                        "direction": "negative" if cut.extrude.reversed else "positive",
                        "operation": "cut",
                        "targets": [target],
                        "name": "f3d2solidcraft trial cut",
                    },
                )
        except Unsupported as exc:
            raise CommandRefused(str(exc)) from None
        finally:
            ctx.sketch_names.clear()
            ctx.sketch_names.update(saved[0])
            ctx.curve_ids.clear()
            ctx.curve_ids.update(saved[1])
            ctx.partial_sketches.clear()
            ctx.partial_sketches.update(saved[2])

    return [c.name for c in cuts], apply


def _added_inside_past_cuts(
    ctx: Context,
    measure: Measure,
    measure_past: Measure,
    names: list[str],
    apply_cuts: Callable[[], None],
    target: str,
    total: int,
    scope: _Scope,
) -> tuple[list[int], list[str]]:
    """Join whose body a later cut works on: what goes in, against what no later cut can take out.

    `_added_inside` assumes what a join adds survives, so a region is judged by the material it puts
    outside the original. A later cut on the same body breaks that: measured on
    `corpus-06`'s `Extrude 4`, the join puts a solid block 7.5 mm deep and
    `Extrude 6` then hollows it to a ring 54.89 mm² in section, so every one of the 12 regions put
    more outside than inside (the best 316.7 against 395.8) and the feature fell back with its 7
    followers.

    What a later cut removes is not known — its own regions are an opaque selection — but how far it
    can reach is: every region of its sketch, to its extent. So each region is read twice. What it
    puts **inside** the original is read as the join leaves it, and what it puts **outside** is read
    after the later cuts have taken all they could: the overshoot no later cut can explain. A region
    belongs when the first outweighs the second. On that feature the 8 regions of the 19 x 20 mm
    rectangle put 37 to 317 mm³ in and leave 0.0 out; the other 4 put 0 to 16.3 in and leave 79 to
    898 out (spec section 150).

    Two questions per region instead of one, and two for the baseline.
    """
    if ctx.oracle.questions_left() < 2:
        raise Unsupported(out_of_budget(ctx.oracle))
    bare, before = ctx.oracle.shortfall(target, scope.of(target))
    depth = ctx.engine.undo_depth()
    try:
        with rolled_back(ctx.engine, depth):
            apply_cuts()
            _, before_past = ctx.oracle.shortfall(target, scope.of(target))
    except CommandRefused as exc:
        raise Unsupported(
            f"the later cuts ({', '.join(names)}) could not be applied to judge it: {exc}"
        ) from None
    tol = noise(before)
    keep: list[int] = []
    balance: dict[int, tuple[float, float]] = {}
    for index in range(total):
        measured = measure([index])
        if measured is None:
            continue
        past = measure_past([index])
        if past is None:
            continue
        covered, outside = bare - measured[0], past[1] - before_past
        balance[index] = (covered, outside)
        if covered > outside + tol:
            keep.append(index)
    if not balance:
        raise Unsupported(f"none of the {total} regions could be joined and cut")
    if not keep:
        best = max(balance.values(), key=lambda pair: pair[0] - pair[1])
        raise Unsupported(
            f"every one of the {total} regions leaves more material the original lacks, even after "
            f"{', '.join(names)}, than it covers of it (the best covers {best[0]:.3f} mm³ against "
            f"{best[1]:.3f} mm³ outside)"
        )
    warnings = [
        (
            f"a later cut works on this body ({', '.join(names)}), so each region was judged by what it "
            "puts inside the original against what it leaves outside after those cuts take all they can"
        )
    ]
    warnings += [
        f"region {i} overshoots the finished part by {balance[i][1]:.3f} mm³ even past the later "
        f"cuts, against {balance[i][0]:.3f} mm³ of it covered"
        for i in keep
        if balance[i][1] > tol
    ]
    return keep, warnings


def _added_inside(
    ctx: Context, measure: Measure, target: str, total: int, scope: _Scope
) -> tuple[list[int], list[str]]:
    """Join: the regions whose material is mostly material the original has.

    Measured against the body as it stands before the join, because that body is itself allowed to
    have material outside the original — something later takes it away. What is judged is only what
    this region adds, on both sides: how much more of the original the body covers afterwards, and
    how much it gained that the original lacks.

    **Not "adds nothing outside".** That absolute test assumed the only things that remove material
    are cuts, and a round or a bevel removes material too: `corpus-01` has no cut at all in
    its timeline and four fillets and chamfers after this feature, so its join legitimately
    overshoots the finished part a little, and the absolute test refused the feature — and with it
    the ten `exact` features before it, the whole file. So the balance decides. Measured there, the
    two regions joined 78.3 mm answer 4034.299 mm³ of the original covered against 15.209 mm³
    outside, and 3718.011 against 46573.208: three orders of magnitude apart, which is why this
    needs no threshold beyond "more in than out".

    Each region is judged on its own, so there is no tie to report, but a region admitted while
    overshooting says so in a warning rather than passing for a clean fit.
    """
    if ctx.oracle.questions_left() < 1:
        raise Unsupported(out_of_budget(ctx.oracle))
    bare, before = ctx.oracle.shortfall(target, scope.of(target))
    tol = noise(before)
    keep: list[int] = []
    balance: dict[int, tuple[float, float]] = {}
    for index in range(total):
        measured = measure([index])
        if measured is None:
            continue
        covered, outside = bare - measured[0], measured[1] - before
        balance[index] = (covered, outside)
        if covered > outside + tol:
            keep.append(index)
    if not balance:
        raise Unsupported(f"none of the {total} regions could be joined")
    if not keep:
        best = max(balance.values(), key=lambda pair: pair[0] - pair[1])
        raise Unsupported(
            f"every one of the {total} regions adds more material the original lacks than it "
            f"covers of it (the best covers {best[0]:.3f} mm³ against {best[1]:.3f} mm³ outside)"
        )
    overshooting = [i for i in keep if balance[i][1] > tol]
    warnings = [
        f"region {i} overshoots the finished part by {balance[i][1]:.3f} mm³ against "
        f"{balance[i][0]:.3f} mm³ of it covered: a later round or bevel has to account for it"
        for i in overshooting
    ]
    return keep, warnings


def _mostly_inside(ctx: Context, measure: Measure, total: int, scope: _Scope) -> tuple[list[int], list[str]]:
    """New body a later join adds to: the regions whose material is mostly material the original has.

    `_smallest_covering`'s premise is that a new body is everything the later features carve from,
    so it has to cover the finished one. A later join breaks that premise outright — the material
    it adds is in the finished body and never was in this one — and the search then asks for a body
    bigger than Fusion's. Measured on `corpus-02`'s `Extruir 12`, whose body two later joins
    add 600 mm³ to, coverage took the one region big enough to cover the finished body (1024 mm²,
    26624 mm³), which overlapped the body `Extruir 11` had already built by 10240 mm³: the answer
    was refused, and the 16 features behind it fell back with it.

    The sound bound in that position is the opposite one. Everything this body has survives into the
    finished one, so a region belongs when what it puts inside the original outweighs what it leaves
    outside — the same balance `_added_inside` uses, and for the same reason: a later round or bevel
    is allowed to account for a little overshoot, so "nothing outside" would be too strict a test.
    What differs is only that a new body starts from nothing, so the whole of what it builds is what
    gets judged.

    Measured on that feature, exactly one of the 18 regions passes: region 15 puts 6045.942 mm³
    inside against **0.000** outside, where the next best puts 672.915 inside against 26018.583
    outside and the rest put 76.276 inside against 151.285 to 10402.939. Its body is 6054.503 mm³
    against a finished 6654.503 — the 600 mm³ the two joins add, which is the answer being right.

    Like `_added_inside`, this is a per-region test and not a reading of Fusion's selection: were
    Fusion's set a strict subset of the regions that pass, this would overshoot it. That is why the
    feature is `inferred` and says how many regions of how many it chose.
    """
    if ctx.oracle.questions_left() < 1:
        raise Unsupported(out_of_budget(ctx.oracle))
    keep: list[int] = []
    balance: dict[int, tuple[float, float]] = {}
    for index in range(total):
        measured = measure([index])
        if measured is None or scope.name is None:
            continue
        # The reference body this search was paired with, not the whole original: `measured` comes
        # back from a comparison with that one body, so it is the only scale its numbers mean.
        whole = ctx.oracle.volume_of(scope.name)
        covered, outside = whole - measured[0], measured[1]
        balance[index] = (covered, outside)
        if covered > outside + noise(whole):
            keep.append(index)
    if not balance:
        raise Unsupported(f"none of the {total} regions could be built and measured")
    if not keep:
        best = max(balance.values(), key=lambda pair: pair[0] - pair[1])
        raise Unsupported(
            f"every one of the {total} regions leaves more material outside the original than it "
            f"puts inside it (the best puts {best[0]:.3f} mm³ in against {best[1]:.3f} mm³ out)"
        )
    return keep, [
        f"region {i} overshoots the finished body by {balance[i][1]:.3f} mm³ against "
        f"{balance[i][0]:.3f} mm³ of it filled: a later round or bevel has to account for it"
        for i in keep
        if balance[i][1] > noise(balance[i][0])
    ]


def _joined_later(ctx: Context, feature: Feature) -> list[str]:
    """The later extrudes that join onto a body this feature outputs, as the file names them.

    Only extrude joins: those are the adding features this reader types today, so a `Combine` that
    joins is not seen here and leaves the coverage premise believed as before.
    """
    mine = set(feature.outputs)
    return [
        f.name
        for f in ctx.design.features
        if f.ordinal > feature.ordinal
        and not f.suppressed
        and mine.intersection(f.outputs)
        and f.extrude is not None
        and f.extrude.op == "join"
    ]


def _measured(
    ctx: Context, feature: Feature, sketch_name: str, extent: dict, targets_named: list[str]
) -> tuple[list[int], int, list[str]]:
    """SolidCraft's own regions, chosen by measuring the result against the original solid.

    The one way to answer "which regions" that does not depend on the file and the engine agreeing
    on what the sketch's regions are. `_infer_regions` has a criterion per operation; what the
    caller adds is why it is being asked.
    """
    try:
        return _infer_regions(
            ctx, feature, sketch_name, extent, _OPERATIONS[feature.extrude.op], targets_named
        )
    except OracleUnavailable as exc:
        raise Unsupported(f"the original solid could not be measured: {exc}") from None


def _join_reading(ctx: Context, feature: Feature, target: str) -> tuple[str, tuple[float, float]] | None:
    """The reference body paired with `target` and what `target` is missing of it, before a join.

    Read before the extrude so that what the join adds can be judged on its own, the way
    `_added_inside` judges a trial. `None` means "nothing to judge it against", never "it is
    fine": no oracle, no budget left, or a pairing or a comparison the oracle refuses. Then the
    file's index stands unchecked, exactly as it did before this guard.

    On a multi-body original the file has to name the body (§54) or the guard abstains. Finding the
    pairing by survey is not its job — it is checking a claim the file makes, and with no claim there
    is nothing to check. Measured on `test weird box`, whose 5 declared bodies against the 3 its STEP
    carries make `declared_reference` say nothing: surveying instead cost that file 0.464 → 3.095 s
    and 86.5 → 1395.8 MB, on a design where every index is right and nothing else asks the oracle
    anything at all.
    """
    if ctx.oracle is None or ctx.oracle.questions_left() < 1:
        return None
    declared = declared_reference(ctx.design, feature, ctx.oracle.bodies)
    if declared is None and len(ctx.oracle.bodies) > 1:
        return None
    try:
        reference = ctx.oracle.standing_for(target, declared)
        return reference, ctx.oracle.shortfall(target, reference)
    except OracleUnavailable:
        return None


def _join_misplaced(ctx: Context, target: str, reading: tuple[str, tuple[float, float]]) -> str | None:
    """Why a join's own material says its profile index is wrong, or None when it does not.

    A join only ever adds material, and the reference body it adds to is the finished one. So if
    **none** of what it added is material the finished body has, the join put its material where the
    finished body has nothing at all, and it cannot be a step of it — the same shape as the
    invariant `shortfall` carries for a candidate missing part of the reference.

    **Not `_added_inside`'s balance**, which is the criterion for a trial on the measured path, and
    it is too fine a knife here. Every declared join the corpus puts to this test, as
    (covered, outside) in mm³ — how much more of the paired reference body the target covers after
    the join against how much it gained that the reference lacks:

    - `corpus-01` `Extrude 2` (350.999, 0.000), `Extrude 3` (4094.970, 15.209) and
      `Extrude 6` (200.212, 11.197)
    - `corpus-03` `Extrude 10` (21.408, -0.064) and `Extrude 12` (21.115, 0.000)
    - `corpus-02` `Extruir 17` (615.307, -0.000), `Extruir 18` (482.489, -0.000) and
      `Extruir 19` (**4.392, 1545.970**)
    - `corpus-04` `Extruir 2` (122.163, 0.000) and `Extruir 3` (**420.445, 424.067**)
    - `test weird box` `Extrude 2`: the pairing is refused, so nothing is read and the index stands

    The balance condemns two of those ten, and `corpus-04`'s `Extruir 3` is why it cannot be the
    criterion: a near tie, 420.445 against 424.067, **3.6 mm³ apart on 424**, and verified correct —
    measured, routing it to the measurement cost that file three `exact` features, because the
    measured path then failed and condemned the two features behind it. A criterion decided by 3.6
    mm³ out of 424 is not a criterion. `Extruir 19` is the other, and nothing says it is wrong; it is
    recorded here as the near miss to watch rather than acted on.

    **This list used to carry a ninth entry that was false.** `corpus-01`'s `Extrude 5` was listed at
    (177.307, 1480.254) with the note that it "is also right" — the overshoot `_added_inside`
    records, on a file with no cut in its timeline. It was not right: those 1480.254 mm³ are a band
    at the wrong radius, and the feature was reported `exact` on an area check that for a reassembly
    is a tautology. Section 121 has the measurement, and that feature now takes the measured road,
    so it never reaches this test. Its own argument stands without it, and `corpus-04` was always
    the stronger half of it.

    **Every join on the declared path is asked, including a declared-loop one.** It used to be only
    `sketch_profiles`, the kind whose profile is a bare index with nothing in the file to check it
    against (§14), on the ground that a `sketch_regions` profile names its loops and `_declared_picks`
    proves the area adds up, so there was "no index to be wrong". Section 121 took that apart: curve
    ids prove the region is the one *that loop* bounds, and nothing proves the file named the right
    loop — a wrong loop index matches the wrong region by curve id and its own area then checks out.
    Lifting the restriction is free, measured: no `sketch_regions` join in the corpus reaches this
    test at all, and over the ten that do, this criterion condemns none.
    """
    reference, before = reading
    whole = ctx.oracle.volume_of(reference)
    if before[0] <= noise(whole):
        # The target already covered the whole reference body before this join, so there was nothing
        # left for it to cover and "covers nothing" is true of any join here, right or wrong. The
        # test abstains rather than condemn an index on a reading that cannot distinguish.
        return None
    try:
        after = ctx.oracle.shortfall(target, reference)
    except OracleUnavailable:
        return None  # the reading that would condemn the index is the one that failed
    covered, gained = before[0] - after[0], after[1] - before[1]
    if covered > noise(whole):
        return None
    return (
        f"this join covers {covered:.3f} mm³ more of the original — none of it — while adding "
        f"{gained:.3f} mm³ the original lacks, so the profile index is not the region Fusion used"
    )


def _extrude_checked(
    ctx: Context, params: dict, operation: str, overlap: bool = True
) -> tuple[dict, str | None]:
    """Run the extrude, and say whether the body it built proves the profile index was wrong.

    A new body sitting inside one the replay has already built is the one sign there is that a bare
    index resolved to the wrong region, since the file carries no region list to check an index
    against. It is a **trigger to measure**, not proof: measured on `corpus-03`'s original, the
    finished design holds four overlapping pairs of its own, two of them whole — `CLICKER` contains
    all 18864.063 mm³ of `corpus-03 (4)`, and `corpus-03 (2)` contains all
    158547.009 mm³ of `(3)`. So neither overlap nor containment distinguishes a wrong index from
    geometry Fusion really has, and what proved the index wrong on `corpus-02` was the
    measurement this trigger leads to, never the overlap itself (spec section 95).

    `overlap=False` therefore turns the trigger off for the call that runs the regions the
    measurement already chose against the original: those regions carry the strongest evidence
    available, and vetoing them with the trigger that sent us to measure throws a measured answer
    away for a heuristic. Measured on `corpus-03` under cadmpeg 0.6.8, where that veto cost
    `Extrude 7`, `Extrude 8` and `Extrude 9` — the new body overlaps the one `Extrude 1` builds by
    3847.588 mm³, 65.9% of its own 5839.975, and `Extrude 1` is the feature 0.6.8 newly rebuilds.

    The reason is **returned, not raised**: it is evidence about the index, not the end of the
    feature. The caller still has the measurement to ask.
    """
    # The bodies a new one must not overlap: everything the replay has built, and never the
    # reference — a new body is supposed to sit inside the original.
    reference = set(ctx.oracle.bodies) if ctx.oracle is not None else set()
    standing = (
        # Names only, so this asks `inspect_design` and not `measure`: on a big assembly the
        # all-bodies measure does not answer at all (spec section 98).
        [name for name in ctx.engine.body_names() if name not in reference]
        if operation == "new" and overlap
        else []
    )
    out = ctx.engine.execute("solid.extrude", params)
    created_bodies = list(out.get("created_bodies") or [])
    # Only the bodies that have a volume. The engine reports one for a closed, consistently
    # oriented body and `None` for an open one, so reading every body's straight **crashed** this
    # mapper: measured on a 74.8 MB sheet metal design of the user's, `corpus-12.f3z`,
    # six extrudes died with `internal mapper error: TypeError: float() argument must be a string
    # or a real number, not 'NoneType'` — the same defect section 89 fixed in `same_body`, in a
    # second place (spec section 93). What this dict is for is the scale `noise` reads, and an open
    # body has no volume to scale by: `built.get` already falls back to the overlap itself.
    built = (
        {
            b["name"]: float(b["volume_mm3"])
            for b in ctx.engine.body_readings(created_bodies)
            if b.get("volume_mm3") is not None
        }
        if standing and created_bodies
        else {}
    )
    for created in created_bodies:
        shared = _shared_with(ctx, created, standing)
        if shared is not None and shared[1] > noise(built.get(created, shared[1])):
            return out, (
                f"this new body overlaps {shared[0]} by {shared[1]:.3f} mm³, which is how a bare "
                "index resolving to the wrong region looks; the original holds overlapping bodies "
                "of its own, so this is a reason to measure the regions and not a verdict on them"
            )
    return out, None


@register("Extrude")
@definition("extrude")
def map_extrude(feature: Feature, ctx: Context) -> list[str] | tuple[str, list[str]]:
    ex = feature.extrude
    # The extent and where it stops come first, before the profile. Both are read straight off the
    # file and neither depends on the oracle, while a profile this converter cannot translate can
    # still be measured — so a feature that is out of reach for its termination is out of reach
    # whatever its profile turns out to be, and saying "profile kind sketch_selection" for it sent
    # the reader after the half that was salvageable. All 4 `to_face` extrudes in the corpus have an
    # opaque profile, and all 4 used to be refused by it (spec section 104).
    if ex is None:
        raise Unsupported("extrude profile kind missing")
    if ex.extent_kind != "one_sided":
        raise Unsupported(f"extrude extent {ex.extent_kind}")
    if ex.termination not in _TERMINATIONS:
        raise Unsupported(f"extrude termination {ex.termination}")
    if ex.termination == "to_face":
        # Read here, with the other termination checks and before the profile, for the same reason
        # they are: a `to_face` whose face the file gives no plane for is out of reach whatever its
        # profile turns out to be (spec sections 104 and 150).
        _to_face_distance(ctx, feature)
    if ex.termination == "blind" and ex.length_mm is None:
        raise Unsupported("the file gives this extrude a blind extent and no length to build it to")
    supported = ex.sketch is not None and (
        ex.profile_kind == "sketch_profiles"
        # A `sketch_regions` extrude is supported when the file names its loops, and also when it
        # describes a `trimmed` region instead: that one names no loop, and is translated by the
        # entity segments it does name, with the measured path still behind it as the backstop.
        or (ex.profile_kind == "sketch_regions" and (ex.regions or ex.trimmed_regions))
        or (ex.profile_kind == "sketch_selection" and ctx.oracle is not None)
        # The whole sketch as the profile, which `solid.extrude` takes as `profiles: "all"`.
        or ex.profile_kind == "sketch"
    )
    if not supported:
        raise Unsupported(f"extrude profile kind {ex.profile_kind}")
    if ex.op not in _OPERATIONS:
        raise Unsupported(f"extrude operation {ex.op}")
    sketch_name = ctx.sketch_names.get(ex.sketch)
    if sketch_name is None:
        raise Unsupported("its sketch was not rebuilt")
    if ex.sketch in ctx.partial_sketches:
        # The sketch is in the design but short an entity this converter cannot draw, so a region of
        # it is not the region Fusion had. Building a body from it would be a body missing whatever
        # that entity contributed — an engraving, say — reported as a rebuild (spec section 87).
        raise Unsupported("its sketch is missing an entity this converter cannot draw")
    extent, warnings = _extent(ctx, feature)
    # While the reference component is in the design, a join or cut must name its bodies or it
    # would also swallow the reference. The trials need the same targets as the real extrude.
    targets_named = [ctx.body_names[o] for o in feature.outputs if o in ctx.body_names]
    inferred = False
    if ex.profile_kind == "sketch" and len(_engine_profiles(ctx, sketch_name)) <= 1:
        # cadmpeg types some extrudes with the sketch itself as the profile: no index, no loop list.
        # Where the sketch holds a single region that is unambiguous — `solid.extrude` takes it as
        # `profiles: "all"`, there is nothing to resolve, and it is `exact`. It is also why this
        # case is not asked the join reading below: that guard is for an index that may point at
        # the wrong region, and the only region cannot.
        picks = "all"
    elif ex.profile_kind == "sketch":
        # With several regions, "the whole sketch" is not what Fusion built. Spec section 95 read
        # this kind as every region and called it `exact`; measured on
        # `corpus-08`, ordinal 5 cuts a sketch of 15 regions 8.5 mm deep, and
        # taking all of them cut the base plate itself (3504.6 mm², 33740 mm³ in one feature) — the
        # body went from 40087 to 6348 mm³ against an original of 44288, and seven more `exact`
        # cuts were built on it. Nothing in the file says which regions the cut used, so they are
        # measured against the original, as an opaque selection is: `inferred`, never `exact`,
        # and refused without an oracle (spec section 149).
        if ctx.oracle is None:
            raise Unsupported(
                f"the file gives the whole sketch as this extrude's profile, and the sketch holds "
                f"{len(_engine_profiles(ctx, sketch_name))} regions, so which of them Fusion used "
                "is not in the file and there is no original to measure them against"
            )
        picks, total, inferred_warnings = _measured(ctx, feature, sketch_name, extent, targets_named)
        warnings.append(
            f"the file gives the whole sketch as the profile and the sketch holds {total} regions, so "
            f"{len(picks)} of them were measured against the original"
        )
        warnings.extend(inferred_warnings)
        inferred = True
    elif ex.profile_kind == "sketch_selection":
        # The polygons are deliberately not built here: the inference works on SolidCraft's own
        # regions, by index, and never needs a polygon. Building them for every extrude meant a
        # selection extrude in a sketch the polygonizer cannot read (a nurbs curve, as in
        # `mango para lima` and `soporte controles`) was refused before the oracle was ever asked.
        picks, total, inferred_warnings = _measured(ctx, feature, sketch_name, extent, targets_named)
        warnings.append(f"{len(picks)} of {total} sketch regions inferred from the original solid")
        warnings.extend(inferred_warnings)
        inferred = True
    else:
        sketch = ctx.design.sketches[ex.sketch]
        try:
            loops = [profile_polygon(sketch, i) for i in range(len(sketch.profiles))]
        except UnsupportedGeometry as exc:
            raise Unsupported(str(exc)) from None
        targets = [(i, ()) for i in ex.profiles] if ex.profile_kind == "sketch_profiles" else ex.regions
        if any(outer >= len(loops) or any(h >= len(loops) for h in holes) for outer, holes in targets):
            raise Unsupported("the chosen profiles are not in the sketch")
        profiles = _engine_profiles(ctx, sketch_name)
        if ex.profile_kind == "sketch_regions":
            # The file declares these profiles — as loops, or as a trimmed boundary of entity
            # segments — so they are translated rather than guessed, by curve id either way.
            try:
                if not targets:
                    if ex.trimmed_regions != ex.region_count:
                        raise Unsupported(
                            f"the file declares {ex.region_count} region(s) for this extrude and "
                            f"only {ex.trimmed_regions} of them as a trimmed boundary, so "
                            "translating the boundaries would build part of the profile"
                        )
                    picks, warnings = _trimmed_picks(ctx, sketch, ex.trimmed_boundaries, profiles, warnings)
                else:
                    picks, warnings = _declared_picks(ctx, sketch, loops, targets, profiles, warnings)
            except Unsupported as untranslatable:
                # The declared loops do not come back together from the engine's regions. That is
                # the same mismatch the index path hits — the engine decomposes the sketch
                # differently from the way the file lists its loops — and that path answers it by
                # measuring rather than by refusing. Refusing here and measuring there was our own
                # inconsistency, not a property of either kind: a region that cannot be translated
                # is exactly as unresolved as an index that cannot, and the measurement is what
                # either one has left. `inferred`, never `exact`, for the same reason.
                if ctx.oracle is None:
                    raise
                try:
                    picks, total, inferred_warnings = _measured(
                        ctx, feature, sketch_name, extent, targets_named
                    )
                except Unsupported as exc:
                    raise Unsupported(
                        f"{untranslatable}; measuring the regions instead did not finish: {exc}",
                        bodies=exc.bodies,
                    ) from None
                warnings.append(
                    f"the loops the file declares could not be translated ({untranslatable}), so "
                    f"{len(picks)} of {total} regions were measured against the original instead"
                )
                warnings.extend(inferred_warnings)
                inferred = True
        else:
            unresolved = _unresolvable(loops, targets, [float(p.get("area_mm2") or 0.0) for p in profiles])
            picks = None if unresolved else _picks_for(loops, targets)
            if picks is None:
                unresolved = unresolved or "no interior point for the chosen profiles"
                # The index cannot be turned into a region, and no structural test can fix that:
                # the file carries no region list to resolve an index against (§14). What answers
                # it is the measurement §15 already runs on the `exact` path's result — so ask the
                # same question the opaque selection groups are asked, rather than refuse.
                #
                # Measured, this is the corpus's single largest own-cause blocker: the same
                # mismatch stops 5 extrudes in 4 files, among them the only own-cause failure left
                # in `corpus-05` and in `corpus-04`, the two cleanest designs. The engine
                # decomposes those sketches differently from the way the file lists its loops —
                # 4 loops against 8 regions, 19 against 38, 1 against 3 — so the targeted profile's
                # area is simply not any one region's.
                #
                # It is `inferred`, never `exact`: the regions came from measuring the result, not
                # from the index the file gave. Without an oracle there is nothing to measure
                # against and the refusal stands as before.
                if ctx.oracle is None:
                    raise Unsupported(unresolved)
                try:
                    picks, total, inferred_warnings = _measured(
                        ctx, feature, sketch_name, extent, targets_named
                    )
                except Unsupported as exc:
                    # Keep why the index was unusable in the refusal. Measured on `corpus-04`,
                    # whose sketch holds 38 regions: the search runs out of the oracle's budget and
                    # says so, and on its own that reads as a timing problem rather than as the
                    # mismatch it started from. Both halves or neither.
                    raise Unsupported(
                        f"{unresolved}; measuring the regions instead did not finish: {exc}",
                        bodies=exc.bodies,
                    ) from None
                warnings.append(
                    f"the index the file gives could not be resolved ({unresolved}), so "
                    f"{len(picks)} of {total} regions were measured against the original instead"
                )
                warnings.extend(inferred_warnings)
                inferred = True
    params = {
        "sketch": sketch_name,
        "profiles": picks,
        **extent,
        "direction": "negative" if ex.reversed else "positive",
        "operation": _OPERATIONS[ex.op],
        "name": feature.name,
        "touching": JOIN_TOUCHING,
    }
    if _OPERATIONS[ex.op] != "new" and targets_named:
        params["targets"] = targets_named
    if _OPERATIONS[ex.op] != "new" and not targets_named:
        raise no_target(ctx, _OPERATIONS[ex.op])
    depth = ctx.engine.undo_depth()
    # A join's target as it stands before it, so what this feature adds can be read on its own.
    # Only on the declared path: on the measured one `_added_inside` already judged every region by
    # what it puts inside the finished body, so asking again would only re-measure its own answer.
    # Asked of a declared-loop profile too, which it used to skip — `_join_misplaced` has what §121
    # measured about why "the loops are named, so the index cannot be wrong" was never the whole
    # claim.
    reading = (
        _join_reading(ctx, feature, targets_named[0])
        if _OPERATIONS[ex.op] == "join" and targets_named and not inferred
        else None
    )
    # `overlap=not inferred`, for the same reason the second call below passes `overlap=False`: when
    # the picks already came from `_measured` — a `sketch_selection`, or a declared-loop or index
    # translation that did not resolve — they were chosen by what each region puts inside the
    # finished body, and the trigger would answer that measurement with a heuristic. Measured on
    # `corpus-02` under cadmpeg 0.6.8, where `Extruir 12` arrives on the measured path and the
    # trigger ended it: "this new body overlaps Body1 by 2328.655 mm³" with no second answer to try,
    # which took `Extruir 15` and `Extruir 16` down behind it (spec section 95).
    out, wrong = _extrude_checked(ctx, params, _OPERATIONS[ex.op], overlap=not inferred)
    if wrong is None and reading is not None:
        wrong = _join_misplaced(ctx, targets_named[0], reading)
    if wrong is not None:
        # One of the two guards has just proved this index is not the region Fusion used: a new
        # body sharing material with one the replay already built, or a join that put its material
        # where the finished part has none. That is the same
        # question §50 answers for an index that does not resolve at all, and it has the same
        # answer: undo, and let the measurement say which regions, instead of losing the feature to
        # an index already shown to be wrong. An area that matches a region's by coincidence is
        # exactly how a wrong index gets this far — §14 measured that on `corpus-02`, where
        # this refusal is the corpus's largest own-cause blocker with 16 features cascading off it.
        #
        # It is `inferred`, never `exact`: the regions came from measuring the result. Without an
        # oracle there is nothing to measure against and the refusal stands as before, and a feature
        # already on the measured path has no second answer to try.
        if inferred or ctx.oracle is None:
            raise Unsupported(wrong)
        roll_back(ctx.engine, depth)
        try:
            picks, total, measured_warnings = _measured(ctx, feature, sketch_name, extent, targets_named)
        except Unsupported as exc:
            # Keep both halves, as the unresolvable-index path does: on its own, the measurement's
            # reason reads as a problem of its own rather than as the wrong index it started from.
            raise Unsupported(
                f"{wrong}; measuring the regions instead did not finish: {exc}", bodies=exc.bodies
            ) from None
        warnings.append(
            f"{wrong}, so {len(picks)} of {total} regions were measured against the original instead"
        )
        warnings.extend(measured_warnings)
        inferred = True
        params["profiles"] = picks
        # No overlap trigger this time: `_measured` picked these regions by what each one puts
        # inside the finished body, so asking the trigger again would answer a measurement with a
        # heuristic. `_join_misplaced` stays — that one is itself a measurement against the
        # original, and it is the half that can still say these regions are wrong.
        out, wrong = _extrude_checked(ctx, params, _OPERATIONS[ex.op], overlap=False)
        if wrong is None and reading is not None:
            wrong = _join_misplaced(ctx, targets_named[0], reading)
        if wrong is not None:
            raise Unsupported(f"the regions the measurement chose are no better: {wrong}")
    name_outputs(feature, ctx, out, warnings)
    return ("inferred", warnings) if inferred else warnings
