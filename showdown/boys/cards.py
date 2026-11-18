"""Card memory: a learned opinion about each single card.

Every other feature describes what a card *is* or *does* (a creature, an X
spell, a land that makes a color I'm missing). Those rules work for any deck,
but they can't know that one particular card is better or worse than its
description suggests. Card memory adds that, the learned way:

    "card:cast:lightning_greaves"          casting this card
    "card:cast:lightning_greaves@early"    ...in the early game (rounds 1-3)
    "card:cast:lightning_greaves@late"     ...in the late game (round 8 on)

Each of these starts at weight 0, so a Boy with card memory plays exactly like
before on day one. Over many games he learns, for example, "this card is worth
casting even when my general rules say wait", or "that opponent card is the
one to remove". Nothing here is written by hand for a specific card: the name
is only a label for a number the Boy learns himself.

Only brains with card memory switched on look at these features (see
``card_memory`` in brain.py). Everyone else ignores them, so the other Boys
play and learn exactly as before.

What the verbs mean:
    cast    casting this spell (play decision)
    use     activating an ability of this card (play decision)
    pick    choosing this card from a search or "look at the top N" (pick_card)
    hurt    removal, damage, -X/-X... aimed at this card (target)
    help    a helpful effect aimed at this card (target)
    attack  attacking with this creature (attack)
    aim     where this card's targeted effect goes: a player, a creature... (target)

Situations: casting a card on someone else's turn, during combat, or in
response to something on the stack is a different play from casting it in
my own main phase. So "cast" and "use" also get those versions:

    "card:cast:lightning_bolt@their_turn"   ...on an opponent's turn
    "card:cast:lightning_bolt@combat"       ...during combat
    "card:cast:lightning_bolt@respond"      ...with something on the stack

Roles: a label for what a card does, read from Forge's description of its
effects (DealDamage -> "damage", Destroy -> "destroy"...). Roles are shared by
every card that does the same thing, so a lesson learned with one burn spell
("damage at a creature during combat pays off") carries over to all burn
spells, in every deck, even cards Prime has never seen:

    "card:role:damage"                 casting/using something that deals damage
    "card:role:damage@combat"          ...during combat (same situations as above)
    "card:aim:damage>creature"         aiming damage at a creature
    "card:aim:lightning_bolt>player"   aiming this very card at a player

A card gets every role that fits: Kolaghan's Command is "damage", "discard"
and "recursion" (the modes Forge's AI chose for this cast), a creature with
"when this enters, draw a card" is "draw".
"""

from __future__ import annotations

import re

from showdown.boys import threat
from showdown.boys.combat import COMBAT_STEPS

Features = dict[str, float]

PREFIX = "card:"   # every card memory feature starts with this

# Game stages, by round (round 1 = everyone's first turn, see threat.game_round).
EARLY_UNTIL = 3    # rounds 1-3
LATE_FROM = 8      # round 8 and later; the rounds in between only get the plain feature


def is_card_feature(name: str) -> bool:
    return name.startswith(PREFIX)


