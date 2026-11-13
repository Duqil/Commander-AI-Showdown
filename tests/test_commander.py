"""Tests for commander traits (showdown/boys/commander.py). No Forge needed."""

from __future__ import annotations

import unittest

from showdown.boys import commander, features

ANHELO = {"name": "Anhelo, the Painter", "type": "Legendary Creature", "cmc": 3, "power": 1, "toughness": 3,
          "commander": True, "commander_tax": 0,
          "oracle": "Deathtouch\nThe first instant or sorcery spell you cast each turn has casualty 2."}
SWARMLORD = {"name": "The Swarmlord", "type": "Legendary Creature", "cmc": 6, "power": 5, "toughness": 5,
             "commander": True, "commander_tax": 2,
             "oracle": "The Swarmlord enters with two +1/+1 counters on it for each time you've cast your "
                       "commander from the command zone this game.\nWhenever a creature you control with a "
                       "counter on it dies, draw a card."}
BOLT = {"name": "Lightning Bolt", "type": "Instant", "cmc": 1}
BEAR = {"name": "Bear", "type": "Creature", "cmc": 2, "power": 2, "toughness": 2}


def state(turn=5, hand=(), battlefield=()):
    return {"turn": turn, "you": "1-A", "active_player": "1-A", "phase": "MAIN1", "stack_size": 0,
            "players": [{"name": "1-A", "life": 40, "hand": list(hand), "battlefield": list(battlefield)},
                        {"name": "2-B", "life": 40, "battlefield": []}]}


class TraitTest(unittest.TestCase):
    def test_different_commanders_different_traits(self):
        a, s = commander.traits(ANHELO), commander.traits(SWARMLORD)
        self.assertEqual((a["cheap"], a["spell_engine"], a["big_body"]), (1.0, 1.0, 0.0))
        self.assertEqual((s["cheap"], s["big_body"], s["draws"], s["grows_when_recast"]), (0.0, 1.0, 1.0, 1.0))
        self.assertEqual(commander.engine_types(ANHELO), {"instant", "sorcery"})

    def test_anhelo_wants_spells_in_hand(self):
        none = commander.cast_features(ANHELO, state(hand=[BEAR]))
        three = commander.cast_features(ANHELO, state(hand=[BOLT, BOLT, BOLT, BEAR]))
        self.assertEqual(none["cmdr_engine_fuel"], 0.0)
        self.assertEqual(three["cmdr_engine_fuel"], 0.75)
        self.assertEqual(three["cmdr_is_anhelo"], 1.0)        # learned per-commander adjustment

    def test_swarmlord_is_a_late_open_board_threat(self):
        early = commander.cast_features(SWARMLORD, state(turn=1))
        late = commander.cast_features(SWARMLORD, state(turn=41))
        self.assertEqual((early["cmdr_big_late"], late["cmdr_big_late"]), (0.0, 1.0))
        self.assertEqual(late["cmdr_big_open_board"], 1.0)    # nobody has blockers
        self.assertAlmostEqual(late["cmdr_grows_recast"], 2 / 6)

    def test_spells_feed_the_engine_while_commander_is_out(self):
        s = state(battlefield=[ANHELO])
        options = [{"index": 0, "is_spell": True, "is_land": False, "card": BOLT},
                   {"index": 1, "is_spell": True, "is_land": False, "card": BEAR}]
        feats = features.play_options({"state": s, "options": options})
        self.assertEqual(feats[0]["feeds_my_commander"], 1.0)
        self.assertEqual(feats[1]["feeds_my_commander"], 0.0)


if __name__ == "__main__":
    unittest.main()


class DiscountTest(unittest.TestCase):
    def test_affinity_counts_my_equipment(self):
        nahiri = {"name": "Nahiri", "cmc": 6, "power": 5, "toughness": 4, "commander": True,
                  "oracle": "Affinity for Equipment (This spell costs {1} less to cast for each Equipment you control.)"}
        sword = {"name": "Sword", "type": "Artifact - Equipment"}
        self.assertEqual(commander.discount_now(nahiri, {"battlefield": []}), 0.0)
        self.assertAlmostEqual(commander.discount_now(nahiri, {"battlefield": [sword, sword]}), 2 / 6)
        self.assertEqual(commander.discount_now({"oracle": "Draw a card."}, {}), 1.0)
