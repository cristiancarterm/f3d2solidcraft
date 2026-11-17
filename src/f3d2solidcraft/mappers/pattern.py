"""Pattern: a mirror or an array of a seed. Read in full, and refused by what the file leaves out."""

from __future__ import annotations

from f3d2solidcraft.mappers import Context, Unsupported, definition, register
from f3d2solidcraft.model import Feature


@register("Mirror")
@definition("pattern")
def map_pattern(feature: Feature, ctx: Context) -> list[str]:
    """Refuse a pattern by the half of it the file does not state, and say which half that is.

    The corpus holds one — `Caja parametrica v2`'s `Mirror 4`, ordinal 33 — and it is the clearest
    *half* recipe in the file. The seed is `resolved` and names a body id; the pattern itself comes
    back `{"kind": "unresolved", "form": "mirror"}`. So the file says it is a mirror and does not say
    about what plane, and a plane is not guessable: a mirror built about the wrong one is a body in
    the wrong place, reported as a rebuild.

    There is no mapper-shaped gap behind this, which is the whole reason the mapper exists:
    `replay._unmapped` said *"no mapper for Mirror (cadmpeg decoded it as pattern)"*, which reads as
    this converter's work still to do. It is not — sections 109 and 113 made the same point for
    `Combine` and for the loft.

    And **the seed is out of reach too**, which the refusals below say in order. Measured on that
    feature (spec section 116):

    | | |
    | --- | --- |
    | the seed id | `…smbh/brep:entity#4` |
    | what produces it in the file | `Combine 3`, **ordinal 46** — thirteen later |
    | `ctx.body_names` at ordinal 33 | **empty**: 11 features have already fallen |
    | that body at ordinal 33 | **already condemned** |

    So an operand the file calls `resolved` is not the same as one the replay can reach. The plane is
    named first all the same, because it is the half that is missing from the *file* — the seed is
    missing from this *run*, and a file whose earlier features all rebuilt would have it.
    """
    pattern = feature.pattern
    if pattern is None:
        raise Unsupported(
            f"cadmpeg decoded this pattern as {feature.definition_kind!r}, so the file hands over "
            "neither a seed nor a pattern to rebuild it from"
        )
    if pattern.pattern_kind != "resolved":
        raise Unsupported(
            f"the file says this is a {pattern.form} pattern and resolves nothing of the pattern "
            f"itself ({pattern.pattern_kind!r}), so there is no plane, axis or count to build it "
            "about — and none of those is guessable"
        )
    if pattern.seed_kind != "bodies" or not pattern.seed_bodies:
        raise Unsupported(
            f"this {pattern.form} pattern takes its seed as {pattern.seed_kind!r}, which names no "
            "body of the design"
        )
    missing = [b for b in pattern.seed_bodies if b not in ctx.body_names]
    if missing:
        raise Unsupported(
            f"the {len(missing)} seed body(ies) of this {pattern.form} pattern were not rebuilt, so "
            "there is nothing here to pattern"
        )
    # Nothing in the corpus reaches here: the one pattern it holds is refused by its own `pattern`
    # field above. A `resolved` pattern is where the geometry to build would be read from, and it is
    # deliberately not written blind — there is no case to measure it against (spec section 116).
    raise Unsupported(
        f"this {pattern.form} pattern resolves its seed and its pattern, which no case in the "
        "corpus does, so the geometry to build it from has never been read"
    )
