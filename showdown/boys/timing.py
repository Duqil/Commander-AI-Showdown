"""When to play a card: instant speed, and cards you can only play for a short time.

Two ideas, and neither one looks at card names.

1. Instant speed. An instant (or a card with flash) doesn't have to be cast in
   your own main phase. Good players keep mana open and use it:
     * in response to an opponent's spell or ability (counter it, save a creature),
     * in a combat, on their own turn or someone else's (pump, remove a blocker),
     * at the end of the turn just before their own, when nothing better came up.
       The mana untaps on their turn anyway, so it costs nothing to wait.
   Casting the same instant in your own main phase, like a sorcery, throws away
   that flexibility. Counterspells and protection spells are worthless there.

2. Impulse draw. "Exile the top card of your library. Until the end of your
   next turn, you may play it." Cards like that run out, so they should be
   played before cards in hand, which keep. And if the exiled card turns out
   to be a land, you can only play it if you haven't played a land yet this
   turn. So it can be smart to cast the impulse draw first and play your land
   afterwards.

Forge tells Python which zone each playable card is in (option["zone"]).
"""

from __future__ import annotations

import re
from typing import Any

# Forge's name for the end step, the last chance to act before a turn ends.
END_STEP = "END_OF_TURN"

# What a spell does (Forge's "api" for it), for effects that only make sense
# as a reaction: counter a spell, protect or pump a creature, prevent combat
# damage. Cast in your own main phase with nothing going on, they do little.
REACTIVE_APIS = {"Counter", "Pump", "PumpAll", "Protection", "ProtectionAll", "Fog",
                 "Regenerate", "PreventDamage"}

# "Exile the top card(s) of your library ... you may play/cast it/them".
_IMPULSE = re.compile(r"exile the top [^.]*library[^•]*?(you may (play|cast)|play (it|that card|them|those cards))")


def instant_speed(option: dict) -> bool:
    """A spell you may cast whenever you have priority: an instant, or flash."""
    if not option.get("is_spell"):
        return False
    card = option.get("card", {})
    return "Instant" in card.get("type", "") or "Flash" in option.get("description", "") \
        or "Flash" in card.get("oracle", "").split("\n")[0]


def reactive(option: dict) -> bool:
    return option.get("api") in REACTIVE_APIS


def from_exile(option: dict) -> bool:
    """Played from exile: almost always an impulse draw that runs out soon."""
    return option.get("zone") == "Exile"


def is_impulse(card: dict) -> bool:
    """Does this card exile cards from the top of your library for you to play?"""
    return bool(_IMPULSE.search(card.get("oracle", "").lower()))


def _alive_in_turn_order(state: dict[str, Any]) -> list[str]:
    # Players are listed in seat order, which is turn order.
    return [p["name"] for p in state.get("players", []) if not p.get("lost")]


def my_turn_is_next(state: dict[str, Any]) -> bool:
    """Is the player whose turn it is now the one right before me?"""
    order = _alive_in_turn_order(state)
    active, you = state.get("active_player"), state.get("you")
    if active not in order or you not in order or active == you:
        return False
    return order[(order.index(active) + 1) % len(order)] == you


def end_step_before_mine(state: dict[str, Any]) -> bool:
    """The end step of the turn right before mine, with nothing on the stack:
    the classic moment to use mana I kept open, since it untaps next."""
    return state.get("phase") == END_STEP and state.get("stack_size", 0) == 0 and my_turn_is_next(state)


def opponent_on_stack(state: dict[str, Any]) -> bool:
    """Is the top of the stack an opponent's spell or ability (something to answer)?"""
    stack = state.get("stack") or []
    if not stack:
        return False
    controller = stack[0].get("controller")  # top of the stack first
    return controller is not None and controller != state.get("you")


def impulse_pending(state: dict[str, Any], options: list[dict]) -> bool:
    """Could I still exile cards to play this turn, before using my land drop?

    Either a play I could make now is an impulse draw itself, or something I
    control does it when I cast a spell (a commander like "whenever you cast
    a modal spell, exile the top card...") and I have a spell to cast.
    """
    if any(not o.get("is_land") and is_impulse(o.get("card", {})) for o in options):
        return True
    me = next((p for p in state.get("players", []) if p["name"] == state.get("you")), {})
    on_cast = any("whenever you cast" in c.get("oracle", "").lower() and is_impulse(c)
                  for c in me.get("battlefield", []))
    return on_cast and any(o.get("is_spell") for o in options)
