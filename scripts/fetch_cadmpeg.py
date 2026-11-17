"""Put a verified cadmpeg inside the app before Briefcase packages it (spec, Empaque).

    uv run python scripts/fetch_cadmpeg.py [--dest DIR]

Downloads the release asset for this system, checks it against the sha256 pinned here (the same
numbers the plan's Global Constraints carry), and installs the binary. On macOS it fetches both
architectures and joins them with `lipo`, so the .dmg runs on Apple silicon and Intel alike.

cadmpeg publishes Linux binaries for x86_64 only: on any other Linux machine this stops with a
message instead of putting an x86_64 binary in an app that could never run it.
"""

import hashlib
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

VERSION = "0.6.11"
BASE = f"https://github.com/cadmpeg/cadmpeg/releases/download/v{VERSION}"
SHA256 = {
    "cadmpeg-x86_64-pc-windows-msvc.zip": "4e1ec1f697b089f7ea9a8f8637e1242607b172f502333569a794a8191ecb47f1",
    "cadmpeg-aarch64-apple-darwin.tar.xz": "71b9ba0b224977774e09f5d1374219623401e13c01f695b341dae6de673c0ee6",
    "cadmpeg-x86_64-apple-darwin.tar.xz": "304a6ca4de389a8935601a3a43312eaca8e29b7ec2314e44a8d63de6f6e1123e",
    "cadmpeg-x86_64-unknown-linux-gnu.tar.xz": "cdc95d3e6f07944f8a9a80c99259bd957d5afeecd4337d1a80ed6659e37a3abd",
}
DEFAULT_DEST = Path(__file__).resolve().parents[1] / "src/f3d2solidcraft_app/resources/bin"
#: Seconds a connection may stay silent (connecting, or between two reads) before the download stops.
TIMEOUT = 60
#: How `platform.machine()` spells x86_64 on the systems that build the app.
X86_64 = {"x86_64", "amd64"}


class FetchError(SystemExit):
    """A download or a machine this script cannot serve: the message is what the user reads."""


def assets_for(system: str, machine: str) -> list[str]:
    """The release assets to fetch for `platform.system()` and `platform.machine()`.

    Windows takes the x86_64 build on any machine (Windows on Arm runs it emulated); macOS takes both
    and joins them. Linux has no emulation to fall back on, so anything but x86_64 is refused.
    """
    if system == "Windows":
        return ["cadmpeg-x86_64-pc-windows-msvc.zip"]
    if system == "Darwin":
        return ["cadmpeg-aarch64-apple-darwin.tar.xz", "cadmpeg-x86_64-apple-darwin.tar.xz"]
    if system == "Linux" and machine.lower() in X86_64:
        return ["cadmpeg-x86_64-unknown-linux-gnu.tar.xz"]
    if system == "Linux":
        raise FetchError(
            f"cadmpeg {VERSION} publishes a Linux binary for x86_64 only, and this machine is "
            f"{machine or 'of an unknown architecture'}: build the Linux app on an x86_64 machine"
        )
    raise FetchError(f"cadmpeg {VERSION} publishes no binary for {system or 'this system'}")


def download(url: str, target: Path, timeout: float = TIMEOUT) -> None:
    """`url` into `target`; a server that does not answer within `timeout` seconds stops it."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response, target.open("wb") as sink:
            shutil.copyfileobj(response, sink)
    except TimeoutError:
        raise FetchError(f"downloading {url} timed out: no answer for {timeout:g} s") from None
    except urllib.error.URLError as exc:
        # A timeout while connecting arrives wrapped in a URLError.
        if isinstance(exc.reason, TimeoutError):
            raise FetchError(f"downloading {url} timed out: no answer for {timeout:g} s") from None
        raise FetchError(f"could not download {url}: {exc.reason}") from None
    except OSError as exc:
        raise FetchError(f"could not download {url}: {exc}") from None


def _fetch(asset: str, into: Path) -> Path:
    target = into / asset
    download(f"{BASE}/{asset}", target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != SHA256[asset]:
        raise FetchError(f"{asset}: sha256 {digest} is not the pinned {SHA256[asset]}")
    return target


def _binary(archive: Path, into: Path, name: str) -> Path:
    out = into / archive.name.split(".")[0]
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(out)
    else:
        with tarfile.open(archive) as t:
            t.extractall(out, filter="data")
    return next(p for p in out.rglob(name) if p.is_file())


def main(argv: list[str], system: str | None = None, machine: str | None = None) -> int:
    dest = Path(argv[argv.index("--dest") + 1]) if "--dest" in argv else DEFAULT_DEST
    system = platform.system() if system is None else system
    machine = platform.machine() if machine is None else machine
    assets = assets_for(system, machine)  # refuses before anything is written or downloaded
    dest.mkdir(parents=True, exist_ok=True)
    name = "cadmpeg.exe" if system == "Windows" else "cadmpeg"
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        binaries = [_binary(_fetch(asset, work), work, name) for asset in assets]
        if len(binaries) > 1:
            subprocess.run(["lipo", "-create", *map(str, binaries), "-output", str(dest / name)], check=True)
        else:
            shutil.copy2(binaries[0], dest / name)
    installed = dest / name
    installed.chmod(0o755)
    print(
        subprocess.run(
            [str(installed), "--version"], capture_output=True, text=True, check=True
        ).stdout.strip()
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
