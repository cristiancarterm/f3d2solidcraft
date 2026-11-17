"""Features that hold no recipe: B-rep a base feature carries, and a canvas that is only an image."""

from __future__ import annotations

from f3d2solidcraft.mappers import Context, NoGeometry, Unsupported, definition, register
from f3d2solidcraft.model import Feature


@register("Base Feature")
@definition("base_feature")
def map_base_feature(feature: Feature, ctx: Context) -> list[str]:
    """Refuse a base feature in its own words, so its bodies come from the original.

    Fusion's base feature is where direct modelling lives: it carries finished B-rep and no history,
    so there is no recipe to replay — `corpus-10` is one of these and nothing else, three
    bodies with no timeline behind them. The fallback import was always the only possible outcome and
    already was what happened; this only replaces "no mapper for Base Feature" with the reason.
    """
    raise Unsupported(
        f"a base feature carries B-rep and no recipe, so its {len(feature.outputs)} body(ies) come "
        "from the original"
    )


@register("Lienzo")
@register("Canvas")
def map_canvas(feature: Feature, ctx: Context) -> list[str]:
    """A canvas is a reference image placed on a plane: no geometry, and nothing failed.

    Reported `failed` it read as something that should have worked, and it sat at ordinal 0 of
    `soporte controles` among four failures that are real gaps. It has no output body, so it condemns
    nothing either.
    """
    raise NoGeometry("a canvas is a reference image on a plane: it makes no geometry")


@register("Component Insert")
def map_component_insert(feature: Feature, ctx: Context) -> list[str]:
    """Refuse the insert in its own words: what it places is built from the design's occurrences.

    Fusion records inserting a component as a timeline feature, and `corpus-08`
    has one for the member cadmpeg merges out of its `.f3z`. There is nothing to replay: the
    component and its placement come from the design's occurrence list, which the replay builds
    before the timeline, and its bodies come back with the rest of the import. Reported `failed` as
    "no mapper for Component Insert" it read like geometry we lose, which is not what happens —
    this says what it is instead, and claims nothing about having rebuilt it.
    """
    raise Unsupported(
        "a component insert places a component the design's occurrences already build, so it "
        "carries no recipe of its own"
    )
