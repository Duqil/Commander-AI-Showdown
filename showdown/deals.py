"""Deals between bots: offers, answers, keeping track, and trust.

Forge knows nothing about deals. They live entirely in Python, here.

The menu:
    peace      "Let's not attack each other for N rounds."
    gang_up    "Let's both attack <X> for N rounds." (and not each other)
    removal    "I'll remove something of <X>'s. In return: peace for N rounds."
    gift       "I'll give you something (a card, a Treasure...). In return: peace for N rounds."
The offerer picks N: 1 to 4 rounds, and a gang-up can also last "until <X>
is out" (rounds = 0). A deal also ends when one of its players is out, or
when the two partners are the only players left.

Removal and gift are favours: the offerer has 1 round to pay (aim something
harmful at one of X's cards, or aim something helpful at the partner). Not
paying in time counts as breaking the deal. Only offered by a bot that
holds a card that can pay (text like "destroy target", or "target opponent
draws/creates..."): no card names anywhere.

Sharing plan (not a 2-player deal): a player with a gift that comes back
every turn (Generous Plunderer, Loran of the Third Path) can announce
"I'll share in turn: player 2, then 3, then 4." Each gift to the next one in
line builds a little trust; skipping someone costs trust with them.

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
DEAL_TYPES = ("peace", "gang_up", "removal", "gift")
FAVOURS = ("removal", "gift")   # deals where the offerer pays first (see "Favours" below)
PAY_ROUNDS = 1                  # a favour must be paid within this many rounds of the deal
SHARE_TRUST_GAIN = 0.05         # you got your turn in someone's sharing plan
SKIP_TRUST_LOSS = 0.1           # someone else got your turn in the sharing plan

# What a card can do, read from its text (any card, no names): remove an
# opponent's thing, or give an opponent something.
REMOVAL_TEXT = re.compile(r"(destroy|exile|return) (up to \w+ )?target (\w+ )*(creature|permanent|artifact|"
                          r"enchantment|planeswalker|nonland)|deals? (\w+|x) damage to (any )?target",
                          re.IGNORECASE)
GIFT_TEXT = re.compile(r"target (opponent|player) (each )?(creates?|draws?|gains? (?!control)|puts?)|"
                       r"you and target (opponent|player) each (draw|create|gain)", re.IGNORECASE)

# Revenge: what counts as really setting the breaker back. Only the victim's
# own actions count. Any one of these and the revenge is done:
REVENGE_DAMAGE = 5            # the victim dealt the breaker this much damage in one game turn,
REVENGE_THREAT_DROP = 0.2     # or the breaker's threat score fell by a fifth during the victim's turn,
IMPORTANT_POWER = 3           # or the victim removed something important: their commander,
IMPORTANT_COST = 3            # a creature with 3+ power, or any non-land costing 3+ mana.
ZONE_LINE = re.compile(r"^(?:Zone Change: )?(?P<name>.+?) \((?P<id>\d+)\) was put into (?P<zone>\w+) from Battlefield\.$")
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
    paid: bool = True          # a favour (removal, gift) is unpaid until the offerer pays
    pay_by: int = 0            # ...and must be paid by this game turn
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
        if self.kind == "removal":
            return f"{self.a} removes something of {self.against}'s, for peace {self.length()}"
        if self.kind == "gift":
            return f"{self.a} gives {self.b} a gift, for peace {self.length()}"
        return f"peace {self.length()}"

    def aims_at(self) -> str | None:
        """Who this deal promises to hurt: a gang-up's target, or an unpaid removal's."""
        if self.kind == "gang_up" or (self.kind == "removal" and not self.paid):
            return self.against
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "a": self.a, "b": self.b, "against": self.against,
                "made_turn": self.made_turn, "expires_turn": self.expires_turn, "rounds": self.rounds,
                "broken_how": self.broken_how, "paid": self.paid}


@dataclass
class SharePlan:
    """A public promise to hand out a recurring gift in turn."""
    giver: str
    order: list[str]           # who gets the next gifts, in turn
    source: str                # the card that gives ("Generous Plunderer")
    next: int = 0              # index into order: who's next
    over: bool = False

    def expected(self, alive: set[str]) -> str | None:
        """Who should get the next gift (skipping players who are out)."""
        for i in range(len(self.order)):
            name = self.order[(self.next + i) % len(self.order)]
            if name in alive:
                return name
        return None

    def gave(self, name: str) -> None:
        """The gift went to ``name``: next in line is whoever comes after them."""
        if name in self.order:
            self.next = (self.order.index(name) + 1) % len(self.order)


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
    shares: list[SharePlan] = field(default_factory=list)             # sharing plans
    _alive: set[str] = field(default_factory=set)
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
            # Favours I promised and haven't paid yet (removal on X, or a gift to my partner):
            "owed": [{"kind": d.kind, "to": d.b, "against": d.against} for d in self.deals_of(name, turn)
                     if d.kind in FAVOURS and not d.paid and d.a == name],
            # My sharing plan: who should get my next gift (None without a plan).
            "share_next": self._share_next(name),
            "sharing": any(sp.giver == name and not sp.over for sp in self.shares),
            # Players whose sharing plan includes me (attacking them is rude).
            "sharing_with_me": sorted({sp.giver for sp in self.shares if not sp.over and name in sp.order}),
        }

    def _share_next(self, giver: str) -> str | None:
        plan = next((sp for sp in self.shares if sp.giver == giver and not sp.over), None)
        return plan.expected(self._alive or set(self.players)) if plan else None

    # --- making deals --------------------------------------------------------------

    def offer_phase(self, state: dict, bots: dict[str, Any]) -> list[dict]:
        """Run the start-of-turn offer. Returns the deal messages that happened."""
        turn = state.get("turn", 0)
        offerer = state.get("active_player")
        self._alive = set(self.alive(state)) or self._alive
        kept = self.settle(turn) + self.end_finished(state) + self._unpaid(turn)
        if turn <= self.last_offer_turn or offerer not in bots or offerer not in self.players:
            return kept
        if len(self.alive(state)) <= 2:
            return kept  # the final two: no more deals, just play it out
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
        if offer["type"] == "share":
            bot.offer_answered({"id": 0, "kind": "share"}, True)  # nobody has to say yes
            return [self._start_share(offerer, public, turn)]
        to = offer["to"]
        rounds = offer.get("rounds", DEAL_ROUNDS)
        # Game turns count every player's turn, so 2 rounds at a 4-player table is 8 turns.
        expires = FOREVER if rounds == UNTIL_OUT else turn + rounds * len(self.alive(public))
        deal = Deal(self._next_id, offer["type"], offerer, to, turn, expires, rounds=rounds,
                    against=offer.get("against"))
        if deal.kind in FAVOURS:
            deal.paid = False
            deal.pay_by = turn + PAY_ROUNDS * len(self.alive(public))
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
        me = next((p for p in state.get("players", []) if p["name"] == offerer), {})
        if offer.get("type") == "share":
            return bool(recurring_gift_cards(me)) and not any(sp.giver == offerer and not sp.over
                                                              for sp in self.shares)
        if offer.get("type") not in DEAL_TYPES or offer.get("to") not in alive or offer["to"] == offerer:
            return False
        if offer["type"] == "removal" and not removal_cards(me):
            return False  # can't pay: nothing that removes
        if offer["type"] == "gift" and not gift_cards(me):
            return False  # can't pay: nothing that gives
        if self.has_deal(offerer, offer["to"], state.get("turn", 0)):
            return False  # already have one
        rounds = offer.get("rounds", DEAL_ROUNDS)
        if rounds not in DEAL_LENGTHS and not (rounds == UNTIL_OUT and offer["type"] == "gang_up"):
            return False
        if offer["type"] in ("gang_up", "removal"):
            against = offer.get("against")
            return against in alive and against not in (offerer, offer["to"])
        return True

    def _contradictions(self, new: Deal, turn: int) -> list[dict]:
        """Old deals the new one goes against. Each one counts as broken, right now.

        A deal means "I won't attack you" (and, for a gang-up, "I will attack X").
        So for each of the two new partners:
          * a gang-up (or removal) on X breaks any deal they had WITH X,
          * a deal with Y breaks any gang-up (or unpaid removal) they had
            AGAINST Y (that betrays whoever they were ganging up with).
        """
        messages = []
        for p in (new.a, new.b):
            q = new.other(p)
            for old in self.deals_of(p, turn):
                if new.aims_at() and old.involves(new.aims_at()):
                    how = "joining a gang-up on" if new.kind == "gang_up" else "promising a removal on"
                    messages.append(self._break(old, p, turn, "new_deal", f"by {how} {new.against}"))
                elif old.aims_at() == q:
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
            elif deal.aims_at() and deal.aims_at() not in alive:
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
        for plan in self.shares:
            giver = players.get(plan.giver)
            if not plan.over and (giver is None or giver.get("lost") or not recurring_gift_cards(giver)):
                plan.over = True  # the gift is gone (or the giver is): no more sharing
        messages = []
        for r in self.revenge:
            if r.done or r.start_threat is None or r.breaker not in players:
                continue
            if threat.threat_score(players[r.breaker]) <= (1 - REVENGE_THREAT_DROP) * r.start_threat:
                messages.append(self._revenge_done(r, f"{r.victim} knocked {r.breaker}'s board down"))
        return messages

    def note_target(self, player: str, decision: dict, choice: list[int] | None) -> list[dict]:
        """A bot aimed something at targets. Returns deal messages (a favour paid, a gift shared).

        Harm aimed at a deal-breaker's cards counts toward revenge (see_log
        notices when the card is gone). Harm aimed at X's cards pays a removal
        favour; help aimed at an opponent pays a gift favour and counts for a
        sharing plan.
        """
        if decision.get("kind") != "choose_targets":
            return []
        from showdown.boys.features import is_harmful  # here, not at the top: features imports a lot
        harmful = is_harmful(decision)
        picked_ids = choice if choice is not None else decision.get("suggested", [])
        picked = [c for c in decision.get("candidates", []) if c.get("index") in picked_ids]
        turn = decision.get("state", {}).get("turn", self._turn)
        messages: list[dict] = []
        owed = self.revenge_owed(player)
        for cand in picked:
            owner = cand["name"] if cand.get("is_player") else cand.get("controller")
            if harmful and not cand.get("is_player") and owner in owed and "id" in cand:
                self._targeted.setdefault(player, set()).add(cand["id"])
            if harmful and not cand.get("is_player"):
                for deal in self._unpaid_of(player, "removal", turn):
                    if owner == deal.against:
                        source = decision.get("source", {}).get("name", "removal")
                        messages.append(self._paid(deal, turn, f"{player} aims {source} at {owner}'s "
                                                               f"{cand.get('name', 'card')} for {deal.b}, as promised"))
            if not harmful and cand.get("is_player") and owner != player:
                source = decision.get("source", {}).get("name", "a gift")
                paid_gift = False
                for deal in self._unpaid_of(player, "gift", turn):
                    if owner == deal.b:
                        messages.append(self._paid(deal, turn, f"{player} gives {owner} a gift ({source}), as promised"))
                        paid_gift = True
                messages += self._shared(player, owner, turn, source, paid_gift)
        return messages

    def _unpaid_of(self, payer: str, kind: str, turn: int) -> list[Deal]:
        return [d for d in self.deals_of(payer, turn) if d.kind == kind and not d.paid and d.a == payer]

    def _paid(self, deal: Deal, turn: int, text: str) -> dict:
        deal.paid = True
        self._gain_trust(deal.b, deal.a, KEPT_TRUST_GAIN)  # they did what they said
        return deal_message("paid", deal, turn, what=text)

    def _unpaid(self, turn: int) -> list[dict]:
        """Favours not paid in time count as broken deals."""
        messages = []
        for deal in self.active(turn):
            if deal.kind in FAVOURS and not deal.paid and turn > deal.pay_by:
                what = f"removal on {deal.against}" if deal.kind == "removal" else "gift"
                messages.append(self._break(deal, deal.a, turn, "unpaid", f"by never paying (the promised {what} never came)"))
        return messages

    # --- sharing plans ---------------------------------------------------------------

    def _start_share(self, giver: str, state: dict, turn: int) -> dict:
        """Announce a sharing plan: opponents in seat order, starting after the giver."""
        names = [p for p in self.players if p in self.alive(state)]
        i = names.index(giver) if giver in names else 0
        order = [n for n in names[i + 1:] + names[:i] if n != giver]
        me = next((p for p in state.get("players", []) if p["name"] == giver), {})
        source = recurring_gift_cards(me)[0].get("name", "gifts")
        plan = SharePlan(giver, order, source)
        self.shares.append(plan)
        text = f"{giver} will share {source} in turn: " + ", then ".join(order)
        return share_message("share", plan, turn, text)

    def _shared(self, giver: str, to: str, turn: int, source: str, paying: bool) -> list[dict]:
        plan = next((sp for sp in self.shares if sp.giver == giver and not sp.over), None)
        if plan is None or to not in plan.order:
            return []
        expected = plan.expected(self._alive or set(self.players))
        plan.gave(to)
        if to == expected:
            self._gain_trust(to, giver, SHARE_TRUST_GAIN)
            return [share_message("shared", plan, turn, f"{giver} shares {source} with {to}, as planned", to=to)]
        if expected is None:
            return []
        self._lose_trust_one(expected, giver, SKIP_TRUST_LOSS)
        why = " to pay for a deal" if paying else ""
        return [share_message("skipped", plan, turn,
                              f"{giver} gives {source} to {to}{why} instead of {expected}: "
                              f"{expected} was skipped and trusts {giver} less", to=to, skipped=expected)]

    def _lose_trust_one(self, me: str, them: str, amount: float) -> None:
        row = self.trust.get(me, {})
        if them in row:
            row[them] = max(0.0, row[them] - amount)

    def see_log(self, line: str, controllers: dict[int, str]) -> list[dict]:
        """One line of Forge's log. Returns "revenge" messages for revenge that's done.

        ``controllers`` maps card id -> who controls it (DamageTracker keeps one).
        """
        owed = [r for r in self.revenge if not r.done]
        messages: list[dict] = []
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
            # A removal favour paid on the payer's own turn (a wipe, a fight, combat...).
            for deal in self._unpaid_of(self._active or "", "removal", self._turn) if card else []:
                if card["controller"] == deal.against and "Land" not in card.get("type", ""):
                    messages.append(self._paid(deal, self._turn, f"{deal.a} removed {card.get('name')} from "
                                                                 f"{deal.against} for {deal.b}, as promised"))
            for r in owed:
                if card and card["controller"] == r.breaker and important(card) and \
                        (self._active == r.victim or card["id"] in self._targeted.get(r.victim, set())):
                    messages.append(self._revenge_done(r, f"{r.victim} removed {card.get('name') or m['name']} from {r.breaker}"))
        return messages

    def _revenge_done(self, r: Revenge, what: str) -> dict:
        r.done = True
        return deal_message("revenge", r.deal, self._turn, victim=r.victim, breaker=r.breaker,
                            what=what)


def _text(card: dict) -> str:
    return card.get("oracle", "") or card.get("text", "")


def removes(text: str) -> bool:
    """Text that removes an opponent's thing: "destroy target creature", "deals 3 damage to any target".
    Not "exile target creature you control" (a blink) or "return target creature card" (from a graveyard)."""
    for m in REMOVAL_TEXT.finditer(text or ""):
        rest = text[m.end():m.end() + 20].lower()
        if not rest.startswith((" card", " you control", "s you control", " from your")):
            return True
    return False


def removal_cards(player: dict) -> list[dict]:
    """Cards in hand or on the battlefield whose text removes an opponent's thing."""
    cards = player.get("hand", []) + player.get("battlefield", [])
    return [c for c in cards if removes(_text(c))]


