"""Tests for The Boys (learning bots) and the batch runner. No Forge needed.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from showdown.bots.boy_bot import BoyBot
from showdown.boys import features, storage, threat
from showdown.boys.brain import Brain
from showdown.boys.reward import game_reward


def creature(cid, power, toughness=None, tapped=False, **extra):
    return {"id": cid, "name": f"C{cid}", "type": "Creature", "cmc": 2, "power": power,
            "toughness": power if toughness is None else toughness, "tapped": tapped, **extra}


def table(you="1-A", active="1-A", phase="MAIN1"):
    """Four players: B has a big board (the leader), C is at 6 life with nothing, D is average."""
    return {
        "turn": 5, "active_player": active, "phase": phase, "you": you, "stack_size": 0,
        "players": [
            {"name": "1-A", "life": 30, "hand_size": 3, "hand": [], "battlefield": [creature(1, 3), creature(2, 2)]},
            {"name": "2-B", "life": 40, "hand_size": 5, "battlefield": [creature(10, 6), creature(11, 5)]},
            {"name": "3-C", "life": 4, "hand_size": 0, "battlefield": []},
            {"name": "4-D", "life": 25, "hand_size": 2, "battlefield": [creature(30, 2)]},
        ],
    }


def attack_decision(state=None):
    return {
        "kind": "attack", "player": "1-A", "state": state or table(),
        "defenders": [{"index": 0, "name": "2-B", "is_player": True},
                      {"index": 1, "name": "3-C", "is_player": True},
                      {"index": 2, "name": "4-D", "is_player": True}],
        "attackers": [{**creature(1, 3), "can_attack": [0, 1, 2]},
                      {**creature(2, 2), "can_attack": [0, 1, 2]}],
    }


class ThreatTest(unittest.TestCase):
    def test_leader_is_biggest_board(self):
        self.assertEqual(threat.leader(table()), "2-B")

    def test_threat_rank(self):
        state = table()
        self.assertEqual(threat.threat_rank(state, "2-B"), 0.0)
        self.assertEqual(threat.threat_rank(state, "3-C"), 1.0)

    def test_can_kill(self):
        self.assertTrue(threat.can_kill({"life": 4}, 5))
        self.assertFalse(threat.can_kill({"life": 40}, 5))

    def test_lost_players_are_ignored(self):
        state = table()
        state["players"][1]["lost"] = True
        self.assertEqual(threat.leader(state), "4-D")


class FeatureTest(unittest.TestCase):
    def test_attack_features(self):
        decision = attack_decision()
        ctx = features.attack_context(decision)
        feats, targets = features.attack_options(decision["attackers"][0], decision, ctx)
        self.assertEqual(targets, [None, 0, 1, 2])
        self.assertEqual(feats[1]["target_is_leader"], 1.0)    # B
        self.assertEqual(feats[2]["target_killable"], 1.0)     # C at 4 life, we have 5 power
        self.assertEqual(feats[2]["target_weak"], 1.0)         # C has no creatures
        self.assertEqual(feats[1]["target_bad_block"], 1.0)    # B's 6/6 eats our 3/3
        # SimpleBot attacks the lowest life player, so its prior points at C.
        self.assertEqual(feats[2]["simple_prior"], 1.0)

    def test_zero_power_creatures_usually_stay_home(self):
        decision = attack_decision()
        ctx = features.attack_context(decision)
        feats, targets = features.attack_options({**creature(5, 0, 1), "can_attack": [0, 1, 2]}, decision, ctx)
        self.assertEqual(targets, [None, 0, 1, 2])  # still allowed
        probs = Brain("t").probabilities("attack", feats)
        self.assertGreater(probs[0], 0.9)  # but staying home is very likely

    def test_target_features(self):
        decision = {"state": table(), "description": "deals 3 damage to any target", "suggested": [1],
                    "api": "DealDamage", "params": {},
                    "min": 1, "max": 1, "candidates": [
                        {"index": 0, "name": "1-A", "life": 30, "is_player": True},
                        {"index": 1, **creature(10, 6, controller="2-B"), "is_player": False},
                        {"index": 2, **creature(1, 3, controller="1-A"), "is_player": False}]}
        feats = features.target_options(decision)
        self.assertEqual(feats[0]["hurt_mine"], 1.0)        # burning ourselves
        self.assertEqual(feats[1]["hurt_opponent"], 1.0)
        self.assertEqual(feats[1]["target_of_leader"], 1.0)
        self.assertEqual(feats[1]["forge_suggested"], 1.0)
        bot = BoyBot("tester_t", learn=False, seed=3)
        self.assertIn(bot.choose_targets(decision)[0], [0, 1, 2])

    def test_harmful_or_helpful(self):
        self.assertTrue(features.is_harmful({"api": "Destroy"}))
        self.assertFalse(features.is_harmful({"api": "Pump", "params": {"NumAtt": "+3"}}))
        self.assertTrue(features.is_harmful({"api": "Pump", "params": {"NumAtt": "-3"}}))
        self.assertTrue(features.is_harmful({"api": "ChangeZone", "params": {"Origin": "Battlefield"}}))
        self.assertFalse(features.is_harmful({"api": "ChangeZone", "params": {"Origin": "Graveyard"}}))
        self.assertTrue(features.is_harmful({"api": None, "description": "Exile target creature"}))

    def test_play_options_add_pass(self):
        decision = {"state": table(), "options": [
            {"index": 0, "card": {"name": "Evolving Wilds", "type": "Land", "cmc": 0},
             "description": "Sacrifice: search", "is_land": False, "is_spell": False}]}
        feats = features.play_options(decision)
        self.assertEqual(len(feats), 2)
        self.assertEqual(feats[0]["land_ability"], 1.0)
        self.assertEqual(feats[1]["pass"], 1.0)

    def test_simple_bot_uses_abilities(self):
        from showdown.bots.simple_bot import SimpleBot
        wilds = {"index": 0, "card": {"name": "Evolving Wilds", "type": "Land", "cmc": 0},
                 "is_land": False, "is_spell": False, "is_ability": True, "forge_would_play": True}
        bear = {"index": 1, "card": {"name": "Bear", "type": "Creature", "cmc": 2},
                "is_land": False, "is_spell": True, "is_ability": False, "forge_would_play": True}
        pump = {"index": 2, "card": {"name": "Shade", "type": "Creature", "cmc": 3},
                "is_land": False, "is_spell": False, "is_ability": True, "forge_would_play": False}
        bot = SimpleBot()
        pick = lambda *opts: bot.choose_play({"state": table(), "options": list(opts)})
        self.assertEqual(pick(bear, wilds), 0)         # fetch the land before casting
        self.assertEqual(pick(pump), None)             # Forge's AI says not now
        self.assertEqual(pick(dict(pump, forge_would_play=True), bear), 1)  # spells before other abilities
        self.assertEqual(pick(dict(pump, forge_would_play=True)), 2)        # ...then the ability
        feats = features.play_options({"state": table(), "options": [wilds]})
        self.assertEqual(feats[0]["ability_forge_would_play"], 1.0)
        # An X spell counts the X we can pay: Tervigon (mana value 4) with X=3 beats a 5-drop.
        tervigon = {"index": 3, "card": {"name": "Tervigon", "type": "Creature", "cmc": 4},
                    "is_land": False, "is_spell": True, "max_x": 3}
        five = {"index": 4, "card": {"name": "Big", "type": "Creature", "cmc": 5}, "is_land": False, "is_spell": True}
        self.assertEqual(pick(tervigon, five), 3)
        # Starstorm with no good X (Forge's AI says no) stays in hand.
        starstorm = {"index": 5, "card": {"name": "Starstorm", "type": "Instant", "cmc": 2},
                     "is_land": False, "is_spell": True, "max_x": 6, "forge_would_play": False}
        self.assertIsNone(pick(starstorm))
        self.assertEqual(pick(dict(starstorm, forge_would_play=True)), 5)


class BrainTest(unittest.TestCase):
    def test_softmax_sums_to_one(self):
        probs = Brain("t").probabilities("play", [{"simple_prior": 1.0}, {}, {}])
        self.assertAlmostEqual(sum(probs), 1.0)
        self.assertGreater(probs[0], probs[1])

    def test_learns_to_prefer_rewarded_feature(self):
        """Option with feature "good" pays off, the other doesn't. The weight should grow."""
        brain = Brain("t")
        rng = random.Random(0)
        options = [{"good": 1.0}, {"bad": 1.0}]
        for _ in range(300):
            step = brain.choose("attack", options, rng)
            brain.learn([step], reward=1.0 if step.picked == 0 else 0.0)
        self.assertGreater(brain.weights["attack"]["good"], brain.weights["attack"].get("bad", 0.0))
        self.assertGreater(brain.probabilities("attack", options)[0], 0.8)

    def test_save_and_load_round_trip(self):
        brain = Brain("t", games=3)
        brain.weights["attack"]["target_is_leader"] = 0.7
        again = Brain.from_dict(brain.to_dict())
        self.assertEqual(again.games, 3)
        self.assertEqual(again.weights["attack"]["target_is_leader"], 0.7)


