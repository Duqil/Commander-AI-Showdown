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
from showdown.boys import cards, colors, combat, commander, mana, threat, timing
from showdown.deals import GIFT_TEXT, PAY_LENGTHS, biggest_threat_card, can_hit, card_of, gift_cards, recurring_gift_cards, removal_cards, removes

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


# Rules text of a spell whose whole point is a fight: pump, first strike, protection...
_COMBAT_TEXT = re.compile(r"[+-]\d+/[+-]\d+|first strike|double strike|trample|indestructible|"
                          r"combat damage|attacking creature|blocking creature|additional combat")


def combat_spell(option: dict) -> bool:
    """An instant-speed spell that does most when creatures fight (Great Train
    Heist, Giant Growth): its text pumps or protects creatures for the turn,
    or talks about combat. Read from the rules text, never from the name.
    Sorceries can't be cast in a fight anyway, and permanents help every turn."""
    if not timing.instant_speed(option) or "Creature" in option.get("card", {}).get("type", ""):
        return False
    text = option.get("card", {}).get("oracle", "").lower()
    return ("until end of turn" in text or "combat" in text) and bool(_COMBAT_TEXT.search(text))


def x_timing_features(option: dict, state: dict, options: list[dict]) -> Features:
    """Cast this X spell now, or let it grow?

    Looks 1 to 3 turns ahead the simple way: every land in my hand is one
    more mana (so one more X) on a later turn, and a land drop still left this
    turn would make X one bigger right now. Against that: does the board need
    it now (opponents hit hard compared to my life), and is there something
    else useful to do with the mana meanwhile (setup)?
    """
    if "max_x" not in option:
        return {}
    me = threat.me(state)
    lands_in_hand = sum(1 for c in me.get("hand", []) if colors.is_land(c))
    land_drop_left = any(o.get("is_land") for o in options)
    other_spells = any(o.get("is_spell") and "max_x" not in o for o in options)
    life = max(1, me.get("life", 40))
    biggest_hitter = max((threat.board_power(p) for p in threat.opponents(state)), default=0)
    needs_it = biggest_hitter * 2 >= life or (biggest_hitter >= 6 and biggest_hitter >= 2 * threat.board_power(me))
    feats = {
        "x_grows_later": min(3, lands_in_hand) / 3,       # X can be up to 3 bigger in 1-3 turns
        "x_before_land_drop": _flag(land_drop_left),        # play the land first: X one bigger now
        "x_board_needs_it": _flag(needs_it),
        "x_while_other_plays": _flag(other_spells),
        "x_small": _flag(option["max_x"] <= 2),            # 1 or 2: hardly worth the card yet
        # My own upkeep or draw step with nothing to answer: the mana is
        # better kept for my main phase (and the card I'm about to draw).
        "x_before_my_main": _flag(state.get("active_player") == state.get("you")
                                  and state.get("phase") in ("UPKEEP", "DRAW") and not state.get("stack_size")),
    }
    return {k: v for k, v in feats.items() if v}


def etb_no_target(option: dict) -> float:
    """A permanent whose "when this enters" has nothing to target right now.

    0 = not the case. Otherwise how much of the card is that missing effect,
    from 0.25 (a fine body anyway) to 1 (a small body for a lot of mana): a
    4-mana 2/2 like Witch Enchanter is mostly its ETB, so better held; a
    5-mana 5/5 with a small ETB is still a good play.
    """
    if not option.get("etb_no_target"):
        return 0.0
    card = option.get("card", {})
    cmc = max(1, card.get("cmc", 0))
    body = (card.get("power", 0) + card.get("toughness", 0)) / 2 if "Creature" in card.get("type", "") else 0
    return max(0.25, min(1.0, (cmc - body) / cmc))


