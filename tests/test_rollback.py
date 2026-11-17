"""Rolling back past the engine's undo cap.

The undo stack holds 200 steps. Past that, `undo_depth()` stops moving, so a rollback that only
aims at a depth is reached without undoing anything — and says nothing. Measured on
`Caja parametrica v2`, whose twelve sketches take the design well past the cap: two of the oracle's
trial extrudes survived into the saved file, named `f3d2solidcraft trial`.
"""

import json
import os
from pathlib import Path

import pytest

from f3d2solidcraft.cli import main
from f3d2solidcraft.engine import Engine, rolled_back

CORPUS = Path(os.environ.get("F3D2SC_CORPUS", "~/.f3d2solidcraft/corpus/propios")).expanduser()
TRIAL = "f3d2solidcraft trial"


def test_the_undo_stack_caps_at_200_steps(tools):
    """The measurement the rest of this rests on, asked of the engine itself."""
    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        eng.call(
            "batch",
            {
                "commands": [
                    {
                        "command": "solid.box",
                        "params": {
                            "length": 1,
                            "width": 1,
                            "height": 1,
                            "body_name": f"b{i}",
                            "operation": "new",
                        },
                    }
                    for i in range(1, 221)
                ]
            },
        )
        assert len(eng.timeline_ids()) == 220
        assert eng.undo_depth() == 200, "the cap this module exists for has moved"


def test_a_trial_is_undone_past_the_cap(tools):
    """A `rolled_back` block leaves the timeline it found, even where undo can no longer reach.

    Aiming at the undo depth alone, this is what used to leave the trial behind: the depth target
    is already met, so the loop undoes nothing and the feature stays.
    """
    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        eng.call(
            "batch",
            {
                "commands": [
                    {
                        "command": "solid.box",
                        "params": {
                            "length": 1,
                            "width": 1,
                            "height": 1,
                            "body_name": f"b{i}",
                            "operation": "new",
                        },
                    }
                    for i in range(1, 221)
                ]
            },
        )
        before = eng.timeline_ids()
        assert eng.undo_depth() == 200
        with rolled_back(eng, eng.undo_depth()):
            eng.execute(
                "solid.box",
                {"length": 5, "width": 5, "height": 5, "body_name": "trial", "operation": "new"},
            )
            assert len(eng.timeline_ids()) == len(before) + 1  # it really was built
        assert eng.timeline_ids() == before
        assert "trial" not in set(eng.body_names())


def test_a_converted_design_keeps_none_of_the_oracle_s_trials(tools, tmp_path):
    """End to end on the file that showed it: nothing named after this tool is left in the design.

    `Caja parametrica v2` runs the oracle on extrudes that then fail, and it is long enough that the
    undo cap bites, so two trial extrudes used to be saved into the result for a user to find in
    their timeline.
    """
    source = CORPUS / "Caja parametrica v2.f3d"
    if not source.exists():
        pytest.skip(f"private corpus file not found: {source}")
    out = tmp_path / "caja.solidcraft"
    report = tmp_path / "caja.report.json"
    assert main(["convert", str(source), "-o", str(out), "--report", str(report)]) == 0
    with Engine(tools.solidcraft_cli, [out.parent]) as eng:
        eng.call("open", {"path": str(out)})
        timeline = eng.call("inspect_design", {"measure": False, "sketches": False})["timeline"]
    assert [f for f in timeline if TRIAL in str(f.get("name"))] == []
    assert json.loads(report.read_text())["verification"]["ok"] is True
