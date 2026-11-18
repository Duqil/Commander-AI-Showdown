"""Tests for deals between bots (showdown/deals.py). No Forge needed."""

from __future__ import annotations

import unittest

from showdown.bots.base import Bot
from showdown.bots.simple_bot import SimpleBot
from showdown.deals import DealBook
from showdown.viewer.events import TimelineBuilder


def state(turn=5, active="1-A", you="1-A", lives=(30, 40, 20, 25)):
    names = ["1-A", "2-B", "3-C", "4-D"]
    return {"turn": turn, "active_player": active, "you": you, "phase": "MAIN1", "stack_size": 0,
            "players": [{"name": n, "life": l, "hand_size": 3, "battlefield": [], "hand": []}
                        for n, l in zip(names, lives)]}


class Offerer(Bot):
    def __init__(self, offer):
        self.offer = offer

    def offer_deal(self, view):
        return self.offer


class Accepter(Bot):
    def answer_deal(self, view):
        return True


def attack(player, turn=6):
    return {"kind": "attack", "player": player, "state": state(turn=turn, active=player, you=player),
            "defenders": [{"index": 0, "name": "1-A"}, {"index": 1, "name": "2-B"},
                          {"index": 2, "name": "3-C"}, {"index": 3, "name": "4-D"}],
            "attackers": [{"id": 7, "power": 2, "can_attack": [0, 1, 2, 3]}]}


class DealBookTest(unittest.TestCase):
    def setUp(self):
        self.book = DealBook(["1-A", "2-B", "3-C", "4-D"])

    def test_offer_accepted(self):
        bots = {"1-A": Offerer({"type": "peace", "to": "2-B"}), "2-B": Accepter()}
        msgs = self.book.offer_phase(state(), bots)
        self.assertEqual([m["event"] for m in msgs], ["offer", "accept"])
        self.assertTrue(self.book.has_deal("1-A", "2-B", 6))
        self.assertEqual(self.book.view_for("2-B", state())["protected"], ["1-A"])

    def test_only_one_offer_per_turn(self):
        bots = {"1-A": Offerer({"type": "peace", "to": "2-B"}), "2-B": Accepter()}
        self.book.offer_phase(state(), bots)
        self.assertEqual(self.book.offer_phase(state(), bots), [])

    def test_forge_seats_decline(self):
        bots = {"1-A": Offerer({"type": "peace", "to": "3-C"})}  # 3-C is Forge's AI
        msgs = self.book.offer_phase(state(), bots)
        self.assertEqual(msgs[-1]["event"], "decline")
        self.assertFalse(self.book.has_deal("1-A", "3-C", 6))

    def test_invalid_offers_are_ignored(self):
        bots = {"1-A": Offerer({"type": "gang_up", "to": "2-B", "against": "1-A"}), "2-B": Accepter()}
        self.assertEqual(self.book.offer_phase(state(), bots), [])

    def test_breaking_a_deal_costs_trust(self):
        self.book.offer_phase(state(), {"1-A": Offerer({"type": "peace", "to": "2-B"}), "2-B": Accepter()})
        msgs = self.book.check_attack("2-B", attack("2-B"), {"7": 0})  # B attacks A
        self.assertEqual(msgs[0]["event"], "break")
        self.assertEqual(self.book.trust["1-A"]["2-B"], 0.5)   # the victim
        self.assertEqual(self.book.trust["3-C"]["2-B"], 0.75)  # everyone else
        self.assertEqual(self.book.view_for("1-A", state())["grudges"], ["2-B"])  # revenge
        self.assertFalse(self.book.has_deal("1-A", "2-B", 6))

    def test_deals_expire(self):
        self.book.offer_phase(state(turn=5), {"1-A": Offerer({"type": "peace", "to": "2-B"}), "2-B": Accepter()})
        self.assertTrue(self.book.has_deal("1-A", "2-B", 13))   # 2 rounds of 4 players
        self.assertFalse(self.book.has_deal("1-A", "2-B", 14))

    def test_kept_deal_restores_trust(self):
        self.book.trust["1-A"]["2-B"] = 0.5
        self.book.offer_phase(state(turn=5), {"1-A": Offerer({"type": "peace", "to": "2-B"}), "2-B": Accepter()})
        self.book.check_attack("2-B", attack("2-B"), {"7": 2})  # B could hit A, hits C instead
        self.assertEqual(self.book.settle(13), [])               # still running
        msgs = self.book.settle(14)
        self.assertEqual([m["event"] for m in msgs], ["kept"])
        self.assertAlmostEqual(self.book.trust["1-A"]["2-B"], 0.6)
        self.assertEqual(self.book.trust["2-B"]["1-A"], 1.0)     # never above 1
        self.assertEqual(self.book.settle(15), [])               # only once
        kinds = [e["kind"] for e in TimelineBuilder().feed(msgs[0])]
        self.assertEqual(kinds, ["deal_kept"])

    def test_untested_deal_earns_no_trust(self):
        """Peace while nobody could attack anyone proves nothing."""
        self.book.trust["1-A"]["2-B"] = 0.5
        self.book.offer_phase(state(turn=5), {"1-A": Offerer({"type": "peace", "to": "2-B"}), "2-B": Accepter()})
        no_creatures = attack("2-B")
        no_creatures["attackers"] = []
        self.book.check_attack("2-B", no_creatures, None)
        zero_power = attack("2-B")
        zero_power["attackers"] = [{"id": 7, "power": 0, "can_attack": [0, 2, 3]}]
        self.book.check_attack("2-B", zero_power, {})
        self.assertEqual(self.book.settle(14), [])
        self.assertEqual(self.book.trust["1-A"]["2-B"], 0.5)

    def test_broken_deal_is_not_kept(self):
        self.book.offer_phase(state(turn=5), {"1-A": Offerer({"type": "peace", "to": "2-B"}), "2-B": Accepter()})
        self.book.check_attack("2-B", attack("2-B"), {"7": 0})
        self.assertEqual(self.book.settle(14), [])

    def test_attacking_gang_target_builds_trust(self):
        self.book.trust["2-B"]["1-A"] = 0.5
        self.book.offer_phase(state(), {"1-A": Offerer({"type": "gang_up", "to": "2-B", "against": "3-C"}),
                                        "2-B": Accepter()})
        self.book.check_attack("1-A", attack("1-A"), {"7": 2})  # A attacks C as promised
        self.assertAlmostEqual(self.book.trust["2-B"]["1-A"], 0.55)

    def test_viewer_shows_deals(self):
        msgs = self.book.offer_phase(state(), {"1-A": Offerer({"type": "peace", "to": "2-B"}), "2-B": Accepter()})
        builder = TimelineBuilder()
        kinds = [e["kind"] for m in msgs for e in builder.feed(m)]
        self.assertEqual(kinds, ["deal_offer", "deal_accept"])


