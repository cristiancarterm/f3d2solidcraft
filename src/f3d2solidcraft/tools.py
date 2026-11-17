"""Locate the external programs f3d2solidcraft drives."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# The versions this project is verified against, as **floors**: the installed tool may be newer, and
# `test_tools_are_found_and_pinned` fails only when it is older. An exact match was the rule until
# SolidCraft went 0.22.1 -> 0.25.1 -> 0.26.1 in two days, which failed the suite three times for an
# app updating itself rather than for anything about the code. What the pin is for — never testing
# against an engine older than the one the measurements were taken on, and installing a known one in
# CI, where the workflow names these same numbers — a floor still does.
SOLIDCRAFT_VERSION = "0.43.0"
CADMPEG_VERSION = "0.6.11"

# The engine the suite's measurements were taken on — not a floor, an exact version, and used by the
# tests only. The floors above are what the converter accepts; this is what the tests' numbers mean.
#
# Why it exists: the macOS app updates itself and the CLI on the PATH is a symlink into it, so the
# suite silently changed engines twice — spec section 123 measured four sections on 0.30.1 believing
# it was 0.30.0, and on 2026-11-12 the app moved to 0.40.1 and a plain `pytest` started running on
# solidcore, 19 tests red, with nobody having changed a line (spec section 145). The test fixture
# prefers `$F3D2SC_ENGINES/<this>/solidcraft-cli` when it exists and falls back to the PATH when it
# does not, so CI and a fresh checkout keep working. `scripts/lane.sh` names the same version as its
# `current` lane, and `test_the_measured_engine_is_the_current_lane_s` keeps the two from drifting.
MEASURED_ENGINE = "0.43.0"
ENGINES = Path(os.environ.get("F3D2SC_ENGINES", "~/.f3d2solidcraft/engines")).expanduser()


def measured_engine() -> Path | None:
    """The pinned copy of `MEASURED_ENGINE`, or None when this machine does not have one."""
    path = ENGINES / MEASURED_ENGINE / "solidcraft-cli"
    return path if path.is_file() else None


class ToolMissing(RuntimeError):
    """An external program is not installed or not where it was said to be."""


class ToolFailed(RuntimeError):
    """An external program exited with an error or hung."""


# What Windows runs a file name with, and which a message should not repeat: `cadmpeg.EXE convert`
# reads as a different program from the `cadmpeg convert` the same run says on macOS and Linux.
_EXECUTABLE_SUFFIXES = {".exe", ".com", ".bat", ".cmd"}


def program_name(path: str | Path) -> str:
    """The name a program goes by in a message: its file name, without Windows' `.exe`.

    Only an executable suffix is dropped, not any suffix: a `python3.13` keeps its version.
    """
    path = Path(path)
    return path.stem if path.suffix.lower() in _EXECUTABLE_SUFFIXES else path.name


def long_path(path: str | Path) -> Path:
    r"""`path` absolute and spelt out in full, the form `solidcraft-cli` accepts on Windows.

    Windows hands out 8.3 short names where a long one exists: on a GitHub runner Python's temp dir
    is `C:\Users\RUNNER~1\AppData\Local\Temp`, and SolidCraft refuses every path spelt that way
    ("not allowed for this agent: short (8.3) names such as `PROGRA~1`"), so the reference import,
    the fallback import and the verification all failed and every conversion exited 2.
    `os.path.realpath` asks Windows for the final path of the longest part that exists, which spells
    each component out, and keeps whatever does not exist yet as given. Elsewhere it is the same
    `resolve()` the output always went through.
    """
    return Path(os.path.realpath(path))


def run_tool(args: list[str], timeout: float) -> str:
    """Run an external program; its stderr becomes the error message when it fails."""
    tool = " ".join([program_name(args[0]), *args[1:2]])
    try:
        # The tools write UTF-8 whatever the system's code page is; Windows' default for a pipe is
        # the ANSI one, which turns an accented name into another one or into an error.
        done = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ToolFailed(f"{tool} failed (no answer within {timeout:.0f} s)") from None
    except (FileNotFoundError, PermissionError) as exc:
        raise ToolFailed(f"{tool} could not be started: {exc}") from None
    if done.returncode != 0:
        raise ToolFailed(f"{tool} failed (exit {done.returncode}): {done.stderr.strip()[-2000:]}")
    return done.stdout


@dataclass(frozen=True)
class Tools:
    solidcraft_cli: Path
    cadmpeg: Path


#: How to get each program, said in the error rather than left to the reader to find out: this is
#: the first thing a new install hits, and "not found" without a way out is a dead end.
HOW_TO_INSTALL = {
    "solidcraft-cli": (
        f"It ships with SolidCraft {SOLIDCRAFT_VERSION} or newer — install the app from "
        "https://github.com/tangency-labs/solidcraft/releases and put its `solidcraft-cli` on your "
        "PATH (Homebrew's cask leaves it there already)"
    ),
    "cadmpeg": (
        f"Get it with `cargo install cadmpeg --version {CADMPEG_VERSION} --locked`, or take the prebuilt binary "
        "from https://github.com/cadmpeg/cadmpeg/releases"
    ),
}


def _find(env: str, name: str, fallbacks: list[Path]) -> Path:
    override = os.environ.get(env)
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return path
        raise ToolMissing(f"{env}={path} does not exist")
    found = shutil.which(name)
    if found:
        return Path(found)
    for path in fallbacks:
        if path.is_file():
            return path
    looked = ", ".join(["your PATH", *(str(p) for p in fallbacks)])
    raise ToolMissing(
        f"{name} not found (looked in {looked}). {HOW_TO_INSTALL[name]}; or set {env} to where it already is"
    )


# Both measured by installing SolidCraft (desktop plan, tasks 1 and 4). The .msi's folder: the public CI's
# `probe-windows-msi` job installed the SolidCraft 0.43.x .msi on a GitHub-hosted Windows Server runner
# and found `C:\Program Files\SolidCraft\solidcraft-cli.exe`, which is what `solidcraft_locations`
# builds from `%ProgramFiles%`. The .deb's path is what `dpkg-deb -c` lists.
WINDOWS_MSI_DIR = "SolidCraft"  # measured: public CI, Windows Server runner, SolidCraft 0.43.x .msi
LINUX_DEB_PATH = Path("/usr/bin/solidcraft-cli")  # measured: `dpkg-deb -c` of the 0.43.0 .deb


def solidcraft_locations(system: str, home: Path, environ: Mapping[str, str]) -> list[Path]:
    """Where to look for `solidcraft-cli` after the PATH, in order, on this system."""
    places = [home / ".local/bin/solidcraft-cli"]
    if system == "Darwin":
        bundle = "SolidCraft.app/Contents/MacOS/solidcraft-cli"
        places += [Path("/Applications") / bundle, home / "Applications" / bundle]
    elif system == "Windows":
        for variable in ("ProgramFiles", "LOCALAPPDATA"):
            base = environ.get(variable)
            if base:
                places.append(Path(base) / WINDOWS_MSI_DIR / "solidcraft-cli.exe")
    elif system == "Linux":
        places.append(LINUX_DEB_PATH)
    return places


def locate_solidcraft() -> Path:
    return _find(
        "F3D2SC_SOLIDCRAFT_CLI",
        "solidcraft-cli",
        solidcraft_locations(platform.system(), Path.home(), os.environ),
    )


def solidcraft_candidates() -> list[Path]:
    """Every existing `solidcraft-cli`, in the order `locate_solidcraft()` considers them.

    `locate_solidcraft()` keeps the first one found, which is what the command line wants. The desktop
    app instead picks the first candidate new enough: an old copy earlier in the order (a standalone
    CLI in ~/.local/bin, say) must not hide a current app bundle further down.
    """
    ordered: list[Path] = []
    override = os.environ.get("F3D2SC_SOLIDCRAFT_CLI")
    if override:
        ordered.append(Path(override).expanduser())
    found = shutil.which("solidcraft-cli")
    if found:
        ordered.append(Path(found))
    ordered += solidcraft_locations(platform.system(), Path.home(), os.environ)
    candidates: list[Path] = []
    seen: set[Path] = set()
    for path in ordered:
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            candidates.append(path)
    return candidates


def locate_cadmpeg() -> Path:
    return _find("F3D2SC_CADMPEG", "cadmpeg", [Path.home() / ".cargo/bin/cadmpeg"])


def find_tools() -> Tools:
    return Tools(solidcraft_cli=locate_solidcraft(), cadmpeg=locate_cadmpeg())


def tool_version(path: Path) -> str:
    out = subprocess.run(
        [str(path), "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=True,
    ).stdout
    return out.strip().split()[-1]
