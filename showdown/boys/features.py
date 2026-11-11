"""Turning a decision into numbers ("features") that a brain can score.

Every decision is a choice between options: keep or mulligan, which play (or
pass), where each creature attacks (or stays home). For each option we build
a small dict of named numbers, for example:

    {"attack": 1.0, "target_is_leader": 1.0, "target_life": 0.3, "simple_prior": 1.0}

The brain multiplies each number by a learned weight and adds them up. That
sum is the option's score. Named features (instead of a bare list of numbers)
make the saved brain readable: you can open boys/Kaiba.json and see how much
Kaiba likes attacking the leader.

Most numbers are scaled to roughly 0..1, so no single feature drowns the rest.

"simple_prior" is 1.0 for the option SimpleBot would pick. The Boys start out
trusting it, which is how they "learn on top of a rule-based bot": at first
they mostly play like SimpleBot, and they drift away where that pays off.
"""

from __future__ import annotations

from typing import Any

from showdown.bots.simple_bot import SimpleBot
from showdown.boys import threat

Features = dict[str, float]
Decision = dict[str, Any]

MAIN_PHASES = {"MAIN1", "MAIN2"}
_simple = SimpleBot()  # asked what it would do, to fill in "simple_prior"


def _flag(condition: bool) -> float:
    """True/False as 1.0/0.0, because the brain only does arithmetic."""
    return 1.0 if condition else 0.0


# ----------------------------------------------------------------------------
# Mulligan: two options, keep (index 0) or mulligan (index 1)
# ----------------------------------------------------------------------------

def mulligan_options(decision: Decision) -> list[Features]:
    hand = threat.me(decision["state"]).get("hand", [])
    lands = sum(1 for c in hand if "Land" in c.get("type", ""))
    to_bottom = decision.get("cards_to_bottom_if_kept", 0)
    simple_keeps = _simple.keep_hand(decision)

    keep = {
        "keep": 1.0,
        "keep_good_lands": _flag(2 <= lands <= 5),
        "keep_land_distance": abs(lands - 3.5) / 3.5,  # 0 = 3-4 lands, 1 = none or all lands
        "keep_cards_to_bottom": to_bottom / 3,          # keeping a small hand vs digging deeper
        "simple_prior": _flag(simple_keeps),
    }
    mulligan = {
        "mulligan": 1.0,
        "simple_prior": _flag(not simple_keeps),
    }
    return [keep, mulligan]


# ----------------------------------------------------------------------------
# Priority: one option per legal play, plus "pass" as the last option
# ----------------------------------------------------------------------------

def play_options(decision: Decision) -> list[Features]:
    state = decision["state"]
    my_turn = state["active_player"] == state["you"]
    main = state["phase"] in MAIN_PHASES
    stack = state.get("stack_size", 0) > 0
    simple_pick = _simple.choose_play(decision)  # an option "index", or None for pass

    result = []
    for option in decision["options"]:
        card = option.get("card", {})
        ctype = card.get("type", "")
        is_land = option.get("is_land", False)
        is_spell = option.get("is_spell", False)
        is_ability = not is_land and not is_spell
        result.append({
            "land_drop": _flag(is_land),
            "spell": _flag(is_spell),
            "spell_cmc": card.get("cmc", 0) / 7 if is_spell else 0.0,
            "creature_spell": _flag(is_spell and "Creature" in ctype),
            "instant_speed_spell": _flag(is_spell and ("Instant" in ctype or "Flash" in option.get("description", ""))),
            "spell_on_my_main": _flag(is_spell and my_turn and main and not stack),
            "spell_off_turn": _flag(is_spell and not my_turn),
            "ability": _flag(is_ability),
            # Abilities of lands, e.g. Evolving Wilds: "sacrifice: search for a land".
            "land_ability": _flag(is_ability and "Land" in ctype),
            "ability_on_my_turn": _flag(is_ability and my_turn),
            "respond_to_stack": _flag(stack),
            "simple_prior": _flag(simple_pick == option["index"]),
        })
    result.append({
        "pass": 1.0,
        "pass_on_my_main": _flag(my_turn and main and not stack),
        "pass_with_stack": _flag(stack),
        "simple_prior": _flag(simple_pick is None),
    })
    return result


# ----------------------------------------------------------------------------
# Attacks: one small choice per creature, "stay home" (option 0) or a defender
# ----------------------------------------------------------------------------

def attack_context(decision: Decision) -> dict[str, Any]:
    """Things that are the same for every creature in this combat, worked out once."""
    state = decision["state"]
    players = {p["name"]: p for p in state["players"]}
    total_power = sum(max(0, a.get("power", 0)) for a in decision["attackers"])
    return {
        "state": state,
        "players": players,
        "leader": threat.leader(state),
        "danger": min(threat.danger_to_me(state), 2.0) / 2,
        "my_life": threat.me(state).get("life", threat.STARTING_LIFE),
        "total_power": total_power,
        "simple": _simple.declare_attackers(decision) or {},
    }


def attack_options(attacker: dict, decision: Decision, ctx: dict[str, Any]) -> tuple[list[Features], list[int | None]]:
    """Options for one creature. Returns (features per option, defender index per option).

    The defender index list starts with None, meaning "stay home".
    """
    power = max(0, attacker.get("power", 0))
    toughness = max(0, attacker.get("toughness", 0))
    simple_target = ctx["simple"].get(str(attacker["id"]))

    feats: list[Features] = [{
        "stay_home": 1.0,
        "home_danger": ctx["danger"],                      # enemies could kill me soon
        "home_low_life": _flag(ctx["my_life"] <= 15),
        "home_power": power / 10,                          # big creatures make good blockers
        "simple_prior": _flag(simple_target is None),
    }]
    targets: list[int | None] = [None]

    # A creature with no power deals no damage, so attacking only risks it.
    # Leave it home without asking the brain (a hand-written rule, or "mask").
    if power == 0:
        return feats, targets

    defenders = {d["index"]: d for d in decision["defenders"]}
    for index in attacker.get("can_attack", []):
        d = defenders.get(index)
        if d is None:
            continue
        target = ctx["players"].get(d["name"]) if d.get("is_player") else None
        f: Features = {
            "attack": 1.0,
            "attack_power": power / 10,
            "attack_small": _flag(power <= 1),      # 0/1 tokens and the like rarely do much
            "attack_is_commander": _flag(attacker.get("commander", False)),
            "attack_planeswalker": _flag(target is None),
            "simple_prior": _flag(simple_target == index),
        }
        if target is not None:
            blockers = threat.untapped_blockers(target)
            # A blocker that would kill this creature and survive the hit.
            bad_block = any(b.get("power", 0) >= toughness and b.get("toughness", 0) > power for b in blockers)
            life = target.get("life", threat.STARTING_LIFE)
            f.update({
                "target_is_leader": _flag(d["name"] == ctx["leader"]),
                "target_threat_rank": threat.threat_rank(ctx["state"], d["name"]),  # 0 = biggest threat
                "target_life": life / threat.STARTING_LIFE,
                "target_low_life": _flag(life <= 10),
                "target_killable": _flag(threat.can_kill(target, ctx["total_power"])),
                "target_blockers": min(len(blockers), 5) / 5,
                "target_bad_block": _flag(bad_block),
                "target_weak": _flag(threat.board_power(target) == 0),  # not a threat right now
            })
        feats.append(f)
        targets.append(index)
    return feats, targets