def play_options(decision: Decision) -> list[Features]:
    state = decision["state"]
    my_turn = state["active_player"] == state["you"]
    main = state["phase"] in MAIN_PHASES
    stack = state.get("stack_size", 0) > 0
    simple_pick = _simple.choose_play(decision)  # an option "index", or None for pass
    xc = x_context(decision["options"])
    gy = graveyard_context(state, decision["options"])
    missing = colors.missing(threat.me(state))  # colors my cards need that I can't make yet
    fight = combat.view(state)  # who attacks whom, who got blocked (combat.py)
    when = timing_context(state, decision["options"])

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
            # A bounce land with no other land out: it returns itself, the land drop is wasted.
            "land_bounces_itself": _flag(is_land and colors.wasted_land_drop(card, threat.me(state))),
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
            # My commander: what it's good for, crossed with the situation (commander.py).
            **(commander.cast_features(card, state) if is_spell and card.get("commander") else {}),
            **(commander.engine_features(card, state) if is_spell and not card.get("commander") else {}),
            **(combat_trick_features(fight, state, my_turn) if not is_land else {}),
            # A pump/first-strike/protection spell: worth most during a fight.
            "combat_spell_in_combat": _flag(combat_spell(option) and state["phase"] in combat.COMBAT_STEPS),
            "combat_spell_outside_combat": _flag(combat_spell(option) and state["phase"] not in combat.COMBAT_STEPS),
            **x_timing_features(option, state, decision["options"]),
            "etb_no_target": etb_no_target(option),
            # Mana this turn and next (mana.py): tapping out, waiting a turn, which land.
            **mana.tap_out_features(option, threat.me(state), when["my_main"]),
            **mana.combo_timing_features(option, threat.me(state), when["my_main"]),
            **(mana.land_fit(card, threat.me(state)) if is_land else {}),
            **(attach_features(option, state, my_turn) if is_ability else {}),
            **timing_features(option, card, is_land, is_spell, when),
            # Card memory: a learned number for this very card (cards.py).
            **cards.play_features(option, state),
            # A play that can pay a favour we owe, or feed our sharing plan (deals.py).
            **favour_play_features(option, card, is_land, decision.get("deals", {})),
        })
    result.append({
        "pass": 1.0,
        "pass_on_my_main": _flag(my_turn and main and not stack),
        "pass_with_stack": _flag(stack),
        "pass_in_fight": _flag(fight.in_trick_window()),
        "simple_prior": _flag(simple_pick is None),
        # Passing while kept-open mana goes to waste (it untaps on my turn).
        "pass_end_step_with_instant": _flag(when["end_step_before_mine"] and when["instants"]),
        # Passing my main phase while a card from exile could still be played.
        "pass_with_exile_card": _flag(my_turn and main and not stack and when["exile_plays"]),
        **mana.keep_up_on_pass(threat.me(state), state["phase"], when["my_main"]),
    })
    return result


def timing_context(state: dict, options: list[dict]) -> dict[str, Any]:
    """The timing facts (timing.py) that are the same for every option."""
    my_turn = state["active_player"] == state["you"]
    return {
        "my_main": my_turn and state["phase"] in MAIN_PHASES and not state.get("stack_size", 0),
        "end_step_before_mine": timing.end_step_before_mine(state),
        "opponent_on_stack": timing.opponent_on_stack(state),
        "in_fight": combat.view(state).in_trick_window(),
        "instants": any(timing.instant_speed(o) for o in options),
        "exile_plays": any(timing.from_exile(o) for o in options),
        "impulse_pending": timing.impulse_pending(state, options),
        "land_drop_left": any(o.get("is_land") for o in options),
    }


