"""Fusion's components: create them in SolidCraft and author each feature inside its owner.

A Fusion design can hold its bodies and sketches in components rather than flat in the root, and
the corpus has three such files. SolidCraft has the same notion, so the tree maps one to one:
`component.create` for each component, parents first, and `component.activate` before a feature so
its geometry lands where Fusion has it.

A placement that is not the identity is **applied**: 34 of the corpus's 60 occurrences are such a
placement, and until now every one of them was reported and left where it was modelled. The file
states a placement as a 3x4 matrix and the engine takes one as a turn and a shift, so the matrix is
decomposed — and the result is then **read back and compared with the matrix**, because a
decomposition that is wrong must not leave a design quietly misplaced (spec section 101).

The geometry goes with it. A sketch reaches the engine as an explicit plane, and an explicit plane
is placed in world coordinates whatever component is active, so `placed_frame` carries each plane
into the world by the placement of the component holding it before the sketch is built; the engine
stores it back in that component's own coordinates, which the placement undoes, so the sketch ends
up local to its component as the file has it **and** in the world where the original has it
(spec section 102). Construction planes are the exception, and the one case that is still open.
"""

from __future__ import annotations

import math

from f3d2solidcraft.engine import CommandRefused
from f3d2solidcraft.mappers import Context
from f3d2solidcraft.model import Component, Feature, Frame, Occurrence, Vec3

ROOT = "root"
# The component the fallback import lands in, under the design's root, and what the user sees it
# called. Once named `Fusion fallback`, which read as an error in the file the user opens.
IMPORT_HOLDER = "Imported from Fusion"

# How far a rotation term may sit from the matrix the file states, and a translation from its
# millimetres, for the placement to count as the one the file asked for. The engine answers in
# doubles through a quaternion, so a quarter turn comes back with 6.1e-17 where the file writes 0;
# this is far over that and far under anything a design would notice.
PLACEMENT_TOLERANCE = 1e-9
# Under this angle there is no turn to ask the engine for, only a shift. The file writes an
# unturned placement as exact 1.0 and 0.0, so this only has to be over the dust a quaternion
# leaves behind.
ROTATION_DUST = 1e-12


def _orthonormal(rotation: tuple[Vec3, ...]) -> bool:
    """Whether this 3x3 is a rotation and not a reflection or a squeeze.

    Fusion can place a component mirrored, and a mirrored placement has no axis and angle to give:
    `occurrence.move` turns and shifts, it does not reflect. Asking it for one anyway would place
    the component the right distance away in the wrong handedness, which is the kind of silent
    wrongness this converter is built to refuse. So the matrix is checked for what it claims to
    be — rows of unit length, mutually perpendicular, right-handed — rather than trusted.
    """
    for row in rotation:
        if abs(sum(c * c for c in row) - 1.0) > 1e-6:
            return False
    for i, j in ((0, 1), (0, 2), (1, 2)):
        if abs(sum(a * b for a, b in zip(rotation[i], rotation[j]))) > 1e-6:
            return False
    (a, b, c), (d, e, f), (g, h, i) = rotation
    determinant = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    return abs(determinant - 1.0) <= 1e-6


