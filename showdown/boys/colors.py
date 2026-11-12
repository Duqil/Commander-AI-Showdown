"""Mana colors: what my cards need, and what my lands (and rocks) can make.

A three-color deck like Anhelo (blue, black, red) can have plenty of lands
and still be stuck: three Islands don't cast a {B}{R} spell. The bridge tells
us two things about every card (see StateSnapshot.java):

    card["cost"]      its mana cost, e.g. "{1}{U}{R}"
    card["produces"]  the colors it can make, e.g. "UB" (Sulfurous Springs)

Colors are the letters W (white), U (blue), B (black), R (red), G (green).
"""

from __future__ import annotations

import re
from collections import Counter

COLORS = "WUBRG"
# Everything between { and }: "{1}{U/R}{B}" -> ["1", "U/R", "B"]
SHARD = re.compile(r"\{([^}]*)\}")


def pips(cost: str | None) -> list[set[str]]:
    """The colored symbols of a mana cost, each as a set of colors that pay it.

    "{2}{U}{U/R}" -> [{"U"}, {"U", "R"}]. Generic mana ({2}, {X}) needs no color.
    """
    result = []
    for shard in SHARD.findall(cost or ""):
        colors = {c for c in shard.upper() if c in COLORS}
        if colors and "P" not in shard.upper():  # Phyrexian mana can be paid with life
            result.append(colors)
    return result


def produces(card: dict) -> set[str]:
    return set(card.get("produces", ""))


def is_land(card: dict) -> bool:
    return "Land" in card.get("type", "")


def identity(player: dict) -> set[str]:
    """My commander's colors (empty if we can't see the commander)."""
    cards = player.get("command", []) + player.get("battlefield", [])
    return {c for card in cards if card.get("commander")
            for pip in pips(card.get("cost")) for c in pip}


def useful_colors(card: dict, player: dict) -> set[str]:
    """Colors this card makes that my deck can use. Command Tower or Arcane
    Signet say "any color", but only my commander's colors matter."""
    mine = identity(player)
    return produces(card) & mine if mine else produces(card)


def available(player: dict) -> set[str]:
    """Colors my permanents can make right now (lands, Signets, ...)."""
    return set().union(*(useful_colors(c, player) for c in player.get("battlefield", [])), set())


def sources(player: dict) -> Counter:
    """How many of my permanents make each color."""
    count: Counter = Counter()
    for card in player.get("battlefield", []):
        for color in useful_colors(card, player):
            count[color] += 1
    return count


def needs(player: dict) -> Counter:
    """How much my hand and my commander want each color.

    Each colored symbol counts 1, split between the colors that can pay it
    (a {U/R} hybrid gives U and R half each).
    """
    want: Counter = Counter()
    cards = [c for c in player.get("hand", []) if not is_land(c)]
    cards += [c for c in player.get("command", []) if c.get("commander")]
    for card in cards:
        for pip in pips(card.get("cost")):
            for color in pip:
                want[color] += 1 / len(pip)
    return want


def missing(player: dict) -> set[str]:
    """Colors my cards want that none of my permanents can make."""
    have = available(player)
    return {c for c, n in needs(player).items() if n > 0 and c not in have}


def castable_by(card: dict, colors: set[str]) -> bool:
    """Could these colors pay every colored symbol of this card (ignoring amounts)?"""
    return all(pip & colors for pip in pips(card.get("cost")))


def land_value(land: dict, player: dict) -> float:
    """How much a new land with these colors would help.

    For each color it makes: how much my cards want that color, divided by
    (1 + how many sources of it I already have). A first black source when
    my hand is full of black spells scores high; a fourth Island scores low.
    """
    want = needs(player)
    have = sources(player)
    return sum(want[c] / (1 + have[c]) for c in produces(land))
