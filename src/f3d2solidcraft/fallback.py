"""Bodies that could not be rebuilt come back from the original B-rep."""

from __future__ import annotations

import re
from pathlib import Path

from f3d2solidcraft.engine import CommandRefused
from f3d2solidcraft.mappers import Context
from f3d2solidcraft.mappers.components import IMPORT_HOLDER
from f3d2solidcraft.pairing import declared_name
from f3d2solidcraft.replay import ReplayResult
from f3d2solidcraft.tools import ToolFailed, run_tool

_CHECK_ERROR = re.compile(r"^\s*\[error/(\w+)\] (.*?)(?: \([^()]*\))?$", re.MULTILINE)


def original_step(cadmpeg: Path, source: Path, out: Path) -> tuple[Path, list[str]]:
    """The design's bodies as STEP, hidden ones included; internal only (fallback and verification).

    source may be the .f3d or the CADIR JSON already dumped from it, which skips a second decode.
    When cadmpeg's own check refuses the export, it is redone with --allow-errors and the check
    errors come back as warnings: an imperfect B-rep beats a missing body.
    """
    args = [str(cadmpeg), "convert", str(source), "-o", str(out), "--force"]
    try:
        run_tool(args, 600)
    except ToolFailed as exc:
        if "refusing to export" not in str(exc):
            raise
        run_tool([*args, "--allow-errors"], 600)
        return out, check_warnings(str(exc))
    return out, []


def check_warnings(text: str) -> list[str]:
    """One warning per kind of cadmpeg check error, with its count (and worst deviation if geometric)."""
    kinds: dict[str, list[str]] = {}
    for kind, message in _CHECK_ERROR.findall(text):
        kinds.setdefault(kind, []).append(message)
    warnings = []
    for kind, messages in kinds.items():
        head = f"cadmpeg check: {len(messages)}\u00d7 {kind}"
        deviations = [float(m) for m in re.findall(r"\bby ([0-9.]+)", " ".join(messages))]
        if deviations:
            warnings.append(
                f"{head} (max deviation {max(deviations):.3g}) \u2014 imported geometry may be inexact"
            )
        else:
            warnings.append(f"{head} ({messages[0].removeprefix('Fusion ')})")
    return warnings


def same_body(
    a: dict,
    b: dict,
    vol_rel: float = 1e-3,
    bbox_abs: float = 0.01,
    faces: bool = True,
    com_abs: float = 0.01,
) -> bool:
    """Whether these two measurements are of the same solid.

    Volume, box and face count do not determine a shape, and for a while those three were the whole
    check. Measured on the engine: a 10 mm cube notched 4 × 4 at corner (0, 0) and the same cube
    notched at (6, 6) agree on every one of them — 840.000 mm³, the same 0..10 box, 8 faces, 18
    edges, 12 vertices, 568.000 mm² — and are not the same solid. A conversion that built one where
    the original has the other verified.

    So the centre of mass is read too, to the same 0.01 mm as the box: it is where the shape's mass
    actually sits, and on that pair it differs by 1.14 mm on two axes.
    """
    if faces and a["faces"] != b["faces"]:
        return False
    # The engine reports a volume and a centre of mass only for a closed, consistently oriented
    # body, so an open one — a surface — carries neither. Reading the volume straight **crashed** the
    # whole conversion on a real package of the user's, `corpus-09.f3z`: `unsupported
    # operand type(s) for -: 'NoneType' and 'float'` (spec section 89).
    va, vb = a.get("volume_mm3"), b.get("volume_mm3")
    if (va is None) != (vb is None):
        return False  # one is a closed solid and the other is not: not the same body
    if va is None:
        # Neither is closed, so there is no volume and no centre of mass to read. The surface area
        # takes volume's place, to the same relative band: without it this pair would be judged on
        # the box and the face count alone, which the measurement above shows do not determine a
        # shape.
        aa, ab = a.get("area_mm2"), b.get("area_mm2")
        if aa is None or ab is None:
            return False
        if abs(aa - ab) > vol_rel * max(abs(aa), abs(ab), 1e-9):
            return False
    elif abs(va - vb) > vol_rel * max(abs(va), abs(vb), 1e-9):
        return False
    corners = zip(a["bbox"]["min"] + a["bbox"]["max"], b["bbox"]["min"] + b["bbox"]["max"])
    if not all(abs(x - y) <= bbox_abs for x, y in corners):
        return False
    here, there = a.get("center_of_mass"), b.get("center_of_mass")
    if here is None or there is None:
        return True  # an older measurement without it: judged on the three as before
    return all(abs(x - y) <= com_abs for x, y in zip(here, there))


