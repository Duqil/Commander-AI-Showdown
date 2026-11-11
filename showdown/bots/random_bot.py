"""A bot that picks uniformly at random. Useful as a baseline: any real bot should beat it."""

from __future__ import annotations

import random

from showdown.bots.base import Bot, Decision


class RandomBot(Bot):
    name = "random"

    def __init__(self, seed: int | None = None) -> None:
        # Our own random generator, so a seed makes games repeatable.
        self.rng = random.Random(seed)

    def keep_hand(self, decision: Decision) -> bool:
        return self.rng.random() < 0.8  # keep most hands

    def choose_play(self, decision: Decision) -> int | None:
        options = decision["options"]
        # One extra slot for "pass", otherwise it would never pass while it has options.
        pick = self.rng.randrange(len(options) + 1)
        return None if pick == len(options) else options[pick]["index"]

    def declare_attackers(self, decision: Decision) -> dict[str, int]:
        attacks = {}
        for creature in decision["attackers"]:
            if creature["can_attack"] and self.rng.random() < 0.5:
                attacks[str(creature["id"])] = self.rng.choice(creature["can_attack"])
        return attacks

    # Deals: random too. It offers a random deal now and then, accepts half of
    # what it's offered, and since it attacks at random it breaks deals often.
    def offer_deal(self, view: Decision) -> dict | None:
        state = view["state"]
        others = [p["name"] for p in state["players"] if p["name"] != state["you"] and not p.get("lost")]
        if len(others) < 2 or self.rng.random() > 0.3:
            return None
        to, against = self.rng.sample(others, 2)
        if self.rng.random() < 0.5:
            return {"type": "peace", "to": to}
        return {"type": "gang_up", "to": to, "against": against}

    def answer_deal(self, view: Decision) -> bool:
        return self.rng.random() < 0.5
