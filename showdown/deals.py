"""Deals between bots: offers, answers, keeping track, and trust.

Forge knows nothing about deals. They live entirely in Python, here.

The menu:
    peace      "Let's not attack each other for N rounds."
    gang_up    "Let's both attack <X> for N rounds." (and not each other)
The offerer picks N: 1 to 4 rounds, and a gang-up can also last "until <X>
is out" (rounds = 0). A deal also ends when one of its players is out, or
when the two partners are the only players left.

How a deal happens, once per turn:
    1. At the start of a turn, the active player's bot may offer ONE deal to
       one opponent (Bot.offer_deal).
    2. That opponent's bot accepts or declines (Bot.answer_deal).
    3. Forge's own AI never makes deals: offers to it are declined.

Breaking a deal: attacking someone you have a deal with breaks it. Bots
decide that themselves; this module only notices. A new deal that goes
against an old one breaks the old one too, the moment it's accepted:
peace with Zangief, then joining a gang-up on Zangief, breaks the peace;
ganging up on Zangief with Zidane, then making peace with Zangief, breaks
the gang-up with Zidane. When a deal is broken:
  * the victim swears revenge: the breaker goes on the victim's "grudges"
    list for the rest of the game (bots see it, SimpleBot attacks grudges
    first, Boys get it as a feature),
  * the victim's trust in the breaker drops a lot and everyone else's a
    little (word gets around), so the breaker's future offers get declined,
  * the victim owes the breaker ONE revenge: a real setback (see Revenge
    below). Once it's done, it's over ("Zidane removed Krenko from
    Zangief. Zidane has taken revenge, and it's over."). Not all-in revenge
    for the rest of the game.
Trust starts at 1.0 and can be won back: keeping a deal until it ends raises
both partners' trust in each other (only if it was tested: one of them could
have attacked the other and held back), and actually attacking the agreed target
in a gang-up raises the partner's trust in you. All of this lasts for one game.

Every offer, answer and break becomes a "deal" message, which shows up in the
game log and in the browser viewer's timeline.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from showdown.boys import threat

DEAL_ROUNDS = 2         # default length (bots that don't say): 2 rounds = every player gets 2 more turns
DEAL_LENGTHS = (1, 2, 3, 4)  # how many rounds an offerer may ask for
UNTIL_OUT = 0           # rounds = 0: a gang-up that lasts until its target is out of the game
FOREVER = 10 ** 6       # expires_turn of an "until they're out" deal (it ends when they're out)
VICTIM_TRUST_LOSS = 0.5
OTHERS_TRUST_LOSS = 0.25
KEPT_TRUST_GAIN = 0.1   # a deal ran its full length: both partners trust each other a bit more
HELP_TRUST_GAIN = 0.05  # you attacked the gang-up target: your partner trusts you a bit more
DEAL_TYPES = ("peace", "gang_up")

# Revenge: what counts as really setting the breaker back. Only the victim's
# own actions count. Any one of these and the revenge is done:
REVENGE_DAMAGE = 5            # the victim dealt the breaker this much damage in one game turn,
REVENGE_THREAT_DROP = 0.2     # or the breaker's threat score fell by a fifth during the victim's turn,
IMPORTANT_POWER = 3           # or the victim removed something important: their commander,
IMPORTANT_COST = 3            # a creature with 3+ power, or any non-land costing 3+ mana.
ZONE_LINE = re.compile(r"^(?P<name>.+?) \((?P<id>\d+)\) was put into (?P<zone>\w+) from Battlefield\.$")
DAMAGE_LINE = re.compile(r"^(?P<source>.+?) \((?P<id>\d+)\) deals (?P<amount>\d+) (?:non-combat |combat )?damage to (?P<target>.+?)\.$")


@dataclass
class Deal:
    id: int
    kind: str                  # "peace" or "gang_up"
    a: str                     # who offered
    b: str                     # who accepted
    made_turn: int
    expires_turn: int          # the deal is over once this game turn has passed
    rounds: int = DEAL_ROUNDS  # 1-4, or UNTIL_OUT (0): a gang-up until its target is out
    against: str | None = None # gang_up: the player they both attack
    broken_by: str | None = None
    broken_how: str | None = None  # "attack", or "new_deal" (a deal that went against this one)
    ended: str | None = None   # why it ended early without anyone breaking it ("3-C is out")
    settled: bool = False      # we already handed out the "kept the deal" trust
    tested: bool = False       # someone could have attacked their partner and didn't

    def involves(self, name: str) -> bool:
        return name in (self.a, self.b)

    def other(self, name: str) -> str:
        return self.b if name == self.a else self.a

    def length(self) -> str:
        if self.rounds == UNTIL_OUT:
            return f"until {self.against} is out"
        return f"for {self.rounds} round" + ("" if self.rounds == 1 else "s")

    def describe(self) -> str:
        if self.kind == "gang_up":
            return f"gang up on {self.against} {self.length()}"
        return f"peace {self.length()}"

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "a": self.a, "b": self.b, "against": self.against,
                "made_turn": self.made_turn, "expires_turn": self.expires_turn, "rounds": self.rounds,
                "broken_how": self.broken_how}


@dataclass
class Revenge:
    """A victim owes a deal-breaker one revenge: one real setback, then it's over."""
    victim: str
    breaker: str
    deal: Deal
    damage: int = 0                  # damage the victim dealt the breaker this game turn
    start_threat: float | None = None  # the breaker's threat at the start of the victim's turn
    done: bool = False