def identical_to_original(ctx: Context, result: ReplayResult, reference: list[dict] | None) -> set[str]:
    """The bodies the replay left unfinished that are, measured, the very body of the original.

    A body is condemned to the fallback when a feature that shapes it could not be rebuilt, and that
    says nothing about whether the body came out right anyway. Measured over the corpus's 13 such
    bodies, one did: `corpus-02`'s `Body2`, 6654.503 mm³ and 14 faces on both sides. Removing
    it threw a native body away for an imported copy of itself (spec 0.4, item 4).

    The original is the oracle's reading, and which of its bodies to compare with is the one
    `pairing.declared_name` names for the Fusion id — a body without one is not compared, because
    against every body it could match the wrong one. The comparison is `same_body` with faces.
    """
    if not reference:
        return set()
    by_name = {r["name"]: r for r in reference}
    standing = set(ctx.engine.body_names())
    candidates = {}
    for body_id in sorted(result.fallback_bodies):
        name = ctx.body_names.get(body_id)
        declared = declared_name(ctx.design, body_id, list(by_name))
        if name in standing and declared is not None:
            candidates[name] = by_name[declared]
    if not candidates:
        return set()
    readings = {b["name"]: b for b in ctx.engine.body_readings(sorted(candidates))}
    return {
        name
        for name, original in candidates.items()
        if name in readings and same_body(readings[name], original, faces=True)
    }


def apply_fallback(
    ctx: Context, result: ReplayResult, step: Path, reference: list[dict] | None = None
) -> list[str]:
    """Swap the bodies the replay could not finish for the original B-rep; record what was imported.

    The names of the imported bodies that stay land in ``result.imported_bodies``; returns warnings.
    `reference` is the oracle's reading of the original: a body it says is already identical to its
    original stays native, and the import's copy of it goes as a duplicate.
    """
    if not result.fallback_bodies:
        return []
    engine = ctx.engine
    # Nothing is warned about a hidden body: `cadmpeg convert` exports every one of them, so the
    # import brings it back like any other. This said the opposite and was simply false.
    warnings: list[str] = []
    present = set(engine.body_names())
    kept_native = identical_to_original(ctx, result, reference)
    partial = sorted(
        ({ctx.body_names[b] for b in result.fallback_bodies if b in ctx.body_names} & present) - kept_native
    )
    if partial:
        engine.execute("solid.remove", {"bodies": partial})
    kept = engine.body_readings()
    kept_names = {b["name"] for b in kept}
    try:
        engine.execute("file.insert_step", {"path": str(step), "name": IMPORT_HOLDER})
    except CommandRefused as exc:
        # This one call *is* the safety net, and it was the only unprotected one in the path, so a
        # refusal here ended the whole conversion with the engine's error and no report at all.
        # Measured on `corpus-07` under solidcraft 0.40.0-rc.3, whose STEP
        # importer refuses the file because 3 of one solid's 242 faces fail their pcurves: the file
        # produced nothing, where under 0.30.1 it converts and verifies (spec section 124).
        #
        # The partial bodies are already removed above, and they should stay removed: a body the
        # replay began and no feature finished is geometry the original never had, so shipping it
        # would read as `extra` where the truth is `missing`. So the design keeps what the replay
        # really built, says what it could not recover in the engine's own words, and goes on to be
        # verified on what it has. `imported_bodies` stays empty, which is what it is.
        warnings.append(
            f"the original could not be imported, so {len(result.fallback_bodies)} body(ies) the "
            f"replay could not finish are missing from the design: {exc.message}"
        )
        return warnings
    bodies = engine.body_readings()
    # What the import left is the difference against what was measured before it, and nothing else
    # happens between those two readings. This used to prefer the import's own `created_bodies` when
    # it answered, and that list is short where the STEP carries nested occurrences: on a real
    # package of the user's, `corpus-09.f3z`, it named 462 of the 494 bodies the design then
    # held, so 32 imported placements were counted as native in the report (spec section 90).
    imported = [b for b in bodies if b["name"] not in kept_names]
    # Two sides of one question, asked against the whole import before anything is removed: the
    # import holds every body of the original, so for each body now in the design there either is a
    # counterpart or there is not.
    #
    # An imported body with a native counterpart is the duplicate — the replay already built that
    # one, so the import goes and the native body stays. A **native** body with no counterpart
    # anywhere in the import is the other case, and it used to stay: it is a body the replay began
    # and no later feature finished, because the features that would have were the ones that fell
    # back. Measured on `corpus-02` under cadmpeg 0.6.7, where the replay left `Body1` at
    # 384.802 mm³ with 3 faces and `Body2` at 10240.000 mm³ with 6 against originals of 22133.318
    # mm³ / 79 faces and 6654.503 / 14 — nothing either one stands for. Shipping them says the
    # design rebuilt to something the original never had, and the verification reported them as
    # `extra`.
    #
    # Condemning the feature's own bodies does not catch these: no failing feature names them. They
    # are only knowable here, by what the import did and did not match.
    duplicates = [b["name"] for b in imported if any(same_body(b, k) for k in kept)]
    surplus = [k["name"] for k in kept if not any(same_body(b, k) for b in imported)]
    if duplicates or surplus:
        try:
            engine.execute("solid.remove", {"bodies": duplicates + surplus})
        except CommandRefused as exc:
            names = ", ".join(duplicates + surplus)
            warnings.append(f"bodies the import superseded not removed ({names}): {exc.message}")
    # What is still standing, counted the way the import was: `body_names` lists a body per
    # definition and `measure` one per placement, and on this package those are 462 against 494, so
    # asking `body_names` here threw away the 32 placements it had just imported. The reading in
    # hand answers it without measuring again — nothing has happened since but the removals above.
    removed = set(duplicates + surplus)
    present = {b["name"] for b in bodies if b["name"] not in removed} | {
        b["name"] for b in kept if b["name"] not in removed
    }
    if surplus_left := [n for n in surplus if n in present]:
        warnings.append(
            f"{len(surplus_left)} body the replay began and no feature finished stayed in the "
            f"design ({', '.join(surplus_left)}): the original has no counterpart for it"
        )
    result.imported_bodies = [b["name"] for b in imported if b["name"] in present]
    return warnings


