"""Delete, split and paste of whole bodies: the operation is in the file and the bodies are not.

Three families cadmpeg types, five features in the corpus, and not one of them names a body this
converter can reach. They are here together because the report used to say *"no mapper for X"* for
all three, which reads as this converter's work still to do — and it is not: the recipe is read in
full and what is missing is in the file. Sections 109, 113 and 116 made the same point for
`Combine`, the loft and the `Mirror`; this is the same move for the rest of them (spec section 117).
"""

from __future__ import annotations

from f3d2solidcraft.mappers import Context, Unsupported, definition, register
from f3d2solidcraft.model import Feature


def _recipe(feature: Feature, family: str) -> object:
    """The body operation the reader parsed, or a refusal naming the decoder."""
    op = feature.body_op
    if op is None:
        raise Unsupported(
            f"cadmpeg decoded this {family} as {feature.definition_kind!r}, so the file hands over "
            "no bodies to work on"
        )
    return op


@register("RemoveBody")
@definition("delete_body")
def map_delete_body(feature: Feature, ctx: Context) -> list[str]:
    """Refuse a delete by the bodies it does not name.

    Both of the corpus's (`corpus-03`, ordinals 51 and 53) state the mode — `delete_selected` —
    and name the bodies as a `native` operand group: one opaque id, no body list. So *that* it
    deletes a selection is in the file and *which* selection is not, and deleting the wrong body is
    not a near miss.

    Worth saying for whoever reads this next: a delete is the one family where the result could in
    principle be read off the original instead, since a body the design does not end with is a
    candidate. That is not done, and the reason is in section 92's rule: the bodies that end up
    missing are already brought back by the fallback import, so a guess here would trade a body the
    user has for a body we picked.
    """
    op = _recipe(feature, "delete")
    raise Unsupported(
        f"the file names the bodies to delete as a {op.operand_kind!r} operand group and lists "
        f"{op.operand_bodies} of them, so which bodies this "
        f"{op.mode or 'delete'} removes is not in the file"
    )


@register("Split")
@definition("split_body")
def map_split_body(feature: Feature, ctx: Context) -> list[str]:
    """Refuse a split by its target, and say what its tool is while doing it.

    `solid.split_body` takes `body` and a plane — by name, which is how this converter builds a
    construction plane — so the two corpus cases are one step apart from each other:

    | | target | tool |
    | --- | --- | --- |
    | `corpus-03` ordinal 55 | `historical` | `faces`, also `historical` |
    | `corpus-07…` ordinal 4 | `historical` | **`plane`**, a feature id of the design |

    The target is what both are missing, so it is what the refusal blames (section 104). The tool is
    named in the same sentence because it is the difference between them: the corpus-07's split needs
    one thing, the corpus-03's needs two.

    And on the corpus-07's, measured at its own ordinal: there is **no body standing at all** — the
    `Base Feature` before it carries B-rep and no recipe, so `body_names` is empty and both body ids
    are already condemned. Its `WorkPlane 1` is `exact`. The plane is there and there is nothing to
    cut (spec section 117).
    """
    op = _recipe(feature, "split")
    tool = {
        "plane": "its tool is a plane the file names by feature id, which `solid.split_body` takes by name",
        "faces": "its tool is a set of faces the file names by kernel identity, which nothing here "
        "resolves either",
    }.get(op.tool_kind, f"its tool is {op.tool_kind!r}")
    raise Unsupported(
        f"the target of this split is a {op.operand_kind!r} body id, which names a state of the "
        f"kernel and no body of the design; {tool}"
    )


@register("CopyPasteBodies")
@definition("insert_bodies")
def map_insert_bodies(feature: Feature, ctx: Context) -> list[str]:
    """Refuse a paste of bodies by the list the file calls resolved and does not fill.

    The corpus's one (`corpus-03` ordinal 56) states `bodies: {kind: "resolved", value: {native:
    …}}`: the word is `resolved` and what is under it is a single opaque id, with no body among
    them. That is section 116's lesson a second time — `resolved` in the file is a claim about the
    file's own record, not about anything this converter can reach.
    """
    op = _recipe(feature, "paste of bodies")
    raise Unsupported(
        f"the file calls this paste's body list {op.operand_kind!r} and lists {op.operand_bodies} "
        "bodies under it, so which bodies are pasted is not in the file"
    )
