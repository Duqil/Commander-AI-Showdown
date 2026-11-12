"""What is my commander good for? Read from its card, not from its name.

Commanders differ a lot. Anhelo is a cheap support piece that powers up
instants and sorceries. The Swarmlord is an expensive big body that draws
cards and grows each time it's recast. Kuja turns noncreature spells into
damage. Nahiri wants equipped attackers.

So instead of one "cast my commander" weight, a Boy sees the commander's
*traits* (read from its rules text, cost and size) crossed with the
*situation*. "Engine for instants and sorceries" x "three of them in hand"
is a good reason to cast Anhelo now; "big body" x "late game, open board" is
a good reason to cast the Swarmlord. The Boy learns how much each pair is
worth. Nothing here names a card, so a new deck's commander works the same
way.

The features are plain numbers between 0 and 1, so they work for the
current linear brain and for a small neural network later.
"""

from __future__ import annotations

import re

from showdown.boys import threat

# "the first instant or sorcery spell you cast", "whenever you cast a noncreature spell"
ENGINE = re.compile(
    r"(instant or sorcery|noncreature|creature|artifact|enchantment|equipment|legendary)\s+spells?\s+you\s+cast"
    r"|whenever you cast an? (instant or sorcery|noncreature|creature|artifact|enchantment|equipment|legendary)?\s*spell",
    re.IGNORECASE)


def traits(card: dict) -> dict[str, float]:
    """The commander's traits, each 0 or 1 (from cost, size and rules text)."""
    text = (card.get("oracle") or "").lower()
    power, toughness = card.get("power", 0) or 0, card.get("toughness", 0) or 0
    name = card.get("name", "").split(",")[0].lower()
    return {
        "cheap": float(card.get("cmc", 0) <= 3),
        "big_body": float(power >= 5 or power + toughness >= 10),
        "spell_engine": float(bool(engine_types(card))),
        "draws": float("draw" in text),
        "makes_tokens": float("create" in text and "token" in text),
        "attack_trigger": float(bool(re.search(r"whenever [^,.]*attacks|deals combat damage", text))
                                or (name and f"whenever {name} attacks" in text)),
        "grows_when_recast": float("each time you've cast your commander" in text),
        "gets_cheaper": float(bool(re.search(r"costs? \{\d+\} less|affinity for", text))),
    }


def engine_types(card: dict) -> set[str]:
    """Which spells the commander rewards: e.g. {"instant", "sorcery"} or {"noncreature"}."""
    found: set[str] = set()
    for m in ENGINE.finditer(card.get("oracle") or ""):
        kind = (m.group(1) or m.group(2) or "any").lower()
        found |= {"instant", "sorcery"} if kind == "instant or sorcery" else {kind}
    return found


def feeds(spell_card: dict, kinds: set[str]) -> bool:
    """Would casting this spell set off an engine that rewards ``kinds``?"""
    ctype = spell_card.get("type", "").lower()
    for kind in kinds:
        if kind == "any" or kind in ctype:
            return True
        if kind == "noncreature" and "creature" not in ctype and "land" not in ctype:
            return True
    return False


def my_commander_out(state: dict) -> dict | None:
    """My commander, if it is on the battlefield."""
    me = threat.me(state)
    return next((c for c in me.get("battlefield", []) if c.get("commander")), None)


def cast_features(card: dict, state: dict) -> dict[str, float]:
    """Features for the option "cast my commander now": traits x situation."""
    t = traits(card)
    me = threat.me(state)
    stage = min(threat.game_round(state) - 1, 10) / 10           # 0 early, 1 by round 11
    hand = me.get("hand", [])
    kinds = engine_types(card)
    fuel = sum(1 for c in hand if feeds(c, kinds)) if kinds else 0
    power = card.get("power", 0) or 0
    # Could it attack into the table? The opponents' biggest untapped blocker.
    blockers = [b.get("power", 0) for p in threat.opponents(state) for b in threat.untapped_blockers(p)]
    open_board = float(not blockers or max(blockers) < (card.get("toughness", 0) or 0))
    slug = re.sub(r"[^a-z0-9]+", "_", card.get("name", "").split(",")[0].lower()).strip("_")
    return {
        "cmdr_cheap_early": t["cheap"] * (1 - stage),
        "cmdr_big_late": t["big_body"] * stage,
        "cmdr_big_open_board": t["big_body"] * open_board,
        "cmdr_engine_fuel": t["spell_engine"] * min(fuel, 4) / 4,   # spells in hand it would power up
        "cmdr_draws_low_hand": t["draws"] * float(len(hand) <= 2),
        "cmdr_makes_tokens": t["makes_tokens"],
        "cmdr_attacker_open_board": t["attack_trigger"] * open_board,
        "cmdr_grows_recast": t["grows_when_recast"] * min(card.get("commander_tax", 0), 6) / 6,
        "cmdr_gets_cheaper": t["gets_cheaper"],
        "cmdr_power": min(power, 10) / 10,
        # A small learned adjustment for this particular commander, on top of
        # its traits. Not a rule: it starts at 0 and only moves with training.
        f"cmdr_is_{slug}": 1.0,
    }


def engine_features(spell_card: dict, state: dict) -> dict[str, float]:
    """For any other spell: does it feed my commander's engine right now?"""
    cmdr = my_commander_out(state)
    if cmdr is None:
        return {}
    kinds = engine_types(cmdr)
    return {"feeds_my_commander": float(bool(kinds) and feeds(spell_card, kinds))}
