"""Choosing modes: "choose one or more" spells and triggers (charms, Riku's trigger).

Forge's AI used to pick the modes for every bot. Now Python does (the bridge
asks "choose_mode"), and Forge's pick comes along as a hint ("suggested").

The idea Adam wanted (2026-11-24): every mode can be the strong one, depending
on the moment. Take Riku of Many Paths: "exile the top card, you may play it
until the end of your next turn", or a +1/+1 counter, or a 1/1 flying Bird.

* A counter or a token is lasting value: it is still there next turn.
* Exiling a card is a card to play soon. Nobody knows which card it will be,
  so go by the moment (Adam's rule): nothing else in hand to cast this turn,
  few cards in hand, and plenty of mana. Then an extra card is welcome.
  With a full hand of things to cast, it's likely wasted: take the counter
  or the token. If it lasts until next turn, plenty of spare mana next turn
  (after the spells already in hand) counts too.

SimpleBot follows that rule. The Boys get the same facts as inputs (plus
SimpleBot's and Forge's picks as hints) and learn when each mode is best, so
they keep trying all of them instead of copying one fixed habit.

Nothing here looks at card names: the modes are read from the effects the
bridge sends (Forge's api names, as in cards.py).
"""

from __future__ import annotations

from showdown.boys import cards, colors, mana, threat

# A mode's effects that leave something lasting on my side of the table.
LASTING_ROLES = {"counters", "tokens", "animate", "steal"}


def is_impulse(mode: dict) -> bool:
    """Exile the top card(s) of my library to play soon (read from the effects:
    a dig or move from the library into exile, then a "you may play it" effect)."""
    effects = mode.get("effects", [])
    to_exile = any(e.get("api") in ("Dig", "ChangeZone", "ChangeZoneAll", "Mill")
                   and "Exile" in (e.get("params", {}).get("Destination", "") + e.get("params", {}).get("DestinationZone", ""))
                   for e in effects)
    text = (mode.get("description", "") or "").lower()
    return (to_exile or "exile the top" in text) and "you may play" in text


def roles(mode: dict) -> list[str]:
    return cards.option_roles(mode)


def lasting(mode: dict) -> bool:
    return any(r in LASTING_ROLES for r in roles(mode))


def can_use_now(decision: dict) -> bool:
    """Mana open right now, or a land drop left (the card might be a land)."""
    me = threat.me(decision["state"])
    return mana.untapped_mana(me) > 0 or bool(decision.get("land_drop_left"))


def spare_next_turn(decision: dict) -> int:
    """Next turn's mana left over after the spells already in my hand."""
    me = threat.me(decision["state"])
    have = colors.available(me)
    in_hand = sum(c.get("cmc", 0) for c in mana.spells_in_hand(me) if colors.castable_by(c, have))
    return mana.mana_next_turn(me) - in_hand


def room_next_turn(decision: dict) -> bool:
    """Next turn's mana covers more than the spells already in my hand: an
    extra card from exile could be played then too."""
    return spare_next_turn(decision) > 0


def lasts_until_next_turn(mode: dict) -> bool:
    return "next turn" in (mode.get("description", "") or "").lower()


# "Few cards in hand" and "a good amount of mana" (Adam's rule, 2026-11-24).
FEW_CARDS = 2
GOOD_MANA = 3


def nothing_else_to_cast(decision: dict) -> bool:
    """No spell in my hand that my open mana could pay for right now."""
    me = threat.me(decision["state"])
    have = colors.available(me)
    open_mana = mana.untapped_mana(me)
    return not any(c.get("cmc", 0) <= open_mana and colors.castable_by(c, have) for c in mana.spells_in_hand(me))


def few_cards(decision: dict) -> bool:
    me = threat.me(decision["state"])
    return len(me.get("hand", [])) <= FEW_CARDS


def lots_of_mana(decision: dict) -> bool:
    """A good amount of mana to play an unknown card with: now, or next turn
    if the exile lasts that long (checked by the caller)."""
    me = threat.me(decision["state"])
    return mana.untapped_mana(me) >= GOOD_MANA


