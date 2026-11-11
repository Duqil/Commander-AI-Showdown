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

from showdown.boys.brain import Brain

REPO_ROOT = Path(__file__).resolve().parents[2]
BOYS_DIR = REPO_ROOT / "boys"
HISTORY_FILE = BOYS_DIR / "history.csv"
HISTORY_COLUMNS = ["time", "game", "boy", "boy_games", "seat", "deck", "lineup",
                   "won", "outlived", "reward", "turns"]

_brains: dict[str, Brain] = {}   # brains already loaded in this Python process
_lock = threading.Lock()
VALID_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def brain_path(name: str) -> Path:
    if not VALID_NAME.match(name):
        raise ValueError(f"Boy names may only use letters, digits, - and _ (got {name!r})")
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
