"""Removal that works: ward, holding instant removal, and board wipes.

Three things a good player checks before pointing removal at something, and
none of them looks at card names (everything is read from rules text):

1. Ward. "Ward {2}" means: when this becomes the target of an opponent's
   spell or ability, counter it unless that player pays {2}. Casting removal
   at it without 2 spare mana just throws the removal away. Better: wait
   until you can pay, or pick another target.

2. Instant removal. Swords to Plowshares can wait for the opponent's turn:
   remove the creature that actually attacks you, or answer what they cast.
   Firing it in your own main phase with nothing urgent going on wastes that.
   Keeping mana up for it is part of the same plan.

3. Board wipes. "Destroy all creatures" or "each player sacrifices a
   creature" hits everyone, you included. Worth it when the others lose more
   than you do. A wipe doesn't target anyone, so it never breaks a deal
   (showdown/deals.py), but a partner losing a lot still matters.
"""

from __future__ import annotations

import re

from showdown.boys import mana, threat, timing

# "Ward {2}", "Ward {1}{U}". Ward that costs life or cards isn't mana: skipped.
_WARD = re.compile(r"\bward\s*((?:\{[^}]+\})+)", re.I)
_SYMBOL = re.compile(r"\{([^}]+)\}")


def ward_cost(card: dict) -> int:
    """How much mana its ward asks for (0 = no ward, or ward that isn't mana)."""
    m = _WARD.search(card.get("oracle", "") or "")
    if not m:
        return 0
    total = 0
    for sym in _SYMBOL.findall(m.group(1)):
        total += int(sym) if sym.isdigit() else 1
    return total


def spare_mana(player: dict, cost: int) -> int:
    """Mana left after paying ``cost`` (targets are chosen before paying)."""
    return mana.untapped_mana(player) - cost


def ward_unpayable(card: dict, player: dict, cost: int) -> bool:
    """An opponent's card whose ward we couldn't pay after paying ``cost``."""
    ward = ward_cost(card)
    return ward > 0 and spare_mana(player, cost) < ward


def cost_still_to_pay(decision: dict, player: dict) -> int:
    """Mana the spell choosing targets still has to pay. A spell being cast
    picks targets before paying; a permanent already out (an "enters" or
    activated ability) is roughly paid for already."""
    source = decision.get("source", {}) or {}
    on_board = any(c.get("id") == source.get("id") for c in player.get("battlefield", []))
    return 0 if on_board else source.get("cmc", 0) or 0


def best_target(state: dict) -> dict | None:
    """The opponent creature removal would most likely go for: the biggest
    hitter of the biggest threat (power, then mana value)."""
    creatures = [(threat.threat_score(p), c.get("power", 0), c.get("cmc", 0), c)
                 for p in threat.opponents(state) for c in threat.creatures(p)]
    if not creatures:
        return None
    return max(creatures, key=lambda t: (t[1], t[2], t[0]))[3]


def removal_features(option: dict, state: dict, my_main: bool, removes) -> dict[str, float]:
    """Play features for a removal spell (``removes`` reads its text, deals.removes).

    removal_ward_unpayable: the creature it would go for has ward I couldn't
      pay after casting it. Wait a turn, or keep the mana up.
    instant_removal_no_rush: instant-speed removal in my own main phase, while
      nothing is attacking me or dealing damage. It could wait for their turn.
    """
    card = option.get("card", {})
    if not option.get("is_spell") or not removes(card.get("oracle", "") or ""):
        return {}
    me = threat.me(state)
    feats: dict[str, float] = {}
    target = best_target(state)
    cost = card.get("cmc", 0) + option.get("max_x", 0)
    if target is not None and ward_unpayable(target, me, cost):
        feats["removal_ward_unpayable"] = 1.0
    if my_main and timing.instant_speed(option) and not urgent(state):
        feats["instant_removal_no_rush"] = 1.0
    return feats


