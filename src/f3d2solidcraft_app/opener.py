"""Open the converted design in SolidCraft, the way each system opens a file with an app."""

import os
import platform
import subprocess
from pathlib import Path


def open_command(system: str, design: Path) -> list[str] | None:
    if system == "Darwin":
        return ["open", "-a", "SolidCraft", str(design)]
    if system == "Windows":
        return None
    return ["xdg-open", str(design)]


def open_design(design: Path) -> None:
    command = open_command(platform.system(), design)
    if command is None:
        os.startfile(design)  # Windows only
    else:
        subprocess.Popen(command)
