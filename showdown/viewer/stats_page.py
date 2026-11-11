"""Numbers for the viewer's Stats screen, read from stats/results.csv.

That file (written by showdown/stats.py) has one row per seat per finished
game. Here we group the rows three ways: by deck, by bot, and by deck + bot,
and work out each group's win rate and average game length.

Python concept: ``defaultdict(list)`` makes a dict where a missing key starts
as an empty list, so ``groups[name].append(row)`` just works.
"""

from __future__ import annotations

from collections import defaultdict

from showdown import stats

SOURCES = ("batch", "viewer", "terminal")


def group(rows: list[dict], key) -> list[dict]:
    """Games, wins and win rate per group, most games first."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    table = []
    for name, members in groups.items():
        wins = sum(int(r["won"]) for r in members)
        table.append({
            "name": name,
            "games": len(members),
            "wins": wins,
            "win_rate": wins / len(members),
        })
    return sorted(table, key=lambda r: (-r["games"], -r["win_rate"]))


def summary(source: str | None = None) -> dict:
    """Everything the Stats screen shows. ``source`` limits it to "batch", "viewer" or "terminal"."""
    rows = stats.read_results()
    if source in SOURCES:
        rows = [r for r in rows if r["source"] == source]
    games = {r["game_id"]: r for r in rows}  # one entry per game
    seats_per_game = len(rows) / len(games) if games else 4
    return {
        "games": len(games),
        "draws": sum(1 for r in games.values() if int(r["draw"])),
        "avg_turns": round(sum(int(r["turns"]) for r in games.values()) / len(games), 1) if games else None,
        # With 4 players, a bot that is exactly as good as the others wins 1 game in 4.
        "fair_share": 1 / seats_per_game,
        "decks": group(rows, lambda r: r["deck"]),
        "bots": group(rows, lambda r: r["bot"]),
        "pairs": group(rows, lambda r: f"{r['deck']} · {r['bot']}"),
        "first": min((r["time"] for r in rows), default=None),
        "last": max((r["time"] for r in rows), default=None),
    }
