"""Tests for card memory (showdown/boys/cards.py). No Forge needed.

Run with:  python -m unittest tests.test_cards -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from showdown.boys import cards, features, storage
from showdown.boys.brain import Brain, Step


def state(turn=5):
    return {"turn": turn, "active_player": "1-A", "phase": "MAIN1", "you": "1-A", "stack_size": 0,
            "players": [{"name": n, "life": 40, "hand": [], "battlefield": []}
                        for n in ("1-A", "2-B", "3-C", "4-D")]}


def play_decision(turn=5):
    return {"state": state(turn), "options": [
        {"index": 0, "card": {"name": "Kuja, Genome Sorcerer", "type": "Creature", "cmc": 4},
         "description": "Cast", "is_land": False, "is_spell": True},
        {"index": 1, "card": {"name": "Forest", "type": "Basic Land", "cmc": 0},
         "description": "Play", "is_land": True, "is_spell": False}]}


class CardFeatureTest(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(cards.slug("Kuja, Genome Sorcerer"), "kuja_genome_sorcerer")
        self.assertEqual(cards.slug("Fire // Ice"), "fire_ice")

    def test_stage_by_round(self):
        self.assertEqual(cards.stage(state(turn=1)), "early")    # round 1
        self.assertEqual(cards.stage(state(turn=12)), "early")   # round 3
        self.assertIsNone(cards.stage(state(turn=13)))           # round 4
        self.assertEqual(cards.stage(state(turn=29)), "late")    # round 8

    def test_play_options_have_card_features(self):
        feats = features.play_options(play_decision(turn=2))
        self.assertEqual(feats[0]["card:cast:kuja_genome_sorcerer"], 1.0)
        self.assertEqual(feats[0]["card:cast:kuja_genome_sorcerer@early"], 1.0)
        # Land drops and passing have no card memory.
        self.assertFalse(any(cards.is_card_feature(k) for k in feats[1]))
        self.assertFalse(any(cards.is_card_feature(k) for k in feats[2]))

    def test_target_hurt_or_help(self):
        self.assertEqual(cards.target_features({"name": "Sol Ring"}, harmful=True), {"card:hurt:sol_ring": 1.0})
        self.assertEqual(cards.target_features({"name": "Sol Ring"}, harmful=False), {"card:help:sol_ring": 1.0})
        self.assertEqual(cards.target_features({"name": "2-B", "is_player": True}, harmful=True), {})


class CardMemoryBrainTest(unittest.TestCase):
    def learn_once(self, brain):
        brain.games, brain.critic["bias"] = 1, 0.5  # an average game is 0.5, this one went well
        options = features.play_options(play_decision())
        brain.learn([Step("play", options, [1 / 3] * 3, picked=0, turn=5, bonus=0.0)], reward=1.5)

    def test_off_by_default(self):
        """Boys without card memory ignore card features: same score, nothing learned."""
        brain = Brain("plain")
        options = features.play_options(play_decision())
        without = {k: v for k, v in options[0].items() if not cards.is_card_feature(k)}
        brain.weights["play"]["card:cast:kuja_genome_sorcerer"] = 5.0  # should be ignored
        self.assertEqual(brain.score("play", options[0]), brain.score("play", without))
        del brain.weights["play"]["card:cast:kuja_genome_sorcerer"]
        self.learn_once(brain)
        self.assertFalse(any(cards.is_card_feature(k) for k in brain.weights["play"]))

    def test_learns_card_numbers_when_on(self):
        brain = Brain("prime", card_memory=True)
        self.learn_once(brain)
        learned = brain.weights["play"]["card:cast:kuja_genome_sorcerer"]
        self.assertGreater(learned, 0)   # the picked card did well
        # Half speed compared with a general feature that had the same gradient.
        # (then the once-per-game shrink of 0.5%).
        self.assertAlmostEqual(learned, brain.weights["play"]["creature_spell"] * 0.5 * (1 - 0.005))

    def test_shrink(self):
        brain = Brain("prime", card_memory=True)
        brain.weights["play"]["card:cast:kuja_genome_sorcerer"] = 1.0
        brain.bonus_baseline = 0.1
        brain.critic["bias"] = 1.5  # exactly as expected: advantage 0, only the shrink acts
        brain.games = 1
        options = features.play_options(play_decision())
        brain.learn([Step("play", options, [1 / 3] * 3, picked=0, turn=5, bonus=0.1)], reward=1.5)
        self.assertAlmostEqual(brain.weights["play"]["card:cast:kuja_genome_sorcerer"], 1 - 0.005)

    def test_shrink_is_once_per_game(self):
        """A card in many decisions of one game shrinks once, not once per decision."""
        brain = Brain("prime", card_memory=True)
        brain.weights["play"]["card:cast:kuja_genome_sorcerer"] = 1.0
        brain.bonus_baseline = 0.1
        brain.critic["bias"] = 1.5
        brain.games = 1
        options = features.play_options(play_decision())
        steps = [Step("play", options, [1 / 3] * 3, picked=0, turn=5, bonus=0.1) for _ in range(50)]
        brain.learn(steps, reward=1.5)
        self.assertAlmostEqual(brain.weights["play"]["card:cast:kuja_genome_sorcerer"], 1 - 0.005)

    def test_save_and_load_keeps_cards_separate(self):
        brain = Brain("prime", card_memory=True)
        self.learn_once(brain)
        data = brain.to_dict()
        self.assertTrue(data["card_memory"])
        self.assertIn("cast:kuja_genome_sorcerer", data["cards"]["play"])
        self.assertFalse(any(cards.is_card_feature(k) for k in data["weights"]["play"]))
        again = Brain.from_dict(json.loads(json.dumps(data)))
        self.assertTrue(again.card_memory)
        self.assertEqual(again.to_dict(), data)

    def test_plain_brain_file_unchanged(self):
        data = Brain("plain").to_dict()
        self.assertNotIn("cards", data)
        self.assertNotIn("card_memory", data)


class CopyBoyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patch_dir = mock.patch.object(storage, "BOYS_DIR", Path(self.tmp.name))
        patch_dir.start()
        self.addCleanup(patch_dir.stop)
        self.addCleanup(self.tmp.cleanup)
        storage._brains.clear()
        self.addCleanup(storage._brains.clear)

    def test_copy_with_card_memory(self):
        original = Brain("Kaiba", games=10)
        original.weights["play"]["spell"] = 0.7
        storage.save_brain(original)
        storage.copy_boy("Kaiba", "Kaiba Prime", card_memory=True)
        copy = json.loads((Path(self.tmp.name) / "Kaiba Prime.json").read_text())
        self.assertEqual((copy["name"], copy["games"], copy["weights"]["play"]["spell"]), ("Kaiba Prime", 10, 0.7))
        self.assertTrue(copy["card_memory"])
        kaiba = json.loads((Path(self.tmp.name) / "Kaiba.json").read_text())
        self.assertNotIn("card_memory", kaiba)  # the original is untouched
        with self.assertRaises(FileExistsError):
            storage.copy_boy("Kaiba", "Kaiba Prime")


if __name__ == "__main__":
    unittest.main()
