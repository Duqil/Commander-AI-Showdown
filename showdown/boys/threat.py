"""Threat assessment: who at the table is dangerous, and who can be finished off?

These are hand-written rules of thumb, not learned. The Boys get them as
*inputs* (features) and learn how much to trust each one. Deals will use them
too ("gang up on the leader" needs to know who the leader is).

Everything here works on the ``state`` dict a bot gets with each decision
(see forge-bridge/.../StateSnapshot.java). Hidden information stays hidden:
you only know an opponent's hand SIZE, never the cards.

A good Python exercise: change the weights in ``threat_score`` and see if
the Boys do better or worse.
"""

from __future__ import annotations

from typing import Any

Player = dict[str, Any]
State = dict[str, Any]

STARTING_LIFE = 40


def creatures(player: Player) -> list[dict]:
    """Creatures on this player's battlefield."""
    return [c for c in player.get("battlefield", []) if "Creature" in c.get("type", "")]


def board_power(player: Player) -> int:
    """Total power of the player's creatures (how hard they can hit)."""
    return sum(max(0, c.get("power", 0)) for c in creatures(player))


def recent_damage_to(player: Player, target: str) -> int:
    """Damage ``player`` dealt to ``target`` in the last round (see showdown/damage.py)."""
    return player.get("recent_damage_to", {}).get(target, 0)


def damage_output(player: Player) -> int:
    """How hard this player hits: their creatures' power, or the most damage
    they actually dealt one opponent in the last round, whichever is bigger.

    Power alone misses engines: Kuja's 0/1 Wizards have no power but ping
    everyone. Taking the bigger number (not the sum) avoids counting a big
    attacker twice.
    """
    dealt = max(player.get("recent_damage_to", {}).values(), default=0)
    return max(board_power(player), dealt)


def untapped_blockers(player: Player) -> list[dict]:
    """Creatures that could block right now."""
    return [c for c in creatures(player) if not c.get("tapped")]


def has_keyword(card: dict, keyword: str) -> bool:
    """Is ``keyword`` (e.g. "flying") one of the card's keyword lines?

    Keywords sit on their own line of rules text: "Flying" or "Flying,
    vigilance". A line like "Creatures you control have flying" doesn't
    count, and neither does a keyword granted by another card, so this is a
    rough guess.
    """
    for line in card.get("oracle", "").lower().splitlines():
        words = [w.strip() for w in line.split("(")[0].split(",")]
        if keyword in words:
            return True
    return False


def can_block(blocker: dict, attacker: dict | None) -> bool:
    """Flyers can only be blocked by creatures with flying or reach."""
    if attacker is None or not has_keyword(attacker, "flying"):
        return True
    return has_keyword(blocker, "flying") or has_keyword(blocker, "reach")


def blockers_for(player: Player, attacker: dict | None = None) -> list[dict]:
    """Untapped creatures of ``player`` that could block this attacker."""
    return [b for b in untapped_blockers(player) if can_block(b, attacker)]


def kills(blocker: dict, toughness: int) -> bool:
    """Would this blocker kill an attacker with ``toughness``? (Deathtouch always does.)"""
    return blocker.get("power", 0) >= toughness or (blocker.get("power", 0) > 0 and has_keyword(blocker, "deathtouch"))


def killer_blockers(player: Player, power: int, toughness: int, attacker: dict | None = None) -> list[dict]:
    """Untapped creatures that could block a ``power``/``toughness`` attacker,
    kill it, and survive the hit (a "bad block" for the attacker)."""
    return [b for b in blockers_for(player, attacker)
            if kills(b, toughness) and b.get("toughness", 0) > power]


def trade_blockers(player: Player, power: int, toughness: int, attacker: dict | None = None) -> list[dict]:
    """Untapped creatures that would kill the attacker but die too (a trade)."""
    return [b for b in blockers_for(player, attacker)
            if kills(b, toughness) and b.get("toughness", 0) <= power]


def double_block_kills(player: Player, power: int, toughness: int, attacker: dict | None = None) -> bool:
    """Could two blockers together kill the attacker, when no single one can?

    Only power, toughness, flying/reach and deathtouch are checked (first
    strike, menace and the like are not), so this is a rough guess.
    """
    blockers = blockers_for(player, attacker)
    if len(blockers) < 2 or any(kills(b, toughness) for b in blockers):
        return False
    strongest = sorted((max(0, b.get("power", 0)) for b in blockers), reverse=True)
    return strongest[0] + strongest[1] >= toughness > 0