def axis_angle(rotation: tuple[Vec3, ...]) -> tuple[Vec3, float] | None:
    """This rotation as the axis and angle (radians) the engine asks for, or None if it is no rotation.

    By way of a quaternion, branching on the largest component, because the direct
    `axis = (R21-R12, R02-R20, R10-R01) / 2 sin angle` divides by zero at a half turn — and a half
    turn is not hypothetical here: `Caja parametrica v2` places its `Top` at exactly 180° about z.
    The branch keeps every case conditioned.

    An unturned placement comes back with a zero angle and an arbitrary axis, which the caller
    leaves out of the move: there is nothing to turn about.
    """
    if not _orthonormal(rotation):
        return None
    trace = rotation[0][0] + rotation[1][1] + rotation[2][2]
    if trace > 0:
        scale = math.sqrt(trace + 1.0) * 2
        w = 0.25 * scale
        x = (rotation[2][1] - rotation[1][2]) / scale
        y = (rotation[0][2] - rotation[2][0]) / scale
        z = (rotation[1][0] - rotation[0][1]) / scale
    elif rotation[0][0] > rotation[1][1] and rotation[0][0] > rotation[2][2]:
        scale = math.sqrt(1.0 + rotation[0][0] - rotation[1][1] - rotation[2][2]) * 2
        w = (rotation[2][1] - rotation[1][2]) / scale
        x = 0.25 * scale
        y = (rotation[0][1] + rotation[1][0]) / scale
        z = (rotation[0][2] + rotation[2][0]) / scale
    elif rotation[1][1] > rotation[2][2]:
        scale = math.sqrt(1.0 + rotation[1][1] - rotation[0][0] - rotation[2][2]) * 2
        w = (rotation[0][2] - rotation[2][0]) / scale
        x = (rotation[0][1] + rotation[1][0]) / scale
        y = 0.25 * scale
        z = (rotation[1][2] + rotation[2][1]) / scale
    else:
        scale = math.sqrt(1.0 + rotation[2][2] - rotation[0][0] - rotation[1][1]) * 2
        w = (rotation[1][0] - rotation[0][1]) / scale
        x = (rotation[0][2] + rotation[2][0]) / scale
        y = (rotation[1][2] + rotation[2][1]) / scale
        z = 0.25 * scale
    length = math.sqrt(x * x + y * y + z * z)
    if length <= ROTATION_DUST:
        return (0.0, 0.0, 1.0), 0.0
    return (x / length, y / length, z / length), 2 * math.atan2(length, w)


def _mismatch(stated: tuple[tuple[float, ...], ...], reached: list) -> str | None:
    """The largest term by which the placement reached differs from the one the file states.

    The engine answers with a 4x4 for row vectors — the rotation transposed, the translation in the
    last row — so it is read that way and not as the file's own 3x4. Measured in the engine on a
    quarter turn about z with a 100 mm shift, the two agree term for term.
    """
    try:
        worst, where = 0.0, ""
        for i, row in enumerate(stated):
            for j, value in enumerate(row):
                got = float(reached[3][i] if j == 3 else reached[j][i])
                if abs(got - value) > worst:
                    worst, where = (
                        abs(got - value),
                        f"row {i} column {j}: {value:.9g} asked, {got:.9g} reached",
                    )
        return where if worst > PLACEMENT_TOLERANCE else None
    except (IndexError, KeyError, TypeError, ValueError):
        return "the engine did not answer with a placement that could be read"


def place(occ: Occurrence, occurrence: object, ctx: Context) -> list[str]:
    """Put this occurrence where the file places it, and prove it landed there; returns warnings.

    The engine composes a move onto whatever the occurrence already carries, and these occurrences
    are made by `component.create` and so start at the identity: one move is the whole placement.
    It is asked for as **pending** and captured only once the placement reads back as the file's,
    so a decomposition that does not reproduce the matrix leaves the component where it was instead
    of somewhere nobody asked for.
    """
    if occ.identity or occurrence is None:
        return []
    if any(abs(s - 1.0) > PLACEMENT_TOLERANCE for s in occ.scale):
        # `occurrence.move` turns and shifts; it does not resize. None of the corpus's 34 moved
        # occurrences is scaled, so this has no case to measure and claims nothing about one.
        said = (
            f"this placement scales its component by {occ.scale}, which the engine's move does not "
            "do, so the component is built at the size it was modelled"
        )
        return [said]
    if len(occ.transform) != 3 or any(len(row) != 4 for row in occ.transform):
        return ["the file does not state this placement as a 3x4 matrix, so it is not applied"]
    rotation = tuple(tuple(row[:3]) for row in occ.transform)
    decomposed = axis_angle(rotation)
    if decomposed is None:
        said = (
            "this placement mirrors its component, and the engine's move turns and shifts without "
            "reflecting, so the component is built in the handedness it was modelled"
        )
        return [said]
    axis, angle = decomposed
    params: dict = {
        "occurrence": occurrence,
        "translate": [row[3] for row in occ.transform],
        "capture": False,
    }
    if angle > ROTATION_DUST:
        params |= {"axis": list(axis), "angle": angle, "origin": [0.0, 0.0, 0.0]}
    grounded = False
    try:
        out = ctx.engine.execute("occurrence.move", params)
    except CommandRefused as exc:
        if "grounded" not in exc.message:
            return [f"this placement was refused by the engine: {exc.message}"]
        # A design's first part is grounded when it is made, and a grounded occurrence does not
        # move. Which component that is depends on what reached the design first: with the
        # reference original inserted, the oracle's own component takes the ground and every
        # placement applies; without one — no oracle, or a design converted on its own — the first
        # component of the design itself is grounded and its placement was silently refused. So the
        # ground is lifted for the move and put back, and the placement no longer depends on what
        # else happens to be in the design (spec section 101).
        ctx.engine.execute("occurrence.ground", {"occurrence": occurrence, "grounded": False})
        grounded = True
        try:
            out = ctx.engine.execute("occurrence.move", params)
        except CommandRefused as second:
            ctx.engine.execute("occurrence.ground", {"occurrence": occurrence, "grounded": True})
            return [f"this placement was refused by the engine: {second.message}"]
    wrong = _mismatch(occ.transform, out.get("transform"))
    if wrong is not None:
        ctx.engine.execute("component.revert_position", {"occurrences": [occurrence]})
    else:
        ctx.engine.execute("component.capture_position", {})
    if grounded:
        ctx.engine.execute("occurrence.ground", {"occurrence": occurrence, "grounded": True})
    if wrong is not None:
        said = (
            "the placement this converter worked out does not reproduce the one the file states "
            f"({wrong}), so it was dropped and the component is built where it was modelled"
        )
        return [said]
    if occ.component:
        ctx.placements[occ.component] = occ.transform
    return []


