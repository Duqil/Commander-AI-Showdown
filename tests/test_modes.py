"""Choosing modes (showdown/boys/modes.py): Riku-style "choose up to X" triggers. No Forge needed."""

from __future__ import annotations

import random
import unittest

from showdown.boys import modes
from showdown.bots.boy_bot import BoyBot
from showdown.bots.simple_bot import SimpleBot

EXILE = {"index": 0, "description": "Exile the top card of your library. Until the end of your next turn, you may play it.",
         "effects": [{"api": "Dig", "params": {"Destination": "Exile"}}, {"api": "Effect", "params": {}}]}
COUNTER = {"index": 1, "description": "Put a +1/+1 counter on Riku. It gains trample until end of turn.",
           "effects": [{"api": "PutCounter", "params": {"CounterType": "P1P1"}}, {"api": "Pump", "params": {}}]}
BIRD = {"index": 2, "description": "Create a 1/1 blue Bird creature token with flying.",
        "effects": [{"api": "Token", "params": {}}]}


def land(cid, tapped=False):
    return {"id": cid, "name": f"L{cid}", "type": "Land", "cmc": 0, "produces": "U", "tapped": tapped, "oracle": ""}


def decision(untapped=0, tapped=3, hand=(), land_drop=False, suggested=(0,), hi=1):
    me = {"name": "1-A", "life": 40, "battlefield": [land(i) for i in range(untapped)] +
          [land(10 + i, True) for i in range(tapped)], "hand": list(hand), "hand_size": len(hand)}
    return {"kind": "choose_mode", "state": {"turn": 5, "active_player": "1-A", "you": "1-A", "phase": "MAIN2",
                                             "stack_size": 0, "players": [me]},
            "modes": [EXILE, COUNTER, BIRD], "min": 0, "max": hi, "suggested": list(suggested),
            "land_drop_left": land_drop}


SPELL = {"id": 50, "name": "Big", "type": "Sorcery", "cmc": 5, "cost": "{4}{U}", "oracle": ""}


class ModeTest(unittest.TestCase):
    def test_reading_modes(self):
        self.assertTrue(modes.is_impulse(EXILE))
        self.assertFalse(modes.is_impulse(COUNTER))
        self.assertTrue(modes.lasting(COUNTER) and modes.lasting(BIRD))

    def test_simple_bot_exiles_only_when_it_can_use_the_card(self):
        bot = SimpleBot()
        cheap = dict(SPELL, id=52, cmc=2, cost="{1}{U}")
        # Nothing to cast, empty hand, 3 mana open: exile (Forge's pick) is fine.
        self.assertIsNone(bot.choose_mode(decision(untapped=3)))
        # A land drop left, nothing to cast, small hand: fine too.
        self.assertIsNone(bot.choose_mode(decision(land_drop=True, hand=[SPELL])))
        # Only 1 mana open, a full hand of spells: counter.
        tapped_out = decision(untapped=1, tapped=3, hand=[SPELL, dict(SPELL, id=51)])
        self.assertEqual(bot.choose_mode(tapped_out), [1])
        # Mana open but a spell in hand to cast with it: counter instead.
        self.assertEqual(bot.choose_mode(decision(untapped=3, tapped=0, hand=[cheap, SPELL, dict(SPELL, id=51)])), [1])
        # Lasts until next turn, small hand, lots of spare mana next turn: exile.
        self.assertIsNone(bot.choose_mode(decision(untapped=0, tapped=6)))

    def test_up_to_two(self):
        d = decision(hand=[SPELL, dict(SPELL, id=51)], suggested=(0, 1), hi=2)
        self.assertEqual(sorted(SimpleBot().choose_mode(d)), [1, 2])  # both lasting modes

    def test_boy_features_and_choice(self):
        d = decision(hand=[SPELL, dict(SPELL, id=51)])
        feats, picks = modes.mode_options(d, [])
        self.assertEqual(picks, [None, 0, 1, 2])  # min 0: "stop" is allowed right away
        self.assertEqual(feats[1]["mode_impulse_wasted"], 1.0)
        self.assertEqual(feats[2]["mode_lasting"], 1.0)
        self.assertEqual(feats[2]["simple_prior"], 1.0)
        bot = BoyBot("tester_modes", learn=False, seed=4)
        bot.rng = random.Random(4)
        chosen = bot.choose_mode(d)
        self.assertLessEqual(len(chosen), 1)
        self.assertTrue(all(i in (0, 1, 2) for i in chosen))


if __name__ == "__main__":
    unittest.main()
