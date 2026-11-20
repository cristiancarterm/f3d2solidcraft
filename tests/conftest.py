import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from f3d2solidcraft.reader import read_design
from f3d2solidcraft.tools import ToolMissing, find_tools, measured_engine

SAMPLE = Path(
    os.environ.get("F3D2SC_SAMPLE", "~/.f3d2solidcraft/corpus/samples/test weird box.f3d")
).expanduser()
CORPUS = Path(os.environ.get("F3D2SC_CORPUS", "~/.f3d2solidcraft/corpus/propios")).expanduser()
PACKAGES = Path(os.environ.get("F3D2SC_PACKAGES", "~/.f3d2solidcraft/corpus/propios-f3z")).expanduser()
# Files kept out of the bench corpus because converting them costs too much to put in a gate that
# runs on every change: `corpus-12.f3z` takes about 5 minutes and peaks near 9 GB
# (spec section 92), against 30 s for the slowest of the 15. Reading one is cheap — 34 s for that
# file — so tests over what the file *says* live here and the conversion is measured by hand.
#
# Beside the corpus and NOT inside it, which is the whole point: `bench` takes a directory and
# walks it with `rglob`, so a file left under `corpus/` would join the gate however it was named.
ASIDE = Path(os.environ.get("F3D2SC_ASIDE", "~/.f3d2solidcraft/aside")).expanduser()
# CI sets this so a missing engine or sample fails the run instead of skipping it green.
REQUIRE_INTEGRATION = os.environ.get("F3D2SC_REQUIRE_INTEGRATION") == "1"


def _unavailable(reason: str) -> None:
    (pytest.fail if REQUIRE_INTEGRATION else pytest.skip)(reason)


#: Set by `pytest_configure` when it pins the engine, so a test can tell "the suite pinned it" from
#: "whoever ran the suite chose an engine on purpose".
PINNED_BY_SUITE = "F3D2SC_ENGINE_PINNED_BY_SUITE"


def pytest_configure(config):
    """Pin the engine for the whole session — the fixture, `main()`, and every subprocess.

    The first version of this pinned only what the `tools` fixture returned (spec section 145), and
    measured on a plain `pytest` that covers less than half of it: `cli.main` and the MCP server
    call `find_tools()` themselves and went straight to the PATH, so every test that converts
    through `main()` — and the bench's child processes — kept running on whatever the app had
    updated itself to. Setting the environment variable `find_tools()` already honours reaches all
    of them, children included, because they inherit it (spec section 146).

    An engine chosen on purpose with `F3D2SC_SOLIDCRAFT_CLI` still wins: that is how the lanes and
    every probe pick one. Without a pinned copy on this machine (CI, a fresh checkout) nothing is
    set and the PATH is used, as before.
    """
    if os.environ.get("F3D2SC_SOLIDCRAFT_CLI"):
        return
    pinned = measured_engine()
    if pinned is not None:
        os.environ["F3D2SC_SOLIDCRAFT_CLI"] = str(pinned)
        os.environ[PINNED_BY_SUITE] = "1"


@pytest.fixture(scope="session")
def tools():
    try:
        return find_tools()
    except ToolMissing as exc:
        _unavailable(f"external tool missing: {exc}")


@pytest.fixture
def short_name():
    """Windows' own 8.3 spelling of an existing path, as `GetShortPathNameW` gives it.

    Skips off Windows, and on a volume that does not make 8.3 names: there is nothing to spell then.
    A GitHub runner makes them — its temp dir is `C:\\Users\\RUNNER~1\\...` — which is how every
    conversion there came to exit 2 before `long_path`.
    """
    if os.name != "nt":
        pytest.skip("8.3 short names exist only on Windows")
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel32.GetShortPathNameW.restype = wintypes.DWORD

    def spell(path: Path) -> Path:
        size = kernel32.GetShortPathNameW(str(path), None, 0)
        if not size:
            raise OSError(ctypes.get_last_error(), f"no short name for {path}")
        buffer = ctypes.create_unicode_buffer(size)
        kernel32.GetShortPathNameW(str(path), buffer, size)
        if buffer.value == str(path):
            pytest.skip(f"this volume makes no 8.3 names: {path} has none")
        return Path(buffer.value)

    return spell


@pytest.fixture
def unc():
    r"""The UNC spelling of a local path, through the machine's own admin share: `\\localhost\C$\...`.

    A network folder (a NAS, a server share) is spelt that way, and a Windows machine reaches its own
    drives through the same SMB client as a remote one, so this is a share without a second machine.
    Skips off Windows. On Windows a share that is not there fails under `F3D2SC_REQUIRE_INTEGRATION`
    (CI: the GitHub runner has `C$`) and skips elsewhere, where admin shares may be turned off.
    """
    if os.name != "nt":
        pytest.skip("UNC paths exist only on Windows")

    def spell(path: Path) -> Path:
        local = Path(os.path.realpath(path))
        drive = local.drive
        if len(drive) != 2 or drive[1] != ":":
            raise ValueError(f"not on a drive letter: {local}")
        share = Path(f"\\\\localhost\\{drive[0]}$\\")
        if not share.is_dir():
            _unavailable(f"the admin share {share} is not reachable on this machine")
        return share / local.relative_to(local.anchor)

    return spell


