"""Who is attacking whom, and who got blocked: the facts behind combat tricks.

A combat trick is a spell or ability used in the middle of combat, after
blockers are declared, when you can see how the fight will go:

  * pump a creature nobody blocked, or give it "draw a card for each damage"
    (Hunter's Insight): the damage is sure to land,
  * pump a blocked creature so it wins its fight,
  * kill or shrink the creature blocking yours,
  * on someone else's turn: remove a creature attacking you, or pump your blocker.

Forge sends a snapshot of the table with each decision. During combat each
creature card can carry:
    "attacking": "<player name>"   the player (or planeswalker) it attacks
    "blocking": true, "blocks": [ids of the attackers it blocks]

This module turns that into a small CombatView the feature code can ask
simple questions. Nothing here looks at card names: it works for any trick.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Forge's names for the combat steps where blocks are known, but combat damage
# (or the regular damage after first strike) is still to come.
AFTER_BLOCKS = {"COMBAT_DECLARE_BLOCKERS", "COMBAT_FIRST_STRIKE_DAMAGE"}
COMBAT_STEPS = {"COMBAT_BEGIN", "COMBAT_DECLARE_ATTACKERS", "COMBAT_DECLARE_BLOCKERS",
                "COMBAT_FIRST_STRIKE_DAMAGE", "COMBAT_DAMAGE", "COMBAT_END"}


@dataclass
class CombatView:
    after_blocks: bool = False            # blocks are declared and damage is still to come
    my_attack: bool = False               # it is my turn and I have attackers
    my_unblocked: set = field(default_factory=set)       # ids of my attackers nobody blocks
    my_blocked: set = field(default_factory=set)         # ids of my attackers that got blocked
    blocking_mine: set = field(default_factory=set)      # ids of creatures blocking my attackers
    attacking_me: set = field(default_factory=set)       # ids of creatures attacking me
    unblocked_at_me: set = field(default_factory=set)    # ... that none of my creatures block
    my_blockers: set = field(default_factory=set)        # ids of my creatures that block

    def in_trick_window(self) -> bool:
        """Is there a fight going on where a trick could matter?"""
        return self.after_blocks and bool(self.my_unblocked or self.my_blocked
                                          or self.attacking_me or self.my_blockers)


def view(state: dict[str, Any]) -> CombatView:
    you = state.get("you")
    phase = state.get("phase") or ""
    cards = [(p["name"], c) for p in state.get("players", []) for c in p.get("battlefield", [])]
    blocked_ids = {aid for _, c in cards if c.get("blocking") for aid in c.get("blocks", [])}

    v = CombatView(after_blocks=phase in AFTER_BLOCKS)
    for owner, c in cards:
        cid = c.get("id")
        if c.get("attacking"):
            if owner == you:
                (v.my_blocked if cid in blocked_ids else v.my_unblocked).add(cid)
            elif c["attacking"] == you:
                v.attacking_me.add(cid)
                if cid not in blocked_ids:
                    v.unblocked_at_me.add(cid)
        elif c.get("blocking"):
            if owner == you:
                v.my_blockers.add(cid)
    my_attackers = v.my_unblocked | v.my_blocked
    v.blocking_mine = {c.get("id") for _, c in cards
                       if c.get("blocking") and set(c.get("blocks", [])) & my_attackers}
    v.my_attack = state.get("active_player") == you and bool(my_attackers)
    if not v.after_blocks:
        # Before blocks nobody is "unblocked" yet: every attacker might still be blocked.
        v.my_unblocked, v.unblocked_at_me = set(), set()
    return v
