#!/usr/bin/env bash
# Compile our small Java bridge against the Forge jar that setup_forge.sh built.
# Re-run this after changing anything in forge-bridge/src.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Forge builds one big jar that contains Forge plus every library it uses.
FORGE_JAR="$(ls "$ROOT"/vendor/forge/forge-gui-desktop/target/forge-gui-desktop-*-jar-with-dependencies.jar | head -1)"
OUT="$ROOT/forge-bridge/target"

rm -rf "$OUT/classes"
mkdir -p "$OUT/classes"
# Collect the .java files into a bash array. -print0 and mapfile -d '' split on
# a "null" character instead of spaces, so folder names like "Code Projects" work.
mapfile -d '' SOURCES < <(find "$ROOT/forge-bridge/src/main/java" -name '*.java' -print0)
javac --release 17 -encoding UTF-8 -cp "$FORGE_JAR" -d "$OUT/classes" "${SOURCES[@]}"
jar --create --file "$OUT/forge-bridge.jar" -C "$OUT/classes" .
echo ">> Built $OUT/forge-bridge.jar"

# Forge's card files as one zip: much faster first start on Windows.
# (Does nothing if the zip is already up to date.)
"$ROOT/scripts/pack_cards.sh"