def gift_cards(player: dict) -> list[dict]:
    """Cards in hand or on the battlefield whose text gives an opponent something."""
    cards = player.get("hand", []) + player.get("battlefield", [])
    return [c for c in cards if GIFT_TEXT.search(_text(c))]


def recurring_gift_cards(player: dict) -> list[dict]:
    """Gifts that come back: on the battlefield (a trigger or an ability, not a one-off spell)."""
    return [c for c in player.get("battlefield", []) if GIFT_TEXT.search(_text(c))]


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


def share_message(event: str, plan: SharePlan, turn: int, text: str, **extra: Any) -> dict:
    """A sharing-plan message: share (announced), shared (as planned) or skipped."""
    return {"type": "deal", "event": event, "text": text, "turn": turn, "giver": plan.giver,
            "deal": {"id": 0, "kind": "share", "a": plan.giver, "b": extra.get("to") or plan.giver,
                     "order": plan.order, "source": plan.source},
            **{k: v for k, v in extra.items() if v is not None}}


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
    elif event == "paid":
        text = extra["what"]
    else:
        breaker = extra["breaker"]
        victim = deal.other(breaker)
        why = extra.get("why")
        if why:  # a new deal that went against this one
            what = {"peace": "peace", "gang_up": f"gang-up on {deal.against}"}.get(deal.kind, "deal")
            text = (f"{breaker} BREAKS the {what} with {victim} {why}! "
                    f"{victim} swears revenge, and the table trusts {breaker} less.")
        else:
            text = (f"{breaker} BREAKS the deal with {victim} by attacking! "
                    f"{victim} swears revenge, and the table trusts {breaker} less.")
    return {"type": "deal", "event": event, "text": text, "turn": turn, "deal": deal.to_dict(),
            **{k: v for k, v in extra.items() if v is not None}}