def nonland_permanents(player: Player) -> int:
    return sum(1 for c in player.get("battlefield", []) if "Land" not in c.get("type", ""))


def threat_score(player: Player) -> float:
    """How dangerous a player looks. Higher = bigger threat.

    A weighted sum of what you can see. The weights are a starting guess:
      * damage output counts most: creature power, or the damage they
        really dealt last round if that's more (catches pingers and drains),
      * other non-land permanents (artifacts, enchantments, planeswalkers),
      * cards in hand (more options next turn),
      * life (a healthy player is harder to get rid of).
    """
    if player.get("lost"):
        return 0.0
    return (
        1.0 * damage_output(player)
        + 0.5 * nonland_permanents(player)
        + 0.5 * player.get("hand_size", len(player.get("hand", [])))
        + 0.1 * player.get("life", STARTING_LIFE)
    )


def game_round(state: State) -> int:
    """Which round we're in: round 1 is everyone's first turn.

    Forge counts turns across all players, so with 4 players turns 1-4 are
    round 1, turns 5-8 are round 2, and so on.
    """
    players = max(1, len(state.get("players", [])))
    return (max(1, state.get("turn", 1)) - 1) // players + 1


def total_board_power(state: State) -> int:
    """Power of every creature on the table: how much fighting there is to deal about."""
    return sum(board_power(p) for p in state.get("players", []))


def opponents(state: State) -> list[Player]:
    """Opponents still in the game."""
    return [p for p in state["players"] if p["name"] != state["you"] and not p.get("lost")]


def me(state: State) -> Player:
    return next(p for p in state["players"] if p["name"] == state["you"])


def leader(state: State) -> str | None:
    """Name of the most threatening opponent (None if there are no opponents left)."""
    opps = opponents(state)
    if not opps:
        return None
    # max(..., key=...) picks the item with the highest score.
    return max(opps, key=threat_score)["name"]


def punching_down(state: State, offerer: str, against: str) -> bool:
    """Would ganging up on ``against`` pick on the weakest player at the table?

    The weakest = lowest threat score among everyone still in except the one
    offering. Ganging up on someone who is already struggling (stuck on lands,
    no board) is frowned upon at a real table: gang-ups are for the leader.
    """
    others = [p for p in state["players"] if not p.get("lost") and p["name"] != offerer]
    if len(others) < 2:
        return False
    return min(others, key=threat_score)["name"] == against


def threat_rank(state: State, name: str) -> float:
    """0.0 for the biggest threat, 1.0 for the smallest, in between for others."""
    opps = sorted(opponents(state), key=threat_score, reverse=True)
    names = [p["name"] for p in opps]
    if name not in names or len(names) < 2:
        return 0.0
    return names.index(name) / (len(names) - 1)


def can_kill(target: Player, attack_power: int) -> bool:
    """Could ``attack_power`` damage finish this player if nothing is blocked?"""
    return 0 < target.get("life", STARTING_LIFE) <= attack_power


def danger_to_me(state: State) -> float:
    """How close the table is to killing me: the most one opponent could do to
    me (board power, or what they actually did to me last round) / my life.

    1.0 or more means one opponent could kill me with one unblocked attack.
    """
    my = me(state)
    my_life = max(1, my.get("life", STARTING_LIFE))
    worst = max((max(board_power(p), recent_damage_to(p, my["name"])) for p in opponents(state)), default=0)
    return worst / my_life


def deal_gains(state: State, partner_name: str) -> tuple[float, float]:
    """For a deal between me and ``partner_name``: (how much I gain, how much they gain).

    A deal means we stop attacking each other, so:
      * my gain = how much of my life the partner could take in one go
        (their damage output / my life, at most 1.0), now held back;
      * their gain = the same the other way around, times how big a threat
        they are next to the biggest threat at the table (1.0 for the leader,
        close to 0 for someone far behind). Helping the leader costs a lot,
        helping the weakest player hardly anything.

    Both are 0.0 to 1.0. A deal looks good when my gain beats their gain.
    """
    my = me(state)
    partner = next((p for p in state["players"] if p["name"] == partner_name), None)
    if partner is None:
        return 0.0, 0.0
    alive = [p for p in state["players"] if not p.get("lost")]
    top = max((threat_score(p) for p in alive), default=0.0) or 1.0
    my_gain = min(1.0, damage_output(partner) / max(1, my.get("life", STARTING_LIFE)))
    their_raw = min(1.0, damage_output(my) / max(1, partner.get("life", STARTING_LIFE)))
    return my_gain, their_raw * threat_score(partner) / top
