"""Read cadmpeg's CADIR dump of a Fusion design."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from f3d2solidcraft.model import (
    BaseFlange,
    Body,
    BodyOp,
    Combine,
    Component,
    EdgeOp,
    Extrude,
    FaceMotion,
    Feature,
    Frame,
    Loft,
    LoftSection,
    Occurrence,
    Pattern,
    ProfileEdge,
    Revolve,
    Shell,
    Sketch,
    SketchConstraint,
    SketchEntity,
    TrimmedSegment,
)
from f3d2solidcraft.reader.locale import SKETCH_LABELS
from f3d2solidcraft.tools import run_tool


@dataclass
class CadirParts:
    sketches: dict[str, Sketch]
    features: list[Feature]
    bodies: list[Body]
    components: dict[str, Component]
    occurrences: list[Occurrence]
    # Records of the features array that are not timeline features, counted so the conversion can
    # say so instead of the reader deciding in silence (spec section 91).
    not_features: tuple[str, ...] = ()


def dump_cadir(cadmpeg: Path, f3d: Path, out: Path) -> Path:
    run_tool([str(cadmpeg), "dump", str(f3d), "-o", str(out)], 600)
    return out


def _vec(d: dict) -> tuple[float, float, float]:
    return (float(d["x"]), float(d["y"]), float(d["z"]))


def _frame_of(frame: object) -> Frame | None:
    """A resolved frame as the model holds it, or None when it is absent or malformed."""
    if not isinstance(frame, dict):
        return None
    try:
        return Frame(_vec(frame["origin"]), _vec(frame["normal"]), _vec(frame["u_axis"]))
    except (KeyError, TypeError, ValueError):
        return None


def _frame(sketch: dict) -> Frame | None:
    placement = sketch.get("placement") or {}
    frame = placement.get("frame")
    if placement.get("kind") != "resolved" or not frame:
        return None
    return _frame_of(frame)


def _trimmed_regions(value: dict) -> list[dict]:
    """The regions this extrude describes as `trimmed` rather than as loop indexes.

    cadmpeg 0.6.8 describes a region it could not reduce to the sketch's own loops as its outer
    boundary: a list of entity segments, each with a parameter range and an optional `reversed`.
    Measured over the corpus, there is exactly one — `test weird box`'s `Extrude 1` — and its five
    entities match no loop of the sketch, being parts of two of them: a 100x15 rectangle whose
    bottom edge a circle cuts a 0.915 mm chord off (spec section 112).
    """
    regions = value.get("regions")
    if not isinstance(regions, list):
        return []
    return [r for r in regions if isinstance(r, dict) and r.get("region") == "trimmed"]


def _boundary(region: dict) -> tuple[TrimmedSegment, ...]:
    """One trimmed region's boundary as segments, or empty when the file's shape is not readable.

    Empty rather than raising, and empty rather than partial: a boundary short one segment is not
    the region Fusion had, and half of one would translate to a smaller area that the mapper's own
    area check would then have to catch. The mapper refuses on an empty boundary and says so.
    """
    segments = region.get("outer_boundary")
    if not isinstance(segments, list) or not segments:
        return ()
    out = []
    for s in segments:
        if not isinstance(s, dict):
            return ()
        entity, span = s.get("entity"), s.get("parameter_range")
        if not isinstance(entity, str) or not isinstance(span, list) or len(span) != 2:
            return ()
        start, end = _number(span[0]), _number(span[1])
        if start is None or end is None:
            return ()
        out.append(TrimmedSegment(entity, start, end, bool(s.get("reversed"))))
    return tuple(out)


def _regions(value: dict) -> tuple[tuple[int, tuple[int, ...]], ...]:
    """Loop regions as (outer, holes); any other region kind leaves none, so the extrude stays unsupported."""
    regions = value.get("regions") or []
    if (
        not regions
        or not isinstance(regions, list)
        or any(not isinstance(r, dict) or r.get("region") != "loops" for r in regions)
    ):
        return ()
    parsed = []
    for r in regions:
        outer, holes = r.get("outer"), r.get("holes", [])
        if not isinstance(holes, list) or not all(
            isinstance(i, int) and not isinstance(i, bool) and i >= 0 for i in [outer, *holes]
        ):
            return ()  # malformed: the mapper reports it unsupported instead of the reader failing
        parsed.append((outer, tuple(holes)))
    return tuple(parsed)


def _number(value: object) -> float | None:
    """A finite float, or None when the CADIR holds anything else, so the feature stays unmapped."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _selection_count(value: dict, groups: dict[str, dict]) -> int:
    """How many profiles the opaque groups picked; 0 whenever the CADIR does not say it plainly."""
    selections = value.get("selections")
    if not isinstance(selections, list):
        return 0
    total = 0
    for group_id in selections:
        if isinstance(group_id, bool) or not isinstance(group_id, (str, int)):
            return 0
        group = groups.get(group_id)
        members = group.get("members") if isinstance(group, dict) else None
        if not isinstance(members, list):
            return 0  # an unknown or malformed group: we cannot say how many profiles it picked
        total += len(members)
    return total