def urgent(state: dict) -> bool:
    """Is something hurting me right now? Danger is high (an opponent could
    hit me for half my life), or an opponent's card dealt 3+ damage last round."""
    if threat.danger_to_me(state) >= 0.5:
        return True
    return any((c.get("recent_damage") or 0) >= 3 for p in threat.opponents(state) for c in p.get("battlefield", []))


def taps_out_holding_removal(option: dict, state: dict, my_main: bool, removes) -> dict[str, float]:
    """Casting this (at sorcery speed) leaves too little mana for the instant
    removal I hold, while an opponent has a creature worth removing."""
    card = option.get("card", {})
    if not my_main or not option.get("is_spell") or timing.instant_speed(option):
        return {}
    me = threat.me(state)
    held = [c.get("cmc", 0) for c in mana.spells_in_hand(me)
            if c.get("id") != card.get("id") and mana.is_instant_speed(c) and removes(c.get("oracle", "") or "")]
    if not held or best_target(state) is None:
        return {}
    left = mana.untapped_mana(me) - card.get("cmc", 0) - option.get("max_x", 0)
    return {"taps_out_holding_removal": 1.0} if left < min(held) else {}


# --- board wipes --------------------------------------------------------------------

_DESTROY_ALL = re.compile(r"(destroy|exile) all (other )?(creatures|nonland permanents|permanents|artifacts and creatures)", re.I)
_SHRINK_ALL = re.compile(r"(all|each) (other )?creatures? gets? -(\d+)/-(\d+)", re.I)
_DAMAGE_ALL = re.compile(r"deals (\d+) damage to each creature", re.I)
_EACH_SACRIFICES = re.compile(r"each player sacrifices (a|an|one) creature", re.I)


def creature_value(card: dict) -> float:
    """What losing this creature costs: mana value plus power, double for a commander."""
    value = (card.get("cmc") or 0) + max(0, card.get("power") or 0)
    return value * (2 if card.get("commander") else 1)


def wipe_losses(card: dict, state: dict) -> dict[str, float] | None:
    """What each player would lose to this board wipe, or None if it isn't one.

    Read from text: destroy/exile all creatures, all creatures get -X/-X,
    N damage to each creature, each player sacrifices a creature (each
    player gives up their least valuable one).
    """
    text = card.get("oracle", "") or ""
    dies = None
    if _DESTROY_ALL.search(text):
        dies = lambda c: True
    elif (m := _SHRINK_ALL.search(text)):
        dies = lambda c, n=int(m.group(4)): (c.get("toughness") or 0) <= n
    elif (m := _DAMAGE_ALL.search(text)):
        dies = lambda c, n=int(m.group(1)): (c.get("toughness") or 0) <= n
    losses: dict[str, float] = {}
    for p in state.get("players", []):
        if p.get("lost"):
            continue
        mine = threat.creatures(p)
        if dies is not None:
            losses[p["name"]] = sum(creature_value(c) for c in mine if dies(c))
        elif _EACH_SACRIFICES.search(text):
            losses[p["name"]] = min((creature_value(c) for c in mine), default=0.0)
        else:
            return None
    return losses


def wipe_features(option: dict, state: dict, deals: dict) -> dict[str, float]:
    """Is this board wipe worth it? Compares what I lose with what the others lose.

    wipe_my_share: my part of everything the wipe destroys (0 = only others lose).
    wipe_good_trade: the biggest threat loses more than I do.
    wipe_partner_share: my deal partners' part of it (they won't like that).
    """
    card = option.get("card", {})
    if not option.get("is_spell"):
        return {}
    losses = wipe_losses(card, state)
    if not losses:
        return {}
    total = sum(losses.values())
    if total <= 0:
        return {"wipe_hits_nothing": 1.0}
    me = state.get("you")
    leader = threat.leader(state)
    partners = set(deals.get("protected", []))
    feats = {
        "wipe_my_share": losses.get(me, 0.0) / total,
        "wipe_good_trade": float(leader is not None and leader != me and losses.get(leader, 0.0) > losses.get(me, 0.0)),
        "wipe_partner_share": sum(v for k, v in losses.items() if k in partners) / total,
    }
    return {k: v for k, v in feats.items() if v}