class RewardTest(unittest.TestCase):
    def test_reward(self):
        self.assertEqual(game_reward(True, 3, 3), 1.5)
        self.assertEqual(game_reward(False, 0, 3), 0.0)
        self.assertAlmostEqual(game_reward(False, 2, 3), 1 / 3)


class BoyBotTest(unittest.TestCase):
    def setUp(self):
        # Keep test brains out of the real boys/ folder.
        self.tmp = tempfile.TemporaryDirectory()
        patch_dir = mock.patch.object(storage, "BOYS_DIR", Path(self.tmp.name))
        patch_dir.start()
        self.addCleanup(patch_dir.stop)
        self.addCleanup(self.tmp.cleanup)
        storage._brains.clear()
        self.addCleanup(storage._brains.clear)

    def test_attacks_are_legal(self):
        bot = BoyBot("tester", seed=1)
        decision = attack_decision()
        for _ in range(20):
            attacks = bot.declare_attackers(decision)
            for cid, target in attacks.items():
                attacker = next(a for a in decision["attackers"] if str(a["id"]) == cid)
                self.assertIn(target, attacker["can_attack"])

    def test_learns_and_saves_after_game(self):
        bot = BoyBot("tester", learn=True, seed=1)
        bot.declare_attackers(attack_decision())
        state = table()
        state["players"][2]["lost"] = True  # C was knocked out before the end
        bot.last_state = state
        bot.game_over({"winner": "2-B", "players": state["players"]}, "1-A")
        self.assertEqual(bot.result["outlived"], 1)
        self.assertAlmostEqual(bot.result["reward"], 0.5 / 3)
        self.assertTrue((Path(self.tmp.name) / "tester.json").exists())
        self.assertEqual(storage.load_brain("tester").games, 1)
        self.assertIn("boy:tester", __import__("showdown.batch", fromlist=["x"]).bots_available())

    def test_turn_bonus_goes_to_that_turns_choices(self):
        bot = BoyBot("tester", learn=False, seed=1)
        before = table()                       # our turn 5, C at 4 life
        bot.declare_attackers(attack_decision(before))
        after = table(active="2-B")
        after["turn"] = 6
        after["players"][2]["life"] = 0
        after["players"][2]["lost"] = True     # C knocked out during our turn
        after["players"][3]["life"] = 20       # D took 5
        bot.choose_play({"state": after, "options": []})
        bot.game_over({"winner": "2-B", "players": after["players"]}, "1-A")
        self.assertEqual(bot.result["damage"], 9)
        self.assertEqual(bot.result["knockouts"], 1)

    def test_mulligan_state_without_turn_info(self):
        """Forge leaves out keys whose value is null, e.g. active_player during mulligans."""
        bot = BoyBot("tester", learn=True, seed=1)
        state = table()
        del state["active_player"], state["turn"]
        decision = {"state": state, "cards_to_bottom_if_kept": 0}
        bot.keep_hand(decision)
        bot.keep_hand(decision)
        bot.choose_play({"state": table(), "options": []})
        bot.game_over({"winner": "1-A", "players": table()["players"]}, "1-A")
        self.assertTrue(bot.result["won"])

    def test_rejects_bad_names(self):
        with self.assertRaises(ValueError):
            storage.brain_path("../evil")


if __name__ == "__main__":
    unittest.main()


class StatsTest(unittest.TestCase):
    def test_win_rates(self):
        from showdown import stats
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(stats, "STATS_DIR", Path(tmp)), \
                mock.patch.object(stats, "RESULTS_FILE", Path(tmp) / "results.csv"):
            stats.record_game(["kuja", "garnet"], ["boy:Kaiba", "simple"], 0, False, 30, "batch", "g1")
            stats.record_game(["kuja", "garnet"], ["boy:Kaiba", "simple"], 1, False, 31, "batch", "g2")
            stats.record_game(["kuja", "garnet"], ["simple", "simple"], 1, False, 32, "batch", "g3")
            decks = {r["deck"]: r for r in stats.win_rates("deck")}
            bots = {r["bot"]: r for r in stats.win_rates("bot")}
        self.assertEqual(decks["garnet"]["wins"], 2)
        self.assertAlmostEqual(decks["kuja"]["win_rate"], 1 / 3)
        self.assertEqual(bots["simple"]["games"], 4)
        self.assertEqual(bots["boy:Kaiba"]["win_rate"], 0.5)