@pytest.fixture(scope="session")
def sample_f3d() -> Path:
    if not SAMPLE.is_file():
        _unavailable(f"real sample missing: put it at {SAMPLE} or set F3D2SC_SAMPLE")
    return SAMPLE


@pytest.fixture(scope="session")
def design(sample_f3d, tools, tmp_path_factory):
    return read_design(sample_f3d, tools.cadmpeg, tmp_path_factory.mktemp("design"))


@pytest.fixture(scope="session")
def package_design(tools, tmp_path_factory):
    """read_design for a `.f3z` of the private corpus; skips when it is not there."""

    def load(filename: str):
        path = PACKAGES / filename
        if not path.is_file():
            pytest.skip(f"private corpus package missing: {path}")
        return read_design(path, tools.cadmpeg, tmp_path_factory.mktemp("package"))

    return load


@pytest.fixture(scope="session")
def aside_design(tools, tmp_path_factory):
    """read_design for a file kept out of the bench corpus; skips when it is not there.

    Session-scoped on purpose: the one file this reads takes 34 s, so every test that asks about
    what it says pays for it once.
    """

    read: dict[str, object] = {}

    def load(filename: str):
        path = ASIDE / filename
        if not path.is_file():
            pytest.skip(f"file kept aside is missing: {path}")
        # Kept by name, not just session-scoped: the scope caches this function, and without the
        # dict every test that called it paid the 34 s again — measured, 226 s for the module
        # against 60 s with it. A `Design` is frozen, so sharing one reading changes nothing a test
        # can see.
        if filename not in read:
            read[filename] = read_design(path, tools.cadmpeg, tmp_path_factory.mktemp("aside"))
        return read[filename]

    return load


@pytest.fixture
def aside_ctx(tools):
    """A mapper context over a design already read, with no oracle to ask.

    Takes the design rather than a filename, so a test can share `aside_design`'s one reading and
    still get an engine of its own.
    """
    from f3d2solidcraft.engine import Engine
    from f3d2solidcraft.mappers import Context

    @contextmanager
    def load(design):
        with Engine(tools.solidcraft_cli) as eng:
            eng.call("new_design", {})
            yield Context(eng, design, {}, {}, {}, {}, {})

    return load


@pytest.fixture(scope="session")
def corpus_design(tools, tmp_path_factory):
    """read_design for a file of the private corpus; skips when it is not there."""
    from f3d2solidcraft.reader import read_design

    def load(filename: str):
        path = CORPUS / filename
        if not path.is_file():
            pytest.skip(f"private corpus file missing: {path}")
        return read_design(path, tools.cadmpeg, tmp_path_factory.mktemp("corpus"))

    return load


@pytest.fixture
def corpus_ctx(tools, tmp_path):
    """A mapper context over any corpus design, with no oracle to ask.

    Was `corpus-04_ctx` in `test_mappers`, fixed to `corpus-04`, then any corpus file; it lives here
    because a second module (`test_revolve`) needs the same thing and copying it would let the two
    drift. Tests on it follow a *kind* of feature rather than a file, and which file carries which
    kind moves with cadmpeg: on 0.6.7 every one of that design's extrudes resolves to explicit
    profile indices, so its region and selection cases went elsewhere.
    """
    from f3d2solidcraft.engine import Engine
    from f3d2solidcraft.mappers import Context
    from f3d2solidcraft.reader import read_design

    @contextmanager
    def load(filename: str):
        path = CORPUS / filename
        if not path.is_file():
            pytest.skip(f"private corpus file missing: {path}")
        design = read_design(path, tools.cadmpeg, tmp_path)
        with Engine(tools.solidcraft_cli) as eng:
            eng.call("new_design", {})
            yield Context(eng, design, {}, {}, {}, {}, {})

    return load


@pytest.fixture
def saved_tree(tools):
    """A saved design's component tree, and the names of the empty components the engine would let go.

    Read in a fresh engine, and each empty component other than the root is offered to
    `component.delete`: one it accepts is scaffolding the conversion should have removed. Nothing is
    saved back, so asking does not change the file.
    """
    from f3d2solidcraft.engine import CommandRefused, Engine

    def read(path: Path) -> tuple[list[dict], list[str]]:
        with Engine(tools.solidcraft_cli, [path.parent]) as eng:
            eng.call("open", {"path": str(path)})
            tree = eng.execute("component.list", {"compact": True, "bodies": True})["components"]
            deletable = []
            for c in tree:
                empty = not any(
                    c.get(k) for k in ("bodies", "children", "features", "sketches", "construction")
                )
                if c.get("parent") is None or not empty:
                    continue
                try:
                    eng.execute("component.delete", {"component": c["id"]})
                except CommandRefused:
                    continue
                deletable.append(c["name"])
        return tree, deletable

    return read