@dataclass
class DealBook:
    """All deals, trust and revenge for one game."""
    players: list[str] = field(default_factory=list)
    deals: list[Deal] = field(default_factory=list)
    trust: dict[str, dict[str, float]] = field(default_factory=dict)  # trust[me][them]
    grudges: dict[str, set[str]] = field(default_factory=dict)        # grudges[victim] = breakers
    revenge: list[Revenge] = field(default_factory=list)              # revenge still owed (and done)
    last_offer_turn: int = 0
    _next_id: int = 1
    # What we last saw of the table, for revenge: every battlefield card by id
    # (and who controls it), the game turn, and whose turn it is.
    _cards: dict[int, dict] = field(default_factory=dict)
    _turn: int = 0
    _active: str | None = None
    _targeted: dict[str, set[int]] = field(default_factory=dict)      # victim -> breaker cards they aimed harm at

    def __post_init__(self) -> None:
        for p in self.players:
            self.trust[p] = {q: 1.0 for q in self.players if q != p}

    # --- looking things up ----------------------------------------------------

    def active(self, turn: int) -> list[Deal]:
        return [d for d in self.deals if d.broken_by is None and d.ended is None and turn <= d.expires_turn]

    def deals_of(self, name: str, turn: int) -> list[Deal]:
        return [d for d in self.active(turn) if d.involves(name)]

    def has_deal(self, a: str, b: str, turn: int) -> bool:
        return any(d.involves(a) and d.involves(b) for d in self.active(turn))

    def revenge_owed(self, victim: str) -> list[str]:
        """Breakers this player still owes a revenge (one real setback each)."""
        return sorted({r.breaker for r in self.revenge if r.victim == victim and not r.done})

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
            # Players who broke a deal with me this game (I won't trust them again soon):
            "grudges": sorted(self.grudges.get(name, set())),
            # ...and the ones I haven't paid back yet: one real setback each.
            "revenge": self.revenge_owed(name),
        }

    # --- making deals --------------------------------------------------------------

    def offer_phase(self, state: dict, bots: dict[str, Any]) -> list[dict]:
        """Run the start-of-turn offer. Returns the deal messages that happened."""
        turn = state.get("turn", 0)
        offerer = state.get("active_player")
        kept = self.settle(turn) + self.end_finished(state)
        if turn <= self.last_offer_turn or offerer not in bots or offerer not in self.players:
            return kept
        self.last_offer_turn = turn
        return kept + self._offer(state, bots, turn, offerer)

    def _offer(self, state: dict, bots: dict[str, Any], turn: int, offerer: str) -> list[dict]:
        public = public_state(state, offerer)
        bot = bots[offerer]
        offer = bot.offer_deal({"kind": "offer_deal", "player": offerer, "state": public,
                                "menu": list(DEAL_TYPES), "lengths": list(DEAL_LENGTHS),
                                "deals": self.view_for(offerer, public)})
        if not offer or not self._valid_offer(offer, offerer, public):
            return []
        to = offer["to"]
        rounds = offer.get("rounds", DEAL_ROUNDS)
        # Game turns count every player's turn, so 2 rounds at a 4-player table is 8 turns.
        expires = FOREVER if rounds == UNTIL_OUT else turn + rounds * len(self.alive(public))
        deal = Deal(self._next_id, offer["type"], offerer, to, turn, expires, rounds=rounds,
                    against=offer.get("against"))
        messages = [deal_message("offer", deal, turn)]
        responder = bots.get(to)
        accepted = False
        if responder is not None:
            responder_state = public_state(state, to)
            accepted = bool(responder.answer_deal({
                "kind": "answer_deal", "player": to, "state": responder_state,
                "offer": deal.to_dict(), "deals": self.view_for(to, responder_state)}))
        bot.offer_answered(deal.to_dict(), accepted)  # the Boys pay a small fee per offer
        if accepted:
            self._next_id += 1
            # A new deal that goes against an old one breaks the old one.
            broken = self._contradictions(deal, turn)
            self.deals.append(deal)
            messages.append(deal_message("accept", deal, turn))
            messages += broken
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
        rounds = offer.get("rounds", DEAL_ROUNDS)
        if rounds not in DEAL_LENGTHS and not (rounds == UNTIL_OUT and offer["type"] == "gang_up"):
            return False
        if offer["type"] == "gang_up":
            against = offer.get("against")
            return against in alive and against not in (offerer, offer["to"])
        return True

    def _contradictions(self, new: Deal, turn: int) -> list[dict]:
        """Old deals the new one goes against. Each one counts as broken, right now.

        A deal means "I won't attack you" (and, for a gang-up, "I will attack X").
        So for each of the two new partners:
          * a gang-up on X breaks any deal they had WITH X (peace partner -> target),
          * a deal with Y breaks any gang-up they had AGAINST Y (that betrays
            whoever they were ganging up with).
        """
        messages = []
        for p in (new.a, new.b):
            q = new.other(p)
            for old in self.deals_of(p, turn):
                if new.kind == "gang_up" and old.involves(new.against):
                    messages.append(self._break(old, p, turn, "new_deal", f"by joining a gang-up on {new.against}"))
                elif old.kind == "gang_up" and old.against == q:
                    joined = "making peace" if new.kind == "peace" else "teaming up"
                    messages.append(self._break(old, p, turn, "new_deal", f"by {joined} with {q}"))
        return messages

    @staticmethod
    def alive(state: dict) -> list[str]:
        return [p["name"] for p in state.get("players", []) if not p.get("lost")]

    # --- keeping deals -----------------------------------------------------------------

    def settle(self, turn: int) -> list[dict]:
        """Reward deals that ran their full length unbroken. Returns "kept" messages."""
        messages = []
        for deal in self.deals:
            if deal.broken_by is None and deal.ended is None and not deal.settled and turn > deal.expires_turn:
                messages += self._kept(deal, turn)
        return messages

    def _kept(self, deal: Deal, turn: int) -> list[dict]:
        deal.settled = True
        if not deal.tested:
            return []  # nobody was ever in a position to break it: no trust earned
        self._gain_trust(deal.a, deal.b, KEPT_TRUST_GAIN)
        self._gain_trust(deal.b, deal.a, KEPT_TRUST_GAIN)
        return [deal_message("kept", deal, turn)]

    def end_finished(self, state: dict) -> list[dict]:
        """End deals that have nothing left to be about.

        * A partner is out of the game: the deal just ends.
        * A gang-up's target is out: the job is done (kept, if it was tested).
        * Only the two partners are left: no peace in the final two.
        """
        turn = state.get("turn", 0)
        alive = set(self.alive(state))
        if not alive:
            return []
        messages = []
        for deal in self.active(turn):
            gone = [p for p in (deal.a, deal.b) if p not in alive]
            if gone:
                deal.ended = f"{gone[0]} is out"
                deal.settled = True
            elif deal.against and deal.against not in alive:
                deal.ended = f"{deal.against} is out"
                kept = self._kept(deal, turn)
                messages += kept or [deal_message("end", deal, turn)]
            elif alive == {deal.a, deal.b}:
                deal.ended = "only the two of them are left"
                deal.settled = True
                messages.append(deal_message("end", deal, turn))
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
                messages.append(self._break(deal, attacker, turn, "attack"))
        return messages

    def _break(self, deal: Deal, breaker: str, turn: int, how: str, why: str | None = None) -> dict:
        """``breaker`` broke ``deal``: trust, grudge, one revenge owed. Returns the message."""
        victim = deal.other(breaker)
        deal.broken_by = breaker
        deal.broken_how = how
        self._lose_trust(breaker, victim)
        self.grudges.setdefault(victim, set()).add(breaker)
        if breaker not in self.revenge_owed(victim):
            self.revenge.append(Revenge(victim, breaker, deal))
        return deal_message("break", deal, turn, breaker=breaker, why=why)

    def _lose_trust(self, breaker: str, victim: str) -> None:
        for p, row in self.trust.items():
            if breaker in row:
                loss = VICTIM_TRUST_LOSS if p == victim else OTHERS_TRUST_LOSS
                row[breaker] = max(0.0, row[breaker] - loss)

    # --- revenge ---------------------------------------------------------------------
    # The game runner shows us the table at every decision (observe), every
    # line of Forge's log (see_log) and every target a bot picks (note_target).
    # From those we notice when a victim has really set their breaker back.

    def observe(self, state: dict) -> list[dict]:
        """A fresh look at the table. Returns "revenge" messages for revenge that's done."""
        turn, active = state.get("turn", 0), state.get("active_player")
        players = {p["name"]: p for p in state.get("players", [])}
        if turn != self._turn:
            self._turn, self._active = turn, active
            for r in self.revenge:
                r.damage = 0  # damage counts per game turn
                r.start_threat = threat.threat_score(players[r.breaker]) \
                    if r.victim == active and r.breaker in players else None
        self._cards = {c["id"]: {**c, "controller": c.get("controller", p["name"])}
                       for p in state.get("players", []) for c in p.get("battlefield", []) if "id" in c}
        messages = []
        for r in self.revenge:
            if r.done or r.start_threat is None or r.breaker not in players:
                continue
            if threat.threat_score(players[r.breaker]) <= (1 - REVENGE_THREAT_DROP) * r.start_threat:
                messages.append(self._revenge_done(r, f"{r.victim} knocked {r.breaker}'s board down"))
        return messages

    def note_target(self, player: str, decision: dict, choice: list[int] | None) -> None:
        """A bot aimed something at targets. Remember harm aimed at a breaker's cards."""
        owed = self.revenge_owed(player)
        if not owed or decision.get("kind") != "choose_targets":
            return
        from showdown.boys.features import is_harmful  # here, not at the top: features imports a lot
        if not is_harmful(decision):
            return
        picked = choice if choice is not None else decision.get("suggested", [])
        for cand in decision.get("candidates", []):
            if cand.get("index") in picked and cand.get("controller") in owed and "id" in cand:
                self._targeted.setdefault(player, set()).add(cand["id"])

    def see_log(self, line: str, controllers: dict[int, str]) -> list[dict]:
        """One line of Forge's log. Returns "revenge" messages for revenge that's done.

        ``controllers`` maps card id -> who controls it (DamageTracker keeps one).
        """
        owed = [r for r in self.revenge if not r.done]
        if not owed:
            return []
        messages = []
        m = DAMAGE_LINE.match(line)
        if m:
            dealer = controllers.get(int(m["id"])) or self._cards.get(int(m["id"]), {}).get("controller")
            for r in owed:
                if dealer == r.victim and m["target"] == r.breaker:
                    r.damage += int(m["amount"])
                    if r.damage >= REVENGE_DAMAGE:
                        messages.append(self._revenge_done(r, f"{r.victim} dealt {r.damage} damage to {r.breaker}"))
        m = ZONE_LINE.match(line)
        if m:
            card = self._cards.get(int(m["id"]))
            for r in owed:
                if card and card["controller"] == r.breaker and important(card) and \
                        (self._active == r.victim or card["id"] in self._targeted.get(r.victim, set())):
                    messages.append(self._revenge_done(r, f"{r.victim} removed {m['name']} from {r.breaker}"))
        return messages

    def _revenge_done(self, r: Revenge, what: str) -> dict:
        r.done = True
        return deal_message("revenge", r.deal, self._turn, victim=r.victim, breaker=r.breaker,
                            what=what)


