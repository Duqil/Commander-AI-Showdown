"""Who has been dealing damage lately? Read from Forge's game log.

Why this exists: bots judge threats by creature power. Some decks win without
big creatures. Kuja makes 0/1 Wizard tokens that ping every opponent whenever
he casts a spell, so a Kuja with four Wizards looks harmless (0 power) while
he drains the whole table. Watching what actually *happened* catches any such
engine, in any deck: pingers, drains, burn, Prisoner's Dilemma, ...

Forge's log has lines like:
    Wizard Token (441) deals 1 non-combat damage to 1-Swarmlord.
    The Swarmlord (100) deals 7 combat damage to 4-Garnet.

DamageTracker remembers those lines and, before each decision, writes the
last round's damage into the game state the bots see:
    player["recent_damage_to"]   {opponent name: damage this player dealt them}
    card["recent_damage"]        damage this card dealt to players

"The last round" is the last N turns, where N is the number of players, so
everyone has had one turn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# (?P<name>...) names a part of the match so we can read it as m["name"].
DAMAGE_LINE = re.compile(
    r"^(?P<source>.+?) \((?P<id>\d+)\) deals (?P<amount>\d+) "
    r"(?:non-combat |combat )?damage to (?P<target>.+?)\.$"
)

ZONES = ("battlefield", "graveyard", "command", "exile")


@dataclass
class Hit:
    turn: int
    source_id: int
    target: str
    amount: int


class DamageTracker:
    def __init__(self) -> None:
        self.turn = 0
        self.hits: list[Hit] = []
        self.controllers: dict[int, str] = {}  # card id -> who controls (or last controlled) it

    def see_log(self, line: str) -> None:
        m = DAMAGE_LINE.match(line)
        if m:
            self.hits.append(Hit(self.turn, int(m["id"]), m["target"], int(m["amount"])))

    def annotate(self, state: dict) -> None:
        """Add recent damage to ``state`` (in place), for the bots to read."""
        self.turn = state.get("turn", self.turn)
        players = state.get("players", [])
        names = {p["name"] for p in players}
        self._learn_controllers(players)

        # Forget hits from before the last round.
        window = max(1, len(players))
        self.hits = [h for h in self.hits if h.turn > self.turn - window]

        to: dict[str, dict[str, int]] = {n: {} for n in names}
        by_card: dict[int, int] = {}
        for h in self.hits:
            dealer = self.controllers.get(h.source_id)
            if h.target not in names or dealer is None or dealer == h.target:
                continue  # hit a creature, or we don't know whose card it was, or self-damage
            to[dealer][h.target] = to[dealer].get(h.target, 0) + h.amount
            by_card[h.source_id] = by_card.get(h.source_id, 0) + h.amount

        for p in players:
            p["recent_damage_to"] = to[p["name"]]
            for card in p.get("battlefield", []):
                card["recent_damage"] = by_card.get(card.get("id"), 0)

    def _learn_controllers(self, players: list[dict]) -> None:
        # A burn spell is in the graveyard by the time we look, so we check
        # every zone we can see. We keep old entries too: a creature that died
        # after hitting someone still counts for its controller.
        for p in players:
            for zone in ZONES:
                for card in p.get(zone, []):
                    if "id" in card:
                        self.controllers[card["id"]] = card.get("controller") or p["name"]
