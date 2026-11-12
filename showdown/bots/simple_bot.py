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
      - Offer (from round 3 on, once the strongest opponent has 3+ power on
        the board): if someone else is the leader, ask the next most threatening
        opponent to gang up on the leader. If WE are the leader, offer peace
        to the strongest opponent.
      - Accept: anything from a player we still trust (trust 0.5 or more),
        unless they are the leader.

Deliberately simple and easy to read. Improving these rules is a good
first Python exercise.
"""

from __future__ import annotations

from showdown.bots.base import Bot, Decision
from showdown.boys import colors, threat
from showdown.boys.threat import game_round

MAIN_PHASES = {"MAIN1", "MAIN2"}
EARLIEST_DEAL_ROUND = 3     # no deals in rounds 1-2
MEANINGFUL_BOARD_POWER = 3  # the leader needs at least this much power before a deal means anything


def is_ability(option: dict) -> bool:
    """An activated ability, like Evolving Wilds' "sacrifice: search for a land"."""
    return option.get("is_ability", not option["is_land"] and not option["is_spell"])


def spell_size(option: dict) -> int:
    """How much mana a spell would use. For X spells (Tervigon: {X}{2}{G}{G})
    the card's mana value counts X as 0, so add the biggest X we can pay."""
    return option["card"]["cmc"] + option.get("max_x", 0)


def risky_x_spell(option: dict) -> bool:
    """A non-creature X spell that Forge's AI wouldn't cast right now at any X."""
    return "max_x" in option and "Creature" not in option["card"].get("type", "") \
        and not option.get("forge_would_play", True)


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
            # The land whose colors help most (a first Swamp for a hand of
            # black spells). max() keeps the first one on a tie.
            me = self.me(decision)
            return max(lands, key=lambda o: colors.land_value(o["card"], me))["index"]

        # Abilities (not spells, not land drops) that Forge's own AI would use
        # right now. "forge_would_play" is Forge's hint: it knows when an
        # ability is worth it, which a simple bot can't work out itself.
        abilities = [o for o in options if is_ability(o) and o.get("forge_would_play")]
        # Abilities of lands first, e.g. Evolving Wilds fetching a land: more mana for spells later.
        land_abilities = [o for o in abilities if "Land" in o["card"].get("type", "")]
        if land_abilities:
            return land_abilities[0]["index"]

        # X spells that aren't creatures (Starstorm: X damage to every creature)
        # only when Forge's AI sees a good X; otherwise they'd hurt us.
        spells = [o for o in options if o["is_spell"] and not risky_x_spell(o)]
        if spells:
            # max(..., key=...) picks the option with the biggest mana value.
            best = max(spells, key=spell_size)
            return best["index"]
        # Nothing left to cast: use other abilities with the mana that's left.
        if abilities:
            return abilities[0]["index"]
        return None

    def pay_keyword_cost(self, decision: Decision) -> int | None:
        """Pay extra costs like Forge's AI does, but never sacrifice our commander."""
        sacrifice = decision.get("sacrifice_candidates", [])
        if sacrifice and all(c.get("commander") for c in sacrifice):
            return 0
        return None

    def choose_card(self, decision: Decision) -> int | None:
        """Pick cards like Forge's AI does, but when the pick is a land, take
        the land whose colors my cards need most (see colors.land_value).
        Works for any card that lets you pick lands, not a list of names."""
        candidates = decision["candidates"]
        lands = [c for c in candidates if colors.is_land(c)]
        suggested = decision.get("suggested", -1)
        picking_a_land = lands and (len(lands) == len(candidates) or
                                    any(c["index"] == suggested for c in lands))
        if not picking_a_land:
            return None  # a spell: Forge's pick is fine
        me = self.me(decision)
        best = max(lands, key=lambda c: colors.land_value(c, me))  # first one on a tie
        if colors.land_value(best, me) == 0:
            return None  # no color matters right now
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
        """Offer at most one deal, and only once boards matter.

        Peace in round 2, with no creatures out, costs nobody anything. Worse,
        a pointless deal teaches the Boys (who play against SimpleBot) that
        deals are just something you spam early.
        """
        state = view["state"]
        opponents = threat.opponents(state)
        if len(opponents) < 2 or view["deals"]["active"]:
            return None  # one deal at a time is plenty for a simple bot
        if game_round(state) < EARLIEST_DEAL_ROUND:
            return None  # early game: nobody knows who's ahead yet, and peace is free
        ranked = sorted(opponents, key=threat.threat_score, reverse=True)
        leader = ranked[0]
        if threat.board_power(leader) < MEANINGFUL_BOARD_POWER:
            return None  # the strongest opponent can barely hit anyone: a deal would mean nothing
        if threat.threat_score(threat.me(state)) > threat.threat_score(leader):
            return {"type": "peace", "to": leader["name"]}  # we're ahead: keep the strongest off us
        return {"type": "gang_up", "to": ranked[1]["name"], "against": leader["name"]}

    def answer_deal(self, view: Decision) -> bool:
        offerer = view["offer"]["a"]
        trust = view["deals"]["trust"].get(offerer, 1.0)
        if offerer in view["deals"].get("grudges", []):
            return False  # you broke a deal with us. No.
        return trust >= 0.5 and threat.leader(view["state"]) != offerer
