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
    def test_no_commander_rule_in_simplebot(self):
        """Adam: don't force commanders. SimpleBot treats it like any other spell."""
        options = [spell(0, "Big Thing", 6), spell(1, "Anhelo", 3, commander=True, commander_tax=0)]
        self.assertEqual(SimpleBot().choose_play({"state": state(), "options": options}), 0)

    def test_boys_lean_towards_the_commander_but_can_unlearn_it(self):
        from showdown.boys.brain import Brain
        options = [spell(0, "Big Thing", 6), spell(1, "Anhelo", 3, commander=True, commander_tax=0)]
        feats = features.play_options({"state": state(), "options": options})
        fresh = Brain("fresh").probabilities("play", feats)
        self.assertGreater(fresh[1], 0.15)                    # a real chance, not a rule
        self.assertLess(fresh[1], fresh[0])                   # SimpleBot's pick still leads
        unlearned = Brain("u"); unlearned.weights["play"]["cast_commander"] = -3.0
        self.assertLess(unlearned.probabilities("play", feats)[1], fresh[1])

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


class AttackTriggerTest(unittest.TestCase):
    def test_equipped_attacker_sets_off_nahiri(self):
        nahiri = {"id": 1, "name": "Nahiri, Forged in Fury", "type": "Legendary Creature", "power": 5,
                  "oracle": "Whenever an equipped creature you control attacks, exile the top card of your library."}
        germ = {"id": 2, "name": "Germ Token", "type": "Creature", "power": 4, "toughness": 4}
        bare = {"id": 3, "name": "Bear", "type": "Creature", "power": 2, "toughness": 2}
        sword = {"id": 4, "name": "Sword", "type": "Artifact - Equipment", "attached_to": 2}
        s = state(); s["players"][0]["battlefield"] = [nahiri, germ, bare, sword]
        decision = {"state": s, "defenders": [{"index": 0, "name": "2-B", "is_player": True}],
                    "attackers": [dict(germ, can_attack=[0]), dict(bare, can_attack=[0])]}
        ctx = features.attack_context(decision)
        germ_attack = features.attack_options(decision["attackers"][0], decision, ctx)[0][1]
        bear_attack = features.attack_options(decision["attackers"][1], decision, ctx)[0][1]
        self.assertEqual((germ_attack["attack_equipped"], germ_attack["attack_triggers"]), (1.0, 1 / 3))
        self.assertEqual((bear_attack["attack_equipped"], bear_attack["attack_triggers"]), (0.0, 0.0))

    def test_own_attack_trigger(self):
        raider = {"id": 5, "name": "Goblin Raider", "type": "Creature", "power": 2,
                  "oracle": "Whenever Goblin Raider attacks, it deals 1 damage to each opponent."}
        s = state(); s["players"][0]["battlefield"] = [raider]
        self.assertEqual(features.attack_triggers(raider, s["players"][0]), 1)


if __name__ == "__main__":
    unittest.main()