def _edge_points(model: dict) -> dict[str, dict[str, tuple[float, float, float]]]:
    """Per kernel history state, a point inside each edge that state gives geometry for.

    IR 10's `feature_input_topologies` carries an `edge_geometry` per state, and only for the edges
    the feature really used: measured on `corpus-01`, `Fillet 1` names 8 edges, its state
    holds 101, and the 8 are the ones with geometry. The interior point is what `solid.fillet` and
    `solid.chamfer` take, so this is the whole translation (spec section 107).
    """
    found: dict[str, dict[str, tuple[float, float, float]]] = {}
    for state in model.get("feature_input_topologies", []):
        points = {}
        for edge in state.get("edge_geometry") or []:
            point = _vec_of(edge.get("interior"))
            if isinstance(edge.get("edge"), str) and point is not None:
                points[edge["edge"]] = point
        if points:
            found[state["id"]] = points
    return found


def _face_boundaries(model: dict) -> dict[str, dict[str, tuple[tuple[float, float, float], ...]]]:
    """Per kernel history state, the interior points of the edges that bound each face it names.

    The same `edge_geometry` `_edge_points` reads, indexed the other way round: each entry lists the
    faces the edge belongs to, so collecting by face gives that face's boundary. Measured on
    `Caja parametrica v2`, whose two `Shell` features name one face each and get 8 edges apiece, all
    with geometry (spec section 142).

    The interior point is used rather than the ends because it is the one the file guarantees is on
    the edge and not at a vertex, which is the same reason the edge mappers take it.
    """
    found: dict[str, dict[str, tuple[tuple[float, float, float], ...]]] = {}
    for state in model.get("feature_input_topologies", []):
        by_face: dict[str, list[tuple[float, float, float]]] = {}
        for edge in state.get("edge_geometry") or []:
            point = _vec_of(edge.get("interior"))
            if point is None:
                continue
            for face in edge.get("faces") or []:
                if isinstance(face, str):
                    by_face.setdefault(face, []).append(point)
        if by_face:
            found[state["id"]] = {k: tuple(v) for k, v in by_face.items()}
    return found


def _shell(
    defn: dict, boundaries: dict[str, dict[str, tuple[tuple[float, float, float], ...]]] | None = None
) -> Shell | None:
    """The Shell recipe, or None when the record is not one this converter can read.

    The thickness and the direction are read straight; the removed face is read only when the file
    names it through the kernel history **and** gives geometry for its edges. A `native` operand
    group is refused here rather than carried as a name nothing can resolve — that is the wall
    `delete_face` is behind (spec section 141).
    """
    thickness = _number(defn.get("thickness"))
    if thickness is None:
        return None
    removed = _dict(defn.get("removed_faces"))
    points: tuple[tuple[float, float, float], ...] = ()
    if removed.get("kind") == "historical":
        value = _dict(removed.get("value"))
        faces = value.get("faces")
        known = (boundaries or {}).get(str(value.get("state") or ""), {})
        # One face only: two removed faces are two planes and one answer, so the reader says nothing
        # rather than picking (the same rule `_state_bodies` follows for a feature named twice).
        if isinstance(faces, list) and len(faces) == 1 and isinstance(faces[0], str):
            points = known.get(faces[0], ())
    return Shell(thickness, bool(defn.get("outward")), points)


def _state_bodies(model: dict) -> dict[str, int]:
    """Per feature, how many bodies the kernel history state it took as input held.

    `feature_input_topologies` carries one entry per state, and `input_of` names the feature it is
    the input of. The `bodies` list is the bodies in scope for that feature — measured on
    `Caja parametrica v2`, from 1 for a fillet on a single body to 5 for the Combine at ordinal 46.

    A feature named by two entries is dropped rather than resolved to one of them: there would be
    two answers to a question that has one, and the arithmetic that uses this must then refuse
    instead of picking (spec section 109).
    """
    counts: dict[str, int] = {}
    twice: set[str] = set()
    for state in model.get("feature_input_topologies", []):
        who = state.get("input_of")
        if not isinstance(who, str):
            continue
        bodies = state.get("bodies")
        if not isinstance(bodies, list):
            continue
        if who in counts and counts[who] != len(bodies):
            twice.add(who)
        counts[who] = len(bodies)
    for who in twice:
        counts.pop(who, None)
    return counts


