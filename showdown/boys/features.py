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

def is_counter_booster(card: dict) -> bool:
    """Cards like Hardened Scales or Branching Evolution: more +1/+1 counters
    on everything after them. Worth casting BEFORE a big X creature."""
    text = card.get("oracle", "").lower()
    return "+1/+1 counter" in text and any(
        phrase in text for phrase in ("that many plus", "twice that many", "an additional +1/+1", "plus one"))


def x_context(options: list[dict]) -> dict[str, float]:
    """The best X creature castable right now and roughly how much mana we have.

    An X option's mana value counts X as 0, so mana value + max_x is about the
    mana we have available. Used to tell whether a cheap play first still
    leaves a decent X afterwards."""
    x_creatures = [o for o in options if "max_x" in o and "Creature" in o.get("card", {}).get("type", "")]
    if not x_creatures:
        return {}
    best = max(x_creatures, key=lambda o: o["max_x"])
    return {"max_x": best["max_x"], "fixed_cost": best["card"].get("cmc", 0)}


def play_options(decision: Decision) -> list[Features]:
    state = decision["state"]
    my_turn = state["active_player"] == state["you"]
    main = state["phase"] in MAIN_PHASES
    stack = state.get("stack_size", 0) > 0
    simple_pick = _simple.choose_play(decision)  # an option "index", or None for pass
    xc = x_context(decision["options"])

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
            "spell_cmc": (card.get("cmc", 0) + option.get("max_x", 0)) / 7 if is_spell else 0.0,
            "x_spell": _flag("max_x" in option),
            # How big the X would be (all mana in): bigger X = bigger Ravenous creature.
            "x_size": option.get("max_x", 0) / 7,
            **sequencing_features(option, card, is_spell, xc),
            "creature_spell": _flag(is_spell and "Creature" in ctype),
            "instant_speed_spell": _flag(is_spell and ("Instant" in ctype or "Flash" in option.get("description", ""))),
            "spell_on_my_main": _flag(is_spell and my_turn and main and not stack),
            "spell_off_turn": _flag(is_spell and not my_turn),
            "ability": _flag(is_ability),
            # Abilities of lands, e.g. Evolving Wilds: "sacrifice: search for a land".
            "land_ability": _flag(is_ability and "Land" in ctype),
            "ability_on_my_turn": _flag(is_ability and my_turn),
            # Forge's own AI would make this play now (a hint, learned like any feature).
            "forge_would_play": _flag(option.get("forge_would_play", False)),
            "ability_forge_would_play": _flag(is_ability and option.get("forge_would_play", False)),
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


def sequencing_features(option: dict, card: dict, is_spell: bool, xc: dict) -> Features:
    """Should this play come BEFORE an X creature we could also cast now?

    E.g. Hardened Scales (1 mana) first, then Tervigon with X one smaller but
    every counter +1. SimpleBot always dumps everything into the biggest X;
    these features let a Boy learn when a cheap play first is better.
    """
    if not xc or "max_x" in option:
        return {"counter_booster": _flag(is_counter_booster(card))}
    x_after = xc["max_x"] - card.get("cmc", 0)  # what X would be left after this play
    booster = is_counter_booster(card)
    return {
        "counter_booster": _flag(booster),
        "before_x_creature": 1.0,                       # an X creature is waiting
        "x_left_after": max(0, x_after) / 7,             # how big it could still be
        "kills_the_x": _flag(x_after < 1),               # no mana left for X at all
        "booster_before_x": _flag(booster and x_after >= 1),
    }


# ----------------------------------------------------------------------------
# Attacks: one small choice per creature, "stay home" (option 0) or a defender
# ----------------------------------------------------------------------------