def timing_features(option: dict, card: dict, is_land: bool, is_spell: bool, when: dict) -> Features:
    """When to play it: instant speed and impulse draws (see timing.py).

    These describe the moment, not a rule. A Boy learns, for any card, that a
    counterspell is wasted in its own main phase, that kept-open mana is best
    used at the end of the turn before its own, or that casting an impulse
    draw before the land drop can turn up a land it may still play.
    """
    quick = timing.instant_speed(option)
    react = timing.reactive(option)
    forge_ok = option.get("forge_would_play", False)
    exiled = timing.from_exile(option)
    impulse = not is_land and timing.is_impulse(card)
    feats = {
        "instant_on_my_main": _flag(quick and when["my_main"]),
        "reactive_on_my_main": _flag(quick and react and when["my_main"]),
        "instant_end_step_before_mine": _flag(quick and when["end_step_before_mine"]),
        "reactive_spell": _flag(is_spell and react),
        "respond_to_opponent": _flag(not is_land and when["opponent_on_stack"]),
        "respond_forge_would_play": _flag(not is_land and when["opponent_on_stack"] and forge_ok),
        "trick_forge_would_play": _flag(not is_land and when["in_fight"] and forge_ok),
        "from_exile": _flag(exiled),
        "land_from_exile": _flag(is_land and exiled),
        "impulse_play": _flag(impulse),
        # Impulse first while the land drop is still free: an exiled land can be played.
        "impulse_before_land": _flag(impulse and when["land_drop_left"]),
        # Playing a land from hand while an impulse draw could still turn one up.
        "land_impulse_pending": _flag(is_land and not exiled and when["impulse_pending"]),
    }
    return {k: v for k, v in feats.items() if v}


def card_by_id(player: dict, card_id) -> dict | None:
    """The card with this id on the player's battlefield, or None."""
    if card_id is None:
        return None
    return next((c for c in player.get("battlefield", []) if c.get("id") == card_id), None)


def attach_features(option: dict, state: dict, my_turn: bool) -> Features:
    """Equip (and anything else that attaches a card to a creature).

    Equipping only helps an attack if it happens *before* combat, on a
    creature that can attack this turn. Without these signals a Boy sees
    Equip as just "an ability" and can't learn the timing. Recognized by what
    the ability does (Forge's "Attach" effect), not by card names.
    """
    if option.get("api") != "Attach":
        return {}
    before_combat = my_turn and state.get("phase") == "MAIN1"
    me = threat.me(state)

    def can_attack(c: dict) -> bool:  # untapped, not summoning sick
        return "Creature" in c.get("type", "") and not c.get("tapped") and not c.get("sick")

    ready = any(can_attack(c) for c in me.get("battlefield", []))
    # Already on a creature that can attack? Then moving it to another one
    # gains (almost) nothing. Without this, a free equip (Lightning Greaves,
    # Equip {0}) looked just as good every time, and a greedy Boy moved it
    # back and forth thousands of times until the game hit its time limit.
    holder = card_by_id(me, option.get("card", {}).get("attached_to"))
    if holder is not None and can_attack(holder):
        return {"attach_ability": 1.0, "attach_already_on_attacker": 1.0}
    return {
        "attach_ability": 1.0,
        "attach_before_combat": _flag(before_combat),
        "attach_attacker_ready": _flag(before_combat and ready),
        "attach_after_combat": _flag(my_turn and state.get("phase") == "MAIN2"),
    }


def combat_trick_features(fight: combat.CombatView, state: dict, my_turn: bool) -> Features:
    """A spell or ability used mid-combat, once blocks are known (a "combat trick").

    Only the situation is described here; the target features below say WHICH
    creature the trick goes on. Together they let a Boy learn, for example,
    "pump or card-draw on my unblocked attacker is great", for any card.
    """
    in_combat = state.get("phase") in combat.COMBAT_STEPS
    if not in_combat:
        return {}
    return {
        "play_in_combat": 1.0,
        "trick_my_unblocked": _flag(my_turn and bool(fight.my_unblocked)),   # damage is sure to land
        "trick_my_blocked": _flag(my_turn and bool(fight.my_blocked)),       # a fight to win
        "trick_defending": _flag(not my_turn and bool(fight.attacking_me)),  # someone attacks me
        "trick_unblocked_at_me": _flag(not my_turn and bool(fight.unblocked_at_me)),
        "trick_my_blocker": _flag(not my_turn and bool(fight.my_blockers)),  # pump my blocker
    }


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
            **cards.features("attack", attacker, ctx["state"]),  # card memory (cards.py)
        }
        deals = decision.get("deals", {})
        if d["name"] in deals.get("protected", []):
            f["attack_breaks_deal"] = 1.0     # we have a deal with them: attacking breaks it
        if d["name"] in deals.get("gang_targets", []):
            f["attack_gang_target"] = 1.0     # we promised to attack them
        if d["name"] in deals.get("grudges", []):
            f["attack_grudge"] = 1.0          # they broke a deal with us this game
        if d["name"] in deals.get("revenge", []):
            f["attack_revenge"] = 1.0         # ...and we haven't paid them back yet (one real setback)
        if d["name"] in deals.get("sharing_with_me", []):
            f["attack_my_sharer"] = 1.0       # they share their gifts with us: attacking them is rude
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
        if "YouCtrl" in params.get("ValidTgts", "") and "OppCtrl" not in params.get("ValidTgts", ""):
            return False  # only your own: a blink or a bounce for value (Ephemerate, Cloudshift)
        return params.get("Origin", "") == "Battlefield"       # exile or bounce from play
    # Who may be targeted says a lot: "creature you control" is almost always
    # a gift (Hunter's Insight: "Creature.YouCtrl"), "opponent's creature" a threat.
    valid = params.get("ValidTgts", "")
    if "OppCtrl" in valid or "OppOwn" in valid:
        return True
    if "YouCtrl" in valid:
        return False
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
        f.update(cards.features("pick", cand))  # card memory (cards.py)
        result.append(f)
    return result