def _combine(defn: dict, state_bodies: int | None) -> Combine | None:
    """The Combine recipe, or None when the record is not one this converter can read at all."""
    operands = _dict(defn.get("operands"))
    target, tools = _dict(operands.get("target")), _dict(operands.get("tools"))
    op = defn.get("op")
    if not isinstance(op, str) or not target or not tools:
        return None
    value = target.get("value")
    members = _dict(value).get("members") if isinstance(value, dict) else None
    bodies: tuple[str, ...] = ()
    if isinstance(members, list):
        # A `resolved_set` member carries the body id beside the native record it came from.
        bodies = tuple(b for m in members if isinstance(b := _dict(m).get("body"), str))
        count = len(members)
    elif isinstance(_dict(value).get("bodies"), list):
        # `historical`: ids of the feature's own state, which nothing here maps to a body. The
        # count is still the file's own statement of how many the target is.
        count = len(_dict(value)["bodies"])
    else:
        count = 1 if value else 0
    tool_value = tools.get("value")
    tool_count = len(tool_value) if isinstance(tool_value, list) else (1 if tool_value else 0)
    if count == 0 or tool_count == 0:
        return None
    kind = target.get("kind")
    return Combine(
        op=op,
        target_kind=kind if isinstance(kind, str) else "",
        target_bodies=bodies if len(bodies) == count else (),
        target_count=count,
        tool_count=tool_count,
        state_bodies=state_bodies,
    )


def _edge_op(
    defn: dict, points: dict[str, dict[str, tuple[float, float, float]]] | None = None
) -> EdgeOp | None:
    """The Fillet/Chamfer spec, or None when it is absent or malformed: the feature stays unmapped."""
    kind = defn.get("definition")
    if kind not in ("fillet", "chamfer"):
        return None
    groups = defn.get("groups")
    if not isinstance(groups, list) or len(groups) != 1 or not isinstance(groups[0], dict):
        return None  # several groups means several sizes at once: not mapped in M1
    group = groups[0]
    spec = _dict(group.get("spec"))
    edges = _dict(group.get("edges"))
    count = None
    edge_points: tuple[tuple[float, float, float], ...] = ()
    if edges.get("kind") == "historical":
        value = _dict(edges.get("value"))
        ids = value.get("edges")
        count = len(ids) if isinstance(ids, list) and ids else None
        # Only when the file gives a point for **every** edge of the group. A partial reading would
        # build a smaller round than Fusion's and report it as read from the file: measured on
        # `corpus-02`, whose `Empalme 1` is the corpus's one `historical_partial` and the only
        # fillet that reaches the mapper at all (spec section 106).
        known = (points or {}).get(value.get("state"), {})
        if count and all(isinstance(i, str) and i in known for i in ids):
            edge_points = tuple(known[i] for i in ids)
    if kind == "fillet":
        # cadmpeg 0.6.3 puts the fillet radius on the group itself; read a nested spec too, in case
        # another version moves it there (chamfer always uses the spec).
        radius = _dict(group.get("radius")) or _dict(spec.get("radius"))
        if radius.get("kind") != "constant":
            return None
        value = _number(radius.get("radius"))
        return EdgeOp("fillet", value, None, count, edge_points) if value is not None else None
    if spec.get("kind") == "distance":
        distance = _number(spec.get("distance"))
        return EdgeOp("chamfer", distance, None, count, edge_points) if distance is not None else None
    if spec.get("kind") == "two_distances":
        # cadmpeg 0.6.7 and earlier: despite the name, "second" is an angle in radians.
        first, second = _number(spec.get("first")), _number(spec.get("second"))
        if first is None or second is None:
            return None
        return EdgeOp("chamfer", first, second, count)
    if spec.get("kind") == "distance_angle":
        # cadmpeg 0.6.8 names the same spec for what it is: `distance` and `angle`, no longer a
        # "second distance" that was never a distance. The value moved with the name — `mango para
        # lima`'s two chamfers read 0.698 rad (40°) on 0.6.7 and 0.0698 rad (4°) here, a factor of
        # ten — and which one is the design's is not something the file settles, so it is not
        # settled here: `edges.py` measures the result against the original and refuses an angle
        # that does not fit, which is the only answer worth having (spec section 83).
        distance, angle = _number(spec.get("distance")), _number(spec.get("angle"))
        if distance is None or angle is None:
            return None
        return EdgeOp("chamfer", distance, angle, count)
    return None


