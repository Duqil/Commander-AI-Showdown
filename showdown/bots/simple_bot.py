"""A tiny rule-based bot: the starting point The Boys will learn on top of.

Rules, in order:
  * Mulligan: keep a 7-card hand with 2 to 5 lands; keep anything smaller.
  * With priority, in our own main phase:
      1. play a land if we can,
      2. otherwise cast the most expensive spell we can afford.
    Outside our main phases we pass (no instant-speed tricks yet).
  * Attack the opponent with the lowest life, with every creature whose
    power is at least 2 (small creatures stay home to block).

Deliberately simple and easy to read. Improving these rules is a good
first Python exercise.
"""

from __future__ import annotations

from showdown.bots.base import Bot, Decision

MAIN_PHASES = {"MAIN1", "MAIN2"}


class SimpleBot(Bot):
    name = "simple"

    def keep_hand(self, decision: Decision) -> bool:
        hand = self.me(decision).get("hand", [])
        lands = sum(1 for card in hand if "Land" in card["type"])
        if len(hand) < 7:
            return True  # already mulliganed once; don't go lower
        return 2 <= lands <= 5

    def choose_play(self, decision: Decision) -> int | None:
        state = decision["state"]
        my_turn = state["active_player"] == state["you"]
        if not my_turn or state["phase"] not in MAIN_PHASES or state["stack_size"] > 0:
            return None

        options = decision["options"]
        lands = [o for o in options if o["is_land"]]
        if lands:
            return lands[0]["index"]

        spells = [o for o in options if o["is_spell"]]
        if not spells:
            return None
        # max(..., key=...) picks the option with the highest mana value.
        best = max(spells, key=lambda o: o["card"]["cmc"])
        return best["index"]

    def declare_attackers(self, decision: Decision) -> dict[str, int]:
        players = {p["name"]: p for p in decision["state"]["players"]}
        # Defenders that are players, weakest first.
        targets = sorted(
            (d for d in decision["defenders"] if d["is_player"]),
            key=lambda d: players[d["name"]]["life"],
        )
        attacks: dict[str, int] = {}
        for creature in decision["attackers"]:
            if creature.get("power", 0) < 2:
                continue
            for target in targets:
                if target["index"] in creature["can_attack"]:
                    attacks[str(creature["id"])] = target["index"]
                    break
        return attacks
