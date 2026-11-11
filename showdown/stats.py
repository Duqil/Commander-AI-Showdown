"""Win rates: every finished game adds one row per seat to stats/results.csv.

    time, game_id, seat, deck, bot, won, draw, turns, source

``source`` says where the game came from: "batch", "viewer" or "terminal".
The stats/ folder is ignored by git, so the numbers are your own.

    python -m showdown.stats             # win rate per deck and per bot

Python concept: ``collections.Counter`` counts things for you, like a dict
whose missing keys start at 0.
"""

from __future__ import annotations

import csv
import threading
import time
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATS_DIR = REPO_ROOT / "stats"
RESULTS_FILE = STATS_DIR / "results.csv"
COLUMNS = ["time", "game_id", "seat", "deck", "bot", "won", "draw", "turns", "source"]

_lock = threading.Lock()


def record_game(decks: list[str], bots: list[str], winner_seat: int | None, draw: bool,
                turns: int, source: str, game_id: str = "") -> None:
    """Add one finished game (one row per seat)."""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    game_id = game_id or f"{now}-{source}"
    rows = [{"time": now, "game_id": game_id, "seat": seat + 1, "deck": deck, "bot": bot,
             "won": int(winner_seat == seat and not draw), "draw": int(draw),
             "turns": turns, "source": source}
            for seat, (deck, bot) in enumerate(zip(decks, bots))]
    with _lock:
        STATS_DIR.mkdir(exist_ok=True)
        new_file = not RESULTS_FILE.exists()
        with open(RESULTS_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            if new_file:
                writer.writeheader()
            writer.writerows(rows)


def read_results() -> list[dict]:
    if not RESULTS_FILE.exists():
        return []
    with open(RESULTS_FILE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def win_rates(key: str) -> list[dict]:
    """Games, wins and win rate grouped by ``key`` ("deck" or "bot"), best first."""
    games: Counter[str] = Counter()
    wins: Counter[str] = Counter()
    for row in read_results():
        games[row[key]] += 1
        wins[row[key]] += int(row["won"])
    table = [{key: name, "games": n, "wins": wins[name], "win_rate": wins[name] / n}
             for name, n in games.items()]
    return sorted(table, key=lambda r: r["win_rate"], reverse=True)


def main() -> None:
    rows = read_results()
    if not rows:
        print("No games recorded yet (stats/results.csv). Play some games first.")
        return
    print(f"{len({r['game_id'] for r in rows})} games recorded\n")
    for key in ("deck", "bot"):
        print(f"{key:<16} {'games':>6} {'wins':>6} {'win rate':>9}")
        for r in win_rates(key):
            print(f"{r[key]:<16} {r['games']:>6} {r['wins']:>6} {r['win_rate']:>9.0%}")
        print()


if __name__ == "__main__":
    main()
