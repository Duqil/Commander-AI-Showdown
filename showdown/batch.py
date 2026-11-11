"""Play many games in a row without watching, e.g. to train The Boys.

Examples:
    python -m showdown.batch --bots boy:Kaiba simple simple simple --games 20
        Kaiba plays 20 games against three SimpleBots and learns after each.

    python -m showdown.batch --bots boy:Kaiba boy:bob boy:carol boy:dave --games 100 --parallel 2
        Four Boys learn against each other, two games at a time.

    python -m showdown.batch --bots boy:Kaiba forge forge forge --games 10 --no-learn --greedy
        Test Kaiba against Forge's AI without changing his brain, always
        playing his best-scoring move (no exploring).

    python -m showdown.batch --report
        Show how each Boy has been doing over time (from boys/history.csv).

The browser viewer's main menu calls run_many() below for its "Run N games" panel.

Python concepts used here:
  * ``concurrent.futures.ThreadPoolExecutor`` runs several games at once.
    Each game is its own Forge (Java) process, so they really run in parallel.
    Each one needs about 2 GB of memory, so 2 or 3 at a time is plenty.
  * ``threading.Event`` is a thread-safe on/off switch, used for "stop".
"""

from __future__ import annotations

import argparse
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from showdown.bots.boy_bot import BoyBot
from showdown.boys import storage
from showdown.bridge.game_runner import GameAborted, Seat, run_game
from showdown.run_game import DECK_DIR, REPO_ROOT, make_bot
from showdown.stats import record_game

BASIC_BOTS = ["simple", "random", "forge"]
MAX_CRASHES_IN_A_ROW = 5


@dataclass
class GameRecord:
    """What happened in one game of a batch."""
    index: int                       # 0, 1, 2, ... in the order games were started
    seed: int | None
    decks: list[str]
    bots: list[str]
    winner_seat: int | None = None   # None for a draw or a crash
    draw: bool = False
    turns: int = 0
    seconds: float = 0.0
    rewards: dict[int, float] = field(default_factory=dict)  # seat -> reward, Boys only
    error: str | None = None
    saved_path: Path | None = None


def bots_available() -> list[str]:
    """Bot names for a menu: the basic bots plus every saved Boy."""
    return BASIC_BOTS + [f"boy:{name}" for name in storage.list_boys()]


def play_one(index: int, decks: list[str], bots: list[str], seed: int | None,
             save_game: bool = False, learn: bool = True, explore: bool = True,
             abort: threading.Event | None = None, deals: bool = False) -> GameRecord | None:
    """Play one game and return its record. Boys learn when it ends (if learn=True).

    Returns None if the game was aborted: it doesn't count, and nobody learns from it.
    """
    record = GameRecord(index, seed, list(decks), list(bots))
    seated = [make_bot(b, None if seed is None else seed + seat, learn=learn, explore=explore)
              for seat, b in enumerate(bots)]
    seats = [Seat(DECK_DIR / f"{d}.dck", bot) for d, bot in zip(decks, seated)]

    on_message = None
    session = None
    if save_game:
        # Reuse the viewer's timeline format, so saved games show up under
        # "Saved games" in the browser viewer.
        from showdown.viewer.events import TimelineBuilder
        from showdown.viewer.session import GameConfig, GameSession, new_game_id
        config = GameConfig(decks=list(decks), bots=list(bots), seed=seed, deals=deals)
        session = GameSession(config, f"{new_game_id(config)}_{index:03d}")
        builder = TimelineBuilder()

        def on_message(msg: dict) -> None:
            for event in builder.feed(msg):
                session.add_event(event)

    start = time.monotonic()
    try:
        result = run_game(seats, seed=seed, on_message=on_message,
                          forge_log=REPO_ROOT / f"forge-batch-{index % 8}.log", abort=abort, deals=deals)
    except GameAborted:
        return None
    except Exception as e:  # one broken game shouldn't end the whole batch
        record.error = f"{type(e).__name__}: {e}"
        record.seconds = time.monotonic() - start
        return record
    record.seconds = time.monotonic() - start
    record.draw = result.draw
    record.turns = result.turns
    # Player names look like "2-Kuja": the number is the seat, counted from 1.
    if result.winner and not result.draw:
        record.winner_seat = int(result.winner.split("-", 1)[0]) - 1
    if session is not None:
        record.saved_path = session.save()
    record_game(decks, bots, record.winner_seat, record.draw, record.turns, "batch",
                game_id=f"{time.strftime('%Y-%m-%d_%H%M%S')}_batch{index:03d}")

    history = []
    for seat, bot in enumerate(seated):
        if isinstance(bot, BoyBot) and bot.result:
            record.rewards[seat] = bot.result["reward"]
            history.append({
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "game": index,
                "boy": bot.brain.name,
                "boy_games": bot.brain.games,
                "seat": seat,
                "deck": decks[seat],
                "lineup": ", ".join(bots),  # commas, since a Boy name can have spaces
                "won": int(bot.result["won"]),
                "outlived": bot.result["outlived"],
                "reward": round(bot.result["reward"], 3),
                "turns": result.turns,
            })
    if learn:
        storage.append_history(history)
    return record