class SimpleBotDealTest(unittest.TestCase):
    def test_never_attacks_a_partner(self):
        decision = attack("1-A")
        decision["state"]["players"][2]["life"] = 1   # C is weakest...
        decision["deals"] = {"protected": ["3-C"], "gang_targets": [], "active": [], "trust": {}}
        decision["attackers"] = [{"id": 7, "power": 3, "can_attack": [1, 2, 3]}]
        decision["defenders"] = [d | {"is_player": True} for d in decision["defenders"][1:]]
        self.assertNotEqual(SimpleBot().declare_attackers(decision)["7"], 2)  # ...but we have a deal

    def test_fresh_boy_rarely_offers_when_simplebot_wouldnt(self):
        """One "no" next to nine offers used to make fresh Boys offer ~55% of turns."""
        from showdown.boys import features
        from showdown.boys.brain import Brain
        view = {"state": state(turn=2), "deals": {"active": [], "trust": {}, "protected": [], "gang_targets": []}}
        probs = Brain("fresh").probabilities("offer_whether", features.offer_whether_options(view))
        self.assertLess(probs[1], 0.15)   # SimpleBot says "no offer" in round 1

    def test_no_offers_in_the_first_round(self):
        view = {"state": state(turn=2), "deals": {"active": [], "trust": {}, "protected": [], "gang_targets": []}}
        self.assertIsNone(SimpleBot().offer_deal(view))

    def test_simplebot_waits_until_boards_matter(self):
        def view(turn, leader_power):
            s = state(turn=turn, lives=(40, 40, 40, 40))
            s["players"][1]["battlefield"] = [{"type": "Creature", "power": leader_power, "toughness": 1}]
            return {"state": s, "deals": {"active": [], "trust": {}, "protected": [], "gang_targets": []}}
        self.assertIsNone(SimpleBot().offer_deal(view(turn=6, leader_power=5)))    # round 2: too early
        self.assertIsNone(SimpleBot().offer_deal(view(turn=10, leader_power=1)))   # round 3, but a 1/1 board
        self.assertIsNotNone(SimpleBot().offer_deal(view(turn=10, leader_power=5)))

    def test_boys_see_game_stage_and_board_size(self):
        from showdown.boys import features
        s = state(turn=1)
        early = features.offer_whether_options({"state": s, "deals": {"active": [], "trust": {}, "protected": [], "gang_targets": []}})[1]
        self.assertEqual((early["offer_game_stage"], early["offer_board_size"]), (0.0, 0.0))
        s = state(turn=41)   # round 11
        s["players"][1]["battlefield"] = [{"type": "Creature", "power": 10, "toughness": 10}]
        late = features.offer_whether_options({"state": s, "deals": {"active": [], "trust": {}, "protected": [], "gang_targets": []}})[1]
        self.assertEqual((late["offer_game_stage"], late["offer_board_size"]), (1.0, 0.5))



