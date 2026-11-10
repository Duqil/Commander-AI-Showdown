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
javac --release 17 -encoding UTF-8 -cp "$FORGE_JAR" -d "$OUT/classes" \
    $(find "$ROOT/forge-bridge/src/main/java" -name '*.java')
jar --create --file "$OUT/forge-bridge.jar" -C "$OUT/classes" .
echo ">> Built $OUT/forge-bridge.jar"
