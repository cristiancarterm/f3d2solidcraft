import tomllib
from pathlib import Path

from f3d2solidcraft import __version__

ROOT = Path(__file__).resolve().parents[2]


def _config() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def test_briefcase_ships_the_same_version_as_the_package():
    assert _config()["tool"]["briefcase"]["version"] == __version__


def test_the_packaged_app_starts_the_window_not_the_converter_cli():
    # Briefcase runs the module named after the app (`-` becomes `_`). An app called `f3d2solidcraft`
    # would run `f3d2solidcraft/__main__.py`, the converter's CLI, and the window would never open.
    (name,) = _config()["tool"]["briefcase"]["app"]
    assert name.replace("-", "_") == "f3d2solidcraft_app"
    assert (ROOT / "src" / "f3d2solidcraft_app" / "__main__.py").is_file()


def test_briefcase_bundles_both_packages_and_qt():
    app = _config()["tool"]["briefcase"]["app"]["f3d2solidcraft-app"]
    assert app["sources"] == ["src/f3d2solidcraft", "src/f3d2solidcraft_app"]
    assert any(r.startswith("PySide6-Essentials") for r in app["requires"])
