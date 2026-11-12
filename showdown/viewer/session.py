"""One game for the viewer: runs it in the background and collects its timeline.

The web server must keep answering the browser while Forge plays, so the game
runs in a separate *thread*. Both threads touch the same list of events, so a
*lock* makes sure only one of them uses it at a time.

Python concepts used here:
  * ``threading.Thread`` - run a function in the background.
  * ``threading.Lock``   - ``with self._lock:`` lets one thread in at a time.
  * ``gzip``             - finished games are saved compressed (about 20x smaller).
"""

from __future__ import annotations

import gzip
import json
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from showdown.bridge.forge_process import FORGE_DATA_DIR
from showdown.bridge.game_runner import GameAborted, Seat, run_game
from showdown.run_game import DECK_DIR, REPO_ROOT, make_bot
from showdown.stats import record_game
from showdown.viewer.events import Event, TimelineBuilder, make_event

# Finished games are saved here (ignored by git), so you can watch them again.
GAMES_DIR = REPO_ROOT / "games"
# How long the last games took from Start to the first table, so the loading
# bar can move smoothly between Forge's steps (some steps take many seconds).
LOADING_TIME_FILE = FORGE_DATA_DIR / "loading-seconds.txt"


def expected_loading_seconds() -> float | None:
    try:
        return float(LOADING_TIME_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # first game on this computer: no guess yet


def remember_loading_seconds(seconds: float) -> None:
    old = expected_loading_seconds()
    # Half old, half new: one slow start (e.g. right after build_bridge.sh) doesn't throw the guess off for long.
    guess = seconds if old is None else (old + seconds) / 2
    try:
        LOADING_TIME_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOADING_TIME_FILE.write_text(f"{guess:.1f}", encoding="utf-8")
    except OSError:
        pass  # only a nicer progress bar is lost


class GameStopped(Exception):
    """Raised inside the game thread to stop a game early."""


@dataclass
class GameConfig:
    decks: list[str]          # deck file names in decks/forge/, without ".dck"
    bots: list[str]           # one per deck: "simple", "random", "forge" or "boy:<name>"
    seed: int | None = None
    deals: bool = False       # bots may make deals with each other (showdown/deals.py)

    def label(self) -> str:
        return " vs ".join(f"{d} ({b})" for d, b in zip(self.decks, self.bots))


class GameSession:
    """A game that is running (or finished, or loaded from a file)."""

    def __init__(self, config: GameConfig | None, game_id: str) -> None:
        self.config = config
        self.id = game_id
        self.status = "starting"  # starting -> running -> finished / error / stopped
        self.events: list[Event] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()  # a thread-safe True/False flag
        self._thread: threading.Thread | None = None
        # How far the start of the game has got, for the viewer's progress bar:
        # {"percent": 0-100, "text": "Loading card rules", "seconds": time since Start}.
        # Not part of the timeline, so saved games don't carry it.
        self.loading: dict | None = None
        self._started_at = time.monotonic()
        self._expected_seconds: float | None = None  # set when the game starts

    # --- reading (called by the web server thread) --------------------------

    def events_since(self, start: int, limit: int = 400) -> list[Event]:
        """Events from position ``start`` on, at most ``limit`` of them."""
        with self._lock:
            return self.events[start:start + limit]

    def summary(self) -> dict:
        with self._lock:
            count = len(self.events)
        return {
            "id": self.id,
            "status": self.status,
            "event_count": count,
            "config": vars(self.config) if self.config else None,
            "loading": self._loading_now(),
        }

    def _loading_now(self) -> dict | None:
        """The loading bar right now: Forge's last step, smoothed by the time the last games took."""
        if self.loading is None or self.status not in ("starting", "running"):
            return None
        seconds = time.monotonic() - self._started_at
        percent = self.loading["percent"]
        expected = self._expected_seconds
        if expected:
            # Between Forge's steps, move on with the clock, but never claim to be done:
            # only the first table ends the loading.
            percent = max(percent, min(97, round(100 * seconds / expected)))
        return {"percent": percent, "text": self.loading["text"], "seconds": round(seconds),
                "expected": round(expected) if expected else None}

    # --- writing (called by the game thread) --------------------------------

    def add_event(self, event: Event) -> None:
        with self._lock:
            event["seq"] = len(self.events)
            self.events.append(event)

    # --- running a live game -------------------------------------------------

    def start(self) -> None:
        # daemon=True: if you close the server, this thread doesn't keep Python alive.
        self._thread = threading.Thread(target=self._run, name=f"game-{self.id}", daemon=True)
        self._thread.start()

    def stop(self, wait: float = 0) -> None:
        """Stop the game now: Forge is ended right away, even while it is still loading.

        ``wait`` = seconds to wait for the game thread to finish (0 = don't wait).
        """
        self._stop.set()
        if wait and self._thread:
            self._thread.join(timeout=wait)

    def _run(self) -> None:
        config = self.config
        assert config is not None
        builder = TimelineBuilder()

        def on_message(msg: dict) -> None:
            # Called by game_runner for every message from Forge, in this thread.
            if self._stop.is_set():
                raise GameStopped()
            if msg.get("type") == "loading":
                # Forge's messages can arrive a moment out of order: never go backwards.
                if self.loading is None or msg["percent"] >= self.loading["percent"]:
                    self.loading = {"percent": msg["percent"], "text": msg["text"]}
                return
            if msg.get("type") == "game_start":
                self.status = "running"
            for event in builder.feed(msg):
                self.add_event(event)
                if "state" in event and self.loading is not None:
                    self.loading = None  # the first table is here: loading is over
                    remember_loading_seconds(time.monotonic() - self._started_at)

        self._started_at = time.monotonic()
        self._expected_seconds = expected_loading_seconds()
        try:
            # Each seat gets its own seed, so two random bots don't roll the same dice.
            # This is inside the try on purpose: making a bot reads its brain file
            # (boys/<name>.json), and if that failed out here, the game thread
            # died silently and the viewer said "Loading rules…" forever.
            seats = [Seat(DECK_DIR / f"{deck}.dck", make_bot(bot, None if config.seed is None else config.seed + seat))
                     for seat, (deck, bot) in enumerate(zip(config.decks, config.bots))]
            # abort=self._stop: Stop kills Forge at once (otherwise it would only
            # notice at its next message, and never while it is still loading).
            result = run_game(seats, seed=config.seed, on_message=on_message, deals=config.deals,
                              forge_log=REPO_ROOT / "forge.log", abort=self._stop)
            self.status = "finished"
            # Count it for the Stats screen. Player names look like "2-Kuja": seat 2 (from 1).
            winner = int(result.winner.split("-", 1)[0]) - 1 if result.winner and not result.draw else None
            record_game(config.decks, config.bots, winner, result.draw, result.turns, "viewer", game_id=self.id)
        except (GameStopped, GameAborted):
            self.add_event(make_event("error", "Game stopped.", turn=builder.turn))
            self.status = "stopped"
        except BaseException as e:  # show any problem in the browser instead of a silent hang
            traceback.print_exc()  # the full story in the terminal that runs the viewer
            self.add_event(make_event("error", f"Game crashed: {type(e).__name__}: {e} (see forge.log)",
                                      turn=builder.turn))
            self.status = "error"
        if self.status == "finished":
            self.save()

    # --- saving and loading --------------------------------------------------

    def save(self) -> Path:
        GAMES_DIR.mkdir(exist_ok=True)
        path = GAMES_DIR / f"{self.id}.json.gz"
        with self._lock:
            data = {"config": vars(self.config) if self.config else None, "events": self.events}
        # "wt" = write text; gzip compresses it on the way to disk.
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(data, f)
        return path

    @classmethod
    def load(cls, path: Path) -> "GameSession":
        """Open a saved game for watching again."""
        with gzip.open(path, "rt", encoding="utf-8") as f:
            data = json.load(f)
        config = GameConfig(**data["config"]) if data.get("config") else None
        session = cls(config, path.name.removesuffix(".json.gz"))
        session.events = data["events"]
        session.status = "finished"
        return session


def new_game_id(config: GameConfig) -> str:
    """A readable, sortable file name like 2026-11-10_1530_zidane-anhelo-nelly-kuja."""
    return time.strftime("%Y-%m-%d_%H%M%S") + "_" + "-".join(config.decks)


def list_saved_games() -> list[dict]:
    """Saved games, newest first."""
    if not GAMES_DIR.exists():
        return []
    files = sorted(GAMES_DIR.glob("*.json.gz"), reverse=True)
    return [{"id": f.name.removesuffix(".json.gz"), "size_kb": f.stat().st_size // 1024} for f in files]
