"""A tiny rule-based bot: the starting point The Boys will learn on top of.

Rules, in order:
  * Mulligan: keep a 7-card hand with 2 to 5 lands; after one mulligan,
    keep any hand with at least one land (even all lands).
  * With priority, in our own main phase:
      1. play a land if we can (a tapped land only when the extra mana
         wouldn't be used this turn anyway),
      2. otherwise cast the most expensive spell we can afford. Our
         commander wins ties, and goes first when our hand holds spells it
         powers up (cast Anhelo, then the instants and sorceries).
         Non-creature X spells wait until X is at least SMALL_X.
         Cards we can only play from exile for a while (impulse draws) go
         before cards in hand, which keep. A creature that's mostly its
         "when this enters" effect waits while that effect has no target,
         unless there's nothing else to cast.
      3. instants (and flash cards) wait: we keep the mana open.
  * Instant speed (see showdown/boys/timing.py), outside our main phases:
      - answer an opponent's spell or ability, in a combat (ours or theirs),
        when Forge's AI says the instant is worth it right now,
      - at the end of the turn right before ours, cast what we kept: the mana
        untaps next turn anyway. Only reactive cards (counter, pump, protect)
        still wait there unless Forge's AI likes them.
  * Attack with every creature whose power is at least 2 (small creatures
    stay home to block). Target: a player we can finish off this turn, or
    else the biggest threat (threat.threat_score), not just whoever is
    lowest on life: picking on the weakest player lets the leader run away.
  * Deals (when the game has them on): never break a deal. Attack a player
    who broke a deal with us first (revenge, until we've set them back once),
    then players we promised to gang up on, then as above.
      - Offer (from round 3 on, once the strongest opponent has 3+ power on
        the board): if someone else is the leader, ask the next most threatening
        opponent to gang up on the leader. If WE are the leader, offer peace
        to the strongest opponent.
      - Share: with a gift that comes back every turn (Generous Plunderer),
        announce a sharing plan and give to each opponent in turn.
      - Accept: anything from a player we still trust (trust 0.5 or more),
        unless they are the leader, or they'd gain clearly more from it than
        we do (threat.deal_gains).

Deliberately simple and easy to read. Improving these rules is a good
first Python exercise.
"""

from __future__ import annotations

from showdown.bots.base import Bot, Decision
from showdown.boys import colors, combat, commander, mana, removal, threat, timing
from showdown.boys.threat import game_round
from showdown.deals import recurring_gift_cards, removes

MAIN_PHASES = {"MAIN1", "MAIN2"}
EARLIEST_DEAL_ROUND = 3     # no deals in rounds 1-2
MEANINGFUL_BOARD_POWER = 3  # the leader needs at least this much power before a deal means anything
GAIN_MARGIN = 0.05          # decline a deal when the offerer gains this much more than we do (threat.deal_gains)
SMALL_X = 3                 # a non-creature X spell (Torment of Hailfire) waits until X is at least this


def is_ability(option: dict) -> bool:
    """An activated ability, like Evolving Wilds' "sacrifice: search for a land"."""
    return option.get("is_ability", not option["is_land"] and not option["is_spell"])


def spell_size(option: dict) -> int:
    """How much mana a spell would use. For X spells (Tervigon: {X}{2}{G}{G})
    the card's mana value counts X as 0, so add the biggest X we can pay."""
    return option["card"]["cmc"] + option.get("max_x", 0)


def risky_x_spell(option: dict) -> bool:
    """A non-creature X spell that's better saved for later: Forge's AI
    wouldn't cast it right now at any X, or X would still be small."""
    if "max_x" not in option or "Creature" in option["card"].get("type", ""):
        return False
    return not option.get("forge_would_play", True) or option["max_x"] < SMALL_X


