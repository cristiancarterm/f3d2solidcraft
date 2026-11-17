"""Put a verified cadmpeg inside the app before Briefcase packages it (spec, Empaque).

    uv run python scripts/fetch_cadmpeg.py [--dest DIR]

Downloads the release asset for this system, checks it against the sha256 pinned here (the same
numbers the plan's Global Constraints carry), and installs the binary. On macOS it fetches both
architectures and joins them with `lipo`, so the .dmg runs on Apple silicon and Intel alike.
"""

import hashlib
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
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


def _fetch(asset: str, into: Path) -> Path:
    target = into / asset
    urllib.request.urlretrieve(f"{BASE}/{asset}", target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != SHA256[asset]:
        raise SystemExit(f"{asset}: sha256 {digest} is not the pinned {SHA256[asset]}")
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


def main(argv: list[str]) -> int:
    dest = Path(argv[argv.index("--dest") + 1]) if "--dest" in argv else DEFAULT_DEST
    dest.mkdir(parents=True, exist_ok=True)
    system = platform.system()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        if system == "Windows":
            binary = _binary(_fetch("cadmpeg-x86_64-pc-windows-msvc.zip", work), work, "cadmpeg.exe")
            shutil.copy2(binary, dest / "cadmpeg.exe")
        elif system == "Darwin":
            arm = _binary(_fetch("cadmpeg-aarch64-apple-darwin.tar.xz", work), work, "cadmpeg")
            intel = _binary(_fetch("cadmpeg-x86_64-apple-darwin.tar.xz", work), work, "cadmpeg")
            subprocess.run(
                ["lipo", "-create", str(arm), str(intel), "-output", str(dest / "cadmpeg")], check=True
            )
        else:
            binary = _binary(_fetch("cadmpeg-x86_64-unknown-linux-gnu.tar.xz", work), work, "cadmpeg")
            shutil.copy2(binary, dest / "cadmpeg")
    installed = dest / ("cadmpeg.exe" if system == "Windows" else "cadmpeg")
    installed.chmod(0o755)
    print(
        subprocess.run(
            [str(installed), "--version"], capture_output=True, text=True, check=True
        ).stdout.strip()
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
