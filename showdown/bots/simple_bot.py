"""A tiny rule-based bot: the starting point The Boys will learn on top of.

Rules, in order:
  * Mulligan: keep a 7-card hand with 2 to 5 lands; keep anything smaller.
  * With priority, in our own main phase:
      1. play a land if we can,
      2. otherwise cast the most expensive spell we can afford.
    Outside our main phases we pass (no instant-speed tricks yet).
  * Attack the opponent with the lowest life, with every creature whose
    power is at least 2 (small creatures stay home to block).
  * Deals (when the game has them on): never break a deal. Attack a player
    who broke a deal with us first (revenge!), then players we promised to
    gang up on, then the weakest.
      - Offer (from round 2 on): if someone else is the leader, ask the next most threatening
        opponent to gang up on the leader. If WE are the leader, offer peace
        to the strongest opponent.
      - Accept: anything from a player we still trust (trust 0.5 or more),
        unless they are the leader.

Deliberately simple and easy to read. Improving these rules is a good
first Python exercise.
"""

from __future__ import annotations

from showdown.bots.base import Bot, Decision
from showdown.boys import threat

MAIN_PHASES = {"MAIN1", "MAIN2"}


class SimpleBot(Bot):
    name = "simple"

    def keep_hand(self, decision: Decision) -> bool:
        hand = self.me(decision).get("hand", [])
        lands = sum(1 for card in hand if "Land" in card["type"])
        if len(hand) < 7:
            return True  # already mulliganed once; don't go lower
        return 2 <= lands <= 5

    def choose_play(self, decision: Decision) -> int | None:
        state = decision["state"]
        my_turn = state["active_player"] == state["you"]
        if not my_turn or state["phase"] not in MAIN_PHASES or state["stack_size"] > 0:
            return None

        options = decision["options"]
        lands = [o for o in options if o["is_land"]]
        if lands:
            return lands[0]["index"]

        spells = [o for o in options if o["is_spell"]]
        if not spells:
            return None
        # max(..., key=...) picks the option with the highest mana value.
        best = max(spells, key=lambda o: o["card"]["cmc"])
        return best["index"]

    def declare_attackers(self, decision: Decision) -> dict[str, int]:
        players = {p["name"]: p for p in decision["state"]["players"]}
        deals = decision.get("deals", {})
        protected = set(deals.get("protected", []))       # players we have a deal with
        gang_targets = set(deals.get("gang_targets", []))  # players we promised to attack
        grudges = set(deals.get("grudges", []))            # players who broke a deal with us
        # Defenders that are players: grudges first, then gang-up targets, then
        # weakest first. (Sorting by a tuple compares the first item, then the
        # next; False sorts before True.) Players we have a deal with are left
        # out, so we never break a deal.
        targets = sorted(
            (d for d in decision["defenders"] if d["is_player"] and d["name"] not in protected),
            key=lambda d: (d["name"] not in grudges, d["name"] not in gang_targets,
                           players[d["name"]]["life"]),
        )
        attacks: dict[str, int] = {}
        for creature in decision["attackers"]:
            if creature.get("power", 0) < 2:
                continue
            for target in targets:
                if target["index"] in creature["can_attack"]:
                    attacks[str(creature["id"])] = target["index"]
                    break
        return attacks

    # --- deals ----------------------------------------------------------------

    def offer_deal(self, view: Decision) -> dict | None:
        state = view["state"]
        you = state["you"]
        opponents = threat.opponents(state)
        if len(opponents) < 2 or view["deals"]["active"]:
            return None  # one deal at a time is plenty for a simple bot
        if state.get("turn", 0) <= len(state["players"]):
            return None  # first round: nobody knows who's ahead yet
        ranked = sorted(opponents, key=threat.threat_score, reverse=True)
        leader = ranked[0]
        if threat.threat_score(threat.me(state)) > threat.threat_score(leader):
            return {"type": "peace", "to": leader["name"]}  # we're ahead: keep the strongest off us
        return {"type": "gang_up", "to": ranked[1]["name"], "against": leader["name"]}

    def answer_deal(self, view: Decision) -> bool:
        offerer = view["offer"]["a"]
        trust = view["deals"]["trust"].get(offerer, 1.0)
        if offerer in view["deals"].get("grudges", []):
            return False  # you broke a deal with us. No.
        return trust >= 0.5 and threat.leader(view["state"]) != offerer