def attack_context(decision: Decision) -> dict[str, Any]:
    """Things that are the same for every creature in this combat, worked out once."""
    state = decision["state"]
    players = {p["name"]: p for p in state["players"]}
    total_power = sum(max(0, a.get("power", 0)) for a in decision["attackers"])
    mine = threat.me(state)
    instants = sum(1 for c in mine.get("hand", []) if "Instant" in c.get("type", ""))
    untapped_lands = sum(1 for c in mine.get("battlefield", [])
                         if "Land" in c.get("type", "") and not c.get("tapped"))
    return {
        # Could we cast a combat trick? (We only see card types, not what the
        # instant does, so this is a rough "maybe".)
        "trick_possible": instants > 0 and untapped_lands > 0,
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
            "attack_zero_power": _flag(power == 0), # no damage unless pumped mid-combat
            # Same, but with an instant in hand and mana open: maybe a pump spell.
            "attack_small_with_trick": _flag(power <= 1 and ctx["trick_possible"]),
            "attack_zero_power_with_trick": _flag(power == 0 and ctx["trick_possible"]),
            "attack_is_commander": _flag(attacker.get("commander", False)),
            "attack_planeswalker": _flag(target is None),
            "simple_prior": _flag(simple_target == index),
        }
        deals = decision.get("deals", {})
        if d["name"] in deals.get("protected", []):
            f["attack_breaks_deal"] = 1.0     # we have a deal with them: attacking breaks it
        if d["name"] in deals.get("gang_targets", []):
            f["attack_gang_target"] = 1.0     # we promised to attack them
        if d["name"] in deals.get("grudges", []):
            f["attack_grudge"] = 1.0          # they broke a deal with us: revenge
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


# ----------------------------------------------------------------------------
# Targets: one option per legal target (only for abilities with one target)
# ----------------------------------------------------------------------------

# What an ability does, by Forge's name for it ("api"). Harmful ones hurt
# their target, helpful ones help it. Some depend on details, checked below.
HARMFUL_APIS = {"Destroy", "DealDamage", "Debuff", "Counter", "Sacrifice", "Tap", "Fight",
                "GainControl", "GainControlVariant", "ExchangeControl", "Goad", "Detain",
                "LoseLife", "Mill", "Discard", "Poison", "MustBlock", "RemoveFromCombat",
                "SetLife"}
HELPFUL_APIS = {"Untap", "Protection", "Regenerate", "GainLife", "Animate", "Draw",
                "HealDamage", "PreventDamage", "Phases"}

# Fallback when the api is unknown: words that usually mean "this hurts".
HARMFUL_WORDS = ("destroy", "exile", "damage", "sacrifice", "counter target", "tap target",
                 "return target", "-1/-1", "loses", "goad", "gain control", "fight")


def is_harmful(decision: Decision) -> bool:
    """Does this ability hurt what it targets? Uses what Forge says it does."""
    api = decision.get("api")
    params = decision.get("params", {})
    if api in HARMFUL_APIS:
        return True
    if api in HELPFUL_APIS:
        return False
    if api in ("Pump", "PumpAll"):
        return params.get("NumAtt", "").startswith("-")      # -2/-2 shrinks, +2/+2 pumps
    if api in ("PutCounter", "PutCounterAll"):
        return "M1M1" in params.get("CounterType", "")        # -1/-1 counters hurt
    if api in ("ChangeZone", "ChangeZoneAll"):
        return params.get("Origin", "") == "Battlefield"       # exile or bounce from play
    text = decision.get("description", "").lower()
    return any(word in text for word in HARMFUL_WORDS)


