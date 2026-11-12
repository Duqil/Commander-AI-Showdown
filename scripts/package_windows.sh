#!/usr/bin/env bash
# Pack a ready-to-run Windows zip: our code + the built Forge + a private copy
# of Java and Python. Whoever downloads it just unzips and double-clicks
# "Start Commander AI Showdown.bat". Nothing gets installed on their PC.
#
# The GitHub Action in .github/workflows/windows-release.yml runs this for you
# when you push a version tag. You can also run it yourself (Linux or Git Bash).
#
# Needs: Forge and the bridge already built (./scripts/setup_forge.sh), plus
#        curl, unzip and zip.
#
# Usage:  ./scripts/package_windows.sh v0.1.0
#   Makes dist/CommanderAIShowdown-v0.1.0-windows.zip
#
# Everything this script downloads or creates goes into ./dist/ (ignored by
# git). Delete dist/ to undo it.
set -euo pipefail

VERSION="${1:?Give a version, for example: ./scripts/package_windows.sh v0.1.0}"

# The Java and Python we bundle. Both are the official Windows 64-bit builds.
#   Java: Eclipse Temurin 21 "JRE" (just enough Java to run programs, no compiler).
#   Python: the "embeddable" Python from python.org: a small Python made for
#           shipping inside other programs. It has the whole standard library,
#           which is all we use.
JRE_URL="https://api.adoptium.net/v3/binary/latest/21/ga/windows/x64/jre/hotspot/normal/eclipse"
PYTHON_URL="https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="CommanderAIShowdown-$VERSION-windows"
DIST="$ROOT/dist"
OUT="$DIST/$NAME"          # the folder that becomes the zip
DOWNLOADS="$DIST/downloads"

FORGE_JAR="$(ls "$ROOT"/vendor/forge/forge-gui-desktop/target/forge-gui-desktop-*-jar-with-dependencies.jar | head -1)"
BRIDGE_JAR="$ROOT/forge-bridge/target/forge-bridge.jar"
[ -f "$BRIDGE_JAR" ] || { echo "Build Forge first: ./scripts/setup_forge.sh"; exit 1; }

rm -rf "$OUT" "$DIST/$NAME.zip"
mkdir -p "$OUT" "$DOWNLOADS"

echo ">> Copying our code (every file git tracks, nothing else)"
# git archive writes the committed files as a tar; we unpack it straight into OUT.
# So your local boys/, games/ and stats/ never end up in the zip.
git -C "$ROOT" archive --format=tar HEAD | tar -x -C "$OUT"
rm -rf "$OUT/.github" "$OUT/tests"

echo ">> Copying the built Forge engine and our bridge"
# Same folder layout as after setup_forge.sh, so the Python code finds
# everything without changes (see showdown/bridge/forge_process.py).
mkdir -p "$OUT/vendor/forge/forge-gui-desktop/target" "$OUT/vendor/forge/forge-gui" \
         "$OUT/forge-bridge/target"
cp "$FORGE_JAR" "$OUT/vendor/forge/forge-gui-desktop/target/"
cp "$BRIDGE_JAR" "$OUT/forge-bridge/target/"
# res/ holds Forge's card scripts and other data it reads while playing.
cp -r "$ROOT/vendor/forge/forge-gui/res" "$OUT/vendor/forge/forge-gui/"
# Skip the parts only Forge's own game window uses (Adventure mode, music):
# about 175 MB we never load.
rm -rf "$OUT/vendor/forge/forge-gui/res/adventure" "$OUT/vendor/forge/forge-gui/res/music"
cp "$ROOT/vendor/forge/LICENSE" "$OUT/vendor/forge/" 2>/dev/null || true

# download URL FILE: fetch once, reuse on the next run. JRE_ZIP / PYTHON_ZIP
# can point at files you already have instead.
download() {
    [ -f "$2" ] || { echo ">> Downloading $1"; curl -fsSL --retry 3 -o "$2.part" "$1"; mv "$2.part" "$2"; }
}

echo ">> Adding Java"
JRE_ZIP="${JRE_ZIP:-$DOWNLOADS/jre.zip}"
download "$JRE_URL" "$JRE_ZIP"
mkdir -p "$DIST/tmp-jre" "$OUT/runtime"
rm -rf "$DIST/tmp-jre"/*
unzip -q "$JRE_ZIP" -d "$DIST/tmp-jre"
# The zip holds one folder like "jdk-21.0.5+11-jre"; we rename it to runtime/java.
mv "$DIST/tmp-jre"/*/ "$OUT/runtime/java"
rm -rf "$DIST/tmp-jre"

echo ">> Adding Python"
PYTHON_ZIP="${PYTHON_ZIP:-$DOWNLOADS/python.zip}"
download "$PYTHON_URL" "$PYTHON_ZIP"
unzip -q "$PYTHON_ZIP" -d "$OUT/runtime/python"
# The embeddable Python only looks for code in the folders listed in its
# pythonXY._pth file. Add the project folder (two levels up from
# runtime/python) so "python -m showdown.viewer" finds our code.
PTH="$(ls "$OUT"/runtime/python/python3*._pth | head -1)"
printf '..\\..\r\n' >> "$PTH"

echo ">> Adding the start file and a short readme"
# .bat files are Windows scripts, so they get Windows line endings (\r\n).
#   %~dp0  = the folder this .bat file is in (works with spaces in the path)
#   cd /d  = also switch drive letter, e.g. from C: to E:
sed 's/$/\r/' > "$OUT/Start Commander AI Showdown.bat" <<'EOF'
@echo off
title Commander AI Showdown
cd /d "%~dp0"
rem Use the Java that came with this download, not one installed on the PC.
set "PATH=%~dp0runtime\java\bin;%PATH%"
echo Starting Commander AI Showdown. Your browser opens in a moment.
echo Close this window to stop it.
"%~dp0runtime\python\python.exe" -m showdown.viewer %*
pause
EOF

sed 's/$/\r/' > "$OUT/READ ME FIRST.txt" <<EOF
Commander AI Showdown $VERSION for Windows
==========================================

1. Unzip this whole folder somewhere you like (not just open it inside the zip).
2. Double-click "Start Commander AI Showdown.bat".
3. Your browser opens. Pick four decks and bots and watch them play.

Close the black window to stop it.

Everything stays inside this folder: Java and Python are bundled in runtime/,
card images download into vendor/card-images/, saved games go in games/,
and trained Boys in boys/. Delete the folder to remove it all.

Windows may warn that the file is from an unknown publisher. That's because
it isn't signed. Click "More info" and then "Run anyway".

Source code: https://github.com/Duqil/Commander-AI-Showdown

Licenses: Forge (the rules engine, vendor/forge) is GPL-3.0, source at
https://github.com/Card-Forge/forge . Java (runtime/java) is Eclipse Temurin,
GPLv2 with Classpath Exception. Python (runtime/python) is under the PSF
License. Their license files are in those folders.
EOF

echo ">> Zipping"
(cd "$DIST" && zip -qr "$NAME.zip" "$NAME")
echo ">> Done: $DIST/$NAME.zip ($(du -h "$DIST/$NAME.zip" | cut -f1))"