def _state_faces(model: dict) -> dict[str, int]:
    """Per feature, how many faces the kernel history state it took as input held.

    The same records `_state_bodies` reads, counted by faces instead. These are the faces of the
    bodies in scope and **not** the faces the feature touched: measured, an `OffsetFaces` that moves
    a few of them has 15 to 206 listed, with no geometry on any (spec section 118).
    """
    counts: dict[str, int] = {}
    twice: set[str] = set()
    for state in model.get("feature_input_topologies", []):
        who, faces = state.get("input_of"), state.get("faces")
        if not isinstance(who, str) or not isinstance(faces, list):
            continue
        if who in counts and counts[who] != len(faces):
            twice.add(who)
        counts[who] = len(faces)
    return {who: n for who, n in counts.items() if who not in twice}


def _face_motion(defn: dict, bodies: int | None, faces: int | None) -> FaceMotion:
    """An `OffsetFaces` as the file states it: the motion, and how it names the faces."""
    motion = defn.get("motion") if isinstance(defn.get("motion"), dict) else {}
    operand = defn.get("faces") if isinstance(defn.get("faces"), dict) else {}
    return FaceMotion(
        kind=str(motion.get("kind") or "unknown"),
        distance_mm=_number(motion.get("distance")),
        faces_kind=str(operand.get("kind") or "unknown"),
        state_faces=faces or 0,
        state_bodies=bodies or 0,
    )


def _body_op(defn: dict, kind: str) -> BodyOp:
    """A delete, a split or a paste of bodies, as the file states it: the operation and the operand."""
    operand = defn.get("bodies") if kind != "split_body" else defn.get("targets")
    operand = operand if isinstance(operand, dict) else {}
    value = operand.get("value") if isinstance(operand.get("value"), dict) else {}
    bodies = value.get("bodies") if isinstance(value.get("bodies"), list) else []
    tool = defn.get("tool") if isinstance(defn.get("tool"), dict) else {}
    tool_value = tool.get("value") if isinstance(tool.get("value"), dict) else {}
    plane = tool_value.get("plane")
    return BodyOp(
        kind=kind,
        operand_kind=str(operand.get("kind") or "unknown"),
        operand_bodies=len([b for b in bodies if isinstance(b, str)]),
        mode=str(defn.get("mode") or ""),
        tool_kind=str(tool.get("kind") or ""),
        tool_plane=str(plane) if isinstance(plane, str) else "",
    )


def _pattern(defn: dict) -> Pattern:
    """A pattern as the file states it: what form it is, and whether it resolves its own geometry."""
    pattern = defn.get("pattern") if isinstance(defn.get("pattern"), dict) else {}
    seeds = defn.get("seeds") if isinstance(defn.get("seeds"), list) else []
    first = seeds[0] if seeds and isinstance(seeds[0], dict) else {}
    value = first.get("value") if isinstance(first.get("value"), dict) else {}
    inner = value.get("value") if isinstance(value.get("value"), dict) else {}
    bodies = inner.get("bodies") if isinstance(inner.get("bodies"), list) else []
    return Pattern(
        form=str(pattern.get("form") or "unknown"),
        pattern_kind=str(pattern.get("kind") or "unknown"),
        seed_kind=str(first.get("kind") or "unknown"),
        seed_bodies=tuple(str(b) for b in bodies if isinstance(b, str)),
    )


def _loft(defn: dict) -> Loft:
    """A loft as the file states it. Every field is read; nothing here is inferred.

    The corpus's one loft (`soporte controles`'s `Solevación 2`) resolves whole: two sketch
    sections, `guidance {kind: "guides", path: []}`, `op: "new_body"`, and the four switches all
    false (spec section 113).
    """
    sections = defn.get("sections")
    guidance = defn.get("guidance") if isinstance(defn.get("guidance"), dict) else {}
    path = guidance.get("path")
    return Loft(
        sections=tuple(
            LoftSection(str(s.get("kind") or "unknown"), str(s.get("value") or ""))
            for s in (sections if isinstance(sections, list) else [])
            if isinstance(s, dict)
        ),
        guidance_kind=str(guidance.get("kind") or "none"),
        guides=len(path) if isinstance(path, list) else 0,
        op=str(defn.get("op") or "unknown"),
        closed=bool(defn.get("closed")),
        solid=bool(defn.get("solid")),
        ruled=bool(defn.get("ruled")),
        linearize=bool(defn.get("linearize")),
    )


