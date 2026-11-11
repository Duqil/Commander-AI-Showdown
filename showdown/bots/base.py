"""The interface every bot implements.

Forge asks a question (a "decision"), the game runner calls the matching
method on the bot, and whatever the method returns goes back to Forge.

Returning ``None`` from any method means "I don't care, let Forge's AI
decide". That makes it easy to write a bot that only handles one kind of
decision and leaves the rest to Forge.

Each decision is a plain dict straight from the Java side. The most useful keys:

    decision["state"]      the game as this player sees it (see StateSnapshot.java)
    decision["player"]     your player name, e.g. "1-Zidane"

Python concept: a *base class*. Other bots write ``class MyBot(Bot):`` and
override only the methods they care about.
"""

from __future__ import annotations

from typing import Any

Decision = dict[str, Any]


class Bot:
    name = "base"

    def keep_hand(self, decision: Decision) -> bool | None:
        """Mulligan. Return True to keep, False to mulligan.

        decision["state"]["players"][...]["hand"] has your cards.
        decision["cards_to_bottom_if_kept"] says how many you'd put back.
        """
        return None

    def choose_play(self, decision: Decision) -> int | None:
        """You have priority. Return the "index" of an option, or None to pass.

        decision["options"] is a list like:
            {"index": 0, "card": {...}, "description": "...", "is_land": true, "is_spell": false}
        Only legal options are listed; the bridge already checked timing and mana.
        """
        return None

    def choose_targets(self, decision: Decision) -> list[int] | None:
        """Targets for the play you just chose. Return candidate indexes, or None to
        keep what Forge's AI suggested.

        decision["candidates"]: legal targets, each a card dict or a player
            ({"name", "life", "is_player": true}), with an "index".
        decision["suggested"]: the indexes Forge's AI would pick.
        decision["min"], decision["max"]: how many targets to pick.
        decision["description"]: the ability's text, e.g. "deals 3 damage to any target".
        """
        return None

    def declare_attackers(self, decision: Decision) -> dict[str, int] | None:
        """Combat. Return {card id (as text): defender index}. {} means no attack.

        decision["attackers"]: creatures that can attack, each with "can_attack"
            (the defender indexes it is allowed to attack).
        decision["defenders"]: players/planeswalkers you can attack.
        """
        return None

    # --- deals (only when the game is played with deals on) -------------------

    def offer_deal(self, view: Decision) -> dict | None:
        """Start of your turn: offer one deal, or None.

        Return {"type": "peace", "to": "<player>"} or
               {"type": "gang_up", "to": "<player>", "against": "<player>"}.
        view["state"] is the table (no hidden cards), view["deals"] your deals and
        trust (see showdown/deals.py). Every decision also carries "deals" then.
        """
        return None

    def answer_deal(self, view: Decision) -> bool:
        """Someone offers you view["offer"]. Return True to accept."""
        return False

    def game_over(self, result: dict, player: str) -> None:
        """Called once when the game ends. ``player`` is this bot's player name.

        result["winner"] is the winning player's name (None for a draw), and
        result["players"] lists everyone with "life" and "lost". Bots that
        learn (The Boys) use this to see how they did. Others can ignore it.
        """

    # --- helpers that many bots need -------------------------------------

    @staticmethod
    def me(decision: Decision) -> dict[str, Any]:
        """The player dict for whoever is deciding."""
        you = decision["state"]["you"]
        return next(p for p in decision["state"]["players"] if p["name"] == you)