class PoliticsRewardTest(unittest.TestCase):
    """Breaking a deal, and accepting one that gets broken, cost a Boy a little."""

    def setUp(self):
        from showdown.bots.boy_bot import BoyBot
        self.boy = BoyBot("politics-test", learn=False)

    def test_breaker_pays_on_the_attack_that_broke_it(self):
        from showdown.boys.brain import Step
        from showdown.boys.reward import BROKE_A_DEAL
        home = Step("attack", [{"stay_home": 1.0}, {"attack_breaks_deal": 1.0}], [0.5, 0.5], picked=1)
        other = Step("attack", [{"stay_home": 1.0}, {"attack": 1.0}], [0.5, 0.5], picked=1)
        self.boy.steps = [home, other]
        self.boy.deal_broken({"id": 3, "a": "1-A", "b": "2-B"}, breaker="1-A", player="1-A")
        self.assertEqual((home.deal_bonus, other.deal_bonus), (BROKE_A_DEAL, 0.0))

    def test_accepting_a_deal_that_gets_broken_hurts(self):
        from showdown.boys.reward import PARTNER_BROKE_MY_DEAL
        view = {"state": state(you="2-B"), "offer": {"id": 5, "kind": "peace", "a": "1-A", "b": "2-B"},
                "deals": {"active": [], "trust": {"1-A": 1.0}, "protected": [], "gang_targets": [], "grudges": []}}
        self.boy.brain.weights["answer"]["accept"] = 10.0  # make sure it accepts
        self.boy.explore = False
        self.assertTrue(self.boy.answer_deal(view))
        accept = self.boy.steps[-1]
        self.boy.deal_broken(view["offer"], breaker="1-A", player="2-B")
        self.assertEqual(accept.deal_bonus, PARTNER_BROKE_MY_DEAL)
        self.boy.deal_broken(view["offer"], breaker="1-A", player="2-B")  # only once per deal
        self.assertEqual(accept.deal_bonus, PARTNER_BROKE_MY_DEAL)