def spell_priority(option: dict, hand: list[dict]) -> tuple:
    """How keen we are to cast this spell now. Python compares tuples item by
    item, so the first number matters most:
      1. our commander when it powers up spells in our hand (engine first),
      2. a card from exile, which we may only play for a short time,
      3. not a creature whose "when this enters" is wasted right now (a
         4-mana 2/2 that destroys nothing): anything else goes first,
      4. the mana it uses (bigger first),
      5. our commander wins a tie on mana.
    """
    card = option["card"]
    is_cmdr = bool(card.get("commander"))
    kinds = commander.engine_types(card) if is_cmdr else set()
    engine_first = bool(kinds) and any(commander.feeds(c, kinds) for c in hand if not c.get("commander"))
    wasted_etb = etb_mostly_wasted(option)
    return (engine_first, timing.from_exile(option), not wasted_etb, spell_size(option), is_cmdr)


def land_priority(option: dict, me: dict, options: list[dict] | None = None) -> tuple:
    """Which land to play: one from exile first (it won't stay playable), then
    not a tapped land when the extra mana would be used before my next turn,
    then not a bounce land that would make me discard, then a tapped land on
    a turn the extra mana wouldn't be used anyway (get it out of the way, so
    the untapped ones are ready when needed), then the land whose colors help
    most. Evolving Wilds and the like count as tapped lands."""
    fit = mana.land_fit(option["card"], me)
    full_hand = colors.returns_a_land(option["card"]) and options is not None and \
        full_hand_bounce(me, options)
    return (timing.from_exile(option), not fit.get("tapland_costs_a_play"), not full_hand,
            bool(fit.get("tapland_on_quiet_turn")), colors.land_value(option["card"], me))


def full_hand_bounce(me: dict, options: list[dict]) -> bool:
    """8+ cards in hand and another land to play: a bounce land would mean a discard."""
    other_land = any(o.get("is_land") and not colors.returns_a_land(o.get("card", {})) for o in options)
    return other_land and me.get("hand_size", len(me.get("hand", []))) > 7


def etb_mostly_wasted(option: dict) -> bool:
    """The bridge says this permanent's ETB has no target now, and the card is
    more ETB than body: at most half its mana value in (power+toughness)/2."""
    if not option.get("etb_no_target"):
        return False
    card = option["card"]
    body = (card.get("power", 0) + card.get("toughness", 0)) / 2
    return body * 2 <= card.get("cmc", 0)