def target_combat_features(cand: dict, harmful: bool, fight: combat.CombatView) -> Features:
    """Where the target stands in the current fight (all 0 outside combat)."""
    cid = None if cand.get("is_player") else cand.get("id")
    if cid is None:
        return {}
    helps = not harmful
    return {
        "help_my_unblocked": _flag(helps and cid in fight.my_unblocked),    # pump / "draw per damage"
        "help_my_blocked": _flag(helps and cid in fight.my_blocked),        # win the fight
        "help_my_blocker": _flag(helps and cid in fight.my_blockers),
        "hurt_blocker_of_mine": _flag(harmful and cid in fight.blocking_mine),   # clear the way
        "hurt_attacker_at_me": _flag(harmful and cid in fight.attacking_me),
        "hurt_unblocked_at_me": _flag(harmful and cid in fight.unblocked_at_me),
    }


def target_options(decision: Decision) -> list[Features]:
    state = decision["state"]
    you = state["you"]
    leader = threat.leader(state)
    players = {p["name"]: p for p in state["players"]}
    harmful = is_harmful(decision)
    # Takes the target off the battlefield without destroying it (exile, bounce, blink).
    moving = decision.get("api") in ("ChangeZone", "ChangeZoneAll") and \
        decision.get("params", {}).get("Origin", "") == "Battlefield"
    suggested = set(decision.get("suggested", []))
    # Candidates are fresh card copies; the damage counts live on the state's battlefield cards.
    recent = {c.get("id"): c.get("recent_damage", 0) for p in state["players"] for c in p.get("battlefield", [])}
    fight = combat.view(state)

    result = []
    for cand in decision["candidates"]:
        is_player = cand.get("is_player", False)
        owner = cand["name"] if is_player else cand.get("controller", "")
        mine = owner == you
        ctype = cand.get("type", "")
        f: Features = {
            "forge_suggested": _flag(cand["index"] in suggested),  # Forge's AI would pick this
            # Destroy/damage/shrink on my own card. Moving it (blink, bounce)
            # is "move_mine" instead: that's often value, and mixing the two
            # taught a Boy that removing his own creatures is good.
            "hurt_mine": _flag(harmful and mine and not moving),
            "move_mine": _flag(moving and mine),
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
            **target_combat_features(cand, harmful, fight),
            **cards.target_features(cand, harmful, decision),  # card memory (cards.py)
        }
        if is_player and not mine:
            life = cand.get("life", threat.STARTING_LIFE)
            f["target_player_life"] = life / threat.STARTING_LIFE
            f["target_player_low_life"] = _flag(life <= 10)
        elif not is_player and not mine and owner in players:
            f["target_owner_threat_rank"] = threat.threat_rank(state, owner)
        # Harm aimed at someone who broke a deal with us: that's revenge
        # (one real setback, see showdown/deals.py). Starts liked (brain.py).
        deals = decision.get("deals", {})
        if harmful and not mine and owner in deals.get("revenge", []):
            f["hurt_revenge_target"] = 1.0
        # Paying a favour we promised (deals.py): harm on X's stuff, or help for our partner.
        owed = deals.get("owed", [])
        # A named removal ("remove Kefka") is only paid by that card.
        if harmful and not is_player and any(o["kind"] == "removal" and o["against"] == owner and
                                             o.get("card_id") in (None, cand.get("id")) for o in owed):
            f["pay_removal"] = 1.0
        if not harmful and is_player and any(o["kind"] == "gift" and o["to"] == owner for o in owed):
            f["pay_gift"] = 1.0
        # Our sharing plan says this opponent gets the next gift.
        if not harmful and is_player and not mine and owner == deals.get("share_next"):
            f["share_planned"] = 1.0
        result.append(f)
    return result