def _extrude(
    defn: dict,
    groups: dict[str, dict],
    boundaries: dict[str, dict[str, tuple[tuple[float, float, float], ...]]] | None = None,
) -> Extrude:
    profile = defn.get("profile", {})
    value = profile.get("value") if isinstance(profile.get("value"), dict) else {}
    # The `sketch` kind of IR 10 names the whole sketch as the profile, so its `value` is the
    # sketch's id on its own rather than an object with a `sketch` field. Read that way it looked
    # like an extrude with no sketch at all, which the mapper refuses before it can ask for
    # `profiles: "all"` (spec section 95).
    if profile.get("kind") == "sketch" and isinstance(profile.get("value"), str):
        value = {"sketch": profile["value"]}
    extent = defn.get("extent", {})
    termination = (extent.get("side") or {}).get("termination") or {}
    length = termination.get("length") if termination.get("kind") == "blind" else None
    trimmed = _trimmed_regions(value)
    to_face: tuple[tuple[float, float, float], ...] = ()
    face = termination.get("face") if termination.get("kind") == "to_face" else None
    if isinstance(face, dict) and face.get("kind") == "historical":
        held = face.get("value") or {}
        named = held.get("faces") or []
        if len(named) == 1:
            to_face = tuple((boundaries or {}).get(held.get("state"), {}).get(named[0], ()))
    return Extrude(
        profile_kind=profile.get("kind", "unknown"),
        sketch=value.get("sketch"),
        profiles=tuple(int(i) for i in value.get("profiles", [])),
        op=defn.get("op", "unknown"),
        reversed=defn.get("direction", {}).get("kind") == "reversed_profile_normal",
        extent_kind=extent.get("kind", "unknown"),
        termination=termination.get("kind", "unknown"),
        length_mm=float(length) if length is not None else None,
        to_face_points=to_face,
        regions=_regions(value),
        trimmed_regions=len(trimmed),
        trimmed_boundaries=tuple(_boundary(r) for r in trimmed),
        region_count=len(value.get("regions") or []) if isinstance(value.get("regions"), list) else 0,
        selection_count=_selection_count(value, groups),
    )


def _axis_of(defn: dict) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    """A datum axis as the file resolves it: a point on it and its direction, or None."""
    origin, direction = _vec_of(defn.get("origin")), _vec_of(defn.get("direction"))
    return None if origin is None or direction is None else (origin, direction)


def _vec_of(value: object) -> tuple[float, float, float] | None:
    """A finite 3-vector, or None when the CADIR holds anything else."""
    d = _dict(value)
    parts = [_number(d.get(k)) for k in ("x", "y", "z")]
    return None if any(v is None for v in parts) else (parts[0], parts[1], parts[2])


def _revolve(defn: dict) -> Revolve:
    """The revolve recipe as the file resolves it.

    The shape differs from an extrude's: a revolve keeps `profile`, `axis`, `extent` and `solid`
    inside a `construction` object, and the operation beside it. An unresolved construction leaves
    every field of that object empty, and the mapper refuses the feature by its profile kind, as it
    does for an extrude whose kind it cannot build.
    """
    construction = _dict(defn.get("construction"))
    profile = _dict(construction.get("profile"))
    value = profile.get("value") if isinstance(profile.get("value"), dict) else {}
    # The `sketch` kind names the whole sketch as the profile, so its `value` is the sketch's id on
    # its own rather than an object — the same spelling IR 10 uses for an extrude (section 95).
    if profile.get("kind") == "sketch" and isinstance(profile.get("value"), str):
        value = {"sketch": profile["value"]}
    axis = _dict(construction.get("axis"))
    extent = _dict(construction.get("extent"))
    termination = _dict(extent.get("termination"))
    return Revolve(
        construction=construction.get("state", "unknown"),
        profile_kind=profile.get("kind", "unknown"),
        sketch=value.get("sketch"),
        profiles=tuple(int(i) for i in value.get("profiles", [])),
        op=defn.get("op", "unknown"),
        extent_kind=extent.get("kind", "unknown"),
        angle_rad=_number(termination.get("angle")) if termination.get("kind") == "angle" else None,
        axis_origin=_vec_of(axis.get("origin")),
        axis_direction=_vec_of(axis.get("direction")),
        solid=bool(construction.get("solid")),
    )


