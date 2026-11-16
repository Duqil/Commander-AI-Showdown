"""Card memory: a learned opinion about each single card.

Every other feature describes what a card *is* or *does* (a creature, an X
spell, a land that makes a color I'm missing). Those rules work for any deck,
but they can't know that one particular card is better or worse than its
description suggests. Card memory adds that, the learned way:

    "card:cast:lightning_greaves"          casting this card
    "card:cast:lightning_greaves@early"    ...in the early game (rounds 1-3)
    "card:cast:lightning_greaves@late"     ...in the late game (round 8 on)

Each of these starts at weight 0, so a Boy with card memory plays exactly like
before on day one. Over many games he learns, for example, "this card is worth
casting even when my general rules say wait", or "that opponent card is the
one to remove". Nothing here is written by hand for a specific card: the name
is only a label for a number the Boy learns himself.

Only brains with card memory switched on look at these features (see
``card_memory`` in brain.py). Everyone else ignores them, so the other Boys
play and learn exactly as before.

What the verbs mean:
    cast    casting this spell (play decision)
    use     activating an ability of this card (play decision)
    pick    choosing this card from a search or "look at the top N" (pick_card)
    hurt    removal, damage, -X/-X... aimed at this card (target)
    help    a helpful effect aimed at this card (target)
    attack  attacking with this creature (attack)
"""

from __future__ import annotations

import re

from showdown.boys import threat

Features = dict[str, float]

PREFIX = "card:"   # every card memory feature starts with this

# Game stages, by round (round 1 = everyone's first turn, see threat.game_round).
EARLY_UNTIL = 3    # rounds 1-3
LATE_FROM = 8      # round 8 and later; the rounds in between only get the plain feature


def is_card_feature(name: str) -> bool:
    return name.startswith(PREFIX)


def slug(name: str) -> str:
    """A card name as a safe label: "Kuja, Genome Sorcerer" -> "kuja_genome_sorcerer"."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def stage(state: dict) -> str | None:
    """'early', 'late', or None for the middle of the game."""
    r = threat.game_round(state)
    if r <= EARLY_UNTIL:
        return "early"
    if r >= LATE_FROM:
        return "late"
    return None


def features(verb: str, card: dict, state: dict | None = None) -> Features:
    """Card memory features for doing ``verb`` with ``card``.

    With a state, it adds the early/late version too, so the Boy can learn
    "good early, bad late" (a cheap creature) or the other way around.
    """
    name = slug(card.get("name", ""))
    if not name:
        return {}
    key = f"{PREFIX}{verb}:{name}"
    f = {key: 1.0}
    when = stage(state) if state is not None else None
    if when:
        f[f"{key}@{when}"] = 1.0
    return f


def play_features(option: dict, state: dict) -> Features:
    """For a play option: cast a spell or use an ability. Land drops get none:
    which land to play is about colors and mana, and mana.py covers that."""
    card = option.get("card", {})
    if option.get("is_land"):
        return {}
    return features("cast" if option.get("is_spell") else "use", card, state)


def target_features(cand: dict, harmful: bool) -> Features:
    """For a target option: hurting or helping this card. Players get none."""
    if cand.get("is_player"):
        return {}
    return features("hurt" if harmful else "help", cand)
