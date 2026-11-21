"""A Boy's brain: weights, scoring, and learning from rewards.

How a choice is made
--------------------
Each option has features (see features.py). Its *score* is

    score = sum(weight[name] * value for name, value in features.items())

Scores become probabilities with the *softmax* function: e^score, divided by
the total over all options. A higher score means more likely, but never
certain, so the Boy keeps trying other things now and then. That is called
*exploration*, and without it a bot can never discover a better move.

How it learns (the REINFORCE algorithm)
---------------------------------------
During a game the brain remembers every choice: the options' features, their
probabilities, which one it picked, and the situation it was in. After the game
it gets a reward (reward.py) and compares it with what its *critic* expected
from that situation (critic.py):

    advantage = reward - expected reward

For every remembered choice, each weight is nudged by

    learning_rate * advantage * (feature of the picked option - average feature)

Choices also get a bonus for damage and knockouts in their own turn
(reward.py), so a good attack is rewarded right away.

So after a better-than-usual game, whatever the picked options had more of
than the alternatives gets a bit more weight, and after a worse game a bit
less. Over many games the good habits win out. This is the simplest form of
"policy gradient" reinforcement learning, written out by hand so you can read
every step. No numpy or PyTorch needed.

Several games can run at once (batch.py), so all changes go through a lock.
"""

from __future__ import annotations

import math
import random
import threading
from dataclasses import dataclass, field

from showdown.boys import cards, critic

Features = dict[str, float]