def _compose(outer: tuple[tuple[float, ...], ...], inner: tuple[tuple[float, ...], ...]):
    """The placement of `inner` seen from where `outer` sits: `outer` applied after `inner`."""
    return tuple(
        tuple(
            sum(outer[i][k] * inner[k][j] for k in range(3)) + (outer[i][3] if j == 3 else 0.0)
            for j in range(4)
        )
        for i in range(3)
    )


def world_transform(ctx: Context, component: str) -> tuple[tuple[float, ...], ...] | None:
    """Where this component's own coordinates sit in the design, or None when that is the identity.

    The chain of placements from the design down to it, composed — each one applied after the ones
    inside it. Only placements `place` actually applied count, so this is the transform the engine
    really carries and not the one the file wished for.

    Measured over the corpus, no component sits under more than one moved ancestor, so every chain
    here is one placement long; it is composed as a chain anyway because the file can nest them and
    a one-deep special case would be wrong the first time one does.
    """
    by_occ = {o.id: o for o in ctx.design.occurrences}
    walk = next((o for o in ctx.design.occurrences if o.component == component), None)
    chain = []
    while walk is not None:
        applied = ctx.placements.get(walk.component or "")
        if applied is not None:
            chain.append(applied)
        walk = by_occ.get(walk.parent) if walk.parent else None
    if not chain:
        return None
    composed = chain[0]
    for outer in chain[1:]:  # from the inside out: each ancestor applies after the ones below it
        composed = _compose(outer, composed)
    return composed


def placed_frame(ctx: Context, holds: str, frame: Frame) -> Frame:
    """This frame where the design really has it, given the placement of the component holding it.

    The file resolves a sketch's plane in its **component's** coordinates, and an explicit plane
    reaches the engine in the world's whatever component is active (measured: a `solid.box` in a
    component moved 100 mm along x lands at x 100..110, a sketch created there with
    `origin: [0,0,0]` lands at x 0..3). So the plane has to be carried into the world by hand, or
    the geometry the replay builds sits where the component was modelled instead of where it is.

    The engine stores it back in the component's own coordinates, which the placement undoes, so
    the sketch ends up local to its component exactly as the file has it **and** in the world where
    the original has it. The normal and the u axis turn with the rotation and ignore the shift,
    being directions.
    """
    placement = world_transform(ctx, holds)
    if placement is None:
        return frame

    def turn(v: Vec3) -> Vec3:
        return tuple(sum(placement[i][k] * v[k] for k in range(3)) for i in range(3))

    origin = tuple(
        sum(placement[i][k] * frame.origin[k] for k in range(3)) + placement[i][3] for i in range(3)
    )
    return Frame(origin, turn(frame.normal), turn(frame.u_axis))


