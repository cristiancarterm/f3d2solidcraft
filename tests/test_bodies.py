"""Delete, split and paste of bodies: the operation is in the file and the bodies are not.

Five features in the corpus across three families cadmpeg types. None of them names a body this
converter can reach, so all three mappers only refuse — and what is under test is that each refusal
blames the right field and says what the file *does* carry (spec section 117).
"""

import pytest

from f3d2solidcraft.mappers import BY_DEFINITION, MAPPERS, Context, Unsupported
from f3d2solidcraft.model import Design, Feature


def _op(design, ordinal):
    return next(f for f in design.features if f.ordinal == ordinal).body_op


# The two splits are two tests and not one: `corpus_ctx` dumps into the test's own `tmp_path`, so
# opening two designs in one test makes the second `cadmpeg dump` land on the first one's file.


def test_a_body_operation_cadmpeg_left_untyped_is_refused_for_having_no_bodies():
    """A feature routed to one of the three whose definition carries nothing."""
    feature = Feature(
        ordinal=1, name="Split 1", tag="Split", definition_kind="native", suppressed=False, outputs=()
    )
    design = Design(source="", parameters=[], sketches={}, features=[feature], bodies=[])
    with pytest.raises(Unsupported, match="hands over no bodies to work on"):
        MAPPERS["Split"](feature, Context(None, design, {}, {}, {}, {}, {}))


def test_the_three_families_are_reachable_from_their_definitions():
    """Which pile each one is reported in — the point of a mapper that only refuses (§109)."""
    assert BY_DEFINITION["delete_body"] is MAPPERS["RemoveBody"]
    assert BY_DEFINITION["split_body"] is MAPPERS["Split"]
    assert BY_DEFINITION["insert_bodies"] is MAPPERS["CopyPasteBodies"]