def impulse_chances(mode: dict, decision: dict) -> tuple[float, float]:
    """(could I likely play the exiled card now, ...next turn), as 0 or 1.
    We can't know the card, so we go by the moment: nothing else to cast,
    few cards in hand and plenty of mana now; or (if it lasts until next
    turn) next turn's mana goes well beyond the spells already in my hand."""
    now = float((nothing_else_to_cast(decision) and few_cards(decision) and lots_of_mana(decision))
                or bool(decision.get("land_drop_left") and few_cards(decision) and nothing_else_to_cast(decision)))
    nxt = float(lasts_until_next_turn(mode) and few_cards(decision) and spare_next_turn(decision) >= GOOD_MANA)
    return now, nxt


def impulse_useful(mode: dict, decision: dict) -> bool:
    return max(impulse_chances(mode, decision)) > 0


def simple_pick(decision: dict) -> list[int] | None:
    """SimpleBot's modes: Forge's pick, except a "play it soon" exile goes only
    when the card could be used (now, or next turn if it lasts that long);
    otherwise a lasting mode (counter, token) takes its place. None = Forge's pick."""
    modes = decision.get("modes", [])
    lo, hi = decision.get("min", 1), decision.get("max", 1)
    if not modes or hi <= 0:
        return None
    forge = list(decision.get("suggested", []))
    by_index = {m["index"]: m for m in modes}
    useful = {m["index"]: not is_impulse(m) or impulse_useful(m, decision) for m in modes}
    if forge and all(useful.get(i, True) for i in forge) and len(forge) >= min(hi, len(modes)):
        return None  # Forge's pick is fine
    # Up to "max" modes: lasting ones first, then useful others, Forge's picks first among equals.
    ranked = sorted(modes, key=lambda m: (not useful[m["index"]], not lasting(m), m["index"] not in forge, m["index"]))
    picked = [m["index"] for m in ranked if useful[m["index"]]][:hi]
    if len(picked) < lo:  # must choose more: add the rest in the same order
        picked += [m["index"] for m in ranked if m["index"] not in picked][:lo - len(picked)]
    if sorted(picked) == sorted(forge):
        return None
    return picked if lo <= len(picked) <= hi else None


def mode_options(decision: dict, chosen: list[int]) -> tuple[list[dict], list[int | None]]:
    """One step of choosing: each mode not chosen yet, plus "stop" (once the
    minimum is reached). Returns (features per option, mode index or None)."""
    modes = decision.get("modes", [])
    lo, hi = decision.get("min", 1), decision.get("max", 1)
    simple = simple_pick(decision)
    simple_set = set(decision.get("suggested", []) if simple is None else simple)
    feats: list[dict] = []
    picks: list[int | None] = []
    if len(chosen) >= lo:
        feats.append({"mode_stop": 1.0, "mode_stop_early": float(len(chosen) < min(hi, len(modes))),
                      "simple_prior": float(simple_set <= set(chosen))})
        picks.append(None)
    if len(chosen) < hi:
        for m in modes:
            if m["index"] in chosen:
                continue
            impulse = is_impulse(m)
            now, nxt = impulse_chances(m, decision) if impulse else (0.0, 0.0)
            f = {
                "mode": 1.0,
                "mode_lasting": float(lasting(m)),
                "mode_impulse": float(impulse),
                # The exiled card could likely be played this turn (nothing else
                # to cast, few cards, plenty of mana)...
                "mode_impulse_now": now,
                # ...or next turn, if it lasts that long (mana left over after my hand).
                "mode_impulse_next_turn": nxt,
                # ...or it's likely wasted: neither.
                "mode_impulse_wasted": (1.0 - max(now, nxt)) if impulse else 0.0,
                "simple_prior": float(m["index"] in simple_set),
            }
            for r in roles(m):
                f[f"mode_role:{r}"] = 1.0
            feats.append(f)
            picks.append(m["index"])
    return feats, picks
