import sys
from pathlib import Path

import pytest

from f3d2solidcraft.engine import CommandRefused, Engine, EngineError, _answer, _without_advice


def test_engine_runs_commands_and_measures(tools):
    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        eng.execute("solid.box", {"length": 20, "width": 30, "height": 10})
        assert eng.mutations == 1
        total = eng.measure()["total"]
        assert total["volume_mm3"] == pytest.approx(6000.0)
        eng.undo()
        assert eng.measure()["body_count"] == 0


def test_batch_is_atomic(tools):
    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        results = eng.batch(
            [
                {"command": "sketch.create", "params": {"plane": "XY"}},
                {"command": "sketch.circle.center", "params": {"center": [0, 0], "radius": 5}},
            ]
        )
        assert results[1]["created"]["curves"] == ["c1"]
        assert eng.mutations == 2
        with pytest.raises(CommandRefused) as err:
            eng.batch(
                [
                    {"command": "sketch.finish", "params": {}},
                    {"command": "solid.fillet", "params": {"edges": [[99, 99, 99]], "radius": 1}},
                ]
            )
        assert err.value.command == "solid.fillet"
        assert eng.mutations == 2


def test_engine_reports_refused_command(tools):
    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        with pytest.raises(CommandRefused) as err:
            eng.execute("solid.fillet", {"edges": [[0, 0, 0]], "radius": 1})
        assert err.value.command == "solid.fillet"
        assert eng.mutations == 0


def test_dead_engine_raises_engine_error(tools):
    eng = Engine(tools.solidcraft_cli)
    eng.start()
    eng._proc.kill()
    eng._proc.wait()
    with pytest.raises(EngineError):
        eng.call("new_design", {})
    with pytest.raises(EngineError, match="not running"):
        eng.call("new_design", {})
    eng.close()
    assert eng._proc is None


def test_start_failure_leaves_no_process():
    """A program that starts and exits instead of answering: `python mcp` finds no script `mcp`.

    It was `/usr/bin/true`, which Windows does not have, so there the test asked about a program that
    cannot start at all rather than one that is not an engine.
    """
    eng = Engine(Path(sys.executable), timeout=10)
    with pytest.raises(EngineError):
        eng.start()
    assert eng._proc is None


# Verbatim text SolidCraft 0.22.1 put in a tool result, captured from `solidcraft-cli mcp` on this
# machine. Every result of that version carries the trailing note; 0.16.2 carried none.
ADVISED = (
    '{\n  "active_sketch": null,\n  "bodies": [],\n  "dirty": false,\n  "marker": 0,\n'
    '  "name": "Untitled",\n  "redo": 0,\n  "undo": 3\n}'
    "Note: this is a headless session because the SolidCraft app was not found; the user cannot "
    "see this work. Ask them to start SolidCraft. "
)


def test_an_answer_is_read_even_when_the_engine_appends_advice():
    """0.22.1 appends a note to every tool result, which used to make the whole answer unusable.

    The answers themselves did not change between 0.16.2 and 0.22.1 — same tools, same keys. The
    break was this one trailing sentence: `json.loads` failed on it, `call` fell back to handing
    back the raw string, and `undo_depth()` then did `"{...}Note: …"["undo"]` and raised
    `TypeError: string indices must be integers`, on the first sketch of any conversion.
    """
    answer = _answer(ADVISED)
    assert answer["undo"] == 3
    assert answer["name"] == "Untitled"
    assert answer["bodies"] == []


def test_a_refusal_keeps_its_own_message_and_drops_the_advice():
    refusal = (
        "solid.extrude: boolean Union: the operation failed: no result"
        "Note: this is a headless session because the SolidCraft app was not found; the user "
        "cannot see this work. Ask them to start SolidCraft. "
    )
    assert _without_advice(refusal) == "solid.extrude: boolean Union: the operation failed: no result"


def test_text_that_is_not_json_still_comes_back_as_text():
    assert _answer("plain words, no json here") == "plain words, no json here"


def test_prose_that_merely_starts_like_json_is_not_truncated_to_a_number():
    """`raw_decode` reads a leading number happily, so the answer has to be the whole text or none.

    Reading the first JSON value is what lets the appended advice be ignored, but on its own it
    would turn "12 bodies removed" into `12`. `json.loads` used to fail there and hand back the
    sentence, and that is still the right answer.
    """
    assert _answer("12 bodies removed") == "12 bodies removed"
    assert _answer("3") == 3
