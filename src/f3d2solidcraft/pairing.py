"""Which reference body the file says a native body becomes.

`Oracle.standing_for` can measure that a candidate body contains a reference body, but on an
original with several, containment alone does not say which one the candidate stands in for: a body
mid timeline often contains more than one of them. Measured on `corpus-02`, the body
`Extruir 12` builds contains **both** reference bodies, and measurement has nothing left to choose
with — which is where that file's whole rebuild stopped.

The file does say. A feature names the bodies it outputs, and the design records which component
holds each body and in what order; cadmpeg writes the STEP's body names from exactly that — the
holder's name, with ` (n)` from the second body of a holder onward. Measured on the corpus:
`Caja parametrica v2`'s components `Bottom`/`Latch`/`Top` (1, 2 and 7 bodies) come back as
`Bottom`, `Latch`, `Latch (2)`, `Top` … `Top (7)`, and `corpus-02`, which has no components
at all, comes back as `cadmpeg_model`, `cadmpeg_model (2)`.

Nothing here concludes anything. This proposes one name; `standing_for` uses it only once it has
measured that the candidate really does contain it, and surveys as before when it does not. So a
proposal can be wrong without costing soundness — measured on `corpus-03`, whose component
`BODY` is named `cadmpeg_model` in the STEP, so the name proposed for its body matches nothing and
that body's features fall back to the survey. What a confirmed proposal buys is one comparison in
place of one per reference body, and an answer where the survey has none.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from f3d2solidcraft.model import Component, Design, Feature

# cadmpeg's suffix for the second body of a holder onward: `Top (2)`.
_SUFFIX = re.compile(r" \(\d+\)$")


def _base(name: str) -> str:
    return _SUFFIX.sub("", name)


def _holder(design: Design, body_id: str) -> Component | None:
    return next((c for c in design.components.values() if body_id in c.bodies), None)


def _root_base(design: Design, reference: Sequence[str]) -> str | None:
    """The base name cadmpeg gave the bodies no named component holds.

    Read off the reference itself — the one base name there that no component accounts for — rather
    than from a default this converter would be guessing at. `None` unless exactly one is left over,
    because with none or several there is nothing a name could be built from.
    """
    named = {c.name for c in design.components.values() if c.name}
    left = {_base(name) for name in reference} - named
    return next(iter(left)) if len(left) == 1 else None


def declared_reference(design: Design, feature: Feature, reference: Sequence[str]) -> str | None:
    """The reference body the file says this feature's output becomes, or None when it says nothing.

    `None` wherever the file is not unambiguous: a feature with no outputs or several, a body whose
    holder shares its name with another component, or a holder whose block of names the reference
    does not carry exactly. A caller gets either one name worth measuring or nothing to measure.

    **The holder's whole block is checked, not just the one name.** The rule above is read off three
    files, so a single name that happens to be there is thin evidence for it; that the reference
    carries `Name` through `Name (k)` for a holder of exactly k bodies is the rule holding on this
    file. It is free — no engine call — and it is what rejects `corpus-03`'s `BODY`, whose one
    body the STEP calls `cadmpeg_model` instead.

    What the check cannot reach is the **order** within a holder: that body #2 of `Top` is `Top (2)`
    rather than `Top (3)` is the rule's assumption, and only `standing_for`'s containment
    measurement stands between a wrong order and a wrong pairing. Where containment holds for
    several reference bodies that measurement cannot separate them, so there the file's order is
    what decides — recorded in spec §54 as the one thing here taken on the file's word.
    """
    if len(feature.outputs) != 1:
        return None
    (body_id,) = feature.outputs
    return declared_name(design, body_id, reference)


def declared_name(design: Design, body_id: str, reference: Sequence[str]) -> str | None:
    """The reference body this body is, by the rule above, or None where the file is not unambiguous.

    The rule reads off a body, not off a feature: `declared_reference` is the feature's one output
    passed through here, and anything else that has to put a Fusion body next to its name in the
    import — replaying which bodies Fusion hides, say — asks the same question of the same rule
    rather than of a second copy of it.
    """
    holder = _holder(design, body_id)
    if holder is not None and holder.name:
        if sum(1 for c in design.components.values() if c.name == holder.name) > 1:
            return None  # two holders of one name: which set of names is whose is not defined
        bodies, base = list(holder.bodies), holder.name
    else:
        base = _root_base(design, reference)
        if base is None:
            return None
        bodies = [b.id for b in design.bodies if _holder(design, b.id) is None]
    if body_id not in bodies:
        return None
    block = [base if i == 0 else f"{base} ({i + 1})" for i in range(len(bodies))]
    if sum(1 for name in reference if _base(name) == base) != len(bodies):
        return None  # the holder's block is not what the reference carries: the rule does not hold here
    if not set(block) <= set(reference):
        return None
    return block[bodies.index(body_id)]
