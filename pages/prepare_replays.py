"""Turn games uploaded straight from games/ into finished replays.

You can drag a saved game (a .json.gz file from your games/ folder) into
pages/replays/ on github.com. That file has the game but not the card picture
URLs or a title yet. GitHub's workflow (.github/workflows/replay-site.yml) runs
this script before building the site: it finishes every such file in place,
and the workflow saves the result back to the repo, so it's done only once.

Run it yourself (from the project folder) to do the same on your computer:

    python -m pages.prepare_replays
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from pages.export_replay import REPLAY_DIR, make_replay


def is_finished(file: Path) -> bool:
    """A finished replay has card picture URLs ("images"); a plain saved game doesn't."""
    with gzip.open(file, "rt", encoding="utf-8") as f:
        return "images" in json.load(f)


def prepare_all() -> list[Path]:
    """Finish every unfinished replay in pages/replays/. Returns the files it changed."""
    changed = []
    for file in sorted(REPLAY_DIR.glob("*.json.gz")):
        if is_finished(file):
            continue
        file.write_bytes(make_replay(file))   # read the plain game, write the finished replay
        print(f"Finished {file.name}")
        changed.append(file)
    if not changed:
        print("All replays are already finished.")
    return changed


if __name__ == "__main__":
    prepare_all()
