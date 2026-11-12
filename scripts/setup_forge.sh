#!/usr/bin/env bash
# Download and build Forge (the rules engine), then build our Java bridge.
#
# Needs: git, Java 21 (19 or newer: showdown/bridge/forge_process.py starts Java with
#        -XX:+AutoCreateSharedArchive, which Java 17 doesn't know), Maven 3.9+.
# Takes 5-15 minutes the first time; Maven downloads a lot of jars.
#
# Usage:  ./scripts/setup_forge.sh
#
# Everything this script downloads or builds goes into ./vendor/ (ignored by git):
#   vendor/forge/       Forge's source code and the built engine
#   vendor/m2/          libraries Maven downloads to build Forge (normally ~/.m2)
#   vendor/forge-data/  Forge's settings and cache, created on the first game
#                       (normally in your home folder; see showdown/bridge/forge_process.py)
# Nothing is installed system-wide. Delete vendor/ to undo it all.
#
# We pin one Forge commit so everyone builds exactly the same engine; bump
# FORGE_COMMIT to upgrade.
set -euo pipefail

FORGE_REPO="https://github.com/Card-Forge/forge.git"
FORGE_COMMIT="c58942e9f3768a11e2336128905e541f73283034"   # master, 2026-11-10

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FORGE_DIR="$ROOT/vendor/forge"
MAVEN_REPO="$ROOT/vendor/m2"

if [ ! -d "$FORGE_DIR/.git" ]; then
    echo ">> Cloning Forge into $FORGE_DIR"
    git clone --filter=blob:none "$FORGE_REPO" "$FORGE_DIR"
fi

echo ">> Checking out pinned Forge commit $FORGE_COMMIT"
git -C "$FORGE_DIR" fetch --quiet origin "$FORGE_COMMIT" || true
git -C "$FORGE_DIR" checkout --quiet "$FORGE_COMMIT"

echo ">> Building Forge (only the modules we need, tests skipped)"
# Run Maven from inside Forge's folder: Forge's .mvn/maven.config points to a
# settings file by a relative path, so it fails from anywhere else.
(cd "$FORGE_DIR" && mvn -B -q -Dmaven.repo.local="$MAVEN_REPO" -DskipTests -Dcheckstyle.skip \
    -pl forge-gui-desktop -am package)

echo ">> Building the bridge"
"$ROOT/scripts/build_bridge.sh"

echo ">> Done. Try:  python -m showdown.run_game"
