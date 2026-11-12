"""The critic: a Boy's guess of how well a game will go from here.

Why a Boy needs one
-------------------
The brain learns by comparing a game's reward with what it *expected*
(see brain.py). Before the critic, "expected" was one running average for
every game. That is unfair in two ways:

  * Decks differ a lot. Swarmlord wins far less often than Garnet, so on
    Swarmlord almost every game looked "worse than usual", and the Boy
    unlearned good habits just because the deck is weaker.
  * Situations differ. Being at 8 life against three healthy opponents is a
    bad spot. Losing from there doesn't mean the choice you made was bad.

The critic fixes that by guessing the reward from the situation a choice was
made in. A choice then gets credit for doing *better than expected from that
spot*, which filters out a lot of the luck in a 4-player game.

How it works
------------
Exactly like the brain's scores: a weighted sum of a few numbers.

    expected reward = sum(weight[name] * value for name, value in situation)

After each game every weight is nudged so the guess gets closer to the real
reward (this is called *linear regression*, learned a little at a time).
The "bias" situation is always 1, so its weight is the plain average reward:
with only that weight, the critic is the same as the old running average.
That is how old brains move over: their average becomes the bias.

Every number is between 0 and about 1, so no weight has to be huge. You can
read the weights in a Boy's file under "critic": a positive weight means
"this makes a good result more likely".
"""

from __future__ import annotations

from showdown.boys import threat

Situation = dict[str, float]

# How far each weight moves towards a better guess after one game. Same speed
# as the old running average (5%), so the bias behaves exactly like it did.
RATE = 0.05


def my_commander(me: dict) -> str | None:
    """The name of this player's commander, wherever it is right now."""
    for zone in ("command", "battlefield", "graveyard", "exile"):
        for card in me.get(zone, []):
            if card.get("commander"):
                return card.get("name")
    return None


def situation(state: dict | None, deck: str | None = None) -> Situation:
    """The few numbers the critic looks at. All roughly between 0 and 1.

    ``deck`` names the deck (its commander as first seen this game). Without it
    we read the commander's current name, but a double-faced commander like
    Kuja changes name when it transforms, which would look like a new deck.
    """
    if not state or not state.get("players"):
        return {"bias": 1.0}
    me = threat.me(state)
    rivals = [p for p in state["players"] if p.get("name") != me.get("name") and not p.get("lost")]
    s: Situation = {"bias": 1.0}
    s["turn"] = min(state.get("turn", 0), 20) / 20
    s["my_life"] = max(0, me.get("life", threat.STARTING_LIFE)) / threat.STARTING_LIFE
    s["my_hand"] = min(me.get("hand_size", 0), 10) / 10
    s["my_lands"] = min(sum(1 for c in me.get("battlefield", []) if "Land" in c.get("type", "")), 10) / 10
    s["rivals_left"] = len(rivals) / 3
    if rivals:
        s["rival_life"] = sum(max(0, p.get("life", 0)) for p in rivals) / len(rivals) / threat.STARTING_LIFE
        # My share of all the hitting power at the table (0 = none, 1 = all of it).
        mine = threat.damage_output(me)
        total = mine + sum(threat.damage_output(p) for p in rivals)
        s["my_power_share"] = mine / total if total else 0.25
    # Which deck I'm playing. Its weight learns how well this deck usually does,
    # so a weak deck's losses aren't blamed on the choices.
    commander = deck or my_commander(me)
    if commander:
        s[f"deck:{commander}"] = 1.0
    return s


def expected(weights: dict[str, float], s: Situation) -> float:
    """The critic's guess of the reward from situation ``s``."""
    return sum(weights.get(name, 0.0) * value for name, value in s.items())


def learn(weights: dict[str, float], situations: list[Situation], reward: float) -> None:
    """Move the weights so the guesses for this game get closer to ``reward``.

    A game has hundreds of choices, and their situations are similar. So we
    average the corrections over the whole game: one game counts as one
    lesson, however long it was.
    """
    if not situations:
        return
    corrections: dict[str, float] = {}
    for s in situations:
        error = reward - expected(weights, s)   # positive = it went better than guessed
        for name, value in s.items():
            corrections[name] = corrections.get(name, 0.0) + error * value
    for name, total in corrections.items():
        weights[name] = weights.get(name, 0.0) + RATE * total / len(situations)
