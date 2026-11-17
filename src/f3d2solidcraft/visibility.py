"""Hide the bodies Fusion hides, so the converted design opens looking like the original.

A Fusion design routinely carries bodies the designer has hidden: earlier states of a part,
alternatives, the pieces a later feature consumed. `Caja parametrica v2` has **ten bodies and shows
three** — the other seven are whole case halves sitting in the same place as the ones it shows.

Nothing of this is geometry, so the verification never saw it: `compare` deliberately holds nothing
back for being hidden (§29 measured that the STEP carries every body, hidden ones included), and it
passed this design with ten matched bodies, zero missing and zero extra while what opened in
SolidCraft was three visible solids' worth of overlap. The report said the truth and the design
still looked wrong, which is the one kind of defect the net here cannot catch by measuring volume.

So the file's own answer is replayed: `Body.visible` is what cadmpeg read, and a body it calls
hidden is hidden here too. Hiding changes no measurement — asked of the engine, `measure` returns a
hidden body and the same total volume — so the verification keeps comparing every body either way.

**Sketches are the same story and a larger one.** Fusion hides a sketch once it has been used, so
measured over the corpus **86 of its 88 sketches are hidden** — 12 of 12 in `Caja parametrica v2`,
29 of 29 in `corpus-03` — and showing them all means opening a design with every sketch drawn over
the geometry. These need no pairing: the replay built them and `ctx.sketch_names` already holds each
one's name in the engine.
"""

from __future__ import annotations

from f3d2solidcraft.engine import CommandRefused
from f3d2solidcraft.mappers import Context
from f3d2solidcraft.pairing import declared_name
from f3d2solidcraft.replay import ReplayResult


def hide_what_fusion_hides(ctx: Context, result: ReplayResult) -> list[str]:
    """Hide every final body and sketch the file calls hidden; returns warnings."""
    return _hide_bodies(ctx, result) + _hide_sketches(ctx)


def _hide_sketches(ctx: Context) -> list[str]:
    """Hide the sketches the file hides. Only those the replay built exist to hide."""
    names = [
        name
        for sketch_id, sketch in ctx.design.sketches.items()
        if not sketch.visible and (name := ctx.sketch_names.get(sketch_id)) is not None
    ]
    if not names:
        return []
    try:
        ctx.engine.execute("browser.visibility", {"visible": False, "items": names})
    except CommandRefused as exc:
        return [f"the {len(names)} sketch(es) Fusion hides could not be hidden: {exc.message}"]
    return []


def _hide_bodies(ctx: Context, result: ReplayResult) -> list[str]:
    """Hide every final body the file calls hidden; returns warnings.

    A body the replay built is named in `ctx.body_names`. One that came back with the import is
    named by the same rule `standing_for` pairs with — the holder's name, numbered from its second
    body — which is read off the import's own names, so a body the rule cannot place unambiguously
    is reported and left visible rather than guessed at.
    """
    hidden = [b.id for b in ctx.design.bodies if not b.visible]
    if not hidden:
        return []
    imported = list(result.imported_bodies)
    present = set(ctx.engine.body_names())
    names: list[str] = []
    unplaced = 0
    for body_id in hidden:
        name = ctx.body_names.get(body_id) or declared_name(ctx.design, body_id, imported)
        if name is None or name not in present:
            unplaced += 1
            continue
        names.append(name)
    warnings = []
    if unplaced:
        warnings.append(
            f"{unplaced} of the {len(hidden)} body(ies) Fusion hides could not be told apart in the "
            "design, so they stay visible"
        )
    if not names:
        return warnings
    try:
        out = ctx.engine.execute("browser.visibility", {"visible": False, "items": names})
    except CommandRefused as exc:
        return [*warnings, f"the {len(names)} body(ies) Fusion hides could not be hidden: {exc.message}"]
    # The engine answers with what it hid, so the ask is checked rather than assumed.
    hid = (
        set((out or {}).get("visibility", {}).get("hidden_bodies") or []) if isinstance(out, dict) else set()
    )
    missed = [name for name in names if name not in hid]
    if missed:
        warnings.append(
            f"the engine did not hide {len(missed)} body(ies) it was asked to: {', '.join(missed)}"
        )
    return warnings
