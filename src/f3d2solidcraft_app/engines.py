"""SolidCraft is the user's own; cadmpeg ships inside the app (spec, Arquitectura 3)."""

import json
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from f3d2solidcraft.tools import (
    SOLIDCRAFT_VERSION,
    ToolMissing,
    locate_cadmpeg,
    solidcraft_candidates,
    solidcraft_locations,
    tool_version,
)


@dataclass(frozen=True)
class EngineStatus:
    name: str
    path: Path | None
    version: str | None
    problem: str | None

    @property
    def ok(self) -> bool:
        return self.problem is None


def _numbers(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version)[:3])


def bundled_cadmpeg() -> Path | None:
    name = "cadmpeg.exe" if platform.system() == "Windows" else "cadmpeg"
    path = Path(__file__).parent / "resources" / "bin" / name
    return path if path.is_file() else None


SOLIDCRAFT_VARIABLE = "F3D2SC_SOLIDCRAFT_CLI"
CADMPEG_VARIABLE = "F3D2SC_CADMPEG"

#: The values this module wrote itself. Any other value of these variables is the user's own (set
#: before the program started, or by whoever runs it) and wins over the saved path and the bundled
#: cadmpeg; a value written here does not, so «Ya lo tengo: buscarlo…» can change it again.
_written: dict[str, str] = {}


def _explicit(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value and value != _written.get(name) else None


def _write(name: str, value: str) -> None:
    os.environ[name] = value
    _written[name] = value


def settings_file() -> Path:
    return Path.home() / ".f3d2solidcraft" / "app.json"


def saved_solidcraft(settings_path: Path | None = None) -> Path | None:
    """The SolidCraft the user located once, or None if nothing usable is saved."""
    try:
        data = json.loads((settings_path or settings_file()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("solidcraft_cli") if isinstance(data, dict) else None
    return Path(value) if isinstance(value, str) and value else None


def save_solidcraft(path: Path, settings_path: Path | None = None) -> None:
    settings = settings_path or settings_file()
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"solidcraft_cli": str(path)}), encoding="utf-8")


def solidcraft_in(chosen: Path) -> Path | None:
    """The `solidcraft-cli` a chosen path means: a file as it is, an app bundle by the CLI inside it.

    On macOS the file dialog returns `SolidCraft.app`, a folder; the engine is in Contents/MacOS. A
    folder without one is not SolidCraft.
    """
    if chosen.is_file():
        return chosen
    inside = chosen / "Contents" / "MacOS" / "solidcraft-cli"
    return inside if chosen.is_dir() and inside.is_file() else None


def prepare_environment(saved_solidcraft: Path | None) -> None:
    """Point the variables at the bundled cadmpeg and the saved SolidCraft, unless the user set them.

    An app opened from the desktop has neither variable, so it uses the cadmpeg it ships and the
    SolidCraft located last. A variable the user set wins over both, as the README says.
    """
    bundled = bundled_cadmpeg()
    if bundled is not None and _explicit(CADMPEG_VARIABLE) is None:
        _write(CADMPEG_VARIABLE, str(bundled))
    if saved_solidcraft is not None and saved_solidcraft.is_file() and _explicit(SOLIDCRAFT_VARIABLE) is None:
        _write(SOLIDCRAFT_VARIABLE, str(saved_solidcraft))


def _examine(name: str, path: Path, minimum: str | None) -> EngineStatus:
    try:
        version = tool_version(path)
    # A binary that does not run, hangs, or prints no version is as good as missing.
    except (OSError, subprocess.SubprocessError, IndexError) as exc:
        return EngineStatus(name, path, None, f"{path} does not run: {exc}")
    if minimum is not None and _numbers(version) < _numbers(minimum):
        return EngineStatus(name, path, version, f"{path} is version {version}; {minimum} or newer is needed")
    return EngineStatus(name, path, version, None)


def _solidcraft() -> EngineStatus:
    """The first `solidcraft-cli` new enough, not merely the first one found.

    `locate_solidcraft()` stops at the first copy, which on a Mac with an old standalone CLI in
    ~/.local/bin hides the current app bundle further down. A path set in `F3D2SC_SOLIDCRAFT_CLI`
    (the one the user saved, or a pin) is a choice, though: it is the only one looked at, and if it
    is too old or does not run, that is what the window says.
    """
    name = "solidcraft-cli"
    override = os.environ.get("F3D2SC_SOLIDCRAFT_CLI")
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            return EngineStatus(name, None, None, f"F3D2SC_SOLIDCRAFT_CLI={path} does not exist")
        return _examine(name, path, SOLIDCRAFT_VERSION)
    rejected: list[EngineStatus] = []
    for path in solidcraft_candidates():
        status = _examine(name, path, SOLIDCRAFT_VERSION)
        if status.ok:
            return status
        rejected.append(status)
    if rejected:
        found = "; ".join(f"{s.path} ({s.version or 'does not run'})" for s in rejected)
        return EngineStatus(name, None, None, f"no {name} {SOLIDCRAFT_VERSION} or newer; found only: {found}")
    places = [
        "your PATH",
        *(str(p) for p in solidcraft_locations(platform.system(), Path.home(), os.environ)),
    ]
    return EngineStatus(name, None, None, f"{name} not found (looked in {', '.join(places)})")


def _cadmpeg() -> EngineStatus:
    try:
        path = locate_cadmpeg()
    except ToolMissing as exc:
        return EngineStatus("cadmpeg", None, None, str(exc))
    return _examine("cadmpeg", path, None)


def check() -> tuple[EngineStatus, EngineStatus]:
    return _solidcraft(), _cadmpeg()
