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

import re

from typing import Any

from showdown.bots.simple_bot import SimpleBot
from showdown.boys import colors, threat

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
    # Commander's first mulligan is free; every one after that costs a card.
    # (game_runner counts mulligans for us in "mulligans_so_far".)
    costs_a_card = decision.get("mulligans_so_far", 0) > 0

    keep = {
        "keep": 1.0,
        "keep_good_lands": _flag(2 <= lands <= 5),
        "keep_land_distance": abs(lands - 3.5) / 3.5,  # 0 = 3-4 lands, 1 = none or all lands
        "keep_cards_to_bottom": to_bottom / 3,          # keeping a small hand vs digging deeper
        "simple_prior": _flag(simple_keeps),
    }
    mulligan = {
        "mulligan": 1.0,
        # Cards we'd have to put back if we mulligan again and then keep. Starts
        # with a negative weight, so going deeper gets less and less tempting.
        # 0 for the free first mulligan.
        "mulligan_deeper": (to_bottom + costs_a_card) / 3,
        "simple_prior": _flag(not simple_keeps),
    }
    # Colors: share of the spells in hand whose colors the lands in hand can make.
    # A 3-Island hand full of black and red spells is worse than it looks.
    spells = [c for c in hand if not colors.is_land(c) and colors.pips(c.get("cost"))]
    hand_colors = set().union(*(colors.produces(c) for c in hand if colors.is_land(c)), set())
    if spells:
        keep["keep_color_coverage"] = sum(colors.castable_by(c, hand_colors) for c in spells) / len(spells)
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
    gy = graveyard_context(state, decision["options"])
    missing = colors.missing(threat.me(state))  # colors my cards need that I can't make yet

    result = []
    for option in decision["options"]:
        card = option.get("card", {})
        ctype = card.get("type", "")
        is_land = option.get("is_land", False)
        is_spell = option.get("is_spell", False)
        is_ability = not is_land and not is_spell
        result.append({
            "land_drop": _flag(is_land),
            # Casting my commander, and how much extra it costs after dying (/10).
            "cast_commander": _flag(is_spell and card.get("commander", False)),
            "commander_tax": card.get("commander_tax", 0) / 10 if is_spell and card.get("commander") else 0.0,
            # A land that makes a color my cards need and I don't have yet.
            "land_new_color": _flag(is_land and bool(colors.produces(card) & missing)),
            "spell": _flag(is_spell),
            "spell_cmc": (card.get("cmc", 0) + option.get("max_x", 0)) / 7 if is_spell else 0.0,
            "x_spell": _flag("max_x" in option),
            # How big the X would be (all mana in): bigger X = bigger Ravenous creature.
            "x_size": option.get("max_x", 0) / 7,
            **sequencing_features(option, card, is_spell, xc),
            **graveyard_features(card, is_land, is_spell, gy),
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


def graveyard_context(state: dict, options: list[dict]) -> dict[str, Any]:
    """What a graveyard deck cares about right now (hints come from the bridge).

    Bloodghast comes back from the graveyard whenever we play a land, so the
    good order is: first sacrifice it (e.g. to Anhelo's casualty), THEN play
    the land. These numbers let a Boy learn that order.
    """
    me = threat.me(state)
    graveyard = me.get("graveyard", [])
    battlefield = me.get("battlefield", [])
    grants = {g for c in battlefield + me.get("command", []) for g in c.get("grants", [])}
    return {
        "returners_in_graveyard": sum(1 for c in graveyard if c.get("graveyard_use") == "landfall"),
        # Landfall returners on the battlefield that would be fine to sacrifice (power 2+ for casualty 2).
        "returners_ready": sum(1 for c in battlefield if c.get("graveyard_use") == "landfall"
                               and (c.get("power") or 0) >= 2),
        "casualty": "Casualty" in grants,
        "land_drop_left": any(o.get("is_land") for o in options),
    }


def graveyard_features(card: dict, is_land: bool, is_spell: bool, gy: dict) -> Features:
    instant_or_sorcery = is_spell and any(t in card.get("type", "") for t in ("Instant", "Sorcery"))
    # Sacrifice a returner to casualty now, while the land drop can still bring it back.
    casualty_combo = gy["casualty"] and gy["returners_ready"] > 0 and gy["land_drop_left"]
    result: Features = {}
    if is_land and gy["returners_in_graveyard"]:
        result["land_returns_creatures"] = min(gy["returners_in_graveyard"], 3) / 3
    if casualty_combo:
        if instant_or_sorcery:
            result["casualty_before_land"] = 1.0   # spell first, then the land returns the creature
        if is_land:
            result["land_before_casualty"] = 1.0   # the land now wastes the comeback
    return result


# ----------------------------------------------------------------------------
# Attacks: one small choice per creature, "stay home" (option 0) or a defender
# ----------------------------------------------------------------------------

# "Whenever <who> attacks, ..." in rules text. <who> decides which attackers
# set it off. Read from the text, so it works for any card, not a list of names.
ATTACK_TRIGGER = re.compile(r"whenever ([^,.]*?) attacks?\b", re.IGNORECASE)


def equipped(attacker: dict, mine: dict) -> bool:
    """Does an Equipment of mine sit on this creature?"""
    return any(c.get("attached_to") == attacker.get("id") and "Equipment" in c.get("type", "")
               for c in mine.get("battlefield", []))


def attack_triggers(attacker: dict, mine: dict) -> int:
    """How many of my "whenever ... attacks" abilities this attacker would set off.

    "Whenever an equipped creature you control attacks" (Nahiri) needs an
    equipped attacker; "whenever <this card's name> attacks" only the card
    itself; "whenever a creature you control attacks" any attacker.
    """
    count = 0
    is_equipped = equipped(attacker, mine)
    for card in mine.get("battlefield", []):
        for who in ATTACK_TRIGGER.findall(card.get("oracle", "") or ""):
            who = who.lower()
            if "you attack" in who or "defending" in who:
                continue
            if "equipped" in who:
                count += is_equipped
            elif card.get("id") == attacker.get("id") and (
                    "this creature" in who or card.get("name", "").split(",")[0].lower() in who):
                count += 1
            elif "creature" in who and "you control" in who:
                count += 1
    return count


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
        "mine": mine,
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
            # Equipment on it, and "whenever ... attacks" abilities it would set off.
            "attack_equipped": _flag(equipped(attacker, ctx["mine"])),
            "attack_triggers": min(attack_triggers(attacker, ctx["mine"]), 3) / 3,
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


def keyword_cost_options(decision: Decision) -> list[Features]:
    """Pay an optional extra cost (casualty, offspring...)? [no, yes].

    For a sacrifice, what we'd lose matters: a token or a creature that comes
    back from the graveyard (Bloodghast) is cheap, a commander is not.
    """
    simple = _simple.pay_keyword_cost(decision)
    simple_pays = simple is None or simple > 0
    sac = decision.get("sacrifice_candidates", [])
    cheapest = min(sac, key=lambda c: (c.get("cmc", 0), c.get("power", 0)), default=None)
    no = {"skip_cost": 1.0, "simple_prior": _flag(not simple_pays)}
    yes = {"pay_cost": 1.0, "simple_prior": _flag(simple_pays), "pay_sacrifice": _flag(bool(sac))}
    if cheapest is not None:
        yes["sac_cheapest_cmc"] = cheapest.get("cmc", 0) / 7
        yes["sac_token"] = _flag(cheapest.get("token", False))
        yes["sac_comes_back"] = _flag(any(c.get("graveyard_use") for c in sac))  # Bloodghast & co.
        yes["sac_only_commander"] = _flag(all(c.get("commander") for c in sac))
    return [no, yes]


def pick_options(decision: Decision) -> list[Features]:
    """Which card to pick for myself: a land from a library search (any fetch
    land) or a card from "look at the top N" (Drawn from Dreams, Impulse...)."""
    me = threat.me(decision["state"])
    missing = colors.missing(me)
    have = colors.available(me)
    # Colors I can make now or with the lands already in my hand.
    reachable = have | set().union(*(colors.produces(c) for c in me.get("hand", []) if colors.is_land(c)), set())
    my_lands = sum(colors.is_land(c) for c in me.get("battlefield", []) + me.get("hand", []))
    short_on_lands = max(0, 5 - my_lands) / 5   # 1.0 with no lands, 0 at 5+
    simple = _simple.choose_card(decision)
    suggested = decision.get("suggested", -1)
    if simple is None:
        simple = suggested  # SimpleBot keeps Forge's pick
    result = []
    for cand in decision["candidates"]:
        land = colors.is_land(cand)
        makes = colors.produces(cand)
        f: Features = {
            "simple_prior": _flag(cand["index"] == simple),
            "forge_suggested": _flag(cand["index"] == suggested),
            "pick_land": _flag(land),
            "pick_land_when_short": short_on_lands if land else 0.0,
        }
        if land:
            f["pick_land_new_color"] = _flag(bool(makes & missing))          # a color I need and lack
            f["pick_land_only_old_colors"] = _flag(bool(makes) and makes <= have)
            f["pick_land_color_value"] = min(colors.land_value(cand, me), 4) / 4
        else:
            f["pick_castable"] = _flag(colors.castable_by(cand, reachable))  # I have its colors
            f["pick_cmc"] = cand.get("cmc", 0) / 7
        result.append(f)
    return result


def target_options(decision: Decision) -> list[Features]:
    state = decision["state"]
    you = state["you"]
    leader = threat.leader(state)
    players = {p["name"]: p for p in state["players"]}
    harmful = is_harmful(decision)
    suggested = set(decision.get("suggested", []))
    # Candidates are fresh card copies; the damage counts live on the state's battlefield cards.
    recent = {c.get("id"): c.get("recent_damage", 0) for p in state["players"] for c in p.get("battlefield", [])}

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
            # Removal on an opponent's card that dealt damage to players last
            # round (1.0 at 5+ damage). A 0/1 that pings everyone has no power
            # and costs nothing, but it is a real threat.
            "hurt_damage_dealer": min(recent.get(cand.get("id"), 0), 5) / 5 if harmful and not mine else 0.0,
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

def offer_whether_options(view: Decision) -> list[Features]:
    """Step 1 of offering: offer a deal this turn at all? [no, yes].

    Deciding "whether" separately from "which" matters: with one "no offer"
    option next to up to nine possible offers, a brain that knows nothing
    would offer 9 times out of 10, every single turn. As two separate choices,
    a fresh Boy starts at 50/50 (then follows SimpleBot via simple_prior)
    and learns when offering pays off.
    """
    state = view["state"]
    opponents = threat.opponents(state)
    me = threat.me(state)
    i_lead = bool(opponents) and threat.threat_score(me) > max(threat.threat_score(p) for p in opponents)
    simple = _simple.offer_deal(view)
    leader = max(opponents, key=threat.threat_score) if opponents else None
    others = [threat.threat_score(p) for p in opponents if p is not leader] + [threat.threat_score(me)]
    runaway = bool(leader) and threat.threat_score(leader) > 1.5 * max(others)  # one player far ahead
    no = {"no_offer": 1.0, "simple_prior": _flag(simple is None)}
    yes = {
        "make_offer": 1.0,
        "simple_prior": _flag(simple is not None),
        "offer_while_in_deal": _flag(bool(view["deals"]["active"])),
        "offer_in_first_round": _flag(threat.game_round(state) == 1),
        # How far into the game (0 at the start, 1 by round 10) and how much
        # fighting there is (0 with an empty board, 1 at 20+ power on the
        # table). A Boy can learn that deals early, or over an empty board,
        # don't pay off, instead of us banning them.
        "offer_game_stage": min(threat.game_round(state) - 1, 10) / 10,
        "offer_board_size": min(threat.total_board_power(state), 20) / 20,
        "offer_when_leading": _flag(i_lead),
        "offer_in_danger": min(threat.danger_to_me(state), 2.0) / 2,
        "offer_vs_runaway_leader": _flag(runaway),
    }
    return [no, yes]


def offer_options(view: Decision) -> tuple[list[Features], list[dict]]:
    """Step 2 of offering: which deal, to whom. Returns (features, offer per option)."""
    state = view["state"]
    trust = view["deals"]["trust"]
    opponents = threat.opponents(state)
    leader = threat.leader(state)
    me = threat.me(state)
    i_lead = bool(opponents) and threat.threat_score(me) > max(threat.threat_score(p) for p in opponents)
    danger = min(threat.danger_to_me(state), 2.0) / 2
    simple = _simple.offer_deal(view)
    protected = set(view["deals"]["protected"])

    feats: list[Features] = []
    offers: list[dict] = []
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
