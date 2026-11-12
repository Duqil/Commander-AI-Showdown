"""Tests for casting the commander and optional extra costs like casualty. No Forge needed."""

from __future__ import annotations

import unittest

from showdown.bots.boy_bot import BoyBot
from showdown.bots.simple_bot import SimpleBot
from showdown.boys import features


def state():
    return {"turn": 9, "you": "1-A", "active_player": "1-A", "phase": "MAIN1", "stack_size": 0,
            "players": [{"name": "1-A", "life": 40, "battlefield": [], "hand": []},
                        {"name": "2-B", "life": 40, "battlefield": []}]}


def spell(i, name, cmc, **card):
    return {"index": i, "is_spell": True, "is_land": False,
            "card": {"name": name, "type": "Creature", "cmc": cmc, **card}}


class CommanderTest(unittest.TestCase):
    def test_commander_first(self):
        options = [spell(0, "Big Thing", 6), spell(1, "Anhelo", 3, commander=True, commander_tax=0)]
        self.assertEqual(SimpleBot().choose_play({"state": state(), "options": options}), 1)

    def test_expensive_commander_waits(self):
        options = [spell(0, "Big Thing", 6), spell(1, "Anhelo", 3, commander=True, commander_tax=4)]
        self.assertEqual(SimpleBot().choose_play({"state": state(), "options": options}), 0)

    def test_boys_see_the_commander(self):
        options = [spell(0, "Big Thing", 6), spell(1, "Anhelo", 3, commander=True, commander_tax=2)]
        feats = features.play_options({"state": state(), "options": options})
        self.assertEqual((feats[1]["cast_commander"], feats[1]["commander_tax"]), (1.0, 0.2))
        self.assertEqual(feats[0]["cast_commander"], 0.0)


class KeywordCostTest(unittest.TestCase):
    def decision(self, *candidates):
        return {"kind": "keyword_cost", "state": state(), "keyword": "Casualty:2", "max": 1,
                "spell": {"name": "Feed the Swarm"}, "sacrifice_candidates": list(candidates)}

    def test_never_sacrifice_the_commander(self):
        cmdr = {"name": "Garnet", "cmc": 4, "power": 2, "commander": True}
        self.assertEqual(SimpleBot().pay_keyword_cost(self.decision(cmdr)), 0)
        bear = {"name": "Bear", "cmc": 2, "power": 2}
        self.assertIsNone(SimpleBot().pay_keyword_cost(self.decision(cmdr, bear)))   # Forge sacs the bear

    def test_boys_see_what_they_would_lose(self):
        ghast = {"name": "Bloodghast", "cmc": 2, "power": 2, "graveyard_use": "landfall"}
        zombie = {"name": "Zombie Token", "cmc": 0, "power": 2, "token": True}
        no, yes = features.keyword_cost_options(self.decision(ghast, zombie))
        self.assertEqual(yes["sac_token"], 1.0)        # the cheapest is a token
        self.assertEqual(yes["sac_comes_back"], 1.0)   # Bloodghast would return anyway
        self.assertEqual(yes["simple_prior"], 1.0)
        self.assertIn(BoyBot("tester_k", learn=False, seed=4).pay_keyword_cost(self.decision(ghast)), [0, 1])


if __name__ == "__main__":
    unittest.main()
