import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from f3d2solidcraft.tools import (
    CADMPEG_VERSION,
    HOW_TO_INSTALL,
    SOLIDCRAFT_VERSION,
    ToolFailed,
    ToolMissing,
    _find,
    long_path,
    program_name,
    run_tool,
    tool_version,
)


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split(".") if part.isdigit())


def test_tools_are_found_and_at_least_the_pinned_versions(tools):
    """The installed tools may be newer than the floors, never older.

    This asserted equality until SolidCraft went 0.22.1 -> 0.25.1 -> 0.26.1 in two days: the suite
    failed three times over an app updating itself, which says nothing about the code. Older is the
    case that matters — it would measure against an engine the project was never verified on.
    """
    assert tools.solidcraft_cli.is_file()
    assert tools.cadmpeg.is_file()
    for path, floor in ((tools.solidcraft_cli, SOLIDCRAFT_VERSION), (tools.cadmpeg, CADMPEG_VERSION)):
        found = tool_version(path)
        assert _version(found) >= _version(floor), f"{path.name} is {found}, older than {floor}"


def test_vendored_reader_stays_light():
    code = (
        "import sys\n"
        "from f3d2solidcraft._vendor.ezf3d.model.sketch import read_sketches\n"
        "heavy = {'numpy', 'pydantic', 'typer', 'rich', 'ezf3d'} & {m.split('.')[0] for m in sys.modules}\n"
        "print(sorted(heavy))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"


def test_run_tool_surfaces_the_tool_error(tools):
    with pytest.raises(ToolFailed) as exc:
        run_tool([str(tools.cadmpeg), "convert", "/nonexistent.f3d", "-o", "/tmp/x.step"], 30)
    assert "cadmpeg convert failed" in str(exc.value)


def test_run_tool_reports_a_program_that_cannot_start(tmp_path):
    with pytest.raises(ToolFailed, match="nope could not be started"):
        run_tool([str(tmp_path / "nope")], 30)


def test_run_tool_gives_up_on_a_program_that_does_not_answer():
    """The timeout is the third way an external program fails, after a bad exit and a bad path.

    Python is the program that sleeps because it is the one every system running this has: Windows
    has no `/bin/sleep`.
    """
    with pytest.raises(ToolFailed, match=r" -c failed \(no answer within 1 s\)"):
        run_tool([sys.executable, "-c", "import time; time.sleep(30)"], 1.0)


def test_a_program_is_named_without_the_suffix_windows_runs_it_by():
    assert program_name("D:/a/_temp/bin/cadmpeg.EXE") == "cadmpeg"
    assert program_name("/usr/local/bin/cadmpeg") == "cadmpeg"
    assert program_name("/usr/bin/python3.13") == "python3.13"


def test_long_path_spells_out_an_8_3_short_name(short_name, tmp_path):
    """SolidCraft refuses a path with an 8.3 short name in it, so the converter spells every one out.

    Measured on the GitHub Windows runner, where Python's temp dir is `C:\\Users\\RUNNER~1\\...`:
    before this, every conversion there exited 2.
    """
    long = tmp_path / "a folder with a long name"
    long.mkdir()
    short = short_name(long)
    assert "~" in short.name
    assert long_path(short) == long_path(long)
    assert "~" not in str(long_path(short))
    # An output that does not exist yet keeps its own name, under a folder spelt out in full.
    assert long_path(short / "box.solidcraft") == long_path(long) / "box.solidcraft"
    # And the temp dir the converter works in, short or not on this machine.
    assert "~" not in str(long_path(tempfile.gettempdir()))


def test_find_reports_each_way_a_tool_can_be_missing(tmp_path, monkeypatch):
    """Four ways `_find` ends, each with the message a broken install gets. The override wins over
    the PATH, and a missing one is an error rather than a quiet fall back to whatever is installed:
    someone who set it meant that file.
    """
    tool = tmp_path / "solidcraft-cli"
    tool.write_text("#!/bin/sh\n")
    monkeypatch.setenv("F3D2SC_TEST_TOOL", str(tool))
    assert _find("F3D2SC_TEST_TOOL", "solidcraft-cli", []) == tool

    missing = tmp_path / "gone"
    monkeypatch.setenv("F3D2SC_TEST_TOOL", str(missing))
    with pytest.raises(ToolMissing, match=re.escape(f"F3D2SC_TEST_TOOL={missing} does not exist")):
        _find("F3D2SC_TEST_TOOL", "solidcraft-cli", [])

    # no override: the PATH first, then the fallbacks this project knows, then the error
    monkeypatch.delenv("F3D2SC_TEST_TOOL")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert _find("F3D2SC_TEST_TOOL", "solidcraft-cli", [missing, tool]) == tool
    with pytest.raises(ToolMissing) as exc:
        _find("F3D2SC_TEST_TOOL", "solidcraft-cli", [missing])
    # the dead end this used to be: "not found" with nowhere to go. It says where it looked, how to
    # get the program, and the variable that overrides all of it.
    said = str(exc.value)
    assert "solidcraft-cli not found" in said
    assert str(missing) in said and "your PATH" in said
    assert "https://github.com/tangency-labs/solidcraft/releases" in said
    assert "set F3D2SC_TEST_TOOL" in said


def test_the_readme_quotes_the_version_floors_this_code_enforces():
    """The floors live in `tools.py` and the README tells a new install which versions to get.

    One fact in two places, which is the drift the README audit of spec §59 found four times over.
    The pair of numbers a new user acts on is checked here instead of by eye, and so is the one
    command they are told to run, which the error message also gives them.
    """
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
    requirements = readme.split("## Requirements", 1)[1].split("\n## ", 1)[0]
    assert SOLIDCRAFT_VERSION in requirements, requirements
    assert CADMPEG_VERSION in requirements, requirements
    # the `cargo install …` line, the same one the missing-tool error hands over
    cargo = HOW_TO_INSTALL["cadmpeg"].split("`")[1]
    assert cargo in requirements, cargo


def test_the_tests_run_on_the_measured_engine_when_this_machine_has_it(tools):
    """The point of the pin, checked where it applies: not the PATH's CLI, the pinned one.

    Skipped where there is no pinned copy (CI, a fresh checkout) and where whoever ran the suite
    chose an engine on purpose, because in both the pin is meant to step aside.
    """
    import os

    from conftest import PINNED_BY_SUITE

    from f3d2solidcraft.tools import MEASURED_ENGINE, measured_engine, tool_version

    if measured_engine() is None:
        pytest.skip(f"no pinned {MEASURED_ENGINE} on this machine")
    if os.environ.get("F3D2SC_SOLIDCRAFT_CLI") and not os.environ.get(PINNED_BY_SUITE):
        pytest.skip("an engine was chosen on purpose")
    assert tools.solidcraft_cli == measured_engine()
    assert MEASURED_ENGINE in tool_version(tools.solidcraft_cli)


def test_code_that_finds_its_own_tools_gets_the_pinned_engine_too():
    """The hole the first pin left, closed and kept closed.

    `cli.main` and the MCP server call `find_tools()` themselves rather than take the fixture, so a
    pin on the fixture alone never reached them: measured, `test_cli`'s conversions kept running on
    the PATH's engine while the fixture-based tests ran on the pinned one (spec section 146). This
    asks the same question `main()` asks, the way `main()` asks it.
    """
    import os

    from conftest import PINNED_BY_SUITE

    from f3d2solidcraft.tools import MEASURED_ENGINE, find_tools, measured_engine

    if measured_engine() is None:
        pytest.skip(f"no pinned {MEASURED_ENGINE} on this machine")
    if os.environ.get("F3D2SC_SOLIDCRAFT_CLI") and not os.environ.get(PINNED_BY_SUITE):
        pytest.skip("an engine was chosen on purpose")
    assert find_tools().solidcraft_cli == measured_engine()