def _base_flange(defn: dict) -> BaseFlange:
    """The base flange recipe as the file states it: a profile, a thickness and a side.

    Flatter than a revolve's — no `construction` wrapper, no operation, no extent. The 3 in the
    corpus all name the whole sketch, and for that kind cadmpeg writes the sketch's id as the
    `value` on its own rather than as an object, the same spelling IR 10 uses for an extrude
    (section 95) and a revolve (section 100).
    """
    profile = _dict(defn.get("profile"))
    value = profile.get("value")
    return BaseFlange(
        profile_kind=profile.get("kind", "unknown"),
        sketch=value if isinstance(value, str) else _dict(value).get("sketch"),
        thickness=_number(defn.get("thickness")),
        side=defn.get("side") if isinstance(defn.get("side"), str) else "unknown",
    )


def _ids(value: object) -> tuple[str, ...]:
    return tuple(v for v in value if isinstance(v, str)) if isinstance(value, list) else ()


def _base_feature_bodies(defn: dict) -> tuple[str, ...]:
    """The bodies a base feature carries, as its own definition resolves them.

    cadmpeg records a feature's bodies in `outputs`, and for a base feature it sometimes writes no
    such field at all: measured on `corpus-03`, all four come with none while their definitions
    resolve the bodies (to none, one, one and three). `corpus-10` fills both
    with the same three ids, so the definition is the same statement in the file, not a guess —
    without reading it those four features condemn nothing and the fallback has nothing to bring
    back for them.
    """
    bodies = _dict(defn.get("bodies"))
    if bodies.get("kind") != "resolved":
        return ()
    return _ids(_dict(bodies.get("value")).get("bodies"))


def _components(model: dict) -> dict[str, Component]:
    """Fusion's components, from the product definitions. A nameless one is the design's root."""
    out = {}
    for c in model.get("product_definitions", []):
        if not isinstance(c, dict) or not isinstance(c.get("id"), str):
            continue
        # A local component states its name in `source_name`; a component cadmpeg merged from a
        # `.f3z` member states it in `label` ("corpus-08"). Both are the file's own name for it.
        name = c.get("source_name") or c.get("label")
        out[c["id"]] = Component(
            id=c["id"],
            name=name if isinstance(name, str) and name else None,
            bodies=_ids(c.get("bodies")),
            sketches=_ids(c.get("sketches")),
        )
    return out


def _transform(value: object) -> tuple[tuple[float, ...], ...]:
    """The 3x4 placement, or () when the file does not state it plainly (then it is not identity)."""
    if not isinstance(value, list) or len(value) != 3:
        return ()
    rows = []
    for row in value:
        if not isinstance(row, list) or len(row) != 4:
            return ()
        numbers = [_number(v) for v in row]
        if any(n is None for n in numbers):
            return ()
        rows.append(tuple(float(n) for n in numbers))  # type: ignore[arg-type]
    return tuple(rows)


def _scale(value: object) -> tuple[float, float, float]:
    """The occurrence's scale; (0, 0, 0) when it is malformed, so `identity` turns it down."""
    if not isinstance(value, list) or len(value) != 3:
        return (0.0, 0.0, 0.0)
    numbers = [_number(v) for v in value]
    if any(n is None for n in numbers):
        return (0.0, 0.0, 0.0)
    x, y, z = numbers
    return (float(x), float(y), float(z))  # type: ignore[arg-type]


def _occurrences(model: dict) -> list[Occurrence]:
    """Where each component sits. The root occurrence is the one whose parent is the design itself."""
    out = []
    for o in model.get("occurrences", []):
        if not isinstance(o, dict) or not isinstance(o.get("id"), str):
            continue
        prototype = _dict(o.get("prototype"))
        definition = prototype.get("definition")
        document = _dict(prototype.get("document")).get("path")
        parent = _dict(o.get("parent"))
        ordinal = _number(o.get("ordinal"))
        out.append(
            Occurrence(
                id=o["id"],
                component=definition if isinstance(definition, str) else None,
                document=document if isinstance(document, str) else None,
                parent=parent.get("occurrence") if parent.get("kind") == "occurrence" else None,
                ordinal=int(ordinal) if ordinal is not None else 0,
                transform=_transform(o.get("transform")),
                scale=_scale(o.get("scale")),
            )
        )
    # Parents before children, so whoever rebuilds them can create a component inside one that
    # already exists. `mango para lima` is the corpus's nested case: Collet sits inside File Handle.
    depth = {}

    def _depth(occ: Occurrence, seen: frozenset[str] = frozenset()) -> int:
        if occ.id in depth:
            return depth[occ.id]
        parent = by_id.get(occ.parent) if occ.parent else None
        # A cycle (or a parent the file never declares) counts as a root: never recurse forever.
        depth[occ.id] = 0 if parent is None or parent.id in seen else _depth(parent, seen | {occ.id}) + 1
        return depth[occ.id]

    by_id = {o.id: o for o in out}
    return sorted(out, key=lambda o: (_depth(o), o.ordinal, o.id))


