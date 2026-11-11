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
    return None  # a kind this bot doesn't know: let Forge decide


class GameAborted(Exception):
    """The game was ended on purpose before it finished (see run_game's abort)."""


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
                log.append(line)
                if on_log:
                    on_log(line)
        elif kind == "decision":
            bot = bots_by_name.get(msg["player"])
            if book is not None and "state" in msg:
                deal_messages(book.offer_phase(msg["state"], bots_by_name))  # start of a new turn?
                msg["deals"] = book.view_for(msg["player"], msg["state"])
            choice = answer_decision(bot, msg) if bot else None
            if book is not None and msg.get("kind") == "attack":
                deal_messages(book.check_attack(msg["player"], msg, choice))
            conn.answer(msg["id"], choice)
            decisions += 1
            if on_message:
                # Tell the viewer what the bot decided (Forge never echoes it back).
                on_message({"type": "answer", "decision": msg, "choice": choice,
                            "bot": bot.name if bot else None})
        elif kind == "game_over":
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
    """Play one full Commander game in Forge and return the result.

    Setting ``abort`` (from any thread) ends the game right away: Forge is
    killed and GameAborted is raised. The viewer's "Stop now" button uses it.
    """
    server = listen()
    port = server.getsockname()[1]
    process = start_forge(
        port,
        [(s.deck, "forge" if s.bot is None else "python") for s in seats],
        seed=seed,
        log_file=forge_log,
    )
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
        # Forge takes a while to load ~30,000 cards. Wait in 1-second steps so
        # an abort doesn't have to wait for the whole 2 minutes.
        server.settimeout(1)
        deadline = time.monotonic() + 120
        while True:
            if abort is not None and abort.is_set():
                raise GameAborted("stopped before the game began")
            try:
                sock, _ = server.accept()
                break
            except socket.timeout:
                if time.monotonic() > deadline:
                    raise TimeoutError("Forge didn't connect within 2 minutes (see the Forge log file)")
        sock.settimeout(timeout)  # no single message should take this long
        conn = BridgeConnection(sock)
        try:
            return play(conn, [s.bot for s in seats], on_log, on_message, deals=deals)
        except Exception:
            if abort is not None and abort.is_set():
                raise GameAborted("stopped by the user")
            raise
        finally:
            conn.close()
    except BaseException:
        # Something went wrong, or the game was stopped on purpose (e.g. the
        # viewer's Stop button): don't let Forge play on, end it right away.
        process.kill()
        raise
    finally:
        game_done.set()
        server.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