# Starting opinions about single features, used until a brain has learned its
# own weight for them. Everything else starts at 0.
#   * simple_prior / forge_suggested: a new Boy trusts SimpleBot's choice (and
#     Forge's suggested target), so its first games look like SimpleBot with
#     some random variation.
#   * attack_breaks_deal: a new Boy mostly keeps its word, but can learn when
#     betrayal pays. attack_grudge / attack_revenge: and it starts out wanting
#     revenge (attack_revenge only until it has set the breaker back once).
#   * attack_zero_power: attacking with a 0-power creature starts very unlikely
#     (about 1 in 20 against staying home), but stays possible: with a combat
#     trick in hand it can be right, and a Boy can learn that.
DEFAULT_WEIGHTS = {"simple_prior": 2.0, "attack_zero_power": -3.0, "forge_suggested": 2.0,
                   "attack_breaks_deal": -1.0, "attack_grudge": 1.0,
                   "mulligan_deeper": -2.0, "hurt_damage_dealer": 2.0,
                   # Removal, damage, -X/-X on your OWN permanent starts strongly
                   # disliked (still learnable: sometimes it pays). Moving your own
                   # creature (blink, bounce) is a separate feature, "move_mine".
                   "hurt_mine": -2.0,
                   # Playing a bounce land as your only land wastes the land drop.
                   "land_bounces_itself": -3.0,
                   # A mild starting liking for casting the commander, which is
                   # always available. Not a rule: training moves it up or down.
                   "cast_commander": 1.0,
                   # Same idea: attacking sets off "whenever ... attacks" abilities.
                   "attack_triggers": 1.0,
                   # And equipping before combat, when a creature can attack this turn.
                   "attach_attacker_ready": 2.0,
                   # but not moving gear that already sits on a ready attacker.
                   "attach_already_on_attacker": -2.0,
                   # Deals (see "Politics" in reward.py). Starting dislikes, all learnable:
                   # offering while nobody has creatures to fight with,
                   "offer_quiet_table": -2.0,
                   # a gang-up neither partner can act on yet (to offer, or to accept),
                   "gang_pointless": -3.0, "accept_gang_pointless": -3.0,
                   # and accepting from someone who broke deals this game
                   # (1 - trust: a broken deal with you makes this 0.5 -> -2.0).
                   "accept_low_trust": -4.0,
                   # The other way around: ganging up on a player far ahead of
                   # everyone (big board or big life lead, see features.runaway_leader)
                   # starts out liked, to offer and to accept.
                   "offer_vs_runaway_leader": 1.0,
                   # Smarter casting (2026-11-16): let X spells grow, save fight
                   # spells for fights, hold a creature whose ETB has nothing to hit.
                   "x_grows_later": -0.8, "x_before_land_drop": -1.0, "x_board_needs_it": 1.0,
                   "x_while_other_plays": -0.4, "x_small": -0.8, "x_before_my_main": -1.0, "combat_spell_outside_combat": -0.8,
                   "combat_spell_in_combat": 0.6, "etb_no_target": -1.2,
                   # Mana this turn and next (mana.py, 2026-11-16).
                   "taps_out_holding_instant": -0.4, "taps_out_holding_counter": -0.4,
                   "pass_keeping_instant_mana": 0.3, "engine_without_fuel_now": -0.6,
                   "fuel_before_engine": -0.5, "tapland_costs_a_play": -1.0,
                   "tapland_on_quiet_turn": 0.6, "untapped_land_on_quiet_turn": -0.3,
                   "gang_against_runaway": 2.0, "accept_against_runaway": 2.0,
                   # Deals v3 (2026-11-18): who gains more from a deal (threat.deal_gains).
                   # My gain starts liked, the partner's (weighted by their threat) disliked.
                   "accept_my_gain": 2.0, "accept_their_gain": -2.0,
                   "peace_my_gain": 2.0, "peace_their_gain": -2.0,
                   "gang_my_gain": 1.0, "gang_their_gain": -1.0,
                   # A new deal that goes against one we have is betrayal: starts disliked.
                   "offer_breaks_deal": -2.0, "accept_breaks_deal": -2.0,
                   # Revenge: one real setback on whoever broke a deal with us, then it's over.
                   "attack_revenge": 1.5, "hurt_revenge_target": 1.5,
                   # Favours and sharing (2026-11-18): pay what we promised, follow our
                   # sharing plan, and go easy on players who share with us.
                   "pay_removal": 3.0, "pay_gift": 3.0, "share_planned": 2.0, "attack_my_sharer": -1.0,
                   # A favour is peace with a sweetener: a little more tempting than plain peace.
                   "accept_removal": 1.0, "accept_gift": 1.0,
                   # Pay within 2 rounds instead of 1: easier to keep for the payer,
                   # a little less sure for the one waiting.
                   "removal_pay_slow": 0.3, "gift_pay_slow": 0.3, "accept_pay_slow": -0.3,
                   # A named removal ("remove Kefka from Kuja"): offer it when our removal
                   # can hit that card; accepting likes a clear promise, more so on an engine.
                   "removal_named_can_hit": 0.5, "removal_named": -0.3,
                   "accept_named": 0.3, "accept_named_engine": 0.5,
                   # Gang-ups need a reason (Adam, 2026-11-20): more tempting against someone
                   # far ahead or someone who hurt us lately. Not a rule, just a starting liking.
                   "type_gang_vs_runaway": 1.0, "type_gang_vs_hurt_me": 1.0,
                   "gang_against_hurt_me": 1.0, "accept_against_hurt_me": 1.0,
                   "offer_no_reason": -1.5,
                   # The target of a gang-up pushes back: a milder pull than revenge (1.5).
                   "attack_push_back": 0.75, "hurt_push_back_target": 0.75,
                   # and plays that can pay it (a removal spell, Loran's "each draw").
                   "play_pays_removal": 1.5, "play_gives_gift": 1.5,
                   # Attacking into blockers (features.attack_options): mild starting
                   # pulls, so the Boys think about blocks without being scared to attack.
                   "target_outnumber": 0.5, "attack_valuable_at_risk": -0.75,
                   "attack_last_creatures_at_risk": -0.5}

# Some decisions learn slower. A mulligan happens once or twice a game, so each
# game moves it a lot, and whether we won is mostly noise for that one choice.
# At full speed Yugi's mulligan weights wandered until he threw away good hands.
KIND_RATE = {"mulligan": 0.25}