def important(card: dict) -> bool:
    """Worth a revenge to remove: a commander, a big creature, or anything costly."""
    if card.get("commander"):
        return True
    if "Creature" in card.get("type", "") and (card.get("power") or 0) >= IMPORTANT_POWER:
        return True
    return "Land" not in card.get("type", "") and (card.get("cmc") or 0) >= IMPORTANT_COST


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
    """A message for the game log / viewer.

    event is offer, accept, decline, kept, end (nothing left to deal about),
    break, or revenge (a victim set their breaker back: revenge is over).
    """
    if event == "offer":
        text = f"{deal.a} offers {deal.b} a deal: {deal.describe()}"
    elif event == "accept":
        text = f"{deal.b} accepts {deal.a}'s deal ({deal.describe()})"
    elif event == "kept":
        text = f"{deal.a} and {deal.b} kept their deal ({deal.describe()}) and trust each other more"
    elif event == "end":
        text = f"{deal.a} and {deal.b}'s deal ({deal.describe()}) is over: {deal.ended}"
    elif event == "decline":
        reason = extra.get("reason")
        text = f"{deal.b} declines {deal.a}'s deal" + (f" ({reason})" if reason else "")
    elif event == "revenge":
        text = f"{extra['what']}. {extra['victim']} has taken revenge, and it's over."
    else:
        breaker = extra["breaker"]
        victim = deal.other(breaker)
        why = extra.get("why")
        if why:  # a new deal that went against this one
            what = "peace" if deal.kind == "peace" else f"gang-up on {deal.against}"
            text = (f"{breaker} BREAKS the {what} with {victim} {why}! "
                    f"{victim} swears revenge, and the table trusts {breaker} less.")
        else:
            text = (f"{breaker} BREAKS the deal with {victim} by attacking! "
                    f"{victim} swears revenge, and the table trusts {breaker} less.")
    return {"type": "deal", "event": event, "text": text, "turn": turn, "deal": deal.to_dict(),
            **{k: v for k, v in extra.items() if v is not None}}