class OfferCostTest(unittest.TestCase):
    """Offering isn't free, a "no" costs more, and low trust and pointless gang-ups look bad."""

    def setUp(self):
        from showdown.bots.boy_bot import BoyBot
        self.boy = BoyBot("offer-test", learn=False, explore=False)
        self.boy.brain.weights["offer_whether"]["make_offer"] = 10.0  # make sure it offers

    def view(self):
        return {"state": state(turn=9), "deals": {"active": [], "trust": {}, "protected": [],
                                                   "gang_targets": [], "grudges": []}}

    def test_offer_fee_and_declined_fee(self):
        from showdown.boys.reward import OFFER_DECLINED, OFFER_MADE
        self.assertIsNotNone(self.boy.offer_deal(self.view()))
        whether, which = self.boy.steps[-2:]
        self.assertEqual((whether.deal_bonus, which.deal_bonus), (OFFER_MADE, 0.0))
        self.boy.offer_answered({"id": 1}, accepted=False)
        self.assertEqual((whether.deal_bonus, which.deal_bonus), (OFFER_MADE + OFFER_DECLINED, OFFER_DECLINED))

    def test_accepted_offer_only_pays_the_small_fee(self):
        from showdown.boys.reward import OFFER_MADE
        self.boy.offer_deal(self.view())
        whether = self.boy.steps[-2]
        self.boy.offer_answered({"id": 1}, accepted=True)
        self.assertEqual(whether.deal_bonus, OFFER_MADE)

    def test_dealbook_tells_the_offerer(self):
        answers = []

        class Recorder(Offerer):
            def offer_answered(self, deal, accepted):
                answers.append(accepted)
        book = DealBook(["1-A", "2-B", "3-C", "4-D"])
        book.offer_phase(state(), {"1-A": Recorder({"type": "peace", "to": "2-B"}), "2-B": Bot()})
        self.assertEqual(answers, [False])

    def test_low_trust_and_pointless_gang_ups_start_unattractive(self):
        from showdown.boys import features
        from showdown.boys.brain import Brain
        brain = Brain("fresh")
        offer = {"id": 1, "kind": "gang_up", "a": "1-A", "b": "2-B", "against": "3-C"}
        trusted = {"state": state(you="2-B"), "offer": offer,
                   "deals": {"active": [], "trust": {"1-A": 1.0}, "protected": [], "gang_targets": [], "grudges": []}}
        p_quiet = brain.probabilities("answer", features.answer_options(trusted))[0]
        st = state(you="2-B")
        for p in st["players"][:2]:  # both partners now have creatures to fight with
            p["battlefield"] = [{"name": "Bear", "type": "Creature", "power": 2}]
        busy = dict(trusted, state=st)
        p_busy = brain.probabilities("answer", features.answer_options(busy))[0]
        broken = dict(busy, deals=dict(busy["deals"], trust={"1-A": 0.5}))
        p_broken = brain.probabilities("answer", features.answer_options(broken))[0]
        self.assertLess(p_quiet, p_busy)   # pointless gang-up: less likely to accept
        self.assertLess(p_broken, p_busy)  # they broke a deal with us: less likely


class RunawayLeaderTest(unittest.TestCase):
    def test_gang_up_on_a_runaway_starts_liked(self):
        from showdown.boys import features
        from showdown.boys.brain import Brain
        st = state(you="2-B")
        big = [{"name": "Dragon", "type": "Creature", "power": 8}, {"name": "Giant", "type": "Creature", "power": 6}]
        st["players"][2]["battlefield"] = big  # 3-C has an overwhelming board
        for p in st["players"][:2]:
            p["battlefield"] = [{"name": "Bear", "type": "Creature", "power": 2}]
        self.assertEqual(features.runaway_leader(st), "3-C")
        deals = {"active": [], "trust": {"1-A": 1.0}, "protected": [], "gang_targets": [], "grudges": []}
        vs_runaway = {"state": st, "deals": deals, "offer": {"id": 1, "kind": "gang_up", "a": "1-A", "b": "2-B", "against": "3-C"}}
        vs_other = dict(vs_runaway, offer=dict(vs_runaway["offer"], against="4-D"))
        brain = Brain("fresh")
        p_runaway = brain.probabilities("answer", features.answer_options(vs_runaway))[0]
        p_other = brain.probabilities("answer", features.answer_options(vs_other))[0]
        self.assertGreater(p_runaway, p_other)


def peace(a, b, rounds=2):
    return {a: Offerer({"type": "peace", "to": b, "rounds": rounds}), b: Accepter()}


def gang(a, b, against, rounds=2):
    return {a: Offerer({"type": "gang_up", "to": b, "against": against, "rounds": rounds}), b: Accepter()}


