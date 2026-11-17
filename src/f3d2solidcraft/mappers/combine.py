"""Combine: read in full, rebuilt never — and the arithmetic that looked like it would work.

The file resolves a Combine's target and leaves its tools opaque: a `native_set` of design-records
that nothing else in the model resolves, so which bodies the tools are cannot be read. What **can**
be read is how many there are, and the census of section 108 noticed that for two of the corpus's
four Combines that count closes against the bodies of the kernel state the feature took as input:

| | target | tools | bodies of the state | |
| --- | --- | --- | --- | --- |
| `Combine 5` Caja ord 25 | 1 | 2 | 3 | adds up |
| `Combine 2` Caja ord 27 | 1 | 2 | 3 | adds up |
| `Combine 3` Caja ord 46 | 1 | 2 | 5 | 2 left over |
| `Combine 3` corpus-03 ord 59 | 1 | 1 | 8 | 6 left over |

Where it adds up there seemed to be nothing left to choose — three bodies in the state, one the
target, two tools, and one way to take 2 of 2 — and a join does not even care which of its bodies
is called the target, so the one thing the file does not say costs nothing. It was built that way
and then measured, and **the measurement refused it** (spec section 109): joining the two bodies
the count leaves over moves `Caja parametrica v2`'s target not one bit closer to the reference body
it stands in for — **2801.398 mm³ of `Bottom` uncovered before the join and 2801.398 mm³ after**, to
the digit. Those two bodies contribute nothing to that body. They are not its tools.

So the count agreeing was the coincidence of small numbers that section 108 warned about for the
other two and then walked into for these. It cannot be read as "the state's other bodies are the
tools", and the timeline cannot settle it either: cadmpeg hands over 37 of that file's 53 features,
and ordinal 26 — between the two Combines — is one of the missing, so what stands in the engine
cannot be reconciled with the file's state by reading alone.

This mapper is therefore all refusals, and that is the point of it: the report used to say "no
mapper for Combine (cadmpeg decoded it as combine)", which puts the family in the pile of mappers
still to write. It is not in that pile. The recipe is read in full and reported, so what the file
says is on the record, and each refusal names the thing that is actually missing.
"""

from __future__ import annotations

from f3d2solidcraft.mappers import Context, Unsupported, definition, register
from f3d2solidcraft.model import Feature


@register("Combine")
@definition("combine")
def map_combine(feature: Feature, ctx: Context) -> list[str]:
    """Refuse this combine, naming what the file does not say about it."""
    combine = feature.combine
    if combine is None:
        raise Unsupported(
            f"cadmpeg decoded this combine as {feature.definition_kind!r}, so the file hands over "
            "no operands to rebuild it from"
        )
    if combine.target_kind != "resolved_set":
        raise Unsupported(
            f"this combine names its target as {combine.target_kind!r}, which is an id of the "
            "feature's own kernel state and not a body of the design"
        )
    if combine.state_bodies is None:
        raise Unsupported(
            f"this combine's {combine.tool_count} tools are an opaque native set and the file names "
            "no input state for it, so there is nothing to count them against"
        )
    accounted = combine.target_count + combine.tool_count
    if accounted != combine.state_bodies:
        raise Unsupported(
            f"this combine takes {combine.target_count} target and {combine.tool_count} tools out "
            f"of the {combine.state_bodies} bodies of its state, and which "
            f"{combine.state_bodies - accounted} of the rest it leaves alone is not in the file"
        )
    raise Unsupported(
        f"this combine's {combine.tool_count} tools are an opaque native set; they would be the "
        f"other {combine.state_bodies - combine.target_count} bodies of its state if the count "
        "named them, and measuring that against the original showed it does not — joining them "
        "leaves exactly as much of the reference body uncovered as before (spec section 109)"
    )
