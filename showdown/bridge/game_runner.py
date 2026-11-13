"""Run one game: start Forge, answer its questions with our bots, report the result.

This is the heart of the Python side. The loop is:

    for each message from Forge:
        "event"     -> print the new log lines
        "decision"  -> ask the right bot, send the answer back
        "game_over" -> done, return the result

Pass ``on_message`` to see every message as it arrives, including the bots'
answers (as {"type": "answer", ...}). The browser viewer
(showdown/viewer/) uses that to build its timeline.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from showdown.bots.base import Bot, Decision
from showdown.bridge.connection import BridgeConnection, listen
from showdown.bridge.forge_process import start_forge
from showdown.damage import DamageTracker
from showdown.deals import DealBook


# A @dataclass writes __init__ and __repr__ for us from the field list.
@dataclass
class Seat:
    deck: Path
    bot: Bot | None  # None = Forge's own AI plays this seat


@dataclass
class GameResult:
    winner: str | None
    draw: bool
    turns: int
    players: list[dict]
    decisions: int = 0  # how many questions Python answered
    log: list[str] = field(default_factory=list)


def answer_decision(bot: Bot, decision: Decision):
    """Route a decision to the bot method that handles its kind."""
    kind = decision["kind"]
    if kind == "mulligan":
        return bot.keep_hand(decision)
    if kind == "choose_play":
        return bot.choose_play(decision)
    if kind == "choose_targets":
        return bot.choose_targets(decision)
    if kind == "attack":
        return bot.declare_attackers(decision)
    if kind == "keyword_cost":
        return bot.pay_keyword_cost(decision)
    if kind == "pick_card":
        return bot.choose_card(decision)
    return None  # a kind this bot doesn't know: let Forge decide


class GameAborted(Exception):
    """The game was ended on purpose before it finished (see run_game's abort)."""


class ForgeGameError(RuntimeError):
    """Forge hit an error inside the game itself (the Forge log has the details)."""


# The type of the on_message callback: it gets one message dict, returns nothing.
MessageCallback = Callable[[dict], None]


def play(
    conn: BridgeConnection,
    bots_by_seat: list[Bot | None],
    on_log: Callable[[str], None] | None = None,
    on_message: MessageCallback | None = None,
    deals: bool = False,
) -> GameResult:
    """Drive a game over an already-open connection. Separate from run_game() so tests can use a fake Forge.

    With ``deals`` on, bots can make deals with each other (see showdown/deals.py).
    """
    bots_by_name: dict[str, Bot] = {}
    decisions = 0
    log: list[str] = []
    book: DealBook | None = None
    damage = DamageTracker()  # who hit whom lately (bots see it in the state)
    mulligans: dict[str, int] = {}  # player -> mulligans taken (the first one is free)

    def deal_messages(messages: list[dict]) -> None:
        # Deals aren't in Forge's log, so we add them to ours (and the viewer's).
        for m in messages:
            log.append("Deal: " + m["text"])
            if on_log:
                on_log("Deal: " + m["text"])
            if on_message:
                on_message(m)

    for msg in conn.messages():
        if on_message:
            on_message(msg)
        kind = msg["type"]
        if kind == "game_start":
            for seat in msg["seats"]:
                bot = bots_by_seat[seat["seat"]]
                if bot is not None:
                    bots_by_name[seat["name"]] = bot
            if deals:
                book = DealBook([seat["name"] for seat in msg["seats"]])
        elif kind == "event":
            for line in msg.get("log", []):
                damage.see_log(line)
                log.append(line)
                if on_log:
                    on_log(line)
        elif kind == "decision":
            bot = bots_by_name.get(msg["player"])
            if "state" in msg:
                damage.annotate(msg["state"])
            if msg.get("kind") == "mulligan":
                msg["mulligans_so_far"] = mulligans.get(msg["player"], 0)
            if book is not None and "state" in msg:
                deal_messages(book.offer_phase(msg["state"], bots_by_name))  # start of a new turn?
                msg["deals"] = book.view_for(msg["player"], msg["state"])
            choice = answer_decision(bot, msg) if bot else None
            if msg.get("kind") == "mulligan" and choice is False:
                mulligans[msg["player"]] = mulligans.get(msg["player"], 0) + 1
            if book is not None and msg.get("kind") == "attack":
                broken = book.check_attack(msg["player"], msg, choice)
                deal_messages(broken)
                for m in broken:  # tell both partners (the Boys learn from it)
                    for partner in (m["deal"]["a"], m["deal"]["b"]):
                        if partner in bots_by_name:
                            bots_by_name[partner].deal_broken(m["deal"], m["breaker"], partner)
            conn.answer(msg["id"], choice)
            decisions += 1
            if on_message:
                # Tell the viewer what the bot decided (Forge never echoes it back).
                on_message({"type": "answer", "decision": msg, "choice": choice,
                            "bot": bot.name if bot else None})
        elif kind == "game_over":
            if msg.get("error"):
                # A broken game isn't a real draw: nobody should learn from it.
                raise ForgeGameError(f"{msg['error']} (see the Forge log file)")
            for player_name, bot in bots_by_name.items():
                bot.game_over(msg, player_name)  # learning bots learn here
            return GameResult(
                winner=msg.get("winner"),
                draw=msg.get("draw", False),
                turns=msg.get("turns", 0),
                players=msg.get("players", []),
                decisions=decisions,
                log=log,
            )
    raise ConnectionError("Forge disconnected before the game ended (see the Forge log file)")


class Forge:
    """One Forge program that can play many games, one after another.

    Starting Forge (Java + loading the rules) takes a few seconds. "Run many
    games" keeps one Forge per parallel slot and just asks it for the next game.
    Single games (the viewer, ``run_game``) still start a fresh Forge each time.

    Use it like this, and always close it (a ``with`` block does that for you):

        with Forge() as forge:
            for seed in range(5):
                result = forge.play(seats, seed=seed)

    If anything goes wrong in a game, this Forge is killed and the next
    ``play`` starts a fresh one, so one bad game can't spoil the rest.
    """

    # Start a fresh Forge now and then anyway, in case Forge keeps a little
    # memory of every game it played. 25 games of ~40 s each is ~15 minutes.
    MAX_GAMES = 25

    def __init__(self, forge_log: Path | None = None) -> None:
        self.forge_log = forge_log
        self.process: subprocess.Popen | None = None
        self.conn: BridgeConnection | None = None
        self.games = 0  # games played by the current Forge program

    # "with Forge() as forge:" calls __enter__ at the start and __exit__ at the end.
    def __enter__(self) -> "Forge":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def play(
        self,
        seats: list[Seat],
        seed: int | None = None,
        on_log: Callable[[str], None] | None = None,
        timeout: float = 600,
        on_message: MessageCallback | None = None,
        abort: threading.Event | None = None,
        deals: bool = False,
    ) -> GameResult:
        """Play one game. Setting ``abort`` (from any thread) kills Forge and raises GameAborted."""
        if self.conn is not None and self.games >= self.MAX_GAMES:
            self.close()
        if self.conn is None:
            self._start(seats, seed, abort, on_message)
        else:
            # Forge is waiting after its last game: tell it what to play next.
            self.conn.send({"type": "next_game", "seed": seed,
                            "seats": [{"deck": str(Path(s.deck).resolve()), "python": s.bot is not None}
                                      for s in seats]})
        assert self.conn is not None and self.process is not None
        process = self.process
        game_done = threading.Event()
        if abort is not None:
            # A small helper thread that watches the abort flag while the game runs.
            # Killing Forge closes its connection, which makes play() below stop.
            def watch() -> None:
                while not game_done.wait(0.5):
                    if abort.is_set():
                        process.kill()
                        return
            threading.Thread(target=watch, name="abort-watch", daemon=True).start()
        try:
            # No single message should take longer than ``timeout``. Loading counts
            # too: on a slow disk, Forge can be silent for minutes while it
            # indexes its ~34,000 card scripts (one Windows PC took 3 minutes).
            self.conn.settimeout(timeout)
            try:
                result = play(self.conn, [s.bot for s in seats], on_log, on_message, deals=deals)
            except socket.timeout:
                raise TimeoutError(f"Forge sent nothing for {timeout:.0f} seconds (see the Forge log file)") from None
            self.games += 1
            return result
        except BaseException:
            # Something went wrong, or the game was stopped on purpose (e.g. the
            # viewer's Stop button): don't let Forge play on, end it right away.
            self.close(kill=True)
            if abort is not None and abort.is_set():
                raise GameAborted("stopped by the user")
            self._keep_crash_log()
            raise
        finally:
            game_done.set()

    def _keep_crash_log(self) -> None:
        """Copy the Forge log aside after a crash.

        The next game starts a fresh Forge, which writes the log file from
        scratch. Without this copy, the reason for the crash would be gone.
        """
        if self.forge_log is None or not self.forge_log.exists():
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        crash_log = self.forge_log.with_name(f"{self.forge_log.stem}-crash-{stamp}.log")
        try:
            shutil.copyfile(self.forge_log, crash_log)
        except OSError:
            pass  # a missing log mustn't hide the real error

    def _start(self, seats: list[Seat], seed: int | None, abort: threading.Event | None,
               on_message: MessageCallback | None = None) -> None:
        def loading(percent: int, text: str) -> None:
            # Same shape as Forge's own "loading" messages (LoadingProgress.java),
            # so the viewer shows one progress bar from start to deal.
            if on_message:
                on_message({"type": "loading", "percent": percent, "text": text})

        server = listen()
        try:
            loading(0, "Starting Java")
            self.process = start_forge(
                server.getsockname()[1],
                [(s.deck, "forge" if s.bot is None else "python") for s in seats],
                seed=seed,
                log_file=self.forge_log,
            )
            # Forge takes a few seconds to load. Wait in 1-second steps so an
            # abort doesn't have to wait for the whole 2 minutes.
            server.settimeout(1)
            deadline = time.monotonic() + 120
            while True:
                if abort is not None and abort.is_set():
                    raise GameAborted("stopped before the game began")
                if self.process.poll() is not None:
                    raise ConnectionError("Forge quit before the game began (see the Forge log file)")
                try:
                    sock, _ = server.accept()
                    break
                except socket.timeout:
                    if time.monotonic() > deadline:
                        raise TimeoutError("Forge didn't connect within 2 minutes (see the Forge log file)")
            self.conn = BridgeConnection(sock)
            self.games = 0
            loading(3, "Java started, waiting for Forge")
        except BaseException:
            self.close(kill=True)
            raise
        finally:
            server.close()

    def close(self, kill: bool = False) -> None:
        """End this Forge: politely ("quit") after a good game, or kill it after a bad one."""
        if self.conn is not None:
            if not kill:
                try:
                    self.conn.send({"type": "quit"})
                except OSError:
                    pass
            self.conn.close()
            self.conn = None
        if self.process is not None:
            if kill:
                self.process.kill()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None


def run_game(
    seats: list[Seat],
    seed: int | None = None,
    on_log: Callable[[str], None] | None = None,
    forge_log: Path | None = None,
    timeout: float = 600,
    on_message: MessageCallback | None = None,
    abort: threading.Event | None = None,
    deals: bool = False,
) -> GameResult:
    """Play one full Commander game in a fresh Forge and return the result.

    Setting ``abort`` (from any thread) ends the game right away: Forge is
    killed and GameAborted is raised. The viewer's "Stop now" button uses it.
    """
    with Forge(forge_log) as forge:
        return forge.play(seats, seed=seed, on_log=on_log, timeout=timeout,
                          on_message=on_message, abort=abort, deals=deals)
