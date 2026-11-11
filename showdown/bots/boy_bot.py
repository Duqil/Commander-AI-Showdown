"""BoyBot: a bot that learns. One of "The Boys".

Each Boy has a name and a brain (showdown/boys/brain.py) saved in
boys/<name>.json. For every decision it:

  1. turns each option into features (showdown/boys/features.py),
  2. asks the brain to pick one (likely the best-scoring, sometimes another),
  3. remembers the choice.

When the game ends, it works out a reward (showdown/boys/reward.py) and the
brain learns from every remembered choice. Then the brain is saved, so the
next game starts a little smarter.

So far a Boy decides mulligans, what to play (or pass), and attacks. Blocks
and targets are still Forge's AI, like for the other bots.

Use it with:  python -m showdown.run_game --bots boy:Kaiba simple simple simple
Train it with: python -m showdown.batch --bots boy:Kaiba simple simple simple --games 20
"""

from __future__ import annotations

import random

from showdown.bots.base import Bot, Decision
from showdown.boys import features
from showdown.boys.brain import Step
from showdown.boys.reward import game_reward, turn_bonus
from showdown.boys.storage import load_brain, save_brain


class BoyBot(Bot):
    def __init__(self, boy_name: str, learn: bool = True, explore: bool = True,
                 seed: int | None = None) -> None:
        self.name = f"boy:{boy_name}"
        self.brain = load_brain(boy_name)
        self.learn = learn
        self.explore = explore
        self.rng = random.Random(seed)
        self.steps: list[Step] = []          # every choice made this game
        self.last_state: dict | None = None  # the table as we last saw it
        self.result: dict | None = None      # filled in by game_over()
        # What we did to opponents on each of our turns: {turn: [damage, knockouts]}
        self.turn_effects: dict[int, list[int]] = {}

    def _observe(self, state: dict) -> None:
        """Compare the table with last time to see what happened on our turn.

        Life lost and players knocked out between two of our decisions are
        credited to the turn of the earlier decision, if that was our turn.
        """
        prev = self.last_state
        self.last_state = state
        if prev is None or prev["active_player"] != prev["you"]:
            return
        before = {p["name"]: p for p in prev["players"] if p["name"] != prev["you"]}
        effects = self.turn_effects.setdefault(prev["turn"], [0, 0])
        for p in state["players"]:
            old = before.get(p["name"])
            if old is None or old.get("lost"):
                continue
            effects[0] += max(0, old["life"] - p["life"])
            if p.get("lost"):
                effects[1] += 1

    def _pick(self, kind: str, options: list[features.Features]) -> int:
        step = self.brain.choose(kind, options, self.rng, explore=self.explore)
        step.turn = self.last_state["turn"] if self.last_state else 0
        self.steps.append(step)
        return step.picked

    # --- decisions ---------------------------------------------------------------

    def keep_hand(self, decision: Decision) -> bool:
        self._observe(decision["state"])
        return self._pick("mulligan", features.mulligan_options(decision)) == 0  # option 0 = keep

    def choose_play(self, decision: Decision) -> int | None:
        self._observe(decision["state"])
        picked = self._pick("play", features.play_options(decision))
        options = decision["options"]
        # The extra last option is "pass".
        return None if picked == len(options) else options[picked]["index"]

    def declare_attackers(self, decision: Decision) -> dict[str, int]:
        self._observe(decision["state"])
        ctx = features.attack_context(decision)
        attacks: dict[str, int] = {}
        for attacker in decision["attackers"]:
            option_features, targets = features.attack_options(attacker, decision, ctx)
            target = targets[self._pick("attack", option_features)]
            if target is not None:
                attacks[str(attacker["id"])] = target
        return attacks

    # --- after the game -------------------------------------------------------------

    def game_over(self, result: dict, player: str) -> None:
        players = result.get("players", [])
        opponents = [p for p in players if p["name"] != player]
        won = result.get("winner") == player
        if won:
            outlived = len(opponents)
        else:
            # Opponents already out the last time we saw the table, while we were alive.
            seen = self.last_state["players"] if self.last_state else []
            outlived = sum(1 for p in seen if p["name"] != player and p.get("lost"))
        reward = game_reward(won, outlived, len(opponents))

        # Damage dealt between our last decision and the end of the game counts too.
        if self.last_state is not None:
            final = {"players": players, "you": player, "turn": self.last_state["turn"],
                     "active_player": self.last_state["active_player"]}
            self._observe({**self.last_state, **final})
        for step in self.steps:
            damage, knockouts = self.turn_effects.get(step.turn, (0, 0))
            step.bonus = turn_bonus(damage, knockouts)
        damage = sum(e[0] for e in self.turn_effects.values())
        knockouts = sum(e[1] for e in self.turn_effects.values())

        advantage = None
        if self.learn and self.steps:
            advantage = self.brain.learn(self.steps, reward)
            save_brain(self.brain)
        self.result = {"won": won, "outlived": outlived, "reward": reward,
                       "advantage": advantage, "choices": len(self.steps),
                       "damage": damage, "knockouts": knockouts}
        self.steps = []