class ContradictionTest(unittest.TestCase):
    """A new deal that goes against an old one breaks the old one (Adam, 2026-11-18)."""

    def setUp(self):
        self.book = DealBook(["1-A", "2-B", "3-C", "4-D"])

    def test_peace_partner_becomes_gang_target(self):
        self.book.offer_phase(state(turn=5, active="1-A"), peace("1-A", "3-C"))      # A has peace with C
        msgs = self.book.offer_phase(state(turn=6, active="2-B"), gang("2-B", "1-A", "3-C"))
        self.assertEqual([m["event"] for m in msgs], ["offer", "accept", "break"])
        self.assertEqual(msgs[-1]["breaker"], "1-A")
        self.assertIn("1-A BREAKS the peace with 3-C by joining a gang-up on 3-C", msgs[-1]["text"])
        self.assertFalse(self.book.has_deal("1-A", "3-C", 6))
        self.assertEqual(self.book.view_for("3-C", state())["revenge"], ["1-A"])
        self.assertEqual(self.book.trust["3-C"]["1-A"], 0.5)

    def test_gang_target_becomes_peace_partner(self):
        self.book.offer_phase(state(turn=5, active="1-A"), gang("1-A", "2-B", "3-C"))  # A+B on C
        msgs = self.book.offer_phase(state(turn=7, active="3-C"), peace("3-C", "1-A"))  # C offers A peace
        self.assertEqual([m["event"] for m in msgs], ["offer", "accept", "break"])
        self.assertEqual(msgs[-1]["breaker"], "1-A")
        self.assertIn("1-A BREAKS the gang-up on 3-C with 2-B by making peace with 3-C", msgs[-1]["text"])
        self.assertEqual(msgs[-1]["deal"]["broken_how"], "new_deal")
        self.assertTrue(self.book.has_deal("1-A", "3-C", 7))

    def test_declined_offer_breaks_nothing(self):
        self.book.offer_phase(state(turn=5, active="1-A"), peace("1-A", "3-C"))
        bots = {"2-B": Offerer({"type": "gang_up", "to": "1-A", "against": "3-C"}), "1-A": Bot()}
        msgs = self.book.offer_phase(state(turn=6, active="2-B"), bots)
        self.assertEqual([m["event"] for m in msgs], ["offer", "decline"])
        self.assertTrue(self.book.has_deal("1-A", "3-C", 6))

    def test_boy_pays_on_the_deal_that_broke_it(self):
        from showdown.bots.boy_bot import BoyBot
        from showdown.boys.brain import Step
        from showdown.boys.reward import BROKE_A_DEAL
        boy = BoyBot("contradiction-test", learn=False)
        attack_step = Step("attack", [{"attack_breaks_deal": 1.0}], [1.0], picked=0)
        answer = Step("answer", [{"accept": 1.0}, {"decline": 1.0}], [0.5, 0.5], picked=0)
        boy.steps = [attack_step, answer]
        boy.deal_broken({"id": 1, "a": "1-A", "b": "3-C", "broken_how": "new_deal"}, breaker="1-A", player="1-A")
        self.assertEqual((attack_step.deal_bonus, answer.deal_bonus), (0.0, BROKE_A_DEAL))


class DealLengthTest(unittest.TestCase):
    def setUp(self):
        self.book = DealBook(["1-A", "2-B", "3-C", "4-D"])

    def test_offerer_picks_the_length(self):
        msgs = self.book.offer_phase(state(turn=5), peace("1-A", "2-B", rounds=1))
        self.assertIn("peace for 1 round", msgs[0]["text"])
        self.assertTrue(self.book.has_deal("1-A", "2-B", 9))
        self.assertFalse(self.book.has_deal("1-A", "2-B", 10))

    def test_bad_lengths_are_ignored(self):
        self.assertEqual(self.book.offer_phase(state(turn=5), peace("1-A", "2-B", rounds=7)), [])
        self.assertEqual(self.book.offer_phase(state(turn=9), peace("1-A", "2-B", rounds=0)), [])  # peace can't be "until out"

    def test_gang_up_until_target_is_out(self):
        self.book.offer_phase(state(turn=5), gang("1-A", "2-B", "3-C", rounds=0))
        self.assertTrue(self.book.has_deal("1-A", "2-B", 500))
        later = state(turn=40, active="4-D")
        later["players"][2]["lost"] = True
        msgs = self.book.offer_phase(later, {})
        self.assertEqual([m["event"] for m in msgs], ["end"])
        self.assertIn("3-C is out", msgs[0]["text"])
        self.assertFalse(self.book.has_deal("1-A", "2-B", 41))

    def test_no_peace_in_the_final_two(self):
        self.book.offer_phase(state(turn=5), peace("1-A", "2-B", rounds=4))
        later = state(turn=8, active="2-B")
        later["players"][2]["lost"] = later["players"][3]["lost"] = True
        msgs = self.book.offer_phase(later, {})
        self.assertEqual([m["event"] for m in msgs], ["end"])
        self.assertFalse(self.book.has_deal("1-A", "2-B", 8))
        kinds = [e["kind"] for m in msgs for e in TimelineBuilder().feed(m)]
        self.assertEqual(kinds, ["deal_end"])


