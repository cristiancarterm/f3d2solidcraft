"""What a feature's result is called, shared by every mapper that builds or changes a body.

Two questions every such mapper has to answer the same way, and which `extrude.py` answered first:
what a join or a cut does when the file names no body for it, and which of the file's output ids
name the bodies that were actually built. Kept here so a second mapper cannot answer them differently — the reasons
in the comments were measured on the corpus and belong to the question, not to extrudes.
"""

from __future__ import annotations

from f3d2solidcraft.mappers import Context, Unsupported
from f3d2solidcraft.model import Feature


def no_target(ctx: Context, operation: str) -> Unsupported:
    """The refusal for a join or cut whose file names no body of its own.

    With no body to name as the target, the engine reaches for whatever body it finds: measured on
    `corpus-02`, `Extruir 18` records no output at all and was reported `exact` while adding
    its 40.98 mm³ to the body of a different Fusion body. The bodies it could have changed are
    condemned with it, or refusing it would lose its material with nothing to attribute an import to
    — and only those: the ones standing in the design right now, which is what the engine had to
    choose from. `body_names` holds every body ever built, and condemning all of them took bodies
    down that this feature could not have touched any more.

    Names, not measurements: a `measure` recomputes every body's volume, area, faces and centre of
    mass and only names are wanted here. On a 74.8 MB package of the user's, 1440 bodies, that is
    **52 s** against the **0.0 s** `body_names` takes — and this runs per feature, which is where
    its 81 minutes went (spec section 92). Every name asked about is one the replay built, a
    definition in the design, so the definition listing answers it; the placements `measure` adds
    beyond it are an import's instances, which are never the replay's.
    """
    live = set(ctx.engine.body_names())
    return Unsupported(
        f"this {operation} records no body of its own, so which of the "
        f"{sum(name in live for name in ctx.body_names.values())} bodies here it changes is "
        "not known",
        bodies=tuple(key for key, name in ctx.body_names.items() if name in live),
    )


def name_outputs(feature: Feature, ctx: Context, out: dict, warnings: list[str]) -> None:
    """Give the file's output ids the names of the bodies that were built, and say what did not fit.

    A body named here is a body a later feature can find: the file's id is how the timeline refers
    to it, so an output left unnamed is a body nothing downstream can touch.
    """
    touched = list(out.get("created_bodies") or []) + list(out.get("changed_bodies") or [])
    for body_id, name in zip(feature.outputs, touched):
        ctx.body_names.setdefault(body_id, name)
    # What the file has no id for. Kept so the next feature can still find it: see `orphan_bodies`.
    ctx.orphan_bodies = tuple(touched[len(feature.outputs) :])
    if len(touched) < len(feature.outputs):
        warnings.append(f"{len(feature.outputs)} output bodies in Fusion, {len(touched)} touched here")
    elif len(touched) > len(feature.outputs):
        # The other direction, which went unsaid: `zip` above pairs the file's outputs with the first
        # of what was built and the rest are orphaned — no later feature can ever name them, because
        # the file has no id for them. Measured on `corpus-02`'s `Extruir 11`, which declares
        # one body and builds two (384.802 mm³ and 10240.0): the 384.802 one takes the output's name
        # and the other is left standing, untouched for the rest of the timeline.
        #
        # It is a warning and nothing else. §56 built both of the responses it suggests — routing the
        # feature to the measurement, and refusing it — and measured both to be worse: either one
        # takes away the overlap that proves the *next* extrude's index wrong, and the body that
        # reproduces the original exactly today goes to 26018.583 mm³ outside it. So this says what
        # happened and changes nothing.
        orphans = ", ".join(touched[len(feature.outputs) :])
        warnings.append(
            f"{len(touched)} bodies built here, {len(feature.outputs)} declared in Fusion: {orphans} "
            "is left with no output of the file to name it, so nothing later can touch it"
        )
    if out.get("warning"):
        warnings.append(str(out["warning"]))
