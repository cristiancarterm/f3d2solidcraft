from pathlib import Path

from f3d2solidcraft_app.opener import open_command

# The design's path is handed on as this system spells it: the tests ask which program opens it on
# each system, wherever they run, and on Windows `Path("/tmp/a.solidcraft")` reads `\tmp\a.solidcraft`.
DESIGN = Path("/tmp/a.solidcraft")


def test_macos_opens_with_the_solidcraft_app():
    assert open_command("Darwin", DESIGN) == ["open", "-a", "SolidCraft", str(DESIGN)]


def test_linux_hands_it_to_the_desktop():
    assert open_command("Linux", DESIGN) == ["xdg-open", str(DESIGN)]


def test_windows_uses_the_file_association():
    assert open_command("Windows", Path("C:/a.solidcraft")) is None
