"""What a failed feature condemns: a datum plane that fails takes no solid down with it.

Named for the question and not for the tag, because `test_construction` is about construction
*curves* in a sketch (spec section 94) and this is about construction *geometry features*.

A feature that records no output body is a suspect — it may have been the one that finished any of
the bodies standing, so `_standing` condemns them all and the fallback brings them back from the
original. That rule is right for a feature that *could* have changed a solid, and wrong for one that
cannot hold one at all. `corpus-01` paid for the difference: its `WorkPlane` at ordinal 11
has no resolved frame, and refusing it threw away the body the eleven exact features before it had
built (spec section 119).
"""

import os
from pathlib import Path

import pytest

from f3d2solidcraft.mappers import Context
from f3d2solidcraft.model import Design, Feature
from f3d2solidcraft.replay import CONSTRUCTION, _suspect

# The same default `conftest` uses; the file path is wanted here and not only the design, because
# the oracle needs the original's STEP.
CORPUS = Path(os.environ.get("F3D2SC_CORPUS", "~/.f3d2solidcraft/corpus/propios")).expanduser()


def test_the_set_is_the_labels_the_corpus_really_carries():
    """Three tags, and the Spanish one is in because the decoder cannot rewrite it.

    `reader.locale` rescues a Spanish label only where an English alias is the same UTF-16 length,
    which `PlanoDeObra` is not, so it reaches the replay untranslated and has to be named here.
    A `Lienzo` is absent on purpose: it raises `NoGeometry` and never reaches this rule.
    """
    assert CONSTRUCTION == {"WorkPlane", "PlanoDeObra", "WorkAxis"}


def test_a_feature_that_is_not_construction_still_condemns_what_is_standing():
    """The rule it has to leave alone: anything that could have changed a solid.

    `_suspect` only decides *whether* to ask, so a plain feature is checked against an engine-less
    context that would raise if it were asked — proving the question is still being put.
    """
    design = Design(source="", parameters=[], sketches={}, features=[], bodies=[])
    ctx = Context(None, design, {}, {}, {}, {}, {})

    def feature(tag):
        return Feature(
            ordinal=1, name=f"{tag} 1", tag=tag, definition_kind="native", suppressed=False, outputs=()
        )

    assert _suspect(feature("WorkPlane"), ctx) == ()
    with pytest.raises(AttributeError):  # reached the engine, which is None here
        _suspect(feature("Shell"), ctx)