def placed_point(ctx: Context, point: Vec3) -> Vec3:
    """A point the file states in a component's coordinates, carried into the world.

    The same correction `placed_frame` makes, for the one other thing the file hands over as
    coordinates: the point inside each edge a fillet or a chamfer used. The component is the one
    being authored into, because that is where the body is — a feature that names no body of its own
    activates nothing and inherits the component the feature before it left active.

    Measured on `Caja parametrica v2`: its `Top` is the one placed component of the corpus, and the
    point its `Fillet 3` names came back from the engine as "no edge at
    [-74.99999999999987, 95.60000000000018, 10.999999999999995]" until this was applied. Its
    `Bottom` sits at the identity, which is why the same feature there worked without it — the
    third time one coordinate frame has hidden behind another in this corpus (spec section 107).
    """
    active = ctx.active_component
    if active is None:
        return point
    component = next((key for key, made in ctx.component_ids.items() if made == active), None)
    placement = world_transform(ctx, component) if component else None
    if placement is None:
        return point
    return tuple(sum(placement[i][k] * point[k] for k in range(3)) + placement[i][3] for i in range(3))


def holder_of(ctx: Context, key: str) -> str | None:
    """Which component of the design owns this body or sketch, by cadmpeg id."""
    owner = _owner_of(ctx).get(key)
    return owner.id if owner is not None else None


def _owner_of(ctx: Context) -> dict[str, Component]:
    """Which component owns each body and each sketch of the design."""
    owner = {}
    for component in ctx.design.components.values():
        for key in (*component.bodies, *component.sketches):
            owner[key] = component
    return owner


def create_components(ctx: Context) -> list[str]:
    """Build the component tree, parents first. Fills `ctx.component_ids`; returns warnings."""
    warnings: list[str] = []
    for occ in ctx.design.occurrences:
        component = ctx.design.components.get(occ.component or "")
        if component is None:
            what = f"the document {occ.document}" if occ.document else "a component it does not declare"
            warnings.append(f"occurrence {occ.id} places {what}, which is not in this model")
            continue
        if component.name is None:
            # The design's own root component is the one the file leaves unnamed, and SolidCraft
            # already has it. An unnamed component placed deeper has no name to create it under, so
            # its geometry stays in the root too — that is what the engine does without us.
            ctx.component_ids[component.id] = ROOT
            if occ.parent is not None:
                warnings.append(f"component {component.id} is placed but unnamed; it stays in the root")
            continue
        if occ.parent is None:
            # A named component whose parent is the design: a `.f3z` member cadmpeg merged arrives
            # this way, with no occurrence of its own for the root.
            parent = ROOT
        else:
            parent_occ = next((o for o in ctx.design.occurrences if o.id == occ.parent), None)
            parent = ctx.component_ids.get(parent_occ.component or "") if parent_occ else None
            if parent is None:
                warnings.append(
                    f"component {component.name} sits inside an occurrence that was not built; "
                    "it goes in the root instead"
                )
                parent = ROOT
        try:
            created = ctx.engine.execute("component.create", {"name": component.name, "parent": parent})
        except CommandRefused as exc:
            warnings.append(f"component {component.name} was refused: {exc.message}")
            continue
        ctx.component_ids[component.id] = created.get("component", component.name)
        warnings.extend(
            f"component {component.name}: {said}" for said in place(occ, created.get("occurrence"), ctx)
        )
    if ctx.component_ids:
        activate(ROOT, ctx)
    return warnings


def activate(component: object, ctx: Context) -> None:
    """Make `component` the one new geometry goes into, unless it already is."""
    if ctx.active_component == component:
        return
    ctx.engine.execute("component.activate", {"component": component})
    ctx.active_component = component


def activate_owner(feature: Feature, ctx: Context) -> list[str]:
    """Activate the component Fusion authored this feature in; returns warnings.

    A sketch feature is placed by its sketch and every other by its output bodies, which is what
    the file states: the components list the sketches and bodies they own, and measured on the
    whole corpus those lists partition the design. A feature that names neither — a datum plane,
    say — leaves the active component alone.
    """
    keys = [feature.sketch] if feature.sketch else list(feature.outputs)
    return activate_holder(keys, feature.name, ctx)


