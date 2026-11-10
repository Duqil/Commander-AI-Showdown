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

    def declare_attackers(self, decision: Decision) -> dict[str, int] | None:
        """Combat. Return {card id (as text): defender index}. {} means no attack.

        decision["attackers"]: creatures that can attack, each with "can_attack"
            (the defender indexes it is allowed to attack).
        decision["defenders"]: players/planeswalkers you can attack.
        """
        return None

    # --- helpers that many bots need -------------------------------------

    @staticmethod
    def me(decision: Decision) -> dict[str, Any]:
        """The player dict for whoever is deciding."""
        you = decision["state"]["you"]
        return next(p for p in decision["state"]["players"] if p["name"] == you)
