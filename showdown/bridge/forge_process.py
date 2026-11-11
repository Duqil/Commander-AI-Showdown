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


# Forge settings we want for every game. Forge keeps its settings in
# user/preferences/forge.preferences, one "KEY=value" per line.
#   LOAD_CARD_SCRIPTS_LAZILY: by default Forge reads all ~34,000 card scripts
#   before every game, although a game only uses a few hundred. Lazily, it
#   reads a card the first time something asks for it. That makes the wait
#   before a game starts about half as long.
FORGE_PREFERENCES = {"LOAD_CARD_SCRIPTS_LAZILY": "true"}


def write_forge_preferences() -> Path:
    """Put FORGE_PREFERENCES into Forge's settings file, keeping any other lines."""
    prefs = FORGE_DATA_DIR / "user" / "preferences" / "forge.preferences"
    prefs.parent.mkdir(parents=True, exist_ok=True)
    lines = prefs.read_text(encoding="utf-8").splitlines() if prefs.exists() else []
    # Drop our keys from the old lines (whatever their value was), then add them back.
    kept = [line for line in lines if line.split("=", 1)[0] not in FORGE_PREFERENCES]
    wanted = kept + [f"{key}={value}" for key, value in FORGE_PREFERENCES.items()]
    if wanted != lines:  # only write when something changed
        prefs.write_text("\n".join(wanted) + "\n", encoding="utf-8")
    return prefs


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
    # -XX:SharedArchiveFile + AutoCreateSharedArchive (Java 19+): the first run
    # saves Java's prepared classes to a file, later runs load them from it
    # instead of preparing them again. Java rebuilds the file by itself when the
    # jars change (e.g. after build_bridge.sh).
    archive = FORGE_DATA_DIR / "forge-classes.jsa"
    cmd = [java, "-Xmx2g", "-XX:+AutoCreateSharedArchive", f"-XX:SharedArchiveFile={archive}",
           "-cp", classpath(), "showdown.bridge.BridgeMain", "--port", str(port)]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    for deck, controller in seats:
        cmd += ["--seat", f"{Path(deck).resolve()}={controller}"]

    write_forge_profile()
    write_forge_preferences()

    # Forge prints a lot of its own logging. Send it to a file so our output stays readable.
    out = open(log_file, "w") if log_file else subprocess.DEVNULL
    # Forge finds its card scripts in "res/", relative to forge-gui/.
    return subprocess.Popen(cmd, cwd=FORGE_DIR / "forge-gui", stdout=out, stderr=subprocess.STDOUT)
