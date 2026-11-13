"""BoyBot: a bot that learns. One of "The Boys".

Each Boy has a name and a brain (showdown/boys/brain.py) saved in
boys/<name>.json. For every decision it:

  1. turns each option into features (showdown/boys/features.py),
  2. asks the brain to pick one (likely the best-scoring, sometimes another),
  3. remembers the choice.

When the game ends, it works out a reward (showdown/boys/reward.py) and the
brain learns from every remembered choice. Then the brain is saved, so the
next game starts a little smarter.

So far a Boy decides mulligans, what to play (or pass), the target of the
play it chose (when there is one target), and attacks. Blocks and other
choices are still Forge's AI, like for the other bots.

Use it with:  python -m showdown.run_game --bots boy:Kaiba simple simple simple
Train it with: python -m showdown.batch --bots boy:Kaiba simple simple simple --games 20
"""

from __future__ import annotations

import random

from showdown.bots.base import Bot, Decision
from showdown.boys import critic, features, threat
from showdown.boys.brain import Step
from showdown.boys.reward import (BROKE_A_DEAL, PARTNER_BROKE_MY_DEAL, game_reward, mulligan_bonus,
                                  turn_bonus)
from showdown.boys.storage import load_brain, save_brain

HAND_LIMIT = 7  # maximum hand size at the end of your turn


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
        self.deck: str | None = None         # our commander's name, as first seen (for the critic)
        # What happened on each of our own turns:
        #   {turn: [damage to opponents, knockouts, cards drawn, cards discarded to hand size]}
        self.turn_effects: dict[int, list[int]] = {}
        self.accepted: dict[int, Step] = {}  # deal id -> the "accept" choice that made the deal

    def _observe(self, state: dict) -> None:
        """Compare the table with last time to see what happened on our turn.

        Life lost and players knocked out between two of our decisions are
        credited to the turn of the earlier decision, if that was our turn.
        So are our own card changes: cards drawn (our library got smaller)
        and cards thrown away to hand size at the end of the turn (we ended
        with more than 7 and the hand shrank before our next decision).
        """
        prev = self.last_state
        self.last_state = state
        # .get(): during mulligans there is no active player or turn yet.
        if prev is None or prev.get("active_player") != prev.get("you"):
            return
        me = prev["you"]
        before = {p["name"]: p for p in prev["players"]}
        effects = self.turn_effects.setdefault(prev.get("turn", 0), [0, 0, 0, 0])
        for p in state["players"]:
            old = before.get(p["name"])
            if old is None or old.get("lost"):
                continue
            if p["name"] == me:
                self._observe_cards(old, p, effects, turn_ended=state.get("turn") != prev.get("turn"))
                continue
            effects[0] += max(0, old.get("life", 0) - p.get("life", 0))
            if p.get("lost"):
                effects[1] += 1

    @staticmethod
    def _observe_cards(old: dict, new: dict, effects: list[int], turn_ended: bool) -> None:
        if "library_size" in old and "library_size" in new:
            effects[2] += max(0, old["library_size"] - new["library_size"])  # drawn (or searched)
        if turn_ended and "hand_size" in old and "hand_size" in new and old["hand_size"] > HAND_LIMIT:
            # Over the limit at our last decision, fewer cards now: discarded at cleanup.
            discarded = max(0, min(old["hand_size"] - HAND_LIMIT, old["hand_size"] - new["hand_size"]))
            # Cards that are useful in the graveyard (flashback, Bloodghast...) don't count.
            seen = {c.get("id") for c in old.get("graveyard", [])}
            useful = sum(1 for c in new.get("graveyard", [])
                         if c.get("id") not in seen and (c.get("graveyard_use") or c.get("discard_me")))
            effects[3] += max(0, discarded - useful)

    def _pick(self, kind: str, options: list[features.Features]) -> int:
        step = self.brain.choose(kind, options, self.rng, explore=self.explore)
        step.turn = self.last_state.get("turn", 0) if self.last_state else 0
        if self.deck is None and self.last_state:
            self.deck = critic.my_commander(threat.me(self.last_state))
        step.situation = critic.situation(self.last_state, self.deck)
        self.steps.append(step)
        return step.picked

    # --- decisions ---------------------------------------------------------------

    def keep_hand(self, decision: Decision) -> bool:
        self._observe(decision["state"])
        keep = self._pick("mulligan", features.mulligan_options(decision)) == 0  # option 0 = keep
        step = self.steps[-1]
        step.pregame = True
        # The first mulligan is free; each one after it costs a card.
        if not keep and decision.get("mulligans_so_far", 0) > 0:
            step.bonus = mulligan_bonus(1)
        return keep

    def choose_play(self, decision: Decision) -> int | None:
        self._observe(decision["state"])
        picked = self._pick("play", features.play_options(decision))
        options = decision["options"]
        # The extra last option is "pass".
        return None if picked == len(options) else options[picked]["index"]

    def choose_targets(self, decision: Decision) -> list[int] | None:
        self._observe(decision["state"])
        if decision.get("max", 1) != 1 or not decision["candidates"]:
            return None  # several targets at once: leave it to Forge's AI for now
        picked = self._pick("target", features.target_options(decision))
        return [decision["candidates"][picked]["index"]]

    def pay_keyword_cost(self, decision: Decision) -> int | None:
        self._observe(decision["state"])
        pay = self._pick("keyword_cost", features.keyword_cost_options(decision)) == 1
        return decision.get("max", 1) if pay else 0

    def choose_card(self, decision: Decision) -> int | None:
        self._observe(decision["state"])
        if not decision["candidates"]:
            return None
        picked = self._pick("pick_card", features.pick_options(decision))
        return decision["candidates"][picked]["index"]

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

    # --- deals -------------------------------------------------------------------------

    def offer_deal(self, view: Decision) -> dict | None:
        # Two steps: first "offer anything this turn?", then "which deal?".
        if self._pick("offer_whether", features.offer_whether_options(view)) == 0:
            return None
        feats, offers = features.offer_options(view)
        if not offers:
            return None  # we already have deals with everyone
        return offers[self._pick("offer", feats)]

    def answer_deal(self, view: Decision) -> bool:
        accept = self._pick("answer", features.answer_options(view)) == 0  # option 0 = accept
        if accept and "id" in view.get("offer", {}):
            self.accepted[view["offer"]["id"]] = self.steps[-1]  # remembered for deal_broken
        return accept

    def deal_broken(self, deal: dict, breaker: str, player: str) -> None:
        """Politics penalties (see "Politics" in showdown/boys/reward.py)."""
        if breaker == player:
            # We broke it, with the attack we just chose: our latest attack
            # choice that went at a deal partner.
            for step in reversed(self.steps):
                if step.kind == "attack" and step.options[step.picked].get("attack_breaks_deal"):
                    step.deal_bonus += BROKE_A_DEAL
                    break
        elif deal.get("id") in self.accepted:
            # We accepted this deal and the partner broke it: that "yes" was a mistake.
            self.accepted.pop(deal["id"]).deal_bonus += PARTNER_BROKE_MY_DEAL

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
            final = {"players": players, "you": player}
            self._observe({**self.last_state, **final})
        for step in self.steps:
            if step.pregame:
                continue  # mulligans got their bonus when they were made
            damage, knockouts, drawn, discarded = self.turn_effects.get(step.turn, (0, 0, 0, 0))
            step.bonus = turn_bonus(damage, knockouts, drawn, discarded) + step.deal_bonus
        damage = sum(e[0] for e in self.turn_effects.values())
        knockouts = sum(e[1] for e in self.turn_effects.values())

        advantage = None
        if self.learn and self.steps:
            advantage = self.brain.learn(self.steps, reward)
            save_brain(self.brain)
        self.result = {"won": won, "outlived": outlived, "reward": reward,
                       "advantage": advantage, "choices": len(self.steps),
                       "damage": damage, "knockouts": knockouts,
                       "drawn": sum(e[2] for e in self.turn_effects.values()),
                       "discarded": sum(e[3] for e in self.turn_effects.values())}
        self.steps = []
        self.deck = None
        self.accepted = {}
