"""Reasons for the viewer's Reasons tab (showdown/boys/explain.py). No Forge needed."""

from __future__ import annotations

import unittest

from showdown.boys import explain
from showdown.boys.brain import Brain
from showdown.viewer.events import TimelineBuilder


class ExplainTest(unittest.TestCase):
    def setUp(self):
        self.brain = Brain("tester_explain", weights={"attack": {"target_is_leader": 4.0, "target_bad_block": -3.0,
                                                                 "simple_prior": 2.0}})

    def test_own_call_and_reasons(self):
        options = [{"stay_home": 1.0, "simple_prior": 1.0},
                   {"target_is_leader": 1.0, "simple_prior": 0.0},
                   {"target_bad_block": 1.0, "simple_prior": 0.0}]
        r = explain.explain(self.brain, "attack", options, 1, [0.1, 0.8, 0.1], ["home", "at Kuja", "at Zidane"])
        self.assertEqual(r["follow"], "own call")
        self.assertEqual(r["simple_pick"], "home")
        self.assertEqual(r["runner_up"], "home")
        self.assertEqual(r["reasons"][0], ("the biggest threat", 4.0, "for"))
        self.assertIn("His own choice (SimpleBot: home). 80% sure. For it: the biggest threat (strong).",
                      explain.sentence(r))

    def test_avoids(self):
        options = [{"stay_home": 1.0, "simple_prior": 1.0}, {"target_bad_block": 1.0, "simple_prior": 0.0}]
        r = explain.explain(self.brain, "attack", options, 0, [0.9, 0.1])
        self.assertEqual(r["follow"], "follows")
        self.assertEqual(r["reasons"], [("a blocker would kill it", 3.0, "against")])
        self.assertIn("Against the 2nd choice, option 2: a blocker would kill it (strong).", explain.sentence(r))

    def test_timeline_counts_every_choice_but_writes_only_interesting_ones(self):
        b = TimelineBuilder()
        routine = {"kind": "play", "picked": "pass", "runner_up": "Bear", "sure": 0.6, "follow": "follows",
                   "simple_pick": None, "reasons": []}
        own = dict(routine, picked="Bear", runner_up="pass", follow="own call", simple_pick="pass")
        events = b.feed({"type": "answer", "decision": {"kind": "play", "player": "1-A"}, "choice": None,
                         "bot": "boy:X", "reasons": [routine, own]})
        self.assertEqual([e["kind"] for e in events], ["bot_reason"])
        self.assertEqual(events[0]["data"]["counts"], {"follows": 1, "own call": 1})


if __name__ == "__main__":
    unittest.main()