# ----------------------------------------------------------------------------
# Deals: what to offer at the start of our turn, and whether to accept
# ----------------------------------------------------------------------------

def runaway_leader(state: dict) -> str | None:
    """The player far ahead of everyone (1.5x the next biggest threat), if any.

    Threat counts board power (or recent damage), other permanents, cards in
    hand and life (threat.threat_score), so a big life lead or an overwhelming
    board both count. That's who a gang-up is for.
    """
    alive = [p for p in state.get("players", []) if not p.get("lost")]
    if len(alive) < 3:
        return None
    ranked = sorted(alive, key=threat.threat_score, reverse=True)
    top, second = threat.threat_score(ranked[0]), threat.threat_score(ranked[1])
    return ranked[0]["name"] if top > 1.5 * second else None


# Less creature power than this (on the whole table, or between two deal
# partners) and a deal can't change much yet. Same number SimpleBot uses.
QUIET_TABLE_POWER = 3


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
        # Nobody has much to fight with yet: a deal now protects nobody from
        # anything. Starts negative (brain.py), so early-game deals are rare.
        "offer_quiet_table": _flag(threat.total_board_power(state) < QUIET_TABLE_POWER),
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


def favour_play_features(option: dict, card: dict, is_land: bool, deals: dict) -> Features:
    """Plays that pay what we promised: removal text while we owe a removal,
    gift text ("you and target opponent each draw") while we owe a gift or
    have a sharing plan. Start liked (brain.py)."""
    if is_land or not deals:
        return {}
    text = (option.get("description", "") + " " + card.get("oracle", ""))
    owed = {o["kind"] for o in deals.get("owed", [])}
    f: Features = {}
    if "removal" in owed and removes(text):
        f["play_pays_removal"] = 1.0
    if GIFT_TEXT.search(text) and ("gift" in owed or deals.get("share_next")):
        f["play_gives_gift"] = 1.0
    return f


DEAL_LENGTHS = (1, 2, 3, 4)  # rounds an offer can last (same as showdown/deals.py)
UNTIL_OUT = 0                # a gang-up until its target is out


def length_features(prefix: str, rounds: int) -> Features:
    """How long a deal lasts: 0.25 for 1 round up to 1.0 for 4, or "until they're out"."""
    if rounds == UNTIL_OUT:
        return {f"{prefix}_until_out": 1.0}
    return {f"{prefix}_rounds": rounds / 4}


def breaks_my_deal(deals: dict, partner: str, against: str | None) -> bool:
    """Would agreeing to this deal break one I already have? (showdown/deals.py
    counts it as betrayal.) A gang-up on someone I have a deal with, or any
    deal with someone I promised to gang up on."""
    return (against is not None and against in deals.get("protected", [])) or \
        partner in deals.get("gang_targets", [])


