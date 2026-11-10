"""Run one game: start Forge, answer its questions with our bots, report the result.

This is the heart of the Python side. The loop is:

    for each message from Forge:
        "event"     -> print the new log lines (later: send them to the viewer)
        "decision"  -> ask the right bot, send the answer back
        "game_over" -> done, return the result
"""

from __future__ import annotations

import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from showdown.bots.base import Bot, Decision
from showdown.bridge.connection import BridgeConnection, listen
from showdown.bridge.forge_process import start_forge


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
    if kind == "attack":
        return bot.declare_attackers(decision)
    return None  # a kind this bot doesn't know: let Forge decide


def play(
    conn: BridgeConnection,
    bots_by_seat: list[Bot | None],
    on_log: Callable[[str], None] | None = None,
) -> GameResult:
    """Drive a game over an already-open connection. Separate from run_game() so tests can use a fake Forge."""
    bots_by_name: dict[str, Bot] = {}
    decisions = 0
    log: list[str] = []

    for msg in conn.messages():
        kind = msg["type"]
        if kind == "game_start":
            for seat in msg["seats"]:
                bot = bots_by_seat[seat["seat"]]
                if bot is not None:
                    bots_by_name[seat["name"]] = bot
        elif kind == "event":
            for line in msg.get("log", []):
                log.append(line)
                if on_log:
                    on_log(line)
        elif kind == "decision":
            bot = bots_by_name.get(msg["player"])
            choice = answer_decision(bot, msg) if bot else None
            conn.answer(msg["id"], choice)
            decisions += 1
        elif kind == "game_over":
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
) -> GameResult:
    """Play one full Commander game in Forge and return the result."""
    server = listen()
    port = server.getsockname()[1]
    process = start_forge(
        port,
        [(s.deck, "forge" if s.bot is None else "python") for s in seats],
        seed=seed,
        log_file=forge_log,
    )
    try:
        server.settimeout(120)  # Forge takes a while to load ~30,000 cards
        try:
            sock, _ = server.accept()
        except socket.timeout:
            raise TimeoutError("Forge didn't connect within 2 minutes (see the Forge log file)")
        sock.settimeout(timeout)  # no single message should take this long
        conn = BridgeConnection(sock)
        try:
            return play(conn, [s.bot for s in seats], on_log)
        finally:
            conn.close()
    finally:
        server.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
