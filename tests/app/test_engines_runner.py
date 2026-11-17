import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from f3d2solidcraft.tools import SOLIDCRAFT_VERSION, solidcraft_candidates, tool_version
from f3d2solidcraft_app.engines import check, prepare_environment, saved_solidcraft, solidcraft_in
from f3d2solidcraft_app.runner import partial_paths, worker_command

OLD_ENGINE = Path.home() / ".f3d2solidcraft/engines/0.30.1/solidcraft-cli"


def _floor() -> tuple[int, ...]:
    return tuple(map(int, SOLIDCRAFT_VERSION.split(".")))


def _version(path: Path) -> tuple[int, ...]:
    try:
        return tuple(int(n) for n in re.findall(r"\d+", tool_version(path))[:3])
    except (OSError, subprocess.SubprocessError):
        return ()


def test_the_engines_on_this_machine_are_found_and_new_enough(tools):
    solidcraft, cadmpeg = check()
    assert solidcraft.ok and cadmpeg.ok, (solidcraft, cadmpeg)
    assert tuple(map(int, solidcraft.version.split(".")[:3])) >= _floor()


def test_a_missing_saved_path_is_not_used(tools, tmp_path, monkeypatch):
    monkeypatch.delenv("F3D2SC_SOLIDCRAFT_CLI", raising=False)
    prepare_environment(tmp_path / "not-there")
    assert "F3D2SC_SOLIDCRAFT_CLI" not in os.environ


def test_an_old_solidcraft_counts_as_missing(tools, monkeypatch):
    # The pinned 0.30.1 copy, when this machine has it: a real engine older than the floor.
    old = OLD_ENGINE
    if not old.is_file():
        pytest.skip("no 0.30.1 engine on this machine")
    monkeypatch.setenv("F3D2SC_SOLIDCRAFT_CLI", str(old))
    solidcraft, _ = check()
    assert not solidcraft.ok
    assert "0.30.1" in solidcraft.problem


def test_an_old_copy_first_on_the_path_does_not_hide_a_newer_one(tools, tmp_path, monkeypatch):
    # The case on this Mac: an old standalone CLI comes first on the PATH and the current app bundle
    # is further down. With no saved path, the window must still find the current one.
    if not OLD_ENGINE.is_file():
        pytest.skip("no 0.30.1 engine on this machine")
    shadow = tmp_path / "bin"
    shadow.mkdir()
    (shadow / "solidcraft-cli").symlink_to(OLD_ENGINE)
    monkeypatch.delenv("F3D2SC_SOLIDCRAFT_CLI", raising=False)
    monkeypatch.setenv("PATH", f"{shadow}{os.pathsep}{os.environ['PATH']}")
    candidates = solidcraft_candidates()
    assert candidates[0].resolve() == OLD_ENGINE.resolve(), candidates
    if not any(_version(c) >= _floor() for c in candidates[1:]):
        pytest.skip(f"no SolidCraft {SOLIDCRAFT_VERSION} or newer installed here besides the shadow")
    solidcraft, _ = check()
    assert solidcraft.ok, solidcraft.problem
    assert solidcraft.path.resolve() != OLD_ENGINE.resolve()
    assert tuple(map(int, solidcraft.version.split(".")[:3])) >= _floor()


