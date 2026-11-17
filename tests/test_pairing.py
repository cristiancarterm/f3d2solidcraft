"""The pairing rule: what the file says a body becomes, and when it says nothing.

The reference names here are what cadmpeg really wrote for these files, read off the STEP it
exports. No engine and no mock: the rule is a translation between two things the files already say,
and these pin it against the real thing on both sides.

Three of the rule's refusals have **no file in the corpus**, measured over all twelve: no design
names two components alike, no design keeps a body in its unnamed root component (every body is
either in a named component or in none at all), and in the three designs that have components the
named ones account for every body, so nothing ever falls to the root's name with more than one base
name left over. Those three are reached here by changing **one field** of a real design — a
component's name, or which components the design declares — and never by inventing a design or a
reference. They are guards for shapes Fusion allows and this corpus happens not to have, which is
exactly why they should not be left unexercised.
"""

import dataclasses

from f3d2solidcraft.pairing import declared_reference

# What `file.insert_step` really reported for each file's original, in its order.
# `Caja parametrica v2` used to be this file's multi-body case: cadmpeg 0.6.7 exported ten bodies
# for it, `Top` through `Top (7)` among them, and its `Extrude 10` declared one. On 0.6.8 the same
# design exports **three** — one per component — and `Extrude 10` declares no output at all, so
# every question below would be answered `None` by default and three of these guards would pass
# without being reached. `corpus-03` is the multi-body holder now: `corpus-03` holds
# five of its seven bodies (spec section 95).
SAMPLE_REFERENCE = ["cadmpeg_model", "cadmpeg_model (2)", "cadmpeg_model (3)"]


def _named(design, name: str):
    return next(f for f in design.features if f.name == name)


def test_it_says_nothing_when_the_holder_block_does_not_match(design):
    """`test weird box` declares 5 bodies where its STEP carries 3, so the rule does not hold here.

    This is the check that keeps a name which merely happens to be in the list from passing for the
    rule holding. Without it the first two bodies would pair off `cadmpeg_model` and
    `cadmpeg_model (2)` on nothing but their position, and the third would go unnamed.
    """
    assert len(design.bodies) == 5, design.bodies
    assert all(declared_reference(design, f, SAMPLE_REFERENCE) is None for f in design.features if f.outputs)


def _with_components(design, components):
    return dataclasses.replace(design, components={c.id: c for c in components})