# Card memory (cards.py): one learned number per card, for Boys that have it
# switched on. A single card shows up in only some games, so a lucky win
# would push its number around a lot. Two things keep that calm:
#   * CARD_RATE: card numbers learn at half the normal speed;
#   * CARD_SHRINK: once per game, every card number that came up in that game
#     loses 0.5% of its size, so it only stays big if the card keeps earning
#     it. A card he rarely sees can't hold on to an opinion from one lucky game.
#     (It used to shrink at every single decision the card was part of, up to
#     a hundred times a game, while each decision only moves it a little. That
#     kept every card number near zero: after ~3,700 games the biggest was 0.1.)
CARD_RATE = 0.5
CARD_SHRINK = 0.005

DECISION_KINDS = ("mulligan", "play", "attack", "target", "pick_card", "keyword_cost", "offer_whether", "offer_type",
                  "offer", "answer")


@dataclass
class Step:
    """One remembered choice."""
    kind: str
    options: list[Features]
    probs: list[float]
    picked: int
    turn: int = 0
    bonus: float = 0.0   # reward for what happened in this step's turn (see reward.py)
    pregame: bool = False  # made before turn 1 (mulligans): its bonus isn't a turn's bonus
    deal_bonus: float = 0.0  # politics penalties (reward.py), added on top of the turn bonus
    situation: dict[str, float] | None = None  # what the critic looks at (critic.situation)


