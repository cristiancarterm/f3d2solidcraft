"""Each feature takes its parameters from the record the timeline item names (spec 0.4, item 1).

The two readers of a file used to be paired by count: ezf3d's timeline and cadmpeg's features had to
hold exactly as many entries, or no parameter was attached anywhere. Measured, 6 of the corpus's 15
files do not agree on the count and lost every parameter that way. The item oid at the end of each
cadmpeg feature id names the record directly, so the pairing is per feature now.
"""

import json
import os
import re
from pathlib import Path

import pytest

from f3d2solidcraft.cli import main
from f3d2solidcraft.engine import Engine

CORPUS = Path(os.environ.get("F3D2SC_CORPUS", "~/.f3d2solidcraft/corpus/propios")).expanduser()
_LITERAL = re.compile(r"^\s*-?[0-9.]+(e-?\d+)?\s*(mm|cm|m|in|deg|rad)?\s*$")


def test_every_feature_of_the_sample_is_paired_and_nothing_is_said_about_it(design):
    assert design.unpaired == []
    assert not any("could not be paired" in w for w in design.warnings)
    by_name = {f.name: f for f in design.features}
    assert by_name["Extrude 1"].parameters == ("d5", "d6")
    assert by_name["Extrude 1"].item is not None


@pytest.fixture(scope="module")
def saved(tools, tmp_path_factory):
    """Convert a corpus file once per module: (exit code, report, the saved design's linked parameters).

    The linked parameters are the model parameters of the saved design — the ones a feature or a
    sketch owns, not the user's — whose expression names another parameter, as `(source, feature,
    expression)`: `("feature", "Extrude 7", "fusion_d125")`.
    """
    done: dict[str, tuple[int, dict, list[tuple[str, str, str]]]] = {}

    def convert(filename: str):
        if filename not in done:
            source = CORPUS / filename
            if not source.is_file():
                pytest.skip(f"private corpus file missing: {source}")
            folder = tmp_path_factory.mktemp("saved")
            out, report_path = folder / "out.solidcraft", folder / "out.report.json"
            code = main(["convert", str(source), "-o", str(out), "--report", str(report_path)])
            with Engine(tools.solidcraft_cli, [folder]) as eng:
                eng.call("open", {"path": str(out)})
                listed = eng.execute("parameters.list", {"filter": "all"})["parameters"]
            linked = [
                (p["source"], p.get("feature") or "", p["expression"])
                for p in listed
                if p.get("source") != "user" and not _LITERAL.match(p.get("expression") or "")
            ]
            done[filename] = (code, json.loads(report_path.read_text()), linked)
        return done[filename]

    return convert


def test_the_saved_caja_carries_its_extrude_distances_and_sketch_dimensions_as_parameters(saved):
    """5 extrude distances follow a parameter (`fusion_d125` -> `LatchFrameThickness`) and 8 sketch
    dimensions do; the counts and the verification are what they were before the pairing."""
    code, report, linked = saved("Caja parametrica v2.f3d")
    assert code == 0 and report["verification"]["ok"] is True
    assert report["unpaired_features"] == 0
    assert report["counts"] == {"exact": 21, "inferred": 0, "fallback": 2, "failed": 14, "skipped": 0}
    extrudes = [
        (feature, expression) for source, feature, expression in linked if feature.startswith("Extrude")
    ]
    assert len(extrudes) == 5
    assert ("Extrude 7", "fusion_d125") in extrudes
    assert len([x for x in linked if x[0] == "sketch"]) == 8


def test_the_saved_mango_carries_its_sketch_dimensions_as_parameters(saved):
    code, report, linked = saved("mango para lima.f3d")
    assert code == 0 and report["verification"]["ok"] is True
    assert report["counts"] == {"exact": 8, "inferred": 0, "fallback": 16, "failed": 0, "skipped": 0}
    assert len([x for x in linked if x[0] == "sketch"]) == 16


def test_the_saved_caja_fillets_and_chamfers_follow_their_parameters(saved):
    """`Fillet 3` follows `X_ComputedCornerRadius` through `d22`, `Chamfer 4` follows `d23`, in both
    components (the second copy's parameters are `d22_1` and `d23_1`)."""
    code, _report, linked = saved("Caja parametrica v2.f3d")
    assert code == 0
    features = {(feature, expression) for source, feature, expression in linked if source == "feature"}
    assert {
        ("Fillet1", "fusion_d22"),
        ("Chamfer1", "fusion_d23"),
        ("Fillet2", "d22_1"),
        ("Chamfer2", "d23_1"),
    } <= features


def test_the_saved_mango_revolves_follow_their_angle(saved):
    """Both `Revolve 2` turn by their `AlongAngle`; the one revolved the other way carries the sign."""
    code, _report, linked = saved("mango para lima.f3d")
    assert code == 0
    revolves = sorted(expression for source, feature, expression in linked if feature == "Revolve 2")
    assert revolves == ["-fusion_d30", "fusion_d21"]