# A cadmpeg id carries the stream its record lives in and that record's index: a sketch's as
# `…#<stream>@<index>`, a feature's as `…#<arity>:<stream><arity>:<label><n>:<index>`. Read only to
# pair an untyped sketch feature with its record, in `_derived_sketch_links`.
_SKETCH_SLOT = re.compile(r"^(?P<stream>.*)@(?P<index>\d+)$")
_FEATURE_SLOT = re.compile(r"^\d+:(?P<stream>.*?)\d+:.*:(?P<index>\d+)$")


def _slot(ident: str, pattern: re.Pattern[str]) -> tuple[str, int] | None:
    _, _, rest = ident.partition("#")
    m = pattern.match(rest)
    return (m["stream"], int(m["index"])) if m else None


def _derived_sketch_links(
    raw: list[dict], pools: dict[str, list[tuple[int, str]]], claimed: set[str]
) -> dict[str, str]:
    """Which sketch record each untyped sketch feature holds, by feature id, where that is decided.

    cadmpeg names a feature's sketch only in a definition it typed. Where the label's spelling is
    not in its table the definition comes back `native` and the link is gone with it — even though
    the sketch records themselves are decoded in full (10 sketches, 388 entities and 134 constraints
    in `corpus-08.f3z`, placements resolved).

    The rule is the sketch record with the highest index at or below the feature's, in the same
    stream. It is measured, not assumed: against the 53 features cadmpeg **did** type as sketch
    across all 12 corpus files — where the answer is known — it reproduces cadmpeg's own link 53
    times out of 53, the two that point into the `.f3z`'s xref and `test weird box`'s second design
    segment included.

    Reading a link off record-index layout is inference about the file format, which is why it is
    gated twice and refuses rather than reaches:

    * The record the rule lands on must not already belong to a feature cadmpeg typed. If it does,
      the derivation contradicts a link that was actually read, and this feature gets none —
      the next record down would be a different rule, and no measurement backs that one.
    * Two features landing on the same record is not a pairing either, so both lose it.

    What survives is a bijection onto records nothing else claims. On that `.f3z` it is exactly the
    six `スケッチ` features against the six of ten records its four typed sketch features leave free.
    A feature that comes out of here with no link falls back as before: the mapper refuses it with
    "sketch record not found".
    """
    picks: dict[str, str] = {}
    takers: dict[str, list[str]] = {}
    for f in raw:
        if (f.get("definition") or {}).get("definition") != "native":
            continue
        if f.get("source_tag") not in SKETCH_LABELS:
            continue
        slot = _slot(str(f.get("id") or ""), _FEATURE_SLOT)
        if slot is None:
            continue
        under = [sid for index, sid in pools.get(slot[0], ()) if index <= slot[1]]
        if not under or under[-1] in claimed:
            continue
        picks[str(f["id"])] = under[-1]
        takers.setdefault(under[-1], []).append(str(f["id"]))
    return {fid: sid for fid, sid in picks.items() if len(takers[sid]) == 1}