def card(cid, name, controller, power=0, cmc=0, kind="Creature"):
    return {"id": cid, "name": name, "type": kind, "power": power, "toughness": 1, "cmc": cmc,
            "controller": controller}


class RevengeTest(unittest.TestCase):
    """A betrayed player owes ONE real setback, then the revenge is over (Adam, 2026-11-13/18)."""

    def setUp(self):
        self.book = DealBook(["1-A", "2-B", "3-C", "4-D"])
        self.book.offer_phase(state(turn=5), peace("1-A", "2-B"))
        self.book.check_attack("2-B", attack("2-B"), {"7": 0})  # B betrays A

    def table(self, turn, active, b_cards=(), b_life=25, b_hand=3):
        s = state(turn=turn, active=active)
        s["players"][1].update(battlefield=list(b_cards), life=b_life, hand_size=b_hand)
        return s

    def test_breaker_is_owed_revenge_once(self):
        self.assertEqual(self.book.view_for("1-A", state())["revenge"], ["2-B"])
        self.assertEqual(self.book.view_for("3-C", state())["revenge"], [])

    def test_damage_counts(self):
        self.book.observe(self.table(9, "1-A", [card(50, "Bear", "2-B", power=2)]))
        controllers = {12: "1-A", 13: "3-C"}
        self.assertEqual(self.book.see_log("Goblin (13) deals 9 combat damage to 2-B.", controllers), [])  # not A's
        self.assertEqual(self.book.see_log("Ogre (12) deals 3 combat damage to 2-B.", controllers), [])
        msgs = self.book.see_log("Ogre (12) deals 2 combat damage to 2-B.", controllers)
        self.assertEqual([m["event"] for m in msgs], ["revenge"])
        self.assertEqual(msgs[0]["text"], "1-A dealt 5 damage to 2-B. 1-A has taken revenge, and it's over.")
        self.assertEqual(self.book.view_for("1-A", state())["revenge"], [])
        self.assertEqual(self.book.view_for("1-A", state())["grudges"], ["2-B"])  # still remembered this game
        events = TimelineBuilder().feed(msgs[0])
        self.assertEqual((events[0]["kind"], events[0]["player"], events[0]["data"]["breaker"]),
                         ("deal_revenge", "1-A", "2-B"))

    def test_removing_something_important_on_my_turn(self):
        cards = [card(50, "Bear", "2-B", power=2, cmc=2), card(51, "Dragon", "2-B", power=5, cmc=6)]
        self.book.observe(self.table(9, "1-A", cards))
        self.assertEqual(self.book.see_log("Bear (50) was put into Graveyard from Battlefield.", {}), [])  # too small
        msgs = self.book.see_log("Dragon (51) was put into Exile from Battlefield.", {})
        self.assertEqual(msgs[0]["text"], "1-A removed Dragon from 2-B. 1-A has taken revenge, and it's over.")

    def test_removal_on_someone_elses_turn_counts_if_i_aimed_it(self):
        dragon = card(51, "Dragon", "2-B", power=5, cmc=6)
        self.book.observe(self.table(10, "2-B", [dragon]))
        self.assertEqual(self.book.see_log("Dragon (51) was put into Graveyard from Battlefield.", {}), [])  # not A's doing
        self.book.observe(self.table(11, "3-C", [dragon]))
        decision = {"kind": "choose_targets", "api": "Destroy", "params": {},
                    "candidates": [dict(dragon, index=0)], "suggested": [0]}
        self.book.note_target("1-A", decision, [0])
        msgs = self.book.see_log("Dragon (51) was put into Graveyard from Battlefield.", {})
        self.assertEqual([m["event"] for m in msgs], ["revenge"])

    def test_knocking_the_board_down_counts(self):
        cards = [card(50 + i, "Bear", "2-B", power=2, cmc=2) for i in range(4)]
        self.assertEqual(self.book.observe(self.table(9, "1-A", cards)), [])
        self.assertEqual(self.book.observe(self.table(9, "1-A", cards[:3])), [])   # a small dent
        msgs = self.book.observe(self.table(9, "1-A", []))                         # wiped
        self.assertIn("1-A knocked 2-B's board down", msgs[0]["text"])

    def test_simplebot_hunts_only_until_revenge_is_done(self):
        decision = attack("1-A")
        decision["defenders"] = [d | {"is_player": True} for d in decision["defenders"][1:]]
        decision["attackers"] = [{"id": 7, "power": 3, "can_attack": [1, 2, 3]}]
        decision["state"]["players"][3]["battlefield"] = [card(60, "Giant", "4-D", power=9)]  # D is the leader
        decision["deals"] = {"protected": [], "gang_targets": [], "grudges": ["2-B"], "revenge": ["2-B"]}
        self.assertEqual(SimpleBot().declare_attackers(decision)["7"], 1)  # revenge on B first
        decision["deals"]["revenge"] = []                                   # revenge taken
        self.assertEqual(SimpleBot().declare_attackers(decision)["7"], 3)  # back to the biggest threat