class SimpleBot(Bot):
    name = "simple"

    def keep_hand(self, decision: Decision) -> bool:
        hand = self.me(decision).get("hand", [])
        lands = sum(1 for card in hand if "Land" in card["type"])
        # London mulligan: every new hand has 7 cards again (the extra ones go
        # to the bottom only after keeping), so the hand size can't tell us
        # whether we already mulliganed. game_runner counts it for us instead.
        if decision.get("mulligans_so_far", 0) > 0:
            # Already mulliganed once: a hand with no lands can't play, so throw
            # it back; anything else (even all lands) beats going down a card.
            return lands > 0
        return 2 <= lands <= 5

    def choose_play(self, decision: Decision) -> int | None:
        state = decision["state"]
        my_turn = state["active_player"] == state["you"]
        if not my_turn or state["phase"] not in MAIN_PHASES or state["stack_size"] > 0:
            return self.instant_speed_play(decision)

        options = decision["options"]
        # A bounce land as our only land would just return to our hand: skip it.
        lands = [o for o in options if o["is_land"] and not colors.wasted_land_drop(o["card"], self.me(decision))]
        if lands:
            # A land from exile first (it won't stay playable), then the land
            # whose colors help most (a first Swamp for a hand of black
            # spells). max() keeps the first one on a tie.
            me = self.me(decision)
            return max(lands, key=lambda o: land_priority(o, me, options))["index"]

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
        spells = [o for o in options if o["is_spell"] and not risky_x_spell(o)
                  and not self.keep_for_later(o, decision)]
        if spells:
            # max(..., key=...) picks the option with the highest priority.
            hand = self.me(decision).get("hand", [])
            best = max(spells, key=lambda o: spell_priority(o, hand))
            return best["index"]
        # Nothing left to cast: use other abilities with the mana that's left.
        if abilities:
            return abilities[0]["index"]
        return None

    def keep_for_later(self, option: dict, decision: Decision) -> bool:
        """Hold an instant in our main phase: it can wait for a better moment.
        Not when it's from exile (it may run out) or we'd discard it at the end
        of the turn anyway."""
        state = decision["state"]
        if removal.removal_features(option, state, True, removes).get("removal_ward_unpayable"):
            return True  # the creature we'd remove has ward we can't pay yet: wait
        wipe = removal.wipe_features(option, state, decision.get("deals", {}))
        if wipe and (wipe.get("wipe_my_share", 0) >= 0.5 or wipe.get("wipe_hits_nothing")):
            return True  # a board wipe that hurts us at least as much as everyone else together
        if not timing.instant_speed(option) or timing.from_exile(option):
            return False
        too_many = state["phase"] == "MAIN2" and self.me(decision).get("hand_size", 0) > 7
        return not too_many

    def instant_speed_play(self, decision: Decision) -> int | None:
        """Not our main phase (or something is on the stack): an instant, or pass."""
        state = decision["state"]
        quick = [o for o in decision["options"] if timing.instant_speed(o) and not risky_x_spell(o)]
        if not quick:
            return None
        if state["stack_size"] > 0:
            # Only answer opponents (not our own spells), and only when Forge's AI sees a point.
            picks = [o for o in quick if o.get("forge_would_play")] if timing.opponent_on_stack(state) else []
        elif combat.view(state).in_trick_window():
            picks = [o for o in quick if o.get("forge_would_play")]
        elif timing.end_step_before_mine(state):
            picks = [o for o in quick if o.get("forge_would_play") or not timing.reactive(o)]
        else:
            return None
        if not picks:
            return None
        return max(picks, key=spell_size)["index"]

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
        revenge = set(deals.get("revenge", []))            # broke a deal with us, not paid back yet
        push_back = set(deals.get("push_back", []))        # ganging up on us, not pushed back yet
        sharers = set(deals.get("sharing_with_me", []))     # share their gifts with us: be nice
        # How much we hit for if everything big attacks one player.
        power = sum(c.get("power", 0) for c in decision["attackers"] if c.get("power", 0) >= 2)
        # Defenders that are players: revenge first (one real setback, then
        # it's over: showdown/deals.py notices), then players ganging up on
        # us (push back, same idea), then gang-up targets, then
        # a player we can knock out this turn, then the biggest threat, and
        # lowest life only to break a tie. (Sorting by a tuple compares the
        # first item, then the next; False sorts before True, and the minus
        # sign puts the highest threat first.) Players we have a deal with are
        # left out, so we never break a deal.
        targets = sorted(
            (d for d in decision["defenders"] if d["is_player"] and d["name"] not in protected),
            key=lambda d: (d["name"] not in revenge, d["name"] not in push_back, d["name"] not in gang_targets,
                           d["name"] in sharers,
                           not threat.can_kill(players[d["name"]], power),
                           -threat.threat_score(players[d["name"]]),
                           players[d["name"]]["life"]),
        )
        # Don't walk into a blocker that eats the attacker and lives, unless:
        # the attack can finish that player, we promised it (revenge, push
        # back, gang-up), or we bring more attackers than they have blockers
        # (one blocker can only stop one attacker). If every target has such
        # a blocker, the creature stays home.
        attackers = sum(1 for c in decision["attackers"] if c.get("power", 0) >= 2)
        promised = revenge | push_back | gang_targets

        def safe_enough(creature: dict, name: str) -> bool:
            player = players[name]
            if name in promised or threat.can_kill(player, power):
                return True
            if attackers > len(threat.untapped_blockers(player)):
                return True
            return not threat.killer_blockers(player, creature.get("power", 0), creature.get("toughness", 0), creature)

        attacks: dict[str, int] = {}
        for creature in decision["attackers"]:
            if creature.get("power", 0) < 2:
                continue
            for target in targets:
                if target["index"] in creature["can_attack"] and safe_enough(creature, target["name"]):
                    attacks[str(creature["id"])] = target["index"]
                    break
        return attacks

    def choose_targets(self, decision: Decision) -> list[int] | None:
        """Gifts to an opponent: follow our sharing plan. Harm: Forge's pick,
        unless it hits a deal partner (that breaks the deal) or has ward we
        can't pay (the spell would be countered); then the best other target."""
        from showdown.boys.features import is_harmful  # features imports this module, so import late
        cands = decision.get("candidates", [])
        if not cands:
            return None
        if is_harmful(decision):
            return self.harm_target(decision)
        if not all(c.get("is_player") for c in cands):
            return None
        planned = decision.get("deals", {}).get("share_next")
        pick = next((c["index"] for c in cands if c["name"] == planned), None)
        return None if pick is None else [pick]

    def harm_target(self, decision: Decision) -> list[int] | None:
        if decision.get("max", 1) != 1:
            return None  # several targets at once: Forge's pick
        me = self.me(decision)
        you = decision["state"]["you"]
        partners = set(decision.get("deals", {}).get("protected", []))
        cost = removal.cost_still_to_pay(decision, me)

        def owner(c: dict) -> str:
            return c["name"] if c.get("is_player") else c.get("controller", "")

        def bad(c: dict) -> bool:
            return owner(c) in partners or (not c.get("is_player") and owner(c) != you
                                            and removal.ward_unpayable(c, me, cost))

        cands = decision["candidates"]
        suggested = [c for c in cands if c["index"] in decision.get("suggested", [])]
        if suggested and not any(bad(c) for c in suggested):
            return None  # Forge's pick is fine
        players = {p["name"]: p for p in decision["state"]["players"]}
        # Same kind of target Forge picked (a creature, a player...), an opponent's, not a bad one.
        kind = (suggested[0].get("is_player", False)) if suggested else None
        good = [c for c in cands if owner(c) not in (you, "") and not bad(c)
                and (kind is None or c.get("is_player", False) == kind)]
        if not good:
            return None
        best = max(good, key=lambda c: (threat.threat_score(players.get(owner(c), {})),
                                        c.get("power", 0) or 0, c.get("cmc", 0) or 0))
        return [best["index"]]

    # --- deals ----------------------------------------------------------------

    def offer_deal(self, view: Decision) -> dict | None:
        """Offer at most one deal, and only once boards matter.

        Peace in round 2, with no creatures out, costs nobody anything. Worse,
        a pointless deal teaches the Boys (who play against SimpleBot) that
        deals are just something you spam early.
        """
        state = view["state"]
        opponents = threat.opponents(state)
        if len(opponents) < 2:
            return None
        if not view["deals"].get("sharing") and recurring_gift_cards(threat.me(state)):
            return {"type": "share"}  # a gift that comes back every turn: hand it out in turn, nobody gets salty
        if view["deals"]["active"]:
            return None  # one deal at a time is plenty for a simple bot
        if game_round(state) < EARLIEST_DEAL_ROUND:
            return None  # early game: nobody knows who's ahead yet, and peace is free
        ranked = sorted(opponents, key=threat.threat_score, reverse=True)
        leader = ranked[0]
        if threat.board_power(leader) < MEANINGFUL_BOARD_POWER:
            return None  # the strongest opponent can barely hit anyone: a deal would mean nothing
        if threat.threat_score(threat.me(state)) > threat.threat_score(leader):
            if "peace" not in view.get("menu", ["peace"]):
                return None  # peace deals are on ice (deals.py)
            return {"type": "peace", "to": leader["name"], "rounds": 2}  # we're ahead: keep the strongest off us
        return {"type": "gang_up", "to": ranked[1]["name"], "against": leader["name"], "rounds": 2}

    def answer_deal(self, view: Decision) -> bool:
        offerer = view["offer"]["a"]
        trust = view["deals"]["trust"].get(offerer, 1.0)
        if offerer in view["deals"].get("grudges", []):
            return False  # you broke a deal with us. No.
        if trust < 0.5 or threat.leader(view["state"]) == offerer:
            return False
        offer = view["offer"]
        if offer.get("kind") == "gang_up" and threat.punching_down(view["state"], offerer, offer.get("against", "")):
            return False  # ganging up on the weakest player: not how we play
        # Who gains more? Say no when they'd gain clearly more than we do.
        my_gain, their_gain = threat.deal_gains(view["state"], offerer)
        return their_gain <= my_gain + GAIN_MARGIN
