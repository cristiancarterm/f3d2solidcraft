"""The sketches no feature of the timeline names.

A sketch reached the result only through a `Sketch` feature, so a sketch no feature names was built
nowhere and said nothing. Measured over the corpus: **22 of its 96 sketches**, all four of
`corpus-11` and 18 of the 29 of `corpus-03`. The verification cannot see the loss
— it compares bodies, and a sketch has no volume.
"""

import json
import os
from pathlib import Path

import pytest

from f3d2solidcraft.cli import main
from f3d2solidcraft.engine import Engine

CORPUS = Path(os.environ.get("F3D2SC_CORPUS", "~/.f3d2solidcraft/corpus/propios")).expanduser()


def _sketches(tools, design: Path) -> list[dict]:
    with Engine(tools.solidcraft_cli, [design.parent]) as eng:
        eng.call("open", {"path": str(design)})
        return eng.call("inspect_design", {"measure": False})["sketches"]


def _convert(source: Path, tmp_path: Path, stem: str) -> tuple[Path, dict]:
    if not source.exists():
        pytest.skip(f"private corpus file not found: {source}")
    out, report = tmp_path / f"{stem}.solidcraft", tmp_path / f"{stem}.report.json"
    assert main(["convert", str(source), "-o", str(out), "--report", str(report)]) == 0
    return out, json.loads(report.read_text())


def test_a_file_whose_every_sketch_has_a_feature_gains_nothing(sample_f3d, tools, tmp_path):
    """The sample's eight sketches all belong to features, so this step finds none to build.

    It is the control: the list is empty and the eight names are the file's own, with the space.
    """
    out = tmp_path / "box.solidcraft"
    report = tmp_path / "box.report.json"
    assert main(["convert", str(sample_f3d), "-o", str(out), "--report", str(report)]) == 0
    assert json.loads(report.read_text())["sketches"] == []
    assert sorted(str(s["name"]) for s in _sketches(tools, out)) == sorted(f"Sketch {n}" for n in range(1, 9))