def target_options(decision: Decision) -> list[Features]:
    state = decision["state"]
    you = state["you"]
    leader = threat.leader(state)
    players = {p["name"]: p for p in state["players"]}
    harmful = is_harmful(decision)
    suggested = set(decision.get("suggested", []))

    result = []
    for cand in decision["candidates"]:
        is_player = cand.get("is_player", False)
        owner = cand["name"] if is_player else cand.get("controller", "")
        mine = owner == you
        ctype = cand.get("type", "")
        f: Features = {
            "forge_suggested": _flag(cand["index"] in suggested),  # Forge's AI would pick this
            "hurt_mine": _flag(harmful and mine),
            "hurt_opponent": _flag(harmful and not mine),
            "help_mine": _flag(not harmful and mine),
            "help_opponent": _flag(not harmful and not mine),
            "target_player": _flag(is_player),
            "target_creature": _flag("Creature" in ctype),
            "target_power": max(0, cand.get("power", 0)) / 10,
            "target_cmc": cand.get("cmc", 0) / 7,
            "target_commander": _flag(cand.get("commander", False)),
            "target_of_leader": _flag(owner == leader),
        }
        if is_player and not mine:
            life = cand.get("life", threat.STARTING_LIFE)
            f["target_player_life"] = life / threat.STARTING_LIFE
            f["target_player_low_life"] = _flag(life <= 10)
        elif not is_player and not mine and owner in players:
            f["target_owner_threat_rank"] = threat.threat_rank(state, owner)
        result.append(f)
    return result


# ----------------------------------------------------------------------------
# Deals: what to offer at the start of our turn, and whether to accept
# ----------------------------------------------------------------------------

def offer_options(view: Decision) -> tuple[list[Features], list[dict | None]]:
    """Options for offering a deal. Returns (features, offer per option); None = no offer."""
    state = view["state"]
    trust = view["deals"]["trust"]
    opponents = threat.opponents(state)
    leader = threat.leader(state)
    me = threat.me(state)
    i_lead = bool(opponents) and threat.threat_score(me) > max(threat.threat_score(p) for p in opponents)
    danger = min(threat.danger_to_me(state), 2.0) / 2
    simple = _simple.offer_deal(view)
    protected = set(view["deals"]["protected"])

    feats: list[Features] = [{"no_offer": 1.0, "simple_prior": _flag(simple is None)}]
    offers: list[dict | None] = [None]
    for to in opponents:
        if to["name"] in protected:
            continue  # already have a deal with them
        base = {
            "to_is_leader": _flag(to["name"] == leader),
            "to_threat_rank": threat.threat_rank(state, to["name"]),
            "to_trust": trust.get(to["name"], 1.0),
            "to_grudge": _flag(to["name"] in view["deals"].get("grudges", [])),
            "i_am_leader": _flag(i_lead),
            "my_danger": danger,
        }
        offer = {"type": "peace", "to": to["name"]}
        feats.append({"offer_peace": 1.0, **{f"peace_{k}": v for k, v in base.items()},
                      "simple_prior": _flag(simple == offer)})
        offers.append(offer)
        for against in opponents:
            if against["name"] == to["name"]:
                continue
            offer = {"type": "gang_up", "to": to["name"], "against": against["name"]}
            feats.append({"offer_gang_up": 1.0, **{f"gang_{k}": v for k, v in base.items()},
                          "gang_against_is_leader": _flag(against["name"] == leader),
                          "gang_against_threat_rank": threat.threat_rank(state, against["name"]),
                          "simple_prior": _flag(simple == offer)})
            offers.append(offer)
    return feats, offers


def answer_options(view: Decision) -> list[Features]:
    """Two options: accept (index 0) or decline (index 1)."""
    state = view["state"]
    offer = view["offer"]
    offerer = offer["a"]
    leader = threat.leader(state)
    simple_accepts = _simple.answer_deal(view)
    kind = offer["kind"]
    accept = {
        "accept": 1.0,
        f"accept_{kind}": 1.0,
        "accept_from_leader": _flag(offerer == leader),
        "accept_offerer_trust": view["deals"]["trust"].get(offerer, 1.0),
        "accept_from_grudge": _flag(offerer in view["deals"].get("grudges", [])),
        "accept_danger": min(threat.danger_to_me(state), 2.0) / 2,
        "simple_prior": _flag(simple_accepts),
    }
    if kind == "gang_up":
        accept["accept_against_leader"] = _flag(offer.get("against") == leader)
        # Attacking someone we already have a deal with would break that deal.
        accept["accept_against_partner"] = _flag(offer.get("against") in view["deals"]["protected"])
    return [accept, {"decline": 1.0, "simple_prior": _flag(not simple_accepts)}]