def run_many(
    decks: list[str],
    bots: list[str],
    games: int,
    parallel: int = 1,
    seed: int | None = None,
    save_games: bool = False,
    learn: bool = True,
    explore: bool = True,
    on_result: Callable[[GameRecord], None] | None = None,
    stop: threading.Event | None = None,
    abort: threading.Event | None = None,
    deals: bool = False,
) -> list[GameRecord]:
    """Play ``games`` games with the same decks and bots in the same seats.

    on_result is called (from a worker thread) after each game. Setting
    ``stop`` means no new games start; games already running finish.
    Setting ``abort`` also ends the running games right away (they don't count).
    Game i uses seed + i when a seed is given, so a batch can be repeated.
    """
    if len(decks) != len(bots):
        raise ValueError("decks and bots need one entry per seat")
    for b in bots:
        if b not in BASIC_BOTS and not b.startswith("boy:"):
            raise ValueError(f"Unknown bot {b!r}")
        if b.startswith("boy:"):
            storage.brain_path(b.removeprefix("boy:"))  # checks the name is valid

    records: list[GameRecord] = []
    records_lock = threading.Lock()
    stop = stop or threading.Event()
    abort = abort or threading.Event()
    crashes_in_a_row = [0]  # a list so worker() can change it

    def worker(index: int) -> None:
        if stop.is_set() or abort.is_set():
            return
        record = play_one(index, decks, bots, None if seed is None else seed + index * 10,
                          save_game=save_games, learn=learn, explore=explore, abort=abort, deals=deals)
        if record is None:  # aborted
            return
        with records_lock:
            records.append(record)
            crashes_in_a_row[0] = crashes_in_a_row[0] + 1 if record.error else 0
            if crashes_in_a_row[0] >= MAX_CRASHES_IN_A_ROW:
                stop.set()  # something is broken; don't waste hours on more crashes
                record.error += f" (stopping: {MAX_CRASHES_IN_A_ROW} crashes in a row)"
        if on_result:
            on_result(record)

    # "with" waits for all games to finish before going on.
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        futures = [pool.submit(worker, i) for i in range(games)]
        try:
            for future in futures:
                future.result()  # waits, and re-raises any bug in worker()
        except BaseException:   # e.g. Ctrl+C: end the running games, start no more
            stop.set()
            abort.set()
            for future in futures:
                future.cancel()
            raise
    return sorted(records, key=lambda r: r.index)


# ----------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------

def print_record(r: GameRecord, total: int) -> None:
    if r.error:
        outcome = f"crashed ({r.error})"
    elif r.draw or r.winner_seat is None:
        outcome = "draw"
    else:
        outcome = f"won by seat {r.winner_seat + 1} ({r.decks[r.winner_seat]}, {r.bots[r.winner_seat]})"
    rewards = "  ".join(f"{r.bots[s]}={v:.2f}" for s, v in sorted(r.rewards.items()))
    print(f"game {r.index + 1:>3}/{total}: {outcome}, {r.turns} turns, {r.seconds:.0f}s  {rewards}", flush=True)


def print_report(window: int = 20) -> None:
    """Win rate and average reward per Boy, in blocks of ``window`` games."""
    rows = storage.read_history()
    if not rows:
        print("No training history yet (boys/history.csv). Run a batch with a boy:<name> bot first.")
        return
    by_boy: dict[str, list[dict]] = {}
    for row in rows:
        by_boy.setdefault(row["boy"], []).append(row)
    for boy, games in by_boy.items():
        print(f"\n{boy}: {len(games)} games")
        print(f"  {'games':>9}  {'win rate':>8}  {'avg reward':>10}  bar (win rate)")
        for start in range(0, len(games), window):
            block = games[start:start + window]
            wins = sum(int(g["won"]) for g in block) / len(block)
            reward = sum(float(g["reward"]) for g in block) / len(block)
            label = f"{start + 1}-{start + len(block)}"
            print(f"  {label:>9}  {wins:>8.0%}  {reward:>10.2f}  {'#' * round(wins * 40)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--decks", nargs="+", default=["zidane", "anhelo", "nelly", "kuja"],
                        help="deck names in decks/forge/ (2 to 4)")
    parser.add_argument("--bots", nargs="+", default=None,
                        help="one per deck: simple, random, forge or boy:<name>")
    parser.add_argument("--games", type=int, default=10)
    parser.add_argument("--parallel", type=int, default=1, help="games at the same time (each needs ~2 GB)")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--save-games", action="store_true", help="save each game for the viewer (games/)")
    parser.add_argument("--no-learn", action="store_true", help="Boys play but don't learn")
    parser.add_argument("--greedy", action="store_true",
                        help="Boys always take their best option, no exploring (use with --no-learn to test them)")
    parser.add_argument("--deals", action="store_true", help="let bots make deals with each other")
    parser.add_argument("--report", action="store_true", help="only show the training history")
    args = parser.parse_args()

    if args.report:
        print_report()
        return
    bots = args.bots or ["simple"] * len(args.decks)
    if len(bots) != len(args.decks):
        raise SystemExit("--bots needs one entry per deck")
    for deck, bot in zip(args.decks, bots):
        print(f"  {deck:<10} played by {bot}")
    print(f"Playing {args.games} games, {args.parallel} at a time. Ctrl+C stops.\n", flush=True)

    records = run_many(args.decks, bots, args.games, parallel=args.parallel, seed=args.seed,
                       save_games=args.save_games, learn=not args.no_learn, explore=not args.greedy,
                       deals=args.deals,
                       on_result=lambda r: print_record(r, args.games))

    print("\nWins per seat:")
    for seat, (deck, bot) in enumerate(zip(args.decks, bots)):
        wins = sum(1 for r in records if r.winner_seat == seat)
        print(f"  seat {seat + 1}  {deck:<10} {bot:<14} {wins:>3} / {len(records)}")
    crashed = sum(1 for r in records if r.error)
    if crashed:
        print(f"  ({crashed} games crashed; see forge-batch-*.log)")


if __name__ == "__main__":
    main()
