"""The bodies Fusion hides, hidden here too — and said plainly when they cannot be told apart.

Both halves run the whole conversion and then open what was saved, because that is where the defect
was: the report said ten bodies matched, zero missing, zero extra, and what opened in SolidCraft was
ten overlapping solids where Fusion shows three.
"""

import json
import os
from pathlib import Path

import pytest

from f3d2solidcraft.cli import main
from f3d2solidcraft.engine import Engine

CORPUS = Path(os.environ.get("F3D2SC_CORPUS", "~/.f3d2solidcraft/corpus/propios")).expanduser()


def _shown_and_hidden(tools, design: Path) -> tuple[set[str], set[str]]:
    """What the saved design shows and hides, read back from the engine."""
    with Engine(tools.solidcraft_cli, [design.parent]) as eng:
        eng.call("open", {"path": str(design)})
        bodies = eng.call("inspect_design", {"measure": False, "sketches": False})["bodies"]
        measured = eng.measure()
    shown = {str(b["name"]) for b in bodies if b.get("visible")}
    hidden = {str(b["name"]) for b in bodies if not b.get("visible")}
    # hiding is not removing: the verification compares every body either way
    assert len(measured["bodies"]) == len(bodies)
    return shown, hidden


def _sketches(tools, design: Path) -> tuple[int, int]:
    """How many sketches the saved design has, and how many of them it shows."""
    with Engine(tools.solidcraft_cli, [design.parent]) as eng:
        eng.call("open", {"path": str(design)})
        sketches = eng.call("inspect_design", {"measure": False})["sketches"]
    return len(sketches), sum(1 for s in sketches if s.get("visible"))


def test_a_design_opens_showing_what_fusion_shows(tools, tmp_path):
    """`Caja parametrica v2` has three bodies and shows all three, and hides all twelve sketches.

    It used to be the body case here: cadmpeg 0.6.7 exported ten bodies for this design — the seven
    hidden ones being case halves in the same place, which is what made the whole upper half look
    lost when all ten came out visible — and 0.6.8 exports **one per component**, so those seven
    are not in the design to hide (spec section 95). What is left of the body half is asserted on
    the sample below, which does hide two of its own.

    The sketches are the half this file still carries, and they are the same defect by another name:
    a design that opens with its sketches drawn over the geometry.
    """
    source = CORPUS / "Caja parametrica v2.f3d"
    if not source.exists():
        pytest.skip(f"private corpus file not found: {source}")
    out = tmp_path / "caja.solidcraft"
    report = tmp_path / "caja.report.json"
    assert main(["convert", str(source), "-o", str(out), "--report", str(report)]) == 0
    shown, hidden = _shown_and_hidden(tools, out)
    assert shown == {"Bottom", "Latch", "Top"}
    assert hidden == set()
    # and all twelve sketches, every one of which Fusion hides: a design that opens with its
    # sketches drawn over the geometry is the same defect by another name
    assert _sketches(tools, out) == (12, 0)
    assert json.loads(report.read_text())["verification"]["ok"] is True


def test_bodies_that_cannot_be_told_apart_stay_visible_and_say_so(sample_f3d, tools, tmp_path):
    """The sample hides four of its five bodies, and two of those four are told apart.

    A body the replay **built** is known by construction — no pairing needed — and cadmpeg 0.6.8
    types this file's first extrude as a trimmed region, which the mapper translates from the entity
    segments it names, so two of its bodies are native and are hidden exactly as Fusion hides them
    (spec sections 95 and 112).

    The rest is as it was: the import's names cannot be matched to the file's bodies — the pairing
    rule refuses a block of five against three, as `test_pairing` pins — so there is no way to know
    which of the imported solids the other two hidden ones are. They stay visible and the report
    says how many and why, which is the honest end of this: a guess here would hide geometry the
    user has.
    """
    out = tmp_path / "box.solidcraft"
    report = tmp_path / "box.report.json"
    assert main(["convert", str(sample_f3d), "-o", str(out), "--report", str(report)]) == 0
    shown, hidden = _shown_and_hidden(tools, out)
    # The two the replay built, and the import left visible. Their numbers are the engine's
    # allocation counter, not a fact about the design — `Body2` and `Body3` now that the trimmed
    # region is translated, `Body1` and `Body2` before it was (spec section 112) — so what is
    # pinned is which pile each body is in.
    assert len(hidden) == 2 and all(n.startswith("Body") for n in hidden), hidden
    assert shown == {"cadmpeg_model (3)"}, shown
    # its sketches are told apart with no pairing at all — the replay built them — so the six of
    # eight this file hides are hidden even where its bodies cannot be
    assert _sketches(tools, out) == (8, 2)
    warnings = json.loads(report.read_text())["warnings"]
    assert "2 of the 4 body(ies) Fusion hides could not be told apart in the design" in " ".join(warnings)
