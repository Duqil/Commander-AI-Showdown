"""Where the Boys live on disk: one JSON file per brain, plus a history file.

    boys/Kaiba.json     Kaiba's brain (open it to see what he has learned)
    boys/history.csv    one row per Boy per training game, for charts

The boys/ folder is ignored by git, so your trained Boys stay on your machine.
Delete a file to start that Boy over from scratch.
"""

from __future__ import annotations

import csv
import json
import re
import threading
from pathlib import Path

from showdown.boys.brain import DECISION_KINDS, Brain

REPO_ROOT = Path(__file__).resolve().parents[2]
BOYS_DIR = REPO_ROOT / "boys"
HISTORY_FILE = BOYS_DIR / "history.csv"
HISTORY_COLUMNS = ["time", "game", "boy", "boy_games", "seat", "deck", "lineup",
                   "won", "outlived", "reward", "turns"]

_brains: dict[str, Brain] = {}   # brains already loaded in this Python process
_lock = threading.Lock()
# A Boy's name is also its file name (boys/<name>.json), so only characters that
# are safe in file names on Windows, Mac and Linux: letters, digits, spaces,
# - _ and '. It must start and end with a letter or digit, so "Nicol Bolas"
# is fine but " Bolas" or "Bolas." are not (Windows drops a trailing dot or space).
#   (?: ... )?   an optional group: a one-letter name has no middle part.
VALID_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9 _'-]{0,30}[A-Za-z0-9])?$")


def brain_path(name: str) -> Path:
    if not VALID_NAME.match(name):
        raise ValueError(f"Boy names may use letters, digits, spaces, - _ and ', up to 32 characters (got {name!r})")
    return BOYS_DIR / f"{name}.json"


def load_brain(name: str) -> Brain:
    """The brain called ``name``: from memory, from boys/<name>.json, or brand new.

    Every bot with the same name shares one Brain object, so a Boy that plays
    several games at once still learns into one brain.
    """
    with _lock:
        if name not in _brains:
            path = brain_path(name)
            if path.exists():
                _brains[name] = Brain.from_dict(json.loads(path.read_text(encoding="utf-8")))
            else:
                _brains[name] = Brain(name)
        return _brains[name]


def save_brain(brain: Brain) -> Path:
    path = brain_path(brain.name)
    with _lock:  # two games ending at once must not write the same file together
        BOYS_DIR.mkdir(exist_ok=True)
        tmp = path.with_suffix(".tmp")
        # Write to a temporary file first, then swap it in, so a crash mid-write
        # never leaves a half-written brain behind.
        tmp.write_text(json.dumps(brain.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(path)
    return path


def list_boys() -> list[str]:
    """Names of all saved Boys."""
    if not BOYS_DIR.exists():
        return []
    return sorted(p.stem for p in BOYS_DIR.glob("*.json"))


def append_history(rows: list[dict]) -> None:
    if not rows:
        return
    with _lock:
        BOYS_DIR.mkdir(exist_ok=True)
        new_file = not HISTORY_FILE.exists()
        with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=HISTORY_COLUMNS)
            if new_file:
                writer.writeheader()
            writer.writerows(rows)


def read_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    with open(HISTORY_FILE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def forget(name: str, kind: str) -> Path:
    """Wipe what a Boy learned for one kind of decision (e.g. "mulligan").

    Everything else stays. That kind goes back to the starting weights, so the
    Boy follows SimpleBot there again and relearns from scratch. Useful when a
    habit went wrong, like Yugi throwing away good opening hands.
    """
    if not brain_path(name).exists():
        raise FileNotFoundError(f"No Boy called {name!r} in {BOYS_DIR}")
    brain = load_brain(name)
    brain.weights.pop(kind, None)
    return save_brain(brain)


if __name__ == "__main__":
    # python -m showdown.boys.storage forget Yugi mulligan
    import argparse

    parser = argparse.ArgumentParser(description="Manage saved Boys.")
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("forget", help="reset one kind of decision for a Boy")
    f.add_argument("name")
    f.add_argument("kind", help=", ".join(DECISION_KINDS))
    args = parser.parse_args()
    if args.kind not in DECISION_KINDS:
        parser.error(f"kind must be one of {', '.join(DECISION_KINDS)}")
    print(f"{args.name} forgot their {args.kind} habits: {forget(args.name, args.kind)}")
