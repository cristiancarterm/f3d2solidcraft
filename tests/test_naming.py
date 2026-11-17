"""The names the design opens with.

A sketch created without a name gets the engine's own numbering — `Sketch1`, `Sketch2`, no space —
so the names Fusion shows were simply lost. What the designer sees in Fusion's browser is the
**feature's** label (`Sketch 1`, `Boceto 1`, with the space), which is also what the report calls
the feature; the sketch record's own `name` is an internal id and spec section 72 ruled it out.
"""

import os
from pathlib import Path

import pytest

from f3d2solidcraft.cli import main
from f3d2solidcraft.engine import Engine

CORPUS = Path(os.environ.get("F3D2SC_CORPUS", "~/.f3d2solidcraft/corpus/propios")).expanduser()


def _sketch_names(tools, design: Path) -> list[str]:
    with Engine(tools.solidcraft_cli, [design.parent]) as eng:
        eng.call("open", {"path": str(design)})
        return [str(s.get("name")) for s in eng.call("inspect_design", {"measure": False})["sketches"]]


def test_the_sketches_carry_the_names_the_file_gives(sample_f3d, tools, tmp_path):
    """The sample's eight sketches are `Sketch 1` … `Sketch 8` **with the space**.

    The space is the whole test: the engine's own numbering writes `Sketch1`, so a run that lost the
    file's names would pass a looser assertion on this file by coincidence.
    """
    out = tmp_path / "box.solidcraft"
    assert main(["convert", str(sample_f3d), "-o", str(out)]) == 0
    assert sorted(_sketch_names(tools, out)) == sorted(f"Sketch {n}" for n in range(1, 9))


def test_a_repeated_name_is_numbered_rather_than_shared(tools, tmp_path):
    """`Caja parametrica v2` names three sketches `Sketch 1` and two `Sketch 3`, one per
    configuration, and the engine would take the repeat without complaint.

    It must not: `_engine_profiles` finds a sketch **by name**, so two sketches under one name would
    have a later feature read the wrong one's regions. The repeats are numbered the way cadmpeg
    numbers a repeated body name, and every sketch in the design ends up with its own name.
    """
    source = CORPUS / "Caja parametrica v2.f3d"
    if not source.exists():
        pytest.skip(f"private corpus file not found: {source}")
    out = tmp_path / "caja.solidcraft"
    assert main(["convert", str(source), "-o", str(out)]) == 0
    names = _sketch_names(tools, out)
    assert len(names) == len(set(names)) == 12
    assert {"Sketch 1", "Sketch 1 (2)", "Sketch 1 (3)", "Sketch 3", "Sketch 3 (2)"} <= set(names)


# The two bodies of the corpus the file carries a name for, and what the designer wrote in them.
