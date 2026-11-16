"""Mana this turn and next: tapping out, waiting a turn, which land (showdown/boys/mana.py)."""

from __future__ import annotations

import unittest

from showdown.boys import mana


def land(cid, produces="G", tapped=False, oracle=""):
    return {"id": cid, "name": f"L{cid}", "type": "Land", "cmc": 0, "produces": produces,
            "tapped": tapped, "oracle": oracle}


def card(cid, ctype, cmc, cost="", oracle=""):
    return {"id": cid, "name": f"S{cid}", "type": ctype, "cmc": cmc, "cost": cost, "oracle": oracle}


TAPLAND = "Swiftwater Cliffs enters tapped.\nWhen it enters, you gain 1 life.\n{T}: Add {U} or {R}."


class ManaTest(unittest.TestCase):
    def test_enters_tapped(self):
        self.assertTrue(mana.enters_tapped({"oracle": TAPLAND}))
        self.assertFalse(mana.enters_tapped({"oracle": "This land enters tapped unless you control two or more other lands."}))
        self.assertFalse(mana.enters_tapped({"oracle": "{T}: Add {G}."}))

    def test_counting_mana(self):
        me = {"battlefield": [land(1), land(2), land(3, tapped=True)], "hand": [land(4)]}
        self.assertEqual(mana.untapped_mana(me), 2)
        self.assertEqual(mana.mana_next_turn(me), 4)  # 3 lands untap + a land drop

    def test_tapping_out_with_an_instant(self):
        counter = card(9, "Instant", 2, "{U}{U}", "Counter target spell.")
        me = {"battlefield": [land(i, "U") for i in range(4)], "hand": [counter]}
        big = {"is_spell": True, "card": card(5, "Sorcery", 3), "description": ""}
        small = {"is_spell": True, "card": card(6, "Sorcery", 2), "description": ""}
        self.assertEqual(mana.tap_out_features(big, me, True),
                         {"taps_out_holding_instant": 1.0, "taps_out_holding_counter": 1.0})
        self.assertEqual(mana.tap_out_features(small, me, True), {})   # 2 left: the counter still fits
        self.assertEqual(mana.tap_out_features(big, me, False), {})    # not my main phase
        self.assertEqual(mana.keep_up_on_pass(me, "MAIN2", True), {"pass_keeping_instant_mana": 1.0})
        self.assertEqual(mana.keep_up_on_pass(me, "MAIN1", True), {})

    def test_engine_waits_for_fuel(self):
        engine = card(5, "Creature", 4, oracle="Whenever you cast an instant or sorcery spell, draw a card.")
        bolt = card(6, "Instant", 1)
        me = {"battlefield": [land(i) for i in range(4)], "hand": [engine, bolt, land(7)]}
        play_engine = {"is_spell": True, "card": engine, "description": ""}
        play_bolt = {"is_spell": True, "card": bolt, "description": ""}
        # 4 mana: the engine leaves none for the bolt; next turn 5 mana does both.
        self.assertEqual(mana.combo_timing_features(play_engine, me, True), {"engine_without_fuel_now": 1.0})
        self.assertEqual(mana.combo_timing_features(play_bolt, me, True), {"fuel_before_engine": 1.0})
        me["battlefield"].append(land(8))  # 5 mana now: do both this turn
        self.assertEqual(mana.combo_timing_features(play_engine, me, True), {})

    def test_land_fit(self):
        tapland = land(20, "UR", oracle=TAPLAND)
        basic = land(21, "U")
        three_drop = card(30, "Creature", 3, "{1}{U}{U}")
        me = {"battlefield": [land(1, "U"), land(2, "U")], "hand": [three_drop, tapland, basic]}
        self.assertEqual(mana.land_fit(tapland, me), {"tapland_costs_a_play": 1.0})
        self.assertEqual(mana.land_fit(basic, me), {})
        me["hand"] = [card(31, "Creature", 2, "{U}{U}"), tapland, basic]  # nothing needs a 3rd mana
        self.assertEqual(mana.land_fit(tapland, me), {"tapland_on_quiet_turn": 1.0})
        self.assertEqual(mana.land_fit(basic, me), {"untapped_land_on_quiet_turn": 1.0})

    def test_simple_bot_picks_the_fitting_land(self):
        from showdown.bots.simple_bot import SimpleBot
        tapland = land(20, "U", oracle=TAPLAND)
        basic = land(21, "U")
        opts = [{"index": 0, "card": tapland, "is_land": True, "is_spell": False, "description": ""},
                {"index": 1, "card": basic, "is_land": True, "is_spell": False, "description": ""}]
        def decision(hand):
            me = {"name": "me", "life": 40, "battlefield": [land(1, "U"), land(2, "U")], "hand": hand}
            return {"state": {"you": "me", "active_player": "me", "phase": "MAIN1", "stack_size": 0,
                              "players": [me]}, "options": opts}
        bot = SimpleBot()
        self.assertEqual(bot.choose_play(decision([card(30, "Creature", 3, "{1}{U}{U}"), tapland, basic])), 1)
        self.assertEqual(bot.choose_play(decision([card(31, "Creature", 2, "{U}{U}"), tapland, basic])), 0)


if __name__ == "__main__":
    unittest.main()