def activate_holder(keys: list[str], what: str, ctx: Context) -> list[str]:
    """Activate the one component that owns these bodies or sketches; returns warnings.

    Split out of `activate_owner` so anything the replay authors outside the timeline asks the same
    question of the same rule: a sketch no feature names is still owned by a component, and
    building it in the root would move it out of the holder Fusion keeps it in.
    """
    if not ctx.component_ids:
        return []
    owner = _owner_of(ctx)
    owners = {owner[k].id for k in keys if k in owner}
    if not owners:
        return []
    if len(owners) > 1:
        names = sorted(str(ctx.design.components[o].name) for o in owners)
        # Fusion can author one feature across components; SolidCraft authors in one at a time.
        activate(ROOT, ctx)
        return [f"{what} spans the components {', '.join(names)}; it is built in the root"]
    target = ctx.component_ids.get(next(iter(owners)))
    if target is None:
        return []
    activate(target, ctx)
    return []


def _our_paths(ctx: Context) -> dict[tuple[str, ...], object]:
    """Each component we created, keyed by the chain of Fusion names from the root down."""
    by_occ = {o.id: o for o in ctx.design.occurrences}
    paths: dict[tuple[str, ...], object] = {}
    for occ in ctx.design.occurrences:
        built = ctx.component_ids.get(occ.component or "")
        if built is None:
            continue
        names, walk = [], occ
        while walk is not None and walk.parent is not None:
            component = ctx.design.components.get(walk.component or "")
            if component is None or component.name is None:
                break
            names.append(component.name)
            walk = by_occ.get(walk.parent)
        paths[tuple(reversed(names))] = built
    return paths


def _placements(cid: object, by_id: dict, memo: dict[object, int]) -> int:
    """How many times a component of a `component.list {compact}` listing is placed in the design.

    Summed over its occurrences, each worth as many placements as the component it sits in, so
    a part placed twice at the top and five times inside a part that is itself placed five times
    counts 27 and not 7: measured on a board where `4 PIN HEADER v2` is exactly that. The root,
    which has no occurrence, is placed once.
    """
    if cid not in memo:
        component = by_id.get(cid) or {}
        occurrences = component.get("occurrences") or []
        memo[cid] = (
            sum(_placements(o.get("parent"), by_id, memo) for o in occurrences)
            if component.get("parent") is not None and occurrences
            else 1
        )
    return memo[cid]


def _unlock(ctx: Context, holder: object, unlocked: set[object]) -> list[str]:
    """Unlock the import's holder once, so its contents can leave it; returns warnings."""
    if holder in unlocked:
        return []
    unlocked.add(holder)
    try:
        # The import lands as one rigid group; its bodies cannot leave it until unlocked.
        ctx.engine.execute("component.unlock", {"component": holder})
    except CommandRefused as exc:
        # A refusal here is not a reason to leave the bodies in the import's own holder, and it used
        # to be: the move was skipped outright. Measured on a real design with no feature history at
        # all (every body imported, one unnamed component), the engine refuses with `nothing holds
        # this component's contents together` — there was no lock to undo.
        #
        # So the move is tried anyway and the engine decides. A component that really is locked
        # refuses the move too, and that refusal is the one worth reporting; this one is only a note.
        return [f"the imported bodies were not unlocked first: {exc.message}"]
    return []


def _import_holder(tree: list[dict]) -> dict | None:
    """The component the fallback import made directly under the design's root, if there is one."""
    root = next((c["id"] for c in tree if c.get("parent") is None), None)
    return next((c for c in tree if c.get("name") == IMPORT_HOLDER and c.get("parent") == root), None)


def _theirs(tree: list[dict], holder: dict | None) -> set[object]:
    """The import's own components: its holder and every component placed under it."""
    if holder is None:
        return set()
    part_of = {occ["id"]: c["id"] for c in tree for occ in c.get("occurrences") or []}
    by_id = {c["id"]: c for c in tree}
    found, pending = set(), [holder["id"]]
    while pending:
        cid = pending.pop()
        if cid not in found:
            found.add(cid)
            pending += [part_of[o] for o in by_id[cid].get("children") or [] if o in part_of]
    return found


