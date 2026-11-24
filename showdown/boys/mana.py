"""Mana over this turn and the next: tapping out, waiting a turn, which land to play.

Three questions a good player asks every turn, and none of them looks at card
names. Everything is a rough count, good enough to learn from:

1. Keep mana up? Holding an instant (a counterspell, a combat trick) only
   helps if there is mana left to cast it. A big sorcery that taps me out
   throws that away.

2. Wait a turn? Some plays are much better together: an engine ("whenever
   you cast an instant or sorcery...") with spells to feed it. If I can cast
   the engine now but no fuel with it, while next turn I could do both, the
   engine might be better next turn (and the fuel better after the engine).

3. Which land? A land that enters tapped is free on a turn where the extra
   mana wouldn't be used, but costs a whole play on a turn where it would.
"""

from __future__ import annotations

import re

from showdown.boys import colors, commander, timing

_ENTERS_TAPPED = re.compile(r"enters( the battlefield)? tapped", re.I)
_MAKES_MANA = re.compile(r"\{t\}[^.:]*: add", re.I)
_COUNTER = re.compile(r"counter target", re.I)


def enters_tapped(card: dict) -> bool:
    """A land (or other card) that always enters tapped. "Unless ..." lands
    (check lands, fast lands) often come in untapped, so they don't count."""
    text = card.get("oracle", "") or ""
    return bool(_ENTERS_TAPPED.search(text)) and "unless" not in text.lower()


# "Sacrifice it: search your library for a land, put it onto the battlefield tapped"
# (Evolving Wilds and the like): no mana this turn either, so it counts as a tapped land.
_FETCHES_TAPPED = re.compile(r"sacrifice[^:]*: search your library for [^.]*land[^.]*onto the battlefield tapped", re.I)


def slow_land(card: dict) -> bool:
    """A land that gives no mana the turn it's played: enters tapped, or
    fetches a land that enters tapped."""
    return enters_tapped(card) or bool(_FETCHES_TAPPED.search(card.get("oracle", "") or ""))


def makes_mana(card: dict) -> bool:
    """A permanent that taps for mana: a land, a mana rock, a mana creature."""
    return bool(card.get("produces")) or colors.is_land(card) or bool(_MAKES_MANA.search(card.get("oracle", "") or ""))


def untapped_mana(player: dict) -> int:
    """Roughly how much mana I could make right now (one per untapped source)."""
    return sum(1 for c in player.get("battlefield", []) if makes_mana(c) and not c.get("tapped")
               and not (("Creature" in c.get("type", "")) and c.get("sick")))


def mana_next_turn(player: dict) -> int:
    """Roughly my mana next turn: every source untaps, plus a land drop if I hold a land."""
    sources = sum(1 for c in player.get("battlefield", []) if makes_mana(c))
    lands_in_hand = any(colors.is_land(c) for c in player.get("hand", []))
    return sources + int(lands_in_hand)


def spells_in_hand(player: dict) -> list[dict]:
    return [c for c in player.get("hand", []) if not colors.is_land(c) and c.get("cmc") is not None]


def is_instant_speed(card: dict) -> bool:
    return timing.instant_speed({"is_spell": True, "card": card, "description": ""})


def cheapest_instant(player: dict, exclude_id=None) -> int | None:
    """Mana value of the cheapest instant-speed card in my hand (None if none)."""
    costs = [c.get("cmc", 0) for c in spells_in_hand(player)
             if c.get("id") != exclude_id and is_instant_speed(c)]
    return min(costs) if costs else None


def holds_counter(player: dict, exclude_id=None) -> bool:
    return any(c.get("id") != exclude_id and is_instant_speed(c) and _COUNTER.search(c.get("oracle", "") or "")
               for c in spells_in_hand(player))