def offer_options(view: Decision) -> tuple[list[Features], list[dict]]:
    """Step 2 of offering: which deal, to whom, for how long. Returns (features, offer per option)."""
    state = view["state"]
    trust = view["deals"]["trust"]
    opponents = threat.opponents(state)
    leader = threat.leader(state)
    me = threat.me(state)
    i_lead = bool(opponents) and threat.threat_score(me) > max(threat.threat_score(p) for p in opponents)
    danger = min(threat.danger_to_me(state), 2.0) / 2
    simple = _simple.offer_deal(view)
    protected = set(view["deals"]["protected"])
    runaway = runaway_leader(state)
    # Favours need a way to pay: a card whose text removes, or gives (deals.py).
    can_remove, can_gift = bool(removal_cards(me)), bool(gift_cards(me))

    feats: list[Features] = []
    offers: list[dict] = []
    for to in opponents:
        if to["name"] in protected:
            continue  # already have a deal with them
        my_gain, their_gain = threat.deal_gains(state, to["name"])
        base = {
            "to_is_leader": _flag(to["name"] == leader),
            "to_threat_rank": threat.threat_rank(state, to["name"]),
            "to_trust": trust.get(to["name"], 1.0),
            "to_grudge": _flag(to["name"] in view["deals"].get("grudges", [])),
            "i_am_leader": _flag(i_lead),
            "my_danger": danger,
            # Who gains more from us not attacking each other (threat.deal_gains).
            "my_gain": my_gain,
            "their_gain": their_gain,
        }
        for rounds in DEAL_LENGTHS:
            offer = {"type": "peace", "to": to["name"], "rounds": rounds}
            feats.append({"offer_peace": 1.0, **{f"peace_{k}": v for k, v in base.items()},
                          **length_features("peace", rounds),
                          "offer_breaks_deal": _flag(breaks_my_deal(view["deals"], to["name"], None)),
                          "simple_prior": _flag(simple == offer)})
            offers.append(offer)
        for against in opponents:
            if against["name"] == to["name"]:
                continue
            gang = {
                "offer_gang_up": 1.0, **{f"gang_{k}": v for k, v in base.items()},
                # Neither of us could hurt them much yet: a pointless gang-up.
                "gang_pointless": _flag(threat.board_power(me) + threat.board_power(to) < QUIET_TABLE_POWER),
                "gang_against_is_leader": _flag(against["name"] == leader),
                # Ganging up on someone far ahead: what gang-ups are for (starts positive).
                "gang_against_runaway": _flag(against["name"] == runaway),
                "gang_against_threat_rank": threat.threat_rank(state, against["name"]),
                "offer_breaks_deal": _flag(breaks_my_deal(view["deals"], to["name"], against["name"])),
            }
            for rounds in DEAL_LENGTHS + (UNTIL_OUT,):
                offer = {"type": "gang_up", "to": to["name"], "against": against["name"], "rounds": rounds}
                feats.append({**gang, **length_features("gang", rounds), "simple_prior": _flag(simple == offer)})
                offers.append(offer)
            # Removal for a favour: I hit something of theirs, you give me peace.
            if can_remove:
                removal = {
                    "offer_removal": 1.0, **{f"removal_{k}": v for k, v in base.items()},
                    "removal_against_is_leader": _flag(against["name"] == leader),
                    "removal_against_runaway": _flag(against["name"] == runaway),
                    "removal_against_threat_rank": threat.threat_rank(state, against["name"]),
                    "offer_breaks_deal": _flag(breaks_my_deal(view["deals"], to["name"], against["name"])),
                }
                # ...either "something important" or one named card: X's biggest threat.
                named = biggest_threat_card(against)
                named_feats = {} if named is None else {
                    "removal_named": 1.0,
                    # Can one of my removal cards actually hit it? (read from text)
                    "removal_named_can_hit": _flag(any(can_hit(r, named) for r in removal_cards(me))),
                    "removal_named_engine": _flag((named.get("recent_damage") or 0) > 0),
                }
                for card, extra in [(None, {})] + ([(named["id"], named_feats)] if named else []):
                    for rounds in DEAL_LENGTHS:
                        for pay in PAY_LENGTHS:  # pay within 1 or 2 rounds
                            feats.append({**removal, **extra, **length_features("removal", rounds),
                                          "removal_pay_slow": _flag(pay == 2)})
                            offer = {"type": "removal", "to": to["name"], "against": against["name"],
                                     "rounds": rounds, "pay_rounds": pay}
                            if card is not None:
                                offer["card"] = card
                            offers.append(offer)
        # Gift for a favour: I give you something, you give me peace.
        if can_gift:
            for rounds in DEAL_LENGTHS:
                for pay in PAY_LENGTHS:
                    feats.append({"offer_gift": 1.0, **{f"gift_{k}": v for k, v in base.items()},
                                  **length_features("gift", rounds), "gift_pay_slow": _flag(pay == 2),
                                  "offer_breaks_deal": _flag(breaks_my_deal(view["deals"], to["name"], None))})
                    offers.append({"type": "gift", "to": to["name"], "rounds": rounds, "pay_rounds": pay})
    # A gift that comes back every turn: announce a sharing plan (no partner needed).
    if recurring_gift_cards(me) and not view["deals"].get("sharing") and len(opponents) >= 2:
        feats.append({"offer_share": 1.0, "share_my_danger": danger, "simple_prior": _flag(simple == {"type": "share"})})
        offers.append({"type": "share"})
    return feats, offers


