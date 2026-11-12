"""Run many games from the browser, without watching them.

The real work is done by showdown/batch.py (run_many), which the command line
and The Boys' training use too. This file only runs it in the background and
keeps a summary the browser can ask for: how many games are done, who won,
how long games take.
"""

from __future__ import annotations

import threading
import time

from showdown.batch import GameRecord, run_many


class BatchJob:
    """One batch of games started from the main menu."""

    def __init__(self, decks: list[str], bots: list[str], games: int, parallel: int = 1,
                 seed: int | None = None, save_games: bool = False, learn: bool = True,
                 deals: bool = False) -> None:
        self.decks, self.bots, self.games = decks, bots, games
        self.parallel, self.seed, self.save_games, self.learn = parallel, seed, save_games, learn
        self.deals = deals
        self.records: list[GameRecord] = []
        self.status = "running"  # running (-> stopping) -> finished / stopped / error
        self.error: str | None = None
        self.started = time.time()
        self._stop = threading.Event()
        self._abort = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="batch", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, now: bool = False, wait: float = 0) -> None:
        """No new games start. With now=True the running games end too (they don't count).

        ``wait`` = seconds to wait for the batch to wind down (0 = don't wait).
        """
        self._stop.set()
        if now:
            self._abort.set()
            self.status = "stopping"
        if wait and self._thread.is_alive():
            self._thread.join(timeout=wait)

    def _on_result(self, record: GameRecord) -> None:
        with self._lock:
            self.records.append(record)

    def _run(self) -> None:
        try:
            run_many(self.decks, self.bots, self.games, parallel=self.parallel, seed=self.seed,
                     save_games=self.save_games, learn=self.learn,
                     on_result=self._on_result, stop=self._stop, abort=self._abort, deals=self.deals)
            self.status = "stopped" if self._stop.is_set() else "finished"
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            self.status = "error"

    def summary(self) -> dict:
        """Everything the browser shows: progress, wins per seat, recent games."""
        with self._lock:
            records = sorted(self.records, key=lambda r: r.index)
        done = [r for r in records if not r.error]
        seats = []
        for seat, (deck, bot) in enumerate(zip(self.decks, self.bots)):
            wins = sum(1 for r in done if r.winner_seat == seat)
            rewards = [r.rewards[seat] for r in done if seat in r.rewards]
            seats.append({
                "seat": seat, "deck": deck, "bot": bot, "wins": wins,
                "avg_reward": round(sum(rewards) / len(rewards), 3) if rewards else None,
            })
        recent = [{
            "game": r.index + 1,
            "winner": None if r.winner_seat is None else f"{r.decks[r.winner_seat]} ({r.bots[r.winner_seat]})",
            "draw": r.draw, "turns": r.turns, "seconds": round(r.seconds), "error": r.error,
            "saved": r.saved_path.name.removesuffix(".json.gz") if r.saved_path else None,
        } for r in records[-12:]]
        return {
            "status": self.status,
            "error": self.error,
            "games": self.games,
            "done": len(records),
            "crashed": len(records) - len(done),
            "draws": sum(1 for r in done if r.draw or r.winner_seat is None),
            "avg_turns": round(sum(r.turns for r in done) / len(done), 1) if done else None,
            "avg_seconds": round(sum(r.seconds for r in done) / len(done)) if done else None,
            "elapsed": round(time.time() - self.started),
            "parallel": self.parallel,
            "learn": self.learn,
            "seats": seats,
            "recent": list(reversed(recent)),  # newest first
        }