def _promote(
    ctx: Context, tree: list[dict], holder: dict, theirs: set[object], unlocked: set[object]
) -> list[str]:
    """Hand each top-level part of the import to the design where ours holds nothing; returns warnings.

    The import already rebuilds Fusion's tree for what it brings: one definition per part, one
    occurrence per placement, Fusion's names. The replay builds one component of ours per Fusion
    occurrence, and where no feature was rebuilt into it, ours is empty scaffolding beside the
    import's real one — measured on a third-party board, 98 of its 130 components. So where every
    component of ours by that name (and everything under it) holds no body, feature, sketch or
    construction geometry, ours go and the import's occurrence moves under the design's root in
    their place: `occurrence.reparent` keeps its world position, and it arrives with every copy it
    places.

    Where ours holds native work, the import's part is left for `_move_imported_bodies`, which is the
    path every body took before. A part with no component of ours by that name is promoted too:
    there is nothing of ours for it to join.
    """
    by_id = {c["id"]: c for c in tree}
    part_of = {occ["id"]: c for c in tree for occ in c.get("occurrences") or []}
    root = holder["parent"]
    ours: dict[str, list[object]] = {}
    for component in tree:
        if component.get("parent") == root and component["id"] not in theirs:
            ours.setdefault(str(component.get("name")), []).append(component["id"])

    def native(cid: object) -> bool:
        component = by_id[cid]
        own = any(component.get(key) for key in ("bodies", "features", "sketches", "construction"))
        return own or any(native(part_of[o]["id"]) for o in component.get("children") or [])

    warnings: list[str] = []
    gone: set[object] = set()
    for occurrence in holder.get("children") or []:
        name = str(part_of[occurrence].get("name"))
        mine = ours.get(name, [])
        if any(native(cid) for cid in mine):
            continue
        try:
            for cid in mine:
                if cid not in gone:
                    ctx.engine.execute("component.delete", {"component": cid})
                    gone.add(cid)
        except CommandRefused as exc:
            warnings.append(f"component {name} kept as built: {exc.message}")
            continue
        warnings += _unlock(ctx, holder["id"], unlocked)
        try:
            ctx.engine.execute("occurrence.reparent", {"occurrence": occurrence, "parent": root})
        except CommandRefused as exc:
            warnings.append(f"{name} stays in the import: {exc.message}")
    ctx.component_ids = {key: value for key, value in ctx.component_ids.items() if value not in gone}
    return warnings


def _drop_empty(ctx: Context, theirs: set[object]) -> None:
    """Delete the components of ours left with nothing in them, until none is left.

    Ours is every component but the root and the import's: `ctx.component_ids` keeps one component
    per Fusion component and the replay makes one per placement, so it does not list them all. A
    component the engine will not delete stays, and is not a failure.

    A component that is empty **in Fusion too** stays: the designer left it there, and the tree
    should read like Fusion's (Cristián's decision, 2026-11-18). Only scaffolding goes, the
    components whose content the import holds or `_promote` took over. Empty in Fusion means a named
    component with no bodies, no sketches and no occurrence placed inside it. No corpus design has
    one, so this branch is guarded by reading, not by a corpus test.
    """
    placed_in = {o.parent for o in ctx.design.occurrences if o.parent is not None}
    holders = {o.component for o in ctx.design.occurrences if o.id in placed_in}
    empty_in_fusion = {
        c.name
        for c in ctx.design.components.values()
        if c.name and not c.bodies and not c.sketches and c.id not in holders
    }
    tried: set[object] = set(theirs)
    gone: set[object] = set()
    while True:
        tree = ctx.engine.execute("component.list", {"compact": True, "bodies": True})["components"]
        empty = [
            c["id"]
            for c in tree
            if c.get("parent") is not None
            and c["id"] not in tried
            and c.get("name") not in empty_in_fusion
            and not any(c.get(key) for key in ("bodies", "children", "features", "sketches", "construction"))
        ]
        if not empty:
            break
        for cid in empty:
            tried.add(cid)
            try:
                ctx.engine.execute("component.delete", {"component": cid})
            except CommandRefused:
                continue
            gone.add(cid)
    ctx.component_ids = {key: value for key, value in ctx.component_ids.items() if value not in gone}