@dataclass
class Brain:
    name: str
    weights: dict[str, dict[str, float]] = field(
        default_factory=lambda: {k: {} for k in DECISION_KINDS})
    baseline: float = 0.0           # running average reward (kept for reports)
    critic: dict[str, float] = field(default_factory=dict)  # expected-reward weights (critic.py)
    bonus_baseline: float = 0.0     # running average turn bonus per choice
    games: int = 0                  # games learned from
    learning_rate: float = 0.1
    temperature: float = 1.0        # higher = more random, lower = greedier
    card_memory: bool = False       # learn a number per card too (cards.py)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    # --- choosing -------------------------------------------------------------

    def score(self, kind: str, features: Features) -> float:
        w = self.weights.setdefault(kind, {})
        # A feature the brain has never seen starts at its default weight (usually 0).
        # Card memory features only count for Boys that have card memory.
        return sum(w.get(name, DEFAULT_WEIGHTS.get(name, 0.0)) * value for name, value in features.items()
                   if self.card_memory or not cards.is_card_feature(name))

    def probabilities(self, kind: str, options: list[Features]) -> list[float]:
        with self._lock:
            scores = [self.score(kind, f) / self.temperature for f in options]
        top = max(scores)  # subtracting the max avoids overflow in exp(); the result is the same
        exps = [math.exp(s - top) for s in scores]
        total = sum(exps)
        return [e / total for e in exps]

    def choose(self, kind: str, options: list[Features], rng: random.Random,
               explore: bool = True) -> Step:
        """Pick an option. explore=False always takes the best-scoring one."""
        probs = self.probabilities(kind, options)
        if explore:
            picked = rng.choices(range(len(options)), weights=probs)[0]
        else:
            picked = max(range(len(options)), key=lambda i: probs[i])
        return Step(kind, options, probs, picked)

    # --- learning -------------------------------------------------------------

    def learn(self, steps: list[Step], reward: float) -> float:
        """Update the weights from one finished game. Returns the average advantage used."""
        with self._lock:
            # The first game has no history, so the baseline starts at its reward.
            if self.games == 0:
                self.baseline = reward
            if "bias" not in self.critic:
                # A new critic starts out guessing the old running average,
                # so a brain from before the critic behaves just like before.
                self.critic["bias"] = self.baseline
            situations = [s.situation or {"bias": 1.0} for s in steps]
            advantages = [max(-2.0, min(2.0, reward - critic.expected(self.critic, sit)))
                          for sit in situations]
            # Each choice also gets its own turn's bonus, compared with the usual bonus.
            bonuses = [s.bonus for s in steps if not s.pregame]
            mean_bonus = sum(bonuses) / len(bonuses) if bonuses else 0.0
            if self.bonus_baseline == 0.0:
                self.bonus_baseline = mean_bonus  # first game with turn bonuses
            # Spread the update over the game, per kind of decision: a long game
            # (hundreds of priority choices) shouldn't count for more, and the
            # few attack choices shouldn't drown in all the priority choices.
            per_kind: dict[str, int] = {}
            cards_seen: set[tuple[str, str]] = set()  # (kind, card feature) that came up this game
            for step in steps:
                per_kind[step.kind] = per_kind.get(step.kind, 0) + 1
            for step, advantage in zip(steps, advantages):
                if len(step.options) < 2:
                    continue  # no real choice, nothing to learn
                # Turn choices are compared with the usual turn bonus. Mulligans
                # have no turn, so only their own bonus (cards lost) counts.
                usual = 0.0 if step.pregame else self.bonus_baseline
                step_advantage = max(-2.0, min(2.0, advantage + step.bonus - usual))
                rate = (self.learning_rate * KIND_RATE.get(step.kind, 1.0) * step_advantage
                        / math.sqrt(per_kind[step.kind]))
                w = self.weights.setdefault(step.kind, {})
                names = set().union(*step.options)
                for name in names:
                    is_card = cards.is_card_feature(name)
                    if is_card and not self.card_memory:
                        continue
                    average = sum(p * f.get(name, 0.0) for p, f in zip(step.probs, step.options))
                    gradient = step.options[step.picked].get(name, 0.0) - average
                    if not gradient:
                        continue
                    if is_card:
                        w[name] = w.get(name, 0.0) + CARD_RATE * rate * gradient
                        cards_seen.add((step.kind, name))
                    else:
                        w[name] = w.get(name, DEFAULT_WEIGHTS.get(name, 0.0)) + rate * gradient
            for kind, name in cards_seen:  # the once-per-game shrink (CARD_SHRINK)
                self.weights[kind][name] *= 1 - CARD_SHRINK
            # The critic learns from this game too, *after* it was used, so a
            # game is never judged by a guess that already knew its result.
            critic.learn(self.critic, situations, reward)
            # Move the baseline 5% of the way towards this reward.
            self.baseline += 0.05 * (reward - self.baseline)
            self.bonus_baseline += 0.05 * (mean_bonus - self.bonus_baseline)
            self.games += 1
            return sum(advantages) / len(advantages) if advantages else 0.0

    # --- saving ----------------------------------------------------------------

    def to_dict(self) -> dict:
        with self._lock:
            data = {
                "name": self.name,
                "games": self.games,
                "baseline": round(self.baseline, 4),
                "bonus_baseline": round(self.bonus_baseline, 4),
                "learning_rate": self.learning_rate,
                "temperature": self.temperature,
                # Expected reward: "bias" is the average, the rest add or subtract.
                "critic": dict(sorted(((k, round(v, 4)) for k, v in self.critic.items()),
                                      key=lambda kv: -abs(kv[1]))),
                # Sorted with the strongest opinions first, so the file is easy to read.
                "weights": {kind: dict(sorted(((k, round(v, 4)) for k, v in w.items()
                                               if not cards.is_card_feature(k)),
                                              key=lambda kv: -abs(kv[1])))
                            for kind, w in self.weights.items()},
            }
            if self.card_memory:
                # Card numbers get their own section at the end, so the general
                # weights above stay short and easy to read.
                data["card_memory"] = True
                data["cards"] = {kind: dict(sorted(((k.removeprefix(cards.PREFIX), round(v, 4))
                                                    for k, v in w.items() if cards.is_card_feature(k)),
                                                   key=lambda kv: -abs(kv[1])))
                                 for kind, w in self.weights.items()}
            return data

    @classmethod
    def from_dict(cls, data: dict) -> "Brain":
        weights = {k: dict(v) for k, v in data["weights"].items()}
        for kind, card_weights in data.get("cards", {}).items():
            weights.setdefault(kind, {}).update(
                {cards.PREFIX + k: v for k, v in card_weights.items()})
        return cls(
            name=data["name"],
            weights=weights,
            baseline=data.get("baseline", 0.0),
            critic=dict(data.get("critic", {})),
            bonus_baseline=data.get("bonus_baseline", 0.0),
            games=data.get("games", 0),
            learning_rate=data.get("learning_rate", 0.1),
            temperature=data.get("temperature", 1.0),
            card_memory=data.get("card_memory", False),
        )
