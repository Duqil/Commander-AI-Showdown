"""The neural add-on (showdown/boys/net.py)."""

import random

from showdown.boys import net
from showdown.boys.brain import Brain, Step


def test_new_addon_changes_almost_nothing():
    plain, neural = Brain("A"), Brain("B", neural=True)
    options = [{"attack": 1.0, "target_is_leader": 1.0, "target_bad_block": 1.0}, {"stay_home": 1.0}]
    for p, q in zip(plain.probabilities("attack", options), neural.probabilities("attack", options)):
        assert abs(p - q) < 0.01


def test_pairs_add_up_to_the_gut_feeling():
    tastes = {"a": [1.0, 0.5, 0, 0], "b": [-1.0, 0.2, 0, 0], "c": [0.3, 0.3, 0.3, 0.3]}
    f = {"a": 1.0, "b": 0.5, "c": 2.0, "card:play:x": 1.0, "simple_prior": 1.0}
    assert abs(net.score(tastes, "play", f) - sum(net.pairs(tastes, "play", f).values())) < 1e-9


def test_saves_and_loads():
    b = Brain("B", neural=True)
    b.nets["attack"] = {"attack": [0.5, 0.0, 0.0, 0.0]}
    again = Brain.from_dict(b.to_dict())
    assert again.neural and again.nets["attack"]["attack"][0] == 0.5
    assert not Brain.from_dict(Brain("A").to_dict()).neural   # old brains stay without it


def situation(rng):
    return rng.random() < 0.5, rng.random() < 0.5


def attack_options(bad, trick):
    return [{"attack": 1.0, "target_bad_block": float(bad), "holding_trick": float(trick)},
            {"stay_home": 1.0}]


def right_call(bad, trick):
    """A made-up table where two inputs only matter together: without a combat trick,
    don't attack into a killer blocker; with one, attack ONLY into it (the trick wins
    that fight, and is wasted otherwise). Plain weights can't learn this: "blocker"
    would need to be a minus and a plus at the same time."""
    return bad == trick


def accuracy(brain, n=400):
    rng, right = random.Random(1), 0
    for _ in range(n):
        s = situation(rng)
        attack = brain.choose("attack", attack_options(*s), rng, explore=False).picked == 0
        right += attack == right_call(*s)
    return right / n


def train(brain, games=8000, seed=7):
    rng = random.Random(seed)
    brain.critic["bias"] = 0.5
    for _ in range(games):
        s = situation(rng)
        step = brain.choose("attack", attack_options(*s), rng)
        reward = 1.0 if (step.picked == 0) == right_call(*s) else 0.0
        brain.learn([Step(step.kind, step.options, step.probs, step.picked, pregame=True)], reward)
    return accuracy(brain)


def test_learns_inputs_that_only_matter_together():
    for seed in range(3):
        assert train(Brain("P", learning_rate=0.05), seed=seed) < 0.9
        assert train(Brain("N", neural=True, learning_rate=0.05), seed=seed) == 1.0
