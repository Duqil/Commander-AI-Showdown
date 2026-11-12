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
probabilities, and which one it picked. After the game it gets a reward
(reward.py) and compares it with what it usually gets (the *baseline*):

    advantage = reward - baseline

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

Features = dict[str, float]

# Starting opinions about single features, used until a brain has learned its
# own weight for them. Everything else starts at 0.
#   * simple_prior / forge_suggested: a new Boy trusts SimpleBot's choice (and
#     Forge's suggested target), so its first games look like SimpleBot with
#     some random variation.
#   * attack_breaks_deal: a new Boy mostly keeps its word, but can learn when
#     betrayal pays. attack_grudge: and it starts out wanting revenge.
#   * attack_zero_power: attacking with a 0-power creature starts very unlikely
#     (about 1 in 20 against staying home), but stays possible: with a combat
#     trick in hand it can be right, and a Boy can learn that.
DEFAULT_WEIGHTS = {"simple_prior": 2.0, "attack_zero_power": -3.0, "forge_suggested": 2.0,
                   "attack_breaks_deal": -1.0, "attack_grudge": 1.0,
                   "mulligan_deeper": -2.0, "hurt_damage_dealer": 2.0}

# Some decisions learn slower. A mulligan happens once or twice a game, so each
# game moves it a lot, and whether we won is mostly noise for that one choice.
# At full speed Yugi's mulligan weights wandered until he threw away good hands.
KIND_RATE = {"mulligan": 0.25}

DECISION_KINDS = ("mulligan", "play", "attack", "target", "offer_whether", "offer", "answer")


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


@dataclass
class Brain:
    name: str
    weights: dict[str, dict[str, float]] = field(
        default_factory=lambda: {k: {} for k in DECISION_KINDS})
    baseline: float = 0.0           # running average reward
    bonus_baseline: float = 0.0     # running average turn bonus per choice
    games: int = 0                  # games learned from
    learning_rate: float = 0.1
    temperature: float = 1.0        # higher = more random, lower = greedier

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    # --- choosing -------------------------------------------------------------

    def score(self, kind: str, features: Features) -> float:
        w = self.weights.setdefault(kind, {})
        # A feature the brain has never seen starts at its default weight (usually 0).
        return sum(w.get(name, DEFAULT_WEIGHTS.get(name, 0.0)) * value for name, value in features.items())

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
        """Update the weights from one finished game. Returns the advantage used."""
        with self._lock:
            # The first game has no history, so the baseline starts at its reward.
            if self.games == 0:
                self.baseline = reward
            advantage = max(-2.0, min(2.0, reward - self.baseline))
            # Each choice also gets its own turn's bonus, compared with the usual bonus.
            bonuses = [s.bonus for s in steps if not s.pregame]
            mean_bonus = sum(bonuses) / len(bonuses) if bonuses else 0.0
            if self.bonus_baseline == 0.0:
                self.bonus_baseline = mean_bonus  # first game with turn bonuses
            # Spread the update over the game, per kind of decision: a long game
            # (hundreds of priority choices) shouldn't count for more, and the
            # few attack choices shouldn't drown in all the priority choices.
            per_kind: dict[str, int] = {}
            for step in steps:
                per_kind[step.kind] = per_kind.get(step.kind, 0) + 1
            for step in steps:
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
                    average = sum(p * f.get(name, 0.0) for p, f in zip(step.probs, step.options))
                    gradient = step.options[step.picked].get(name, 0.0) - average
                    if gradient:
                        w[name] = w.get(name, DEFAULT_WEIGHTS.get(name, 0.0)) + rate * gradient
            # Move the baseline 5% of the way towards this reward.
            self.baseline += 0.05 * (reward - self.baseline)
            self.bonus_baseline += 0.05 * (mean_bonus - self.bonus_baseline)
            self.games += 1
            return advantage

    # --- saving ----------------------------------------------------------------

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "games": self.games,
                "baseline": round(self.baseline, 4),
                "bonus_baseline": round(self.bonus_baseline, 4),
                "learning_rate": self.learning_rate,
                "temperature": self.temperature,
                # Sorted with the strongest opinions first, so the file is easy to read.
                "weights": {kind: dict(sorted(((k, round(v, 4)) for k, v in w.items()),
                                              key=lambda kv: -abs(kv[1])))
                            for kind, w in self.weights.items()},
            }

    @classmethod
    def from_dict(cls, data: dict) -> "Brain":
        return cls(
            name=data["name"],
            weights={k: dict(v) for k, v in data["weights"].items()},
            baseline=data.get("baseline", 0.0),
            bonus_baseline=data.get("bonus_baseline", 0.0),
            games=data.get("games", 0),
            learning_rate=data.get("learning_rate", 0.1),
            temperature=data.get("temperature", 1.0),
        )
