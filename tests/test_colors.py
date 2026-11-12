"""Tests for mana colors (showdown/boys/colors.py) and fetching lands. No Forge needed."""

from __future__ import annotations

import unittest

from showdown.bots.boy_bot import BoyBot
from showdown.bots.simple_bot import SimpleBot
from showdown.boys import colors, features

ISLAND = {"name": "Island", "type": "Basic Land - Island", "produces": "U"}
SWAMP = {"name": "Swamp", "type": "Basic Land - Swamp", "produces": "B"}
MOUNTAIN = {"name": "Mountain", "type": "Basic Land - Mountain", "produces": "R"}


def anhelo_state(battlefield, hand):
    """Anhelo-like: Islands out, a hand of black and red spells."""
    commander = {"name": "Anhelo", "type": "Legendary Creature", "cost": "{U}{B}{R}", "commander": True}
    return {"turn": 7, "you": "1-A", "active_player": "1-A", "phase": "MAIN1", "stack_size": 0,
            "players": [{"name": "1-A", "life": 40, "battlefield": battlefield, "hand": hand,
                         "command": [commander]},
                        {"name": "2-B", "life": 40, "battlefield": []}]}


BLACK_RED_HAND = [{"name": "Terminate", "type": "Instant", "cost": "{B}{R}"},
                  {"name": "Night's Whisper", "type": "Sorcery", "cost": "{1}{B}"}]


def fetch_decision(state):
    cands = [dict(land, index=i, id=100 + i) for i, land in enumerate([ISLAND, SWAMP, MOUNTAIN])]
    return {"kind": "pick_card", "how": "library_search", "state": state, "candidates": cands, "suggested": 0,
            "source": {"name": "Evolving Wilds"}}


class ColorTest(unittest.TestCase):
    def test_pips(self):
        self.assertEqual(colors.pips("{2}{U}{U/R}"), [{"U"}, {"U", "R"}])
        self.assertEqual(colors.pips("{X}{R}{G/P}"), [{"R"}])   # Phyrexian can be paid with life
        self.assertEqual(colors.pips(None), [])

    def test_needs_and_missing(self):
        me = anhelo_state([ISLAND, ISLAND], BLACK_RED_HAND)["players"][0]
        want = colors.needs(me)
        self.assertEqual((want["B"], want["R"], want["U"]), (3, 2, 1))   # hand + commander
        self.assertEqual(colors.missing(me), {"B", "R"})
        self.assertGreater(colors.land_value(SWAMP, me), colors.land_value(ISLAND, me))

    def test_any_color_means_my_colors(self):
        tower = {"name": "Command Tower", "type": "Land", "produces": "WUBRG"}
        me = anhelo_state([tower], BLACK_RED_HAND)["players"][0]
        self.assertEqual(colors.available(me), {"U", "B", "R"})   # no white or green in Anhelo


class FetchTest(unittest.TestCase):
    def test_simplebot_fetches_the_missing_color(self):
        """The bug Adam saw: Anhelo fetching Island after Island."""
        decision = fetch_decision(anhelo_state([ISLAND, ISLAND, ISLAND], BLACK_RED_HAND))
        self.assertEqual(SimpleBot().choose_card(decision), 1)   # Swamp: black is wanted most

    def test_simplebot_plays_the_land_it_needs(self):
        state = anhelo_state([ISLAND, ISLAND], BLACK_RED_HAND + [ISLAND, MOUNTAIN])
        options = [{"index": 0, "card": ISLAND, "is_land": True, "is_spell": False},
                   {"index": 1, "card": MOUNTAIN, "is_land": True, "is_spell": False}]
        self.assertEqual(SimpleBot().choose_play({"state": state, "options": options}), 1)

    def test_boy_sees_new_colors(self):
        decision = fetch_decision(anhelo_state([ISLAND, ISLAND], BLACK_RED_HAND))
        feats = features.pick_options(decision)
        self.assertEqual([f["pick_land_new_color"] for f in feats], [0.0, 1.0, 1.0])
        self.assertEqual([f["pick_land_only_old_colors"] for f in feats], [1.0, 0.0, 0.0])
        self.assertEqual(feats[1]["simple_prior"], 1.0)
        self.assertIn(BoyBot("tester_f", learn=False, seed=2).choose_card(decision), [0, 1, 2])

    def test_terramorphic_expanse_works_too(self):
        """No card names in the logic: any land search gets the same choice."""
        decision = fetch_decision(anhelo_state([ISLAND, ISLAND, ISLAND], BLACK_RED_HAND))
        decision["source"] = {"name": "Terramorphic Expanse"}
        self.assertEqual(SimpleBot().choose_card(decision), 1)

    def test_drawn_from_dreams_picks_the_right_land(self):
        """Look at the top 7, take 2: if Forge's pick is a land, take the one with the needed color."""
        top = [{"name": "Preordain", "type": "Sorcery", "cost": "{U}", "cmc": 1}, ISLAND, SWAMP,
               {"name": "Bloodghast", "type": "Creature", "cost": "{B}{B}", "cmc": 2}]
        cands = [dict(c, index=i) for i, c in enumerate(top)]
        decision = {"kind": "pick_card", "how": "look_at_top", "candidates": cands, "suggested": 1,
                    "state": anhelo_state([ISLAND, ISLAND], BLACK_RED_HAND), "source": {"name": "Drawn from Dreams"}}
        self.assertEqual(SimpleBot().choose_card(decision), 2)       # Swamp, not the Island
        decision["suggested"] = 3
        self.assertIsNone(SimpleBot().choose_card(decision))         # a spell: keep Forge's pick
        feats = features.pick_options(decision)
        self.assertEqual(feats[3]["pick_castable"], 0.0)              # no black source for Bloodghast yet
        self.assertEqual(feats[0]["pick_castable"], 1.0)
        self.assertEqual(feats[3]["simple_prior"], 1.0)

    def test_mulligan_sees_color_coverage(self):
        state = anhelo_state([], BLACK_RED_HAND + [ISLAND, ISLAND, SWAMP])
        keep = features.mulligan_options({"state": state, "cards_to_bottom_if_kept": 0})[0]
        self.assertEqual(keep["keep_color_coverage"], 0.5)   # Night's Whisper yes, Terminate no


if __name__ == "__main__":
    unittest.main()
