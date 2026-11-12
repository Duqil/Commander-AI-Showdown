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



if __name__ == "__main__":
    unittest.main()
