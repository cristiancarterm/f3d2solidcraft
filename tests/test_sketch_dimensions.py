"""A sketch's Fusion dimensions arrive in SolidCraft, on the right sketch, linked to their parameters.

Reported by a user: the sketches arrived "without any dimensions". Measured over the corpus's 13
`.f3d` (91 sketches) on 0.4.1, engine 0.43.0, cadmpeg 0.6.11: of 157 Fusion dimensions, 81 reached
the saved design and 13 sketches that have dimensions in Fusion lost every one of them. Two causes
carried most of it:

- **the wrong sketch.** ezf3d's positional rule gives a parameter to the nearest feature record
  before it, so a dimension added after later features landed on an Extrude, a Mirror or another
  sketch — 43 of them. cadmpeg's `model.parameters[].owner` names the right sketch feature for all 43.
- **the wrong question.** A dimension was only applied as the length of one line or the radius of
  one circle of the same size, and most of Fusion's are horizontal or vertical distances between two
  points. cadmpeg types each dimension with the entities it measures, which is what
  `sketch.dimension` takes.

Every test here converts a real file, saves it, reopens it and reads it back.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from f3d2solidcraft.cli import main
from f3d2solidcraft.engine import Engine

CORPUS = Path(os.environ.get("F3D2SC_CORPUS", "~/.f3d2solidcraft/corpus/propios")).expanduser()
SAMPLES = Path(os.environ.get("F3D2SC_SAMPLES", "~/.f3d2solidcraft/corpus/samples")).expanduser()
PACKAGES = Path(os.environ.get("F3D2SC_PACKAGES", "~/.f3d2solidcraft/corpus/propios-f3z")).expanduser()

# The corpus the probe measured: the 12 designs of the user's own and the public sample, 91 sketches.


def _source(filename: str) -> Path:
    for folder in (CORPUS, SAMPLES, PACKAGES):
        if (folder / filename).is_file():
            return folder / filename
    pytest.skip(f"private corpus file missing: {filename}")


def _sketch_of(design, feature_name: str):
    feature = next(f for f in design.features if f.name == feature_name)
    return design.sketches[feature.sketch]


@pytest.fixture(scope="module")
def converted(tools, tmp_path_factory):
    """Convert a design once per module and read the saved file back.

    Returns `(code, report, sketches, parameters)`: `sketches` is the saved design's sketches by name,
    each with the constraints `inspect_design` lists; `parameters` is the saved design's parameter
    table, as `parameters.list` gives it.
    """
    done: dict[str, tuple] = {}

    def convert(filename: str):
        if filename not in done:
            source = _source(filename)
            folder = tmp_path_factory.mktemp("dims")
            out, report_path = folder / "out.solidcraft", folder / "out.report.json"
            code = main(["convert", str(source), "-o", str(out), "--report", str(report_path)])
            with Engine(tools.solidcraft_cli, [folder]) as eng:
                eng.call("open", {"path": str(out)})
                inspected = eng.call("inspect_design", {"measure": False, "sketches": True})
                listed = eng.execute("parameters.list", {"filter": "all"})["parameters"]
            sketches = {s["name"]: s for s in inspected.get("sketches") or []}
            done[filename] = (code, json.loads(report_path.read_text()), sketches, listed)
        return done[filename]

    return convert


def _driving(sketch: dict) -> list[dict]:
    """The dimensions of a saved sketch that carry a parameter: the ones a value change moves."""
    return [c for c in sketch.get("constraints", []) if c.get("param")]


def _expressions(parameters: list[dict], sketch: dict) -> list[str]:
    """The expressions of the parameters this saved sketch's dimensions carry.

    A sketch dimension's parameter names no feature in `parameters.list`; the constraint names it.
    """
    by_name = {p["name"]: p["expression"] for p in parameters}
    return sorted(by_name[c["param"]] for c in _driving(sketch))


# --- the reader --------------------------------------------------------------------------------------


def test_a_dimension_added_after_later_features_belongs_to_the_sketch_its_owner_names(corpus_design):
    """`Caja parametrica v2`: ezf3d gave `Sketch 8` nine dimensions that cadmpeg's owner puts in
    `Sketch 9`, `Sketch 5` and `Sketch 10`. `Sketch 8` keeps only its own, `d245`."""
    design = corpus_design("Caja parametrica v2.f3d")
    misplaced = {f"d{n}" for n in range(220, 229)}
    sketch_8 = _sketch_of(design, "Sketch 8")
    assert {p.name for p in sketch_8.dimensions} == {"d245"}
    assert {p.name for p in _sketch_of(design, "Sketch 9").dimensions} == {"d220", "d221", "d222", "d223"}
    assert {p.name for p in _sketch_of(design, "Sketch 5").dimensions} == {"d224", "d225", "d226", "d227"}
    assert {p.name for p in _sketch_of(design, "Sketch 10").dimensions} == {"d228"}
    assert not misplaced & {d.parameter for d in sketch_8.typed_dimensions}


def _corrupt_parameters_not_a_list(model):
    model["parameters"] = None


def _corrupt_a_parameter_not_a_dict(model):
    model["parameters"][0] = "d1"


def _corrupt_a_parameter_without_a_name(model):
    for p in model["parameters"]:
        p.pop("name", None)


def _corrupt_a_dimension_definition_not_a_dict(model):
    for c in model["sketch_constraints"]:
        if "parameter" in (c.get("definition") or {}):
            c["definition"] = ["horizontal_distance"]


def _corrupt_a_dimension_parameter_not_text(model):
    for c in model["sketch_constraints"]:
        if "parameter" in (c.get("definition") or {}):
            c["definition"]["parameter"] = 7


# --- the conversion ----------------------------------------------------------------------------------


def test_the_caja_dimensions_land_on_their_own_sketches(converted):
    """`Caja parametrica v2`: `Sketch 8` carries its one dimension, `Tolerance`, and none of the nine
    ezf3d used to give it; those reach `Sketch 9`, `Sketch 5` and `Sketch 10`."""
    code, report, sketches, parameters = converted("Caja parametrica v2.f3d")
    assert code == 0 and report["verification"]["ok"] is True
    assert _expressions(parameters, sketches["Sketch 8"]) == ["Tolerance"]
    assert len(_driving(sketches["Sketch 8"])) == 1
    for name in ("Sketch 9", "Sketch 5"):
        assert {"LatchDiameter", "LatchDiameter / 2", "LatchFrameOverhang"} <= set(
            _expressions(parameters, sketches[name])
        )
    assert _expressions(parameters, sketches["Sketch 10"]) == ["LatchDiameter - 2.5 mm"]
    assert all(s["status"] == "solved" for s in sketches.values())


def test_the_report_counts_the_dimensions_applied_and_the_ones_skipped_with_why(converted):
    """The report says how many of the file's dimensions arrived and why each other one did not."""
    _code, report, sketches, _parameters = converted("Caja parametrica v2.f3d")
    dims = report["dimensions"]
    assert dims["fusion"] == dims["applied"] + dims["reference"] + dims["ambiguous"] + dims["skipped"]
    assert dims["applied"] == sum(len(_driving(s)) for s in sketches.values())
    assert sum(dims["reasons"].values()) == dims["skipped"]
    # Caja's offsets are the ones still left: `sketch.dimension` is not given an offset yet.
    assert any("offset" in reason for reason in dims["reasons"]), dims["reasons"]
    rows = {row["sketch"]: row for row in dims["sketches"]}
    assert rows["Sketch 8"]["applied"] == 1


def _fusion_dimensions(design) -> dict[str, set[str]]:
    """Every sketch's Fusion dimensions, by sketch id: what cadmpeg types and what only ezf3d sees.

    A sketch pattern's count and angle are parameters of the sketch, not dimensions, and are left out.
    """
    out: dict[str, set[str]] = {}
    for sid, sketch in design.sketches.items():
        names = {d.parameter for d in sketch.typed_dimensions if d.parameter}
        names |= {p.name for p in sketch.dimensions if not p.role.startswith("C-Pattern")}
        out[sid] = names
    return out


# --- the review fixes: nothing drives that was not read or checked ----------------------------------


def _typed_entities(design, sketch_feature: str, parameter: str) -> list:
    """The entities of the drawn sketch a typed dimension of this parameter measures, in its order."""
    sketch = _sketch_of(design, sketch_feature)
    record = next(d for d in sketch.typed_dimensions if d.parameter == parameter)
    by_id = {e.id: e for e in sketch.entities}
    return [by_id[x] for x in record.definition["entities"]]