class GainCheckTest(unittest.TestCase):
    """How much do I gain, and how much do they gain (weighted by their threat)?"""

    def test_gains(self):
        from showdown.boys import threat
        s = state(you="2-B", lives=(40, 20, 40, 40))
        s["players"][0]["battlefield"] = [card(1, "Giant", "1-A", power=10)]
        s["players"][1]["battlefield"] = [card(2, "Bear", "2-B", power=2)]
        my_gain, their_gain = threat.deal_gains(s, "1-A")
        self.assertEqual(my_gain, 0.5)                 # A could take half my life in one go
        self.assertAlmostEqual(their_gain, 2 / 40)     # I could take 5% of theirs, and A is the top threat
        # The same small threat to a far weaker player counts for less.
        s["players"][0]["battlefield"] = []
        s["players"][2]["battlefield"] = [card(3, "Titan", "3-C", power=20)]
        _, weak_gain = threat.deal_gains(s, "1-A")
        self.assertLess(weak_gain, 2 / 40)

    def test_simplebot_declines_when_they_gain_more(self):
        s = state(you="2-B", lives=(40, 20, 40, 10))
        s["players"][1]["battlefield"] = [card(2, "Hydra", "2-B", power=8)]   # I could hit A hard
        s["players"][3]["battlefield"] = [card(3, "Titan", "4-D", power=12)]  # D leads
        s["players"][0]["battlefield"] = [card(10 + i, "Relic", "1-A", kind="Artifact") for i in range(6)]
        offer = {"id": 1, "kind": "peace", "a": "1-A", "b": "2-B", "rounds": 2}
        deals = {"active": [], "trust": {"1-A": 1.0}, "protected": [], "gang_targets": [], "grudges": []}
        self.assertFalse(SimpleBot().answer_deal({"state": s, "offer": offer, "deals": deals}))  # A gains, I don't
        s["players"][0]["battlefield"].append(card(1, "Giant", "1-A", power=5))   # now A threatens me too
        self.assertTrue(SimpleBot().answer_deal({"state": s, "offer": offer, "deals": deals}))

    def test_fresh_boy_prefers_deals_where_i_gain(self):
        from showdown.boys import features
        from showdown.boys.brain import Brain
        s = state(you="2-B", lives=(40, 40, 40, 40))
        s["players"][1]["battlefield"] = [card(2, "Hydra", "2-B", power=8)]
        deals = {"active": [], "trust": {"1-A": 1.0}, "protected": [], "gang_targets": [], "grudges": []}
        view = {"state": s, "deals": deals, "offer": {"id": 1, "kind": "peace", "a": "1-A", "b": "2-B", "rounds": 2}}
        brain = Brain("fresh")
        p_they_gain = brain.probabilities("answer", features.answer_options(view))[0]
        s2 = state(you="2-B", lives=(40, 40, 40, 40))
        s2["players"][0]["battlefield"] = [card(1, "Hydra", "1-A", power=8)]
        p_i_gain = brain.probabilities("answer", features.answer_options(dict(view, state=s2)))[0]
        self.assertLess(p_they_gain, p_i_gain)

    def test_offers_come_in_every_length(self):
        from showdown.boys import features
        view = {"state": state(turn=9), "deals": {"active": [], "trust": {}, "protected": [],
                                                   "gang_targets": [], "grudges": []}}
        _, offers = features.offer_options(view)
        self.assertEqual({o["rounds"] for o in offers if o["type"] == "peace"}, {1, 2, 3, 4})
        self.assertEqual({o["rounds"] for o in offers if o["type"] == "gang_up"}, {0, 1, 2, 3, 4})


if __name__ == "__main__":
    unittest.main()