def reconcile_components(ctx: Context, imported_bodies: list[str]) -> list[str]:
    """Give the imported bodies Fusion's component tree; returns warnings.

    `cadmpeg convert` writes the assembly structure into the STEP and SolidCraft's importer
    rebuilds it, so the imported bodies arrive inside components carrying Fusion's own names,
    nesting and placements. Three steps, in this order (spec 0.4, item 2):

    1. `_promote`: a top-level part of the import whose components of ours hold no native work
       replaces them, every placement included.
    2. `_move_imported_bodies`: what is left in the import joins the components of ours that do
       hold native work, as before 0.4.
    3. `_drop_empty`: the components of ours that ended up with nothing in them go.

    The import's holder itself stays, under the name `IMPORT_HOLDER`: the engine does not delete it
    and its Import feature is in the timeline either way.
    """
    if not imported_bodies:
        return []
    tree = ctx.engine.execute("component.list", {"compact": True, "bodies": True})["components"]
    holder = _import_holder(tree)
    theirs = _theirs(tree, holder)
    unlocked: set[object] = set()
    warnings = _promote(ctx, tree, holder, theirs, unlocked) if holder is not None else []
    warnings += _move_imported_bodies(ctx, set(imported_bodies), unlocked)
    _drop_empty(ctx, theirs)
    return warnings


def _move_imported_bodies(ctx: Context, imported: set[str], unlocked: set[object]) -> list[str]:
    """Move the imported bodies still in the import into the components Fusion owns them in.

    **A design that declares no components at all is still reconciled.** Its bodies used to be left
    in the import's holder — a name of this converter's own, in the file the user opens. The file
    attributing no body to a named component is the case the loop below answers with the design
    root. Measured on `corpus-11` and `corpus-10`, the two corpus designs
    with no component tree and every body imported.

    **A part placed more than once stays in the import's structure.** The import places a
    definition as many times as the original does, and the replay builds one component per Fusion
    occurrence, so there is no one component its bodies could go to: moving them into any single
    one of ours left one placement where the original has N. Measured on a third-party board of 302
    placements of 43 definitions, the saved file held 43 bodies. So a component the import places
    more than once, counting the placements of every component above it up to the import's holder,
    keeps its bodies, and one aggregated warning says how many parts stayed.

    Only bodies still inside the import's holder are moved: a part `_promote` already handed to the
    design is in its place.
    """
    warnings: list[str] = []
    tree = ctx.engine.execute("component.list", {"compact": True, "bodies": True})["components"]
    by_id = {c["id"]: c for c in tree}
    import_holder = _import_holder(tree)
    ours = _our_paths(ctx)
    attributed = any(c.bodies for c in ctx.design.components.values() if c.name)
    repeated: list[tuple[int, int]] = []  # (bodies, placements) of each part left in the import
    placed: dict[object, int] = {}
    for component in tree:
        held = [b for b in component.get("bodies") or [] if b in imported]
        if not held:
            continue
        names, walk, holder = [], component, component
        while walk is not None and walk.get("parent") is not None:
            holder = walk
            names.append(str(walk.get("name")))
            walk = by_id.get(walk["parent"])
        if import_holder is None or holder["id"] != import_holder["id"]:
            continue  # promoted, or never in the import: already where the design has it
        times = _placements(component["id"], by_id, placed)
        if times > 1:
            repeated.append((len(held), times))
            continue
        path = tuple(reversed(names[:-1]))  # the holder's own name is the import's, not Fusion's
        if not path:
            # Bodies at the holder's own level carry no component. They are the design root's when
            # the file attributes none of its bodies to a named component; when it does, which of
            # them is whose cannot be told from the import — measured on the `.f3z`, where both
            # solids arrive together and the file says one of them is the merged member's. Claiming
            # the root for both would be a guess, so they stay where the import put them.
            if attributed:
                warnings.append(
                    f"the import did not say which component {len(held)} imported body(ies) belong "
                    "to; they stay in its holder"
                )
                continue
            target: object = ROOT
        elif (found := ours.get(path)) is not None:
            target = found
        else:
            warnings.append(
                f"imported bodies arrived in {'/'.join(path)}, which is no component of the "
                "design; they stay where the import put them"
            )
            continue
        warnings += _unlock(ctx, holder["id"], unlocked)
        try:
            ctx.engine.execute("component.move_bodies", {"bodies": held, "component": target})
        except CommandRefused as exc:
            where = "/".join(path) or "the root"
            warnings.append(f"imported bodies not moved into {where}: {exc.message}")
    if repeated:
        warnings.append(
            f"{len(repeated)} part(s) placed more than once ({sum(b * t for b, t in repeated)} "
            f"placements of {sum(b for b, _ in repeated)} body(ies)) stay in the import's structure, "
            "under Fusion's component names, so that every copy keeps its place"
        )
    return warnings