def slug(name: str) -> str:
    """A card name as a safe label: "Kuja, Genome Sorcerer" -> "kuja_genome_sorcerer"."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def stage(state: dict) -> str | None:
    """'early', 'late', or None for the middle of the game."""
    r = threat.game_round(state)
    if r <= EARLY_UNTIL:
        return "early"
    if r >= LATE_FROM:
        return "late"
    return None


def features(verb: str, card: dict, state: dict | None = None) -> Features:
    """Card memory features for doing ``verb`` with ``card``.

    With a state, it adds the early/late version too, so the Boy can learn
    "good early, bad late" (a cheap creature) or the other way around.
    """
    name = slug(card.get("name", ""))
    if not name:
        return {}
    key = f"{PREFIX}{verb}:{name}"
    f = {key: 1.0}
    when = stage(state) if state is not None else None
    if when:
        f[f"{key}@{when}"] = 1.0
    return f


def situations(state: dict) -> list[str]:
    """When a play happens, beyond early/late: on their turn, in combat, responding."""
    out = []
    if state.get("active_player") != state.get("you"):
        out.append("their_turn")
    if state.get("phase") in COMBAT_STEPS:
        out.append("combat")
    if state.get("stack_size", 0) > 0:
        out.append("respond")
    return out


# Forge's effect names ("api") grouped into roles. Anything not listed keeps
# its own name in lower case ("mill", "goad"...), so new effects still get a label.
ROLE_OF_API = {
    "DealDamage": "damage", "DamageAll": "damage", "EachDamage": "damage",
    "Destroy": "destroy", "DestroyAll": "destroy",
    "Counter": "counter",
    "Draw": "draw", "Dig": "draw", "DigUntil": "draw", "PeekAndReveal": "draw",
    "Token": "tokens", "CopyPermanent": "tokens", "Amass": "tokens",
    "GainLife": "life",
    "Mana": "mana",
    "Protection": "protect", "ProtectionAll": "protect", "Regenerate": "protect", "PreventDamage": "protect",
    "GainControl": "steal", "GainControlVariant": "steal", "ExchangeControl": "steal",
    "Sacrifice": "sacrifice", "SacrificeAll": "sacrifice",
    "Pump": "pump", "PumpAll": "pump",
    "PutCounter": "counters", "PutCounterAll": "counters",
    "Tap": "tap", "TapAll": "tap", "Untap": "untap", "UntapAll": "untap",
    "Fight": "fight", "Animate": "animate", "Discard": "discard", "Scry": "scry", "Surveil": "scry",
}
# Effects that are glue rather than a role (choose a mode, remember a card...).
NO_ROLE = {"Charm", "Cleanup", "PermanentCreature", "PermanentNoncreature", "Effect", "StoreSVar", "ChooseCard", "ChooseType", "ChooseColor",
           "ChoosePlayer", "ChooseNumber"}


def role(api: str | None, params: dict | None = None) -> str | None:
    """One effect's role: "damage", "destroy", "shrink", "bounce"... or None."""
    if not api or api in NO_ROLE:
        return None
    params = params or {}
    if api in ("Pump", "PumpAll") and params.get("NumAtt", "").startswith("-"):
        return "shrink"                                   # -2/-2 is removal, not a pump
    if api in ("PutCounter", "PutCounterAll") and "M1M1" in params.get("CounterType", ""):
        return "shrink"
    if api in ("ChangeZone", "ChangeZoneAll"):
        origin, dest = params.get("Origin", ""), params.get("Destination", "")
        if "Battlefield" in origin:
            valid = params.get("ValidTgts", "")
            if "YouCtrl" in valid and "OppCtrl" not in valid:
                return "blink"                            # my own creature: a flicker for value
            return "exile" if dest == "Exile" else "bounce"
        if "Graveyard" in origin:
            return "recursion"
        if "Library" in origin:
            return "tutor"
        return "move"
    return ROLE_OF_API.get(api, api.lower())


def option_roles(option: dict) -> list[str]:
    """Every role of a play option, from the effects the bridge sent (no repeats)."""
    out = []
    for e in option.get("effects", []):
        r = role(e.get("api"), e.get("params"))
        if r and r not in out:
            out.append(r)
    return out


def play_features(option: dict, state: dict) -> Features:
    """For a play option: cast a spell or use an ability. Land drops get none:
    which land to play is about colors and mana, and mana.py covers that."""
    card = option.get("card", {})
    if option.get("is_land"):
        return {}
    verb = "cast" if option.get("is_spell") else "use"
    f = features(verb, card, state)
    name = slug(card.get("name", ""))
    when = situations(state)
    if name:
        for sit in when:
            f[f"{PREFIX}{verb}:{name}@{sit}"] = 1.0
    phase = stage(state)
    for r in option_roles(option):
        f[f"{PREFIX}role:{r}"] = 1.0
        for sit in when + ([phase] if phase else []):
            f[f"{PREFIX}role:{r}@{sit}"] = 1.0
    return f


def target_kind(cand: dict, you: str | None) -> str:
    """What a target is, in a few words: player, me, commander, creature, mine, permanent."""
    if cand.get("is_player"):
        return "me" if cand.get("name") == you else "player"
    if you is not None and cand.get("controller") == you:
        return "mine"
    if cand.get("commander"):
        return "commander"
    if "Creature" in cand.get("type", ""):
        return "creature"
    return "permanent"


def target_features(cand: dict, harmful: bool, decision: dict | None = None) -> Features:
    """For a target option: hurting or helping this card (players get none),
    and where this source card / this role is aimed (aim features)."""
    f = {} if cand.get("is_player") else features("hurt" if harmful else "help", cand)
    if decision is None:
        return f
    you = decision.get("state", {}).get("you")
    kind = target_kind(cand, you)
    source = slug(decision.get("source", {}).get("name", ""))
    if source:
        f[f"{PREFIX}aim:{source}>{kind}"] = 1.0
    r = role(decision.get("api"), decision.get("params"))
    if r:
        f[f"{PREFIX}aim:{r}>{kind}"] = 1.0
        for sit in situations(decision.get("state", {})):
            f[f"{PREFIX}aim:{r}>{kind}@{sit}"] = 1.0
    return f