def answer_options(view: Decision) -> list[Features]:
    """Two options: accept (index 0) or decline (index 1)."""
    state = view["state"]
    offer = view["offer"]
    offerer = offer["a"]
    leader = threat.leader(state)
    simple_accepts = _simple.answer_deal(view)
    kind = offer["kind"]
    my_gain, their_gain = threat.deal_gains(state, offerer)
    accept = {
        "accept": 1.0,
        f"accept_{kind}": 1.0,
        "accept_from_leader": _flag(offerer == leader),
        "accept_offerer_trust": view["deals"]["trust"].get(offerer, 1.0),
        # How much trust they've lost this game (0 = none, 1 = all). Starts
        # strongly negative (brain.py): a deal-breaker's offers get a "no".
        "accept_low_trust": 1.0 - view["deals"]["trust"].get(offerer, 1.0),
        "accept_from_grudge": _flag(offerer in view["deals"].get("grudges", [])),
        "accept_danger": min(threat.danger_to_me(state), 2.0) / 2,
        # Who gains more from us not attacking each other (threat.deal_gains):
        # starts out liking my gain and disliking theirs (brain.py).
        "accept_my_gain": my_gain,
        "accept_their_gain": their_gain,
        # Saying yes would break a deal we already have (that's betrayal).
        "accept_breaks_deal": _flag(breaks_my_deal(view["deals"], offerer, offer.get("against"))),
        **length_features("accept", offer.get("rounds", 2)),
        "simple_prior": _flag(simple_accepts),
    }
    if kind in ("removal", "gift"):
        # They'd pay within 2 rounds instead of 1: a slower promise is a bit less sure.
        accept["accept_pay_slow"] = _flag(offer.get("pay_rounds", 1) == 2)
    if kind == "removal":
        # They'd remove something of X's for us: worth more the bigger X's threat (to me).
        accept["accept_removal_vs_leader"] = _flag(offer.get("against") == leader)
        accept["accept_removal_target_rank"] = threat.threat_rank(state, offer.get("against", ""))
        if offer.get("card_id") is not None:
            # A named card ("remove Kefka"): a clearer promise, better still if it's been hurting people.
            accept["accept_named"] = 1.0
            named = card_of(state, offer.get("against"), offer["card_id"])
            accept["accept_named_engine"] = _flag((named.get("recent_damage") or 0) > 0)
    if kind == "gang_up":
        accept["accept_against_leader"] = _flag(offer.get("against") == leader)
        accept["accept_against_runaway"] = _flag(offer.get("against") == runaway_leader(state))
        # Attacking someone we already have a deal with would break that deal.
        accept["accept_against_partner"] = _flag(offer.get("against") in view["deals"]["protected"])
        # Neither of us could hurt the target much yet: nothing to gain.
        offerer_p = next((p for p in state.get("players", []) if p["name"] == offerer), {})
        accept["accept_gang_pointless"] = _flag(threat.board_power(threat.me(state)) + threat.board_power(offerer_p)
                                                < QUIET_TABLE_POWER)
    return [accept, {"decline": 1.0, "simple_prior": _flag(not simple_accepts)}]
