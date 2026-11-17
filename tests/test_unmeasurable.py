"""What happens when the engine cannot answer about a design, or about one of its bodies.

Both paths were found on `Hypercube Evo+.f3z`, a real assembly of the user's that is **not in the
corpus** — three imports of 196 s each put it out of reach of a test suite — so until now neither
had one. They are reached here on corpus files by shortening the clock rather than by faking an
answer: the code, the engine and the designs are the real ones, and what changes is how long a call
is given before it is given up on (spec section 99).
"""

import os
from pathlib import Path

from f3d2solidcraft import cli as cli_module
from f3d2solidcraft import engine as engine_module
from f3d2solidcraft.cli import main
from f3d2solidcraft.engine import Engine

PACKAGES = Path(os.environ.get("F3D2SC_PACKAGES", "~/.f3d2solidcraft/corpus/propios-f3z")).expanduser()


def test_a_design_whose_bodies_cannot_be_measured_ends_in_a_refusal_and_not_a_loop(
    sample_f3d, tools, tmp_path, monkeypatch, capsys
):
    """Every attempt learns one more unanswerable body, and the attempts are capped.

    With the per-body clock cut to nothing no body answers, so each attempt names another one and
    the next starts over knowing it. That is the loop the cap is there to end: the run refuses, and
    the refusal carries the last reason rather than a bare timeout (spec section 99).

    The cap is lowered with it, because what is asserted is the shape and not the number — six
    attempts of a design that cannot measure anything would only cost six more imports.
    """
    monkeypatch.setattr(engine_module, "BODY_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(cli_module, "_RETRIES", 2)
    out = tmp_path / "box.solidcraft"
    # `main` reports an engine failure as an exit code and a line on stderr, as every other
    # unrecoverable one: what matters is that it ends, says why, and writes no half design.
    assert main(["convert", str(sample_f3d), "-o", str(out)]) != 0
    said = capsys.readouterr().err
    assert "gave up after 2 attempts" in said, said
    assert "could not be measured" in said, said
    assert not out.exists(), "a run that gave up must not leave a design behind"


def test_a_body_already_proved_unanswerable_is_left_out_of_a_reading(tools, tmp_path):
    """The other half: a body in `unmeasurable` costs nothing, because nothing asks about it.

    No clock is shortened here, and no file is needed: two boxes the engine builds itself are a
    design, and what is asserted is that naming one unanswerable takes it out of the reading and
    leaves the other alone. That is what makes a retry cheaper than the attempt that taught it
    (spec section 99).
    """
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        eng.execute("solid.box", {"length": "10 mm", "width": "10 mm", "height": "10 mm", "name": "one"})
        eng.execute(
            "solid.box",
            {
                "length": "20 mm",
                "width": "20 mm",
                "height": "20 mm",
                "corner": [40, 0, 0],
                "name": "two",
                "operation": "new",
            },
        )
        names = sorted(eng.body_names())
        assert len(names) == 2, names
        assert len(eng.body_readings()) == 2
        eng.unmeasurable = {names[0]}
        left = [b["name"] for b in eng.body_readings()]
        assert left == [names[1]], left
        # The one that is left still measures, so what was skipped is the named body and not the call.
        assert eng.body_readings([names[1]])[0]["volume_mm3"] > 0
