"""Where each system installs SolidCraft, besides the PATH. Measured, not guessed (spec, Arquitectura 3)."""

import platform
from pathlib import Path, PurePosixPath

import pytest

from f3d2solidcraft.tools import locate_solidcraft, solidcraft_candidates, solidcraft_locations


def test_macos_looks_inside_the_app_in_both_applications_folders():
    home = Path("/Users/someone")
    places = solidcraft_locations("Darwin", home, {})
    assert places[0] == home / ".local/bin/solidcraft-cli"
    assert Path("/Applications/SolidCraft.app/Contents/MacOS/solidcraft-cli") in places
    assert home / "Applications/SolidCraft.app/Contents/MacOS/solidcraft-cli" in places


def test_windows_looks_where_the_msi_installs(tmp_path):
    places = solidcraft_locations("Windows", tmp_path, {"ProgramFiles": r"C:\Program Files"})
    assert any(str(p).endswith("solidcraft-cli.exe") for p in places)


def test_linux_looks_where_the_deb_installs(tmp_path):
    places = solidcraft_locations("Linux", tmp_path, {})
    # Read as a Linux path wherever the suite runs: on Windows `/usr/bin` has no drive, so `Path`
    # does not call it absolute, though on the system the list is for it is.
    assert PurePosixPath("/usr/bin/solidcraft-cli") in [PurePosixPath(p.as_posix()) for p in places[1:]]


def test_an_unknown_system_only_has_the_home_fallback(tmp_path):
    assert solidcraft_locations("Plan9", tmp_path, {}) == [tmp_path / ".local/bin/solidcraft-cli"]


@pytest.mark.skipif(platform.system() != "Darwin", reason="the app bundle only exists on macOS")
def test_the_app_bundle_is_found_without_the_path(monkeypatch, tmp_path):
    app = Path("/Applications/SolidCraft.app/Contents/MacOS/solidcraft-cli")
    if not app.is_file():
        pytest.skip("SolidCraft.app is not installed on this machine")
    monkeypatch.delenv("F3D2SC_SOLIDCRAFT_CLI", raising=False)
    # An empty home: a ~/.local/bin/solidcraft-cli on this machine comes first and would answer instead.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "")  # nothing on the PATH: only the system's own locations are left
    assert locate_solidcraft() == app


APP_BUNDLE_CLI = Path("/Applications/SolidCraft.app/Contents/MacOS/solidcraft-cli")


@pytest.mark.skipif(platform.system() != "Darwin", reason="the app bundle only exists on macOS")
def test_the_candidates_include_the_app_bundle_without_the_path(monkeypatch):
    if not APP_BUNDLE_CLI.is_file():
        pytest.skip("SolidCraft.app is not installed on this machine")
    monkeypatch.delenv("F3D2SC_SOLIDCRAFT_CLI", raising=False)
    monkeypatch.setenv("PATH", "")
    assert APP_BUNDLE_CLI in solidcraft_candidates()


@pytest.mark.skipif(platform.system() != "Darwin", reason="the app bundle only exists on macOS")
def test_a_copy_in_the_home_does_not_hide_the_app_bundle(monkeypatch):
    if not APP_BUNDLE_CLI.is_file():
        pytest.skip("SolidCraft.app is not installed on this machine")
    monkeypatch.delenv("F3D2SC_SOLIDCRAFT_CLI", raising=False)
    # Without the PATH: a PATH entry that links into the bundle would stand in for it (same resolved file).
    monkeypatch.setenv("PATH", "")
    candidates = solidcraft_candidates()
    home_copy = Path.home() / ".local/bin/solidcraft-cli"
    if home_copy.is_file():
        assert candidates.index(home_copy) < candidates.index(APP_BUNDLE_CLI)
    else:
        assert APP_BUNDLE_CLI in candidates
