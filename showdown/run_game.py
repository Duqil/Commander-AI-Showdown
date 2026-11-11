"""Play one Commander game from the command line.

Examples:
    python -m showdown.run_game
        Four default decks, every seat played by SimpleBot (in Python).

    python -m showdown.run_game --bots simple forge random forge --seed 7
        Mix Python bots with Forge's own AI ("forge").

    python -m showdown.run_game --bots boy:Kaiba simple simple simple
        Watch a Boy play (it doesn't learn here; use showdown.batch to train).

    python -m showdown.run_game --decks zidane kuja --quiet
        A two-player game, only the result printed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from showdown.bots.base import Bot
from showdown.bots.boy_bot import BoyBot
from showdown.bots.random_bot import RandomBot
from showdown.bots.simple_bot import SimpleBot
from showdown.bridge.game_runner import Seat, run_game
from showdown.stats import record_game

REPO_ROOT = Path(__file__).resolve().parents[1]
DECK_DIR = REPO_ROOT / "decks" / "forge"


def make_bot(kind: str, seed: int | None, learn: bool = False, explore: bool = True) -> Bot | None:
    """Make a bot from its name: "simple", "random", "forge" or "boy:<name>".

    A Boy only learns from the game when ``learn`` is True (training does that).
    With ``explore`` False it always takes its best-scoring option (no dice rolls).
    """
    if kind.startswith("boy:"):
        return BoyBot(kind.removeprefix("boy:"), learn=learn, explore=explore, seed=seed)
    if kind == "forge":
        return None  # Forge's built-in AI
    if kind == "simple":
        return SimpleBot()
    if kind == "random":
        return RandomBot(seed)
    raise SystemExit(f"Unknown bot '{kind}'. Choose from: simple, random, forge, boy:<name>")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--decks", nargs="+", default=["zidane", "anhelo", "nelly", "kuja"],
                        help="deck names in decks/forge/ (2 to 4)")
    parser.add_argument("--bots", nargs="+", default=None,
                        help="one per deck: simple, random, forge or boy:<name> (default: all simple)")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--quiet", action="store_true", help="don't print the game log")
    parser.add_argument("--forge-log", type=Path, default=REPO_ROOT / "forge.log",
                        help="where Forge's own output goes")
    args = parser.parse_args()

    bots = args.bots or ["simple"] * len(args.decks)
    if len(bots) != len(args.decks):
        raise SystemExit("--bots needs one entry per deck")

    seats = [Seat(DECK_DIR / f"{d}.dck", make_bot(b, args.seed)) for d, b in zip(args.decks, bots)]
    for deck, bot in zip(args.decks, bots):
        print(f"  {deck:<10} played by {bot}")

    result = run_game(seats, seed=args.seed, on_log=None if args.quiet else print, forge_log=args.forge_log)

    winner_seat = int(result.winner.split("-", 1)[0]) - 1 if result.winner and not result.draw else None
    record_game(args.decks, bots, winner_seat, result.draw, result.turns, "terminal")

    print()
    print("Winner:", "draw" if result.draw else result.winner, f"after {result.turns} turns")
    for p in result.players:
        print(f"  {p['name']:<14} life {p['life']:>4}  {'lost' if p['lost'] else ''}")
    print(f"Python answered {result.decisions} decisions.")


if __name__ == "__main__":
    main()
