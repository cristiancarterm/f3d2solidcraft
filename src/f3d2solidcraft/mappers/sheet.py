"""Sheet metal: the base flange, which is where a sheet metal body is born.

What the corpus holds, re-measured on cadmpeg 0.6.11: one file has sheet metal at all,
`corpus-12.f3z`, with 4 `BaseFlange` and 19 `EdgeFlange`. The 4 base flanges all
resolve to the same readable shape and are rebuilt here. **None of the 19 edge flanges is
reachable**, and that was measured rather than assumed: 7 carry no definition at all (cadmpeg hands
them back `native`), and every one of the 12 it types stands behind an edge flange of the first
kind, a Mirror, an R-Pattern or a Move that this converter cannot rebuild, so their body is an
import before they are reached. A mapper for them would refuse all 19 today. (Under 0.6.8 this file
read 3 and 14 — spec section 136.)

The thickness does not go to the feature. `sheet.flange` refuses it in those words — "the sheet
metal rule sets it" — so a rule is made for each thickness the file states and passed by name. The
rule also carries a bend radius and a K-factor that the file says nothing about here, and for a
base flange nothing depends on them: the body is a flat plate, measured at exactly length x width
x thickness. They would matter to a flange bending off it, which is the family above.
"""

from __future__ import annotations

from f3d2solidcraft.mappers import Context, Unsupported, definition, register
from f3d2solidcraft.mappers.outputs import name_outputs
from f3d2solidcraft.model import Feature

# Which way the file says the material goes from the sketch's plane, and whether `sheet.flange`
# needs `flip` to build it that way. Measured on all 3 in the corpus: `forward` is along the
# plane's normal, which is what the engine builds with no flip. The opposite word is not here
# because the corpus never says it, and inventing its spelling would be inventing the side.
_SIDES = {"forward": False}


def rule_for(thickness: float, ctx: Context) -> str:
    """The name of a sheet metal rule of this thickness, made once per design.

    `sheet.manage_rules` takes a name "new or existing", so calling it twice would be harmless;
    it is still remembered, because each call is a step in the design being written and repeating
    it would put the same rule in the timeline once per flange.
    """
    spelled = f"{thickness:.10g} mm"
    name = ctx.sheet_rules.get(spelled)
    if name is None:
        name = f"f3d {spelled}"
        ctx.engine.execute("sheet.manage_rules", {"name": name, "thickness": spelled})
        ctx.sheet_rules[spelled] = name
    return name


@register("BaseFlange")
@definition("sheet_metal_base_flange")
def map_base_flange(feature: Feature, ctx: Context) -> list[str]:
    """Rebuild a base flange: the whole sketch, given the thickness the file states.

    `exact`, and for the same reason an extrude or a revolve of a whole sketch is (sections 95 and
    100): the profile kind is `sketch`, which names the sketch itself with no index and no loop
    list, so there is no region to work out and nothing to infer. The thickness and the side are
    read from the file, and everything else the engine needs is refused rather than guessed.
    """
    flange = feature.base_flange
    if flange is None:
        raise Unsupported(
            f"cadmpeg decoded this base flange as {feature.definition_kind!r}, so the file hands "
            "over no profile or thickness to rebuild it from"
        )
    if flange.profile_kind != "sketch":
        raise Unsupported(f"base flange profile kind {flange.profile_kind}")
    if flange.sketch is None:
        raise Unsupported("the file names no sketch for this base flange's profile")
    if flange.thickness is None or flange.thickness <= 0:
        raise Unsupported(f"the file states no usable thickness for this base flange: {flange.thickness!r}")
    if flange.side not in _SIDES:
        raise Unsupported(
            f"this base flange puts its material {flange.side!r} of its sketch, and the only side "
            "this converter has measured is 'forward', along the plane's normal"
        )
    sketch_name = ctx.sketch_names.get(flange.sketch)
    if sketch_name is None:
        raise Unsupported("its sketch was not rebuilt")
    if flange.sketch in ctx.partial_sketches:
        # Same reason as an extrude's (spec section 87): the sketch is in the design but short an
        # entity this converter cannot draw, so thickening it would thicken a profile Fusion did
        # not have.
        raise Unsupported("its sketch is missing an entity this converter cannot draw")
    params = {
        "type": "base",
        "sketch": sketch_name,
        "profiles": "all",
        "rule": rule_for(flange.thickness, ctx),
        "name": feature.name,
    }
    if _SIDES[flange.side]:
        params["flip"] = True
    warnings: list[str] = []
    out = ctx.engine.execute("sheet.flange", params)
    name_outputs(feature, ctx, out, warnings)
    return warnings