def tap_out_features(option: dict, player: dict, my_main: bool) -> dict[str, float]:
    """Casting this (at sorcery speed) leaves too little mana for the instant I hold."""
    card = option.get("card", {})
    if not my_main or not option.get("is_spell") or timing.instant_speed(option):
        return {}
    cheapest = cheapest_instant(player, exclude_id=card.get("id"))
    if cheapest is None:
        return {}
    left = untapped_mana(player) - card.get("cmc", 0) - option.get("max_x", 0)
    if left >= cheapest:
        return {}
    return {"taps_out_holding_instant": 1.0,
            "taps_out_holding_counter": float(holds_counter(player, exclude_id=card.get("id")))}


def keep_up_on_pass(player: dict, phase: str, my_main: bool) -> dict[str, float]:
    """Passing my second main phase with mana open for an instant I hold."""
    if not my_main or phase != "MAIN2":
        return {}
    cheapest = cheapest_instant(player)
    if cheapest is None or untapped_mana(player) < cheapest:
        return {}
    return {"pass_keeping_instant_mana": 1.0}


def combo_timing_features(option: dict, player: dict, my_main: bool) -> dict[str, float]:
    """Engine and fuel: better cast together next turn than apart now?

    engine_without_fuel_now: this card rewards a kind of spell I hold, but
      after casting it I couldn't cast any of them this turn, and next turn
      I could cast it AND one of them.
    fuel_before_engine: this spell would feed an engine in my hand that I
      can't cast together with it now, but could next turn.
    """
    card = option.get("card", {})
    if not my_main or not option.get("is_spell"):
        return {}
    now = untapped_mana(player)
    later = mana_next_turn(player)
    cost = card.get("cmc", 0) + option.get("max_x", 0)
    others = [c for c in spells_in_hand(player) if c.get("id") != card.get("id")]
    feats: dict[str, float] = {}
    kinds = commander.engine_types(card) if "Instant" not in card.get("type", "") else set()
    if kinds:
        fuel = [c.get("cmc", 0) for c in others if commander.feeds(c, kinds)]
        if fuel and now - cost < min(fuel) <= later - card.get("cmc", 0):
            feats["engine_without_fuel_now"] = 1.0
    engines = [c for c in others if commander.engine_types(c) and commander.feeds(card, commander.engine_types(c))]
    if any(e.get("cmc", 0) + card.get("cmc", 0) > now and e.get("cmc", 0) + card.get("cmc", 0) <= later
           for e in engines):
        feats["fuel_before_engine"] = 1.0
    return feats


def land_fit(land: dict, player: dict) -> dict[str, float]:
    """Does this land drop fit this turn's mana?

    tapland_costs_a_play: it enters tapped (or fetches a tapped land), and the
      one extra mana an untapped land gives would be used before my next turn
      (a spell needs exactly one more, or it lets me keep an instant up).
    tapland_on_quiet_turn: it enters tapped, and the extra mana wouldn't be
      used anyway: the best turn to get a tapped land out of the way.
    untapped_land_on_quiet_turn: an untapped land on such a turn, while a
      tapped one could have taken this quiet turn instead.
    """
    now = untapped_mana(player)
    have = colors.available(player) | colors.useful_colors(land, player)
    castable = [c for c in spells_in_hand(player) if colors.castable_by(c, have)]
    # The extra mana is useful before my next turn if a spell needs exactly
    # one more, or if I could cast a spell now and still keep an instant up
    # for the other players' turns only with that extra mana.
    needs_one_more = any(c.get("cmc", 0) == now + 1 for c in castable) or any(
        is_instant_speed(i) and s.get("id") != i.get("id") and not is_instant_speed(s)
        and s.get("cmc", 0) <= now < s.get("cmc", 0) + i.get("cmc", 0) <= now + 1
        for i in castable for s in castable)
    tapped = slow_land(land)
    tapland_in_hand = any(colors.is_land(c) and slow_land(c) and c.get("id") != land.get("id")
                          for c in player.get("hand", []))
    feats = {
        "tapland_costs_a_play": float(tapped and needs_one_more),
        "tapland_on_quiet_turn": float(tapped and not needs_one_more),
        "untapped_land_on_quiet_turn": float(not tapped and not needs_one_more and tapland_in_hand),
    }
    return {k: v for k, v in feats.items() if v}
