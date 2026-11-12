"""Deals between bots: offers, answers, keeping track, and trust.

Forge knows nothing about deals. They live entirely in Python, here.

The menu (v1):
    peace      "Let's not attack each other for N turns."
    gang_up    "Let's both attack <leader> for N turns." (and not each other)

How a deal happens, once per turn:
    1. At the start of a turn, the active player's bot may offer ONE deal to
       one opponent (Bot.offer_deal).
    2. That opponent's bot accepts or declines (Bot.answer_deal).
    3. Forge's own AI never makes deals: offers to it are declined.

Breaking a deal: attacking someone you have a deal with breaks it. Bots
decide that themselves; this module only notices. When a deal is broken:
  * the victim swears revenge: the breaker goes on the victim's "grudges"
    list for the rest of the game (bots see it, SimpleBot attacks grudges
    first, Boys get it as a feature),
  * the victim's trust in the breaker drops a lot and everyone else's a
    little (word gets around), so the breaker's future offers get declined.
Trust starts at 1.0 and can be won back: keeping a deal until it ends raises
both partners' trust in each other (only if it was tested: one of them could
have attacked the other and held back), and actually attacking the agreed target
in a gang-up raises the partner's trust in you. All of this lasts for one game.

Every offer, answer and break becomes a "deal" message, which shows up in the
game log and in the browser viewer's timeline.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

DEAL_ROUNDS = 2         # how long a deal lasts: 2 rounds = every player gets 2 more turns
VICTIM_TRUST_LOSS = 0.5
OTHERS_TRUST_LOSS = 0.25
KEPT_TRUST_GAIN = 0.1   # a deal ran its full length: both partners trust each other a bit more
HELP_TRUST_GAIN = 0.05  # you attacked the gang-up target: your partner trusts you a bit more
DEAL_TYPES = ("peace", "gang_up")


@dataclass
class Deal:
    id: int
    kind: str                  # "peace" or "gang_up"
    a: str                     # who offered
    b: str                     # who accepted
    made_turn: int
    expires_turn: int          # the deal is over once this game turn has passed
    rounds: int = DEAL_ROUNDS
    against: str | None = None # gang_up: the player they both attack
    broken_by: str | None = None
    settled: bool = False      # we already handed out the "kept the deal" trust
    tested: bool = False       # someone could have attacked their partner and didn't

    def involves(self, name: str) -> bool:
        return name in (self.a, self.b)

    def other(self, name: str) -> str:
        return self.b if name == self.a else self.a

    def describe(self) -> str:
        if self.kind == "gang_up":
            return f"gang up on {self.against} for {self.rounds} rounds"
        return f"peace for {self.rounds} rounds"

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "a": self.a, "b": self.b, "against": self.against,
                "made_turn": self.made_turn, "expires_turn": self.expires_turn, "rounds": self.rounds}


@dataclass
class DealBook:
    """All deals and trust for one game."""
    players: list[str] = field(default_factory=list)
    deals: list[Deal] = field(default_factory=list)
    trust: dict[str, dict[str, float]] = field(default_factory=dict)  # trust[me][them]
    grudges: dict[str, set[str]] = field(default_factory=dict)        # grudges[victim] = breakers
    last_offer_turn: int = 0
    _next_id: int = 1

    def __post_init__(self) -> None:
        for p in self.players:
            self.trust[p] = {q: 1.0 for q in self.players if q != p}

    # --- looking things up ----------------------------------------------------

    def active(self, turn: int) -> list[Deal]:
        return [d for d in self.deals if d.broken_by is None and turn <= d.expires_turn]

    def deals_of(self, name: str, turn: int) -> list[Deal]:
        return [d for d in self.active(turn) if d.involves(name)]

    def has_deal(self, a: str, b: str, turn: int) -> bool:
        return any(d.involves(a) and d.involves(b) for d in self.active(turn))

    def view_for(self, name: str, state: dict) -> dict[str, Any]:
        """What a bot is told about deals (added to its decisions as "deals")."""
        turn = state.get("turn", 0)
        return {
            "active": [d.to_dict() for d in self.deals_of(name, turn)],
            "trust": dict(self.trust.get(name, {})),
            # Players I may not attack without breaking a deal:
            "protected": sorted({d.other(name) for d in self.deals_of(name, turn)}),
            # gang_up targets I promised to attack:
            "gang_targets": sorted({d.against for d in self.deals_of(name, turn) if d.against}),
            # Players who broke a deal with me: revenge!
            "grudges": sorted(self.grudges.get(name, set())),
        }

    # --- making deals --------------------------------------------------------------

    def offer_phase(self, state: dict, bots: dict[str, Any]) -> list[dict]:
        """Run the start-of-turn offer. Returns the deal messages that happened."""
        turn = state.get("turn", 0)
        offerer = state.get("active_player")
        kept = self.settle(turn)
        if turn <= self.last_offer_turn or offerer not in bots or offerer not in self.players:
            return kept
        self.last_offer_turn = turn
        return kept + self._offer(state, bots, turn, offerer)

    def _offer(self, state: dict, bots: dict[str, Any], turn: int, offerer: str) -> list[dict]:
        public = public_state(state, offerer)
        bot = bots[offerer]
        offer = bot.offer_deal({"kind": "offer_deal", "player": offerer, "state": public,
                                "menu": list(DEAL_TYPES), "deals": self.view_for(offerer, public)})
        if not offer or not self._valid_offer(offer, offerer, public):
            return []
        to = offer["to"]
        # Game turns count every player's turn, so 2 rounds at a 4-player table is 8 turns.
        deal = Deal(self._next_id, offer["type"], offerer, to, turn,
                    turn + DEAL_ROUNDS * len(self.alive(public)), against=offer.get("against"))
        messages = [deal_message("offer", deal, turn)]
        responder = bots.get(to)
        accepted = False
        if responder is not None:
            responder_state = public_state(state, to)
            accepted = bool(responder.answer_deal({
                "kind": "answer_deal", "player": to, "state": responder_state,
                "offer": deal.to_dict(), "deals": self.view_for(to, responder_state)}))
        if accepted:
            self._next_id += 1
            self.deals.append(deal)
            messages.append(deal_message("accept", deal, turn))
        else:
            messages.append(deal_message("decline", deal, turn,
                                         reason=None if responder else "Forge's AI doesn't make deals"))
        return messages

    def _valid_offer(self, offer: dict, offerer: str, state: dict) -> bool:
        alive = self.alive(state)
        if offer.get("type") not in DEAL_TYPES or offer.get("to") not in alive or offer["to"] == offerer:
            return False
        if self.has_deal(offerer, offer["to"], state.get("turn", 0)):
            return False  # already have one
        if offer["type"] == "gang_up":
            against = offer.get("against")
            return against in alive and against not in (offerer, offer["to"])
        return True

    @staticmethod
    def alive(state: dict) -> list[str]:
        return [p["name"] for p in state.get("players", []) if not p.get("lost")]

    # --- keeping deals -----------------------------------------------------------------

    def settle(self, turn: int) -> list[dict]:
        """Reward deals that ran their full length unbroken. Returns "kept" messages."""
        messages = []
        for deal in self.deals:
            if deal.broken_by is None and not deal.settled and turn > deal.expires_turn:
                deal.settled = True
                if not deal.tested:
                    continue  # nobody was ever in a position to break it: no trust earned
                self._gain_trust(deal.a, deal.b, KEPT_TRUST_GAIN)
                self._gain_trust(deal.b, deal.a, KEPT_TRUST_GAIN)
                messages.append(deal_message("kept", deal, turn))
        return messages

    def _gain_trust(self, me: str, them: str, amount: float) -> None:
        row = self.trust.get(me, {})
        if them in row:
            row[them] = min(1.0, row[them] + amount)

    # --- breaking deals ---------------------------------------------------------------

    def check_attack(self, attacker: str, decision: dict, choice: dict | None) -> list[dict]:
        """Did this attack break a deal? Returns deal messages for each break.

        Also notes when a deal was really *tested*: the attacker had creatures
        that could have hit their partner, and held back. Only tested deals
        earn trust when they end; a peace deal while nobody has creatures
        proves nothing.
        """
        turn = decision["state"].get("turn", 0)
        defenders = {d["index"]: d["name"] for d in decision.get("defenders", [])}
        attacked = {defenders.get(i) for i in (choice or {}).values()}
        could_hit = {defenders.get(i) for a in decision.get("attackers", [])
                     if (a.get("power") or 0) > 0 for i in a.get("can_attack", [])}
        messages = []
        for deal in self.deals_of(attacker, turn):
            victim = deal.other(attacker)
            if victim in could_hit and victim not in attacked:
                deal.tested = True
            if deal.against in attacked and victim not in attacked:
                self._gain_trust(victim, attacker, HELP_TRUST_GAIN)  # kept their word
            if victim in attacked:
                deal.broken_by = attacker
                self._lose_trust(attacker, victim)
                self.grudges.setdefault(victim, set()).add(attacker)
                messages.append(deal_message("break", deal, turn, breaker=attacker))
        return messages

    def _lose_trust(self, breaker: str, victim: str) -> None:
        for p, row in self.trust.items():
            if breaker in row:
                loss = VICTIM_TRUST_LOSS if p == victim else OTHERS_TRUST_LOSS
                row[breaker] = max(0.0, row[breaker] - loss)


def public_state(state: dict, viewer: str) -> dict:
    """The table without anyone's hand except the viewer's own (if we have it)."""
    s = copy.deepcopy(state)
    s["you"] = viewer
    for p in s.get("players", []):
        if p["name"] != viewer:
            p.pop("hand", None)
        elif p["name"] != state.get("you"):
            p.pop("hand", None)  # we don't know this player's hand from someone else's view
    return s


def deal_message(event: str, deal: Deal, turn: int, **extra: Any) -> dict:
    """A message for the game log / viewer: event is offer, accept, decline, kept or break."""
    if event == "offer":
        text = f"{deal.a} offers {deal.b} a deal: {deal.describe()}"
    elif event == "accept":
        text = f"{deal.b} accepts {deal.a}'s deal ({deal.describe()})"
    elif event == "kept":
        text = f"{deal.a} and {deal.b} kept their deal ({deal.describe()}) and trust each other more"
    elif event == "decline":
        reason = extra.get("reason")
        text = f"{deal.b} declines {deal.a}'s deal" + (f" ({reason})" if reason else "")
    else:
        breaker = extra["breaker"]
        victim = deal.other(breaker)
        text = (f"{breaker} BREAKS the deal with {victim} by attacking! "
                f"{victim} swears revenge, and the table trusts {breaker} less.")
    return {"type": "deal", "event": event, "text": text, "turn": turn, "deal": deal.to_dict(),
            **{k: v for k, v in extra.items() if v is not None}}