def parse_cadir(data: dict) -> CadirParts:
    model = data["model"]
    entities: dict[str, list[SketchEntity]] = {}
    for e in model.get("sketch_entities", []):
        geometry = e.get("geometry", {})
        entities.setdefault(e["sketch"], []).append(
            SketchEntity(e["id"], geometry.get("kind", "unknown"), bool(e.get("construction")), geometry)
        )
    constraints: dict[str, list[SketchConstraint]] = {}
    for c in model.get("sketch_constraints", []):
        defn = c.get("definition", {})
        constraints.setdefault(c["sketch"], []).append(SketchConstraint(defn.get("kind", "unknown"), defn))
    sketches = {
        s["id"]: Sketch(
            id=s["id"],
            frame=_frame(s),
            entities=tuple(entities.get(s["id"], [])),
            profiles=tuple(
                tuple(ProfileEdge(p["entity"], bool(p.get("reversed"))) for p in loop)
                for loop in s.get("profiles", [])
            ),
            constraints=tuple(constraints.get(s["id"], [])),
            visible=s.get("visible") is not False,
        )
        for s in model.get("sketches", [])
    }
    raw_groups = _dict(_dict(data.get("native")).get("f3d")).get("design_extrude_selection_groups")
    groups = {
        g["id"]: g
        for g in (raw_groups if isinstance(raw_groups, list) else [])
        if isinstance(g, dict) and isinstance(g.get("id"), (str, int)) and not isinstance(g["id"], bool)
    }
    # Sketch records by stream, ordered by index, and the ones a typed feature already names.
    pools: dict[str, list[tuple[int, str]]] = {}
    for sid in sketches:
        slot = _slot(sid, _SKETCH_SLOT)
        if slot is not None:
            pools.setdefault(slot[0], []).append((slot[1], sid))
    for pool in pools.values():
        pool.sort()
    raw_features = sorted(model.get("features", []), key=lambda f: f["ordinal"])
    edge_points = _edge_points(model)
    face_boundaries = _face_boundaries(model)
    state_bodies = _state_bodies(model)
    state_faces = _state_faces(model)
    claimed = {
        ((f.get("definition") or {}).get("sketch") or {}).get("sketch")
        for f in raw_features
        if (f.get("definition") or {}).get("definition") == "sketch"
    }
    derived = _derived_sketch_links(raw_features, pools, claimed)
    features = []
    # A record with no `name` is not a feature of the timeline, and reading one as a feature
    # **crashed** the conversion of a real package of the user's with `KeyError: 'name'`. Measured on
    # `corpus-12.f3z`: 2 of its 261 records have no name and no `source_tag` either,
    # both `definition: tree_node` holding `model:document-parameters#0` — what the merge of its five
    # external references put in the array beside the timeline. Skipped and counted, never given an
    # invented label (spec section 91).
    not_features = tuple(
        str(f.get("id") or f"ordinal {f.get('ordinal')}") for f in raw_features if "name" not in f
    )
    for f in raw_features:
        if "name" not in f:
            continue
        defn = f.get("definition", {})
        kind = defn.get("definition")
        outputs = tuple(f.get("outputs") or ())
        if not outputs and kind == "base_feature":
            outputs = _base_feature_bodies(defn)
        features.append(
            Feature(
                ordinal=int(f["ordinal"]),
                name=f["name"],
                tag=f.get("source_tag", "unknown"),
                definition_kind=kind if isinstance(kind, str) else "",
                suppressed=bool(f.get("suppressed")),
                outputs=outputs,
                sketch=(
                    (defn.get("sketch") or {}).get("sketch")
                    if kind == "sketch"
                    else derived.get(str(f.get("id") or ""))
                ),
                sketch_derived=str(f.get("id") or "") in derived,
                frame=_frame_of(defn.get("frame")) if kind == "datum_plane" else None,
                datum_axis=_axis_of(defn) if kind == "datum_axis" else None,
                component=f.get("product_definition")
                if isinstance(f.get("product_definition"), str)
                else None,
                extrude=_extrude(defn, groups, face_boundaries) if kind == "extrude" else None,
                revolve=_revolve(defn) if kind == "revolve" else None,
                base_flange=(_base_flange(defn) if kind == "sheet_metal_base_flange" else None),
                combine=(
                    _combine(defn, state_bodies.get(str(f.get("id") or ""))) if kind == "combine" else None
                ),
                loft=_loft(defn) if kind == "loft" else None,
                pattern=_pattern(defn) if kind == "pattern" else None,
                body_op=(
                    _body_op(defn, kind) if kind in ("delete_body", "split_body", "insert_bodies") else None
                ),
                face_motion=(
                    _face_motion(
                        defn,
                        state_bodies.get(str(f.get("id") or "")),
                        state_faces.get(str(f.get("id") or "")),
                    )
                    if kind == "move_face"
                    else None
                ),
                edge_op=_edge_op(defn, edge_points),
                shell=_shell(defn, face_boundaries) if kind == "shell" else None,
                primitive=_dict(defn.get("solid")) if kind == "primitive" else None,
            )
        )
    bodies = [
        Body(b["id"], bool(b.get("visible", True)), b.get("name") or None) for b in model.get("bodies", [])
    ]
    return CadirParts(sketches, features, bodies, _components(model), _occurrences(model), not_features)
