import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESOURCES = ROOT / "src/f3d2solidcraft_app/resources"


def test_briefcase_has_an_icon_for_every_system():
    app = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["briefcase"]["app"][
        "f3d2solidcraft-app"
    ]
    base = ROOT / app["icon"]
    assert base.with_suffix(".icns").is_file()  # macOS
    assert base.with_suffix(".ico").is_file()  # Windows
    for size in (16, 32, 64, 128, 256, 512):  # Linux
        assert (base.parent / f"{base.name}-{size}.png").is_file()


def test_the_icon_files_are_real_images():
    assert (RESOURCES / "icon.icns").read_bytes()[:4] == b"icns"
    assert (RESOURCES / "icon.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"
    assert (RESOURCES / "icon-256.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
