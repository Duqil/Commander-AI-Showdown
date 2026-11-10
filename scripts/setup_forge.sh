#!/usr/bin/env bash
# Download and build Forge (the rules engine), then build our Java bridge.
#
# Needs: git, Java 17+ (21 recommended), Maven 3.9+.
# Takes 5-15 minutes the first time; Maven downloads a lot of jars.
#
# Usage:  ./scripts/setup_forge.sh
#
# Forge is cloned into ./vendor/forge (ignored by git). We pin one Forge commit
# so everyone builds exactly the same engine; bump FORGE_COMMIT to upgrade.
set -euo pipefail

FORGE_REPO="https://github.com/Card-Forge/forge.git"
FORGE_COMMIT="c58942e9f3768a11e2336128905e541f73283034"   # master, 2026-11-10

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FORGE_DIR="$ROOT/vendor/forge"

if [ ! -d "$FORGE_DIR/.git" ]; then
    echo ">> Cloning Forge into $FORGE_DIR"
    git clone --filter=blob:none "$FORGE_REPO" "$FORGE_DIR"
fi

echo ">> Checking out pinned Forge commit $FORGE_COMMIT"
git -C "$FORGE_DIR" fetch --quiet origin "$FORGE_COMMIT" || true
git -C "$FORGE_DIR" checkout --quiet "$FORGE_COMMIT"

echo ">> Building Forge (only the modules we need, tests skipped)"
mvn -B -q -f "$FORGE_DIR/pom.xml" -DskipTests -Dcheckstyle.skip \
    -pl forge-gui-desktop -am package

echo ">> Building the bridge"
"$ROOT/scripts/build_bridge.sh"

echo ">> Done. Try:  python -m showdown.run_game"