def name_bodies(ctx: Context, result: ReplayResult) -> list[str]:
    """Give a body the name the designer gave it in Fusion, built or imported; returns warnings.

    A body the replay built carries the engine's own numbering and an imported one carries the STEP
    export's name — `Body1`, `cadmpeg_model (2)` — and neither is the design's. Where the file says
    what the designer called the body, that is what the file the user opens should show.

    Measured over the corpus's 90 bodies, **two** carry a name: `corpus-03`'s `corpus-03
    (dont print)` and `corpus-03 (dont print)`. A real design of the user's,
    `corpus-06.f3d`, carries a third — `RielArriba` — and it is the one that
    made this cover built bodies too: that body is the output of a primitive the mapper now rebuilds
    natively, so a step that only renamed imports would hand it back the engine's `Body1` and lose
    the name it had just been given (spec section 85).

    The other bodies are left exactly as they were named rather than renumbered: the file offers
    nothing to call them, and an invented label would be this converter's word presented as the
    design's (spec section 84).

    Which body is which is not guessed. A body the replay built is in `ctx.body_names` by its own
    Fusion id; an imported one goes through `pairing.declared_name`, the one rule for that question
    and the same one `visibility` asks, which answers `None` wherever the import's block does not
    line up with the file's bodies. A name the engine refuses, or one another body already has, is
    reported and left alone.
    """
    wanted = {b.id: b.name for b in ctx.design.bodies if b.name}
    if not wanted:
        return []
    warnings: list[str] = []
    taken = set(ctx.engine.body_names())
    renamed: dict[str, str] = {}
    for body_id, name in sorted(wanted.items()):
        # The replay's own name for it first, but only while that body is still standing: a body the
        # replay began and the import superseded leaves a stale entry in `body_names`, and taking it
        # would skip the import's counterpart without ever asking the pairing. Measured on `corpus-03`, where exactly that lost `corpus-03 (dont print)` while the body beside it,
        # which the replay never touched, was renamed.
        built = ctx.body_names.get(body_id)
        current = built if built in taken else declared_name(ctx.design, body_id, result.imported_bodies)
        if current is None or current not in taken:
            continue  # nothing standing is this body, or the pairing cannot say which one is
        if current == name:
            continue  # it already carries the name the file gives it: nothing to do and nothing to say
        if name in taken:
            # Another body holds it. Measured on `corpus-12.f3z`, this used to fire for
            # `Lenovo`, `Caja1`, `Tapa` and `Fuente` where the body **already had** the name, because
            # `name in taken` is true of a name a body holds itself — 16 warnings that read as
            # failures and were not (spec section 93).
            warnings.append(
                f"the body Fusion calls {name!r} keeps the name {current!r}: another body holds {name!r}"
            )
            continue
        try:
            ctx.engine.execute("body.rename", {"body": current, "name": name})
        except CommandRefused as exc:
            warnings.append(f"the body Fusion calls {name!r} could not be renamed: {exc.message}")
            continue
        taken.discard(current)
        taken.add(name)
        renamed[current] = name
    # Everything downstream reads these names: the rebuild check, the component reconciliation, the
    # visibility replay and the report's native/imported count.
    result.imported_bodies = [renamed.get(n, n) for n in result.imported_bodies]
    for key, value in list(ctx.body_names.items()):
        if value in renamed:
            ctx.body_names[key] = renamed[value]
    return warnings