def test_the_worker_runs_the_converter_in_another_process(tools, sample_f3d, tmp_path):
    out = tmp_path / "box.solidcraft"
    done = subprocess.run(
        worker_command(["convert", str(sample_f3d), "-o", str(out), "--progress"]),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert out.is_file()
    assert '"event": "done"' in done.stdout


def test_a_packaged_executable_is_its_own_worker():
    app = "/Applications/f3d2solidcraft.app/Contents/MacOS/f3d2solidcraft"
    assert worker_command(["inspect", "x.f3d"], executable=app) == [app, "--worker", "inspect", "x.f3d"]


def test_the_partial_files_are_the_ones_convert_writes(tmp_path):
    assert partial_paths(tmp_path / "a.solidcraft", tmp_path / "a.json") == [
        tmp_path / "a.solidcraft.partial",
        tmp_path / "a.json.partial",
    ]


def test_check_prints_the_engines(tools):
    done = subprocess.run(
        [sys.executable, "-m", "f3d2solidcraft_app", "--check"], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert "solidcraft-cli" in done.stdout and "cadmpeg" in done.stdout


def test_check_starts_a_worker_the_way_the_window_does(tools):
    # `--check` also runs `worker_command(["--help"])`: on a packaged app that is the measure that
    # the app is its own worker, which the engine lines alone do not show.
    done = subprocess.run(
        [sys.executable, "-m", "f3d2solidcraft_app", "--check"], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stdout + done.stderr
    (line,) = [line for line in done.stdout.splitlines() if line.startswith("worker:")]
    assert line == f"worker: {sys.executable} -m f3d2solidcraft_app --worker ok", line


# --- Final review: an explicit variable wins over the saved path; the saved path is read the same way ---


def _symlinked_engine(tools, tmp_path) -> Path:
    """The real engine under another name: a saved path that is a real engine yet tells itself apart."""
    link = tmp_path / "saved" / "solidcraft-cli"
    link.parent.mkdir()
    try:
        link.symlink_to(tools.solidcraft_cli)
    except OSError as exc:  # Windows without the symlink privilege
        pytest.skip(f"cannot make a symlink here: {exc}")
    return link


def _child_env(**values: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("F3D2SC_SOLIDCRAFT_CLI", "F3D2SC_CADMPEG")}
    env.update(values)
    return env


CHOSEN = (
    "import sys; from pathlib import Path; "
    "from f3d2solidcraft_app.engines import check, prepare_environment; "
    "prepare_environment(Path(sys.argv[1])); s, c = check(); print(s.path); print(c.path)"
)


def test_an_explicit_solidcraft_variable_wins_over_the_saved_path(tools, tmp_path):
    # The window calls `prepare_environment(saved)` and then `check()`: a variable the user set before
    # starting the program must stay the engine, as the README promises.
    saved = _symlinked_engine(tools, tmp_path)
    done = subprocess.run(
        [sys.executable, "-c", CHOSEN, str(saved)],
        env=_child_env(F3D2SC_SOLIDCRAFT_CLI=str(tools.solidcraft_cli)),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[0] == str(tools.solidcraft_cli), done.stdout


def test_an_explicit_cadmpeg_variable_wins_over_the_bundled_one(tools, tmp_path):
    done = subprocess.run(
        [sys.executable, "-c", CHOSEN, str(tmp_path / "nothing-saved")],
        env=_child_env(F3D2SC_CADMPEG=str(tools.cadmpeg)),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[1] == str(tools.cadmpeg), done.stdout


def _settings_home(tmp_path: Path, content: str) -> dict[str, str]:
    home = tmp_path / "home"
    (home / ".f3d2solidcraft").mkdir(parents=True)
    (home / ".f3d2solidcraft" / "app.json").write_text(content, encoding="utf-8")
    return {"HOME": str(home), "USERPROFILE": str(home)}


def _check_line(env: dict[str, str]) -> str:
    done = subprocess.run(
        [sys.executable, "-m", "f3d2solidcraft_app", "--check"],
        env=_child_env(**env),
        capture_output=True,
        text=True,
        check=False,
    )
    (line,) = [line for line in done.stdout.splitlines() if line.startswith("solidcraft-cli:")]
    return line


def test_check_uses_the_saved_solidcraft_first(tools, tmp_path):
    # Spec, Pruebas: «ruta guardada». `--check` reads the same saved path the window does, and a valid
    # one is used before anything found on the PATH.
    saved = _symlinked_engine(tools, tmp_path)
    line = _check_line(_settings_home(tmp_path, json.dumps({"solidcraft_cli": str(saved)})))
    assert f" {saved} ok" in line, line


def test_an_explicit_variable_wins_over_the_saved_path_in_check_too(tools, tmp_path):
    saved = _symlinked_engine(tools, tmp_path)
    env = _settings_home(tmp_path, json.dumps({"solidcraft_cli": str(saved)}))
    line = _check_line({**env, "F3D2SC_SOLIDCRAFT_CLI": str(tools.solidcraft_cli)})
    assert f" {tools.solidcraft_cli} ok" in line, line


@pytest.mark.parametrize("content", ["[]", '"x"', "3", '{"solidcraft_cli": 5}', "{not json"])
def test_a_settings_file_that_is_not_a_saved_path_is_ignored(tmp_path, content):
    settings = tmp_path / "app.json"
    settings.write_text(content, encoding="utf-8")
    assert saved_solidcraft(settings) is None


APP_BUNDLE = Path("/Applications/SolidCraft.app")


@pytest.mark.skipif(sys.platform != "darwin", reason="app bundles are macOS")
def test_choosing_the_app_bundle_means_the_cli_inside_it():
    if not (APP_BUNDLE / "Contents/MacOS/solidcraft-cli").is_file():
        pytest.skip("SolidCraft.app is not installed on this machine")
    assert solidcraft_in(APP_BUNDLE) == APP_BUNDLE / "Contents/MacOS/solidcraft-cli"


def test_a_folder_without_the_cli_is_not_solidcraft(tmp_path):
    empty = tmp_path / "Other.app"
    empty.mkdir()
    assert solidcraft_in(empty) is None
    assert solidcraft_in(tmp_path) is None


def test_a_file_chosen_is_taken_as_it_is(tools):
    assert solidcraft_in(tools.solidcraft_cli) == tools.solidcraft_cli
