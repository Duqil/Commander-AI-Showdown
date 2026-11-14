#!/usr/bin/env bash
# Pack Forge's ~34,000 card files into one zip, so a game starts faster.
#
# Why: before each first game, Forge opens every card file once. On Windows
# the virus scanner checks each file the first time it is opened, so a cold
# start could take minutes. One zip is one file to open and check. Forge's
# own official downloads ship the cards exactly like this.
#
# What it does:
#   1. Makes vendor/forge/forge-gui/res/cardsfolder/cardsfolder.zip from the
#      card files of the Forge version that is checked out.
#   2. Hides the loose card files with git's "sparse checkout". Forge reads
#      loose files AND the zip, so they have to go for the zip to help. They
#      are not lost: git still has them, and --undo brings them back.
#
# setup_forge.sh and build_bridge.sh run this for you. It does nothing when
# the zip already matches the Forge version, so running it again is cheap.
#
# Usage:  ./scripts/pack_cards.sh          pack (or check it is up to date)
#         ./scripts/pack_cards.sh --undo   loose card files back, zip removed
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FORGE_DIR="$ROOT/vendor/forge"
CARDS="forge-gui/res/cardsfolder"
ZIP="$FORGE_DIR/$CARDS/cardsfolder.zip"
STAMP="$ZIP.commit"   # which Forge version the zip was made from

if [ ! -d "$FORGE_DIR/.git" ]; then
    echo "Forge isn't downloaded yet. Run ./scripts/setup_forge.sh first." >&2
    exit 1
fi

if [ "${1:-}" = "--undo" ]; then
    git -C "$FORGE_DIR" sparse-checkout disable   # puts the hidden files back on disk
    rm -f "$ZIP" "$STAMP"
    echo ">> Loose card files are back and the zip is gone."
    exit 0
fi

COMMIT="$(git -C "$FORGE_DIR" rev-parse HEAD)"
if [ -f "$ZIP" ] && [ "$(cat "$STAMP" 2>/dev/null)" = "$COMMIT" ] \
        && [ "$(git -C "$FORGE_DIR" config core.sparseCheckout || true)" = "true" ]; then
    echo ">> Card zip is up to date."
    exit 0
fi

echo ">> Packing Forge's card files into one zip (takes a few seconds)"
# "git archive" builds the zip straight from git's copy of the files, so it
# works whether the loose files are on disk or already hidden. -1 = quick
# compression, like Forge's own downloads. Write to a temporary name first,
# so a half-written zip never sits where Forge looks.
git -C "$FORGE_DIR" archive --format=zip -1 -o "$ZIP.tmp" "HEAD:$CARDS"
mv -f "$ZIP.tmp" "$ZIP"
echo "$COMMIT" > "$STAMP"

# Keep "git status" inside vendor/forge clean: our two files aren't Forge's.
EXCLUDE="$FORGE_DIR/.git/info/exclude"
grep -qxF "/$CARDS/cardsfolder.zip*" "$EXCLUDE" 2>/dev/null || echo "/$CARDS/cardsfolder.zip*" >> "$EXCLUDE"

echo ">> Hiding the loose card files (git keeps them; --undo brings them back)"
# Sparse checkout rules (same syntax as .gitignore): keep everything ("/*")
# except the card folders a, b, c, ... inside cardsfolder.
if ! git -C "$FORGE_DIR" sparse-checkout set --no-cone '/*' "!/$CARDS/*/"; then
    echo "Hiding the files needs Git 2.35 or newer (git --version shows yours)." >&2
    exit 1
fi
echo ">> Done: $(du -h "$ZIP" | cut -f1) card zip."
