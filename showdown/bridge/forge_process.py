"""Starting the Java side (Forge + our bridge) as a background process."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Path(__file__) is this file. .parents[2] walks up: bridge/ -> showdown/ -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
FORGE_DIR = REPO_ROOT / "vendor" / "forge"
BRIDGE_TARGET = REPO_ROOT / "forge-bridge" / "target"
# Forge's settings and cache. By default Forge puts these in your home folder
# (e.g. %APPDATA%\Forge on Windows). We keep them inside vendor/ instead, so
# deleting the project folder removes everything.
FORGE_DATA_DIR = REPO_ROOT / "vendor" / "forge-data"


def classpath() -> str:
    """The jars Java needs: our bridge plus Forge's all-in-one jar."""
    bridge_jar = BRIDGE_TARGET / "forge-bridge.jar"
    forge_jars = sorted((FORGE_DIR / "forge-gui-desktop" / "target").glob("*-jar-with-dependencies.jar"))
    if not bridge_jar.exists() or not forge_jars:
        raise FileNotFoundError("Forge or the bridge isn't built yet. Run ./scripts/setup_forge.sh first.")
    # os.pathsep is ":" on Mac/Linux and ";" on Windows.
    return os.pathsep.join([str(bridge_jar), str(forge_jars[-1])])


def write_forge_profile() -> Path:
    """Tell Forge where to keep its settings and cache.

    Forge reads ``forge.profile.properties`` from its forge-gui/ folder at
    startup. It's a plain "key=value" text file. We point both folders into
    vendor/forge-data/.
    """
    profile = FORGE_DIR / "forge-gui" / "forge.profile.properties"
    # .as_posix() writes forward slashes, even on Windows. Java accepts those,
    # and a backslash would be read as an escape character in this file format.
    lines = [
        "# Written by showdown/bridge/forge_process.py. Keeps Forge's files inside the project.",
        f"userDir={(FORGE_DATA_DIR / 'user').as_posix()}",
        f"cacheDir={(FORGE_DATA_DIR / 'cache').as_posix()}",
    ]
    profile.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return profile


def start_forge(
    port: int,
    seats: list[tuple[Path, str]],
    seed: int | None = None,
    java: str = "java",
    log_file: Path | None = None,
) -> subprocess.Popen:
    """Launch Forge, which connects back to us on ``port``.

    ``seats`` is a list of (deck file, controller) pairs, where controller is
    "python" or "forge".
    """
    cmd = [java, "-Xmx2g", "-cp", classpath(), "showdown.bridge.BridgeMain", "--port", str(port)]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    for deck, controller in seats:
        cmd += ["--seat", f"{Path(deck).resolve()}={controller}"]

    write_forge_profile()

    # Forge prints a lot of its own logging. Send it to a file so our output stays readable.
    out = open(log_file, "w") if log_file else subprocess.DEVNULL
    # Forge finds its card scripts in "res/", relative to forge-gui/.
    return subprocess.Popen(cmd, cwd=FORGE_DIR / "forge-gui", stdout=out, stderr=subprocess.STDOUT)
