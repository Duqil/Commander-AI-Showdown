"""Tests for recent-damage tracking (showdown/damage.py). No Forge needed."""

from __future__ import annotations

import unittest

from showdown.boys import features, threat
from showdown.damage import DamageTracker


def kuja_table(turn=10):
    """B (Kuja) has only a 0/1 Wizard token; D has a 5/5. You are A."""
    wizard = {"id": 441, "name": "Wizard Token", "type": "Creature Wizard", "cmc": 0,
              "power": 0, "toughness": 1, "token": True, "controller": "2-B"}
    beast = {"id": 30, "name": "Beast", "type": "Creature", "cmc": 5, "power": 5, "toughness": 5,
             "controller": "4-D"}
    return {"turn": turn, "you": "1-A", "active_player": "1-A", "phase": "MAIN1", "stack_size": 0,
            "players": [
                {"name": "1-A", "life": 30, "hand_size": 3, "battlefield": []},
                {"name": "2-B", "life": 40, "hand_size": 3, "battlefield": [wizard]},
                {"name": "3-C", "life": 30, "hand_size": 3, "battlefield": []},
                {"name": "4-D", "life": 30, "hand_size": 3, "battlefield": [beast]},
            ]}


class DamageTrackerTest(unittest.TestCase):
    def ping_everyone(self, tracker, times):
        for _ in range(times):
            for who in ("1-A", "3-C", "4-D"):
                tracker.see_log(f"Wizard Token (441) deals 2 non-combat damage to {who}.")

    def test_counts_damage_by_player_and_card(self):
        tracker = DamageTracker()
        tracker.annotate(kuja_table(turn=9))
        self.ping_everyone(tracker, 3)
        tracker.see_log("Wizard Token (441) deals 2 damage to Beast (30).")  # creatures don't count
        state = kuja_table(turn=10)
        tracker.annotate(state)
        kuja = state["players"][1]
        self.assertEqual(kuja["recent_damage_to"], {"1-A": 6, "3-C": 6, "4-D": 6})
        self.assertEqual(kuja["battlefield"][0]["recent_damage"], 18)

    def test_old_damage_is_forgotten(self):
        tracker = DamageTracker()
        tracker.annotate(kuja_table(turn=5))
        self.ping_everyone(tracker, 1)
        state = kuja_table(turn=9)   # 4 players: turn 5 is more than a round ago
        tracker.annotate(state)
        self.assertEqual(state["players"][1]["recent_damage_to"], {})

    def test_kuja_engine_is_seen_as_a_threat(self):
        state = kuja_table()
        self.assertEqual(threat.leader(state), "4-D")          # power alone: the 5/5 looks scarier
        tracker = DamageTracker()
        tracker.annotate(kuja_table(turn=9))
        self.ping_everyone(tracker, 4)                          # 8 to each of us last round
        tracker.annotate(state)
        self.assertEqual(threat.leader(state), "2-B")
        self.assertAlmostEqual(threat.danger_to_me(state), 8 / 30)

    def test_boys_like_removing_the_pinger(self):
        tracker = DamageTracker()
        tracker.annotate(kuja_table(turn=9))
        self.ping_everyone(tracker, 3)
        state = kuja_table()
        tracker.annotate(state)
        wizard, beast = state["players"][1]["battlefield"][0], state["players"][3]["battlefield"][0]
        decision = {"state": state, "description": "Destroy target creature.", "api": "Destroy",
                    "params": {}, "suggested": [1], "min": 1, "max": 1,
                    "candidates": [{**wizard, "index": 0, "is_player": False},
                                   {**beast, "index": 1, "is_player": False}]}
        feats = features.target_options(decision)
        self.assertEqual(feats[0]["hurt_damage_dealer"], 1.0)
        self.assertEqual(feats[1]["hurt_damage_dealer"], 0.0)


if __name__ == "__main__":
    unittest.main()
