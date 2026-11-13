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
from showdown.boys import combat, critic, features, storage, threat
from showdown.boys.brain import Brain
from showdown.boys.reward import game_reward, turn_bonus


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

    def test_combat_trick_features(self):
        """After blocks: my creature 1 is unblocked, my creature 2 is blocked by B's creature 10."""
        state = table(phase="COMBAT_DECLARE_BLOCKERS")
        me, b = state["players"][0], state["players"][1]
        me["battlefield"] = [creature(1, 3, attacking="3-C"), creature(2, 2, attacking="2-B")]
        b["battlefield"][0].update(blocking=True, blocks=[2])
        decision = {"state": state, "description": "Choose target creature you control. Whenever "
                    "that creature deals combat damage to a player, draw that many cards.",
                    "api": "Effect", "params": {"ValidTgts": "Creature.YouCtrl"}, "suggested": [0],
                    "min": 1, "max": 1, "candidates": [
                        {"index": 0, **creature(1, 3, controller="1-A"), "is_player": False},
                        {"index": 1, **creature(2, 2, controller="1-A"), "is_player": False}]}
        feats = features.target_options(decision)
        self.assertEqual(feats[0]["help_my_unblocked"], 1.0)   # "damage" in the text, but it's a gift
        self.assertEqual(feats[1]["help_my_blocked"], 1.0)
        self.assertEqual(feats[0]["hurt_mine"], 0.0)

        kill = {**decision, "api": "Destroy", "params": {}, "candidates": [
            {"index": 0, **b["battlefield"][0], "controller": "2-B", "is_player": False}]}
        self.assertEqual(features.target_options(kill)[0]["hurt_blocker_of_mine"], 1.0)

        plays = features.play_options({"state": state, "options": [
            {"index": 0, "card": {"name": "Trick", "type": "Instant", "cmc": 3},
             "description": "trick", "is_land": False, "is_spell": True}]})
        self.assertEqual(plays[0]["trick_my_unblocked"], 1.0)
        self.assertEqual(plays[0]["trick_my_blocked"], 1.0)
        self.assertEqual(plays[1]["pass_in_fight"], 1.0)

    def test_no_unblocked_before_blocks(self):
        state = table(phase="COMBAT_DECLARE_ATTACKERS")
        state["players"][0]["battlefield"] = [creature(1, 3, attacking="3-C")]
        fight = combat.view(state)
        self.assertEqual(fight.my_unblocked, set())   # blockers aren't declared yet
        self.assertFalse(fight.in_trick_window())
        state["phase"] = "MAIN1"
        plays = features.play_options({"state": state, "options": [
            {"index": 0, "card": {"name": "Trick", "type": "Instant", "cmc": 3},
             "description": "trick", "is_land": False, "is_spell": True}]})
        self.assertNotIn("play_in_combat", plays[0])

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
        # Even when Forge's AI likes it, an instant waits in our main phase...
        self.assertIsNone(pick(dict(starstorm, forge_would_play=True)))
        # ...until the end of the turn right before ours (D plays just before A).
        end_step = table(active="4-D", phase="END_OF_TURN")
        self.assertEqual(bot.choose_play({"state": end_step, "options": [dict(starstorm, forge_would_play=True)]}), 5)


RIKU = ("Whenever you cast a modal spell, choose up to X, where X is the number of times you chose a mode "
        "for that spell \u2014\n\u2022 Exile the top card of your library. Until the end of your next turn, "
        "you may play it.\n\u2022 Put a +1/+1 counter on Riku.\n\u2022 Create a 1/1 blue Bird creature token with flying.")


def spell(index, name="Spell", ctype="Sorcery", cmc=2, **extra):
    return {"index": index, "card": {"name": name, "type": ctype, "cmc": cmc}, "description": name,
            "is_land": False, "is_spell": True, "is_ability": False, **extra}


def land(index, name="Forest", **extra):
    return {"index": index, "card": {"name": name, "type": "Land", "cmc": 0}, "description": name,
            "is_land": True, "is_spell": False, "is_ability": False, **extra}


class TimingTest(unittest.TestCase):
    """Instant speed and impulse draws (showdown/boys/timing.py)."""

    def test_end_step_before_mine(self):
        from showdown.boys import timing
        self.assertTrue(timing.end_step_before_mine(table(active="4-D", phase="END_OF_TURN")))
        self.assertFalse(timing.end_step_before_mine(table(active="3-C", phase="END_OF_TURN")))
        self.assertFalse(timing.end_step_before_mine(table(active="4-D", phase="MAIN2")))
        # D is out: C's turn is now the one right before mine.
        state = table(active="3-C", phase="END_OF_TURN")
        state["players"][3]["lost"] = True
        self.assertTrue(timing.end_step_before_mine(state))

    def test_impulse_text(self):
        from showdown.boys import timing
        self.assertTrue(timing.is_impulse({"oracle": RIKU}))
        self.assertTrue(timing.is_impulse({"oracle": "Exile the top two cards of your library. Choose one "
                                                     "of them. Until the end of your next turn, you may play that card."}))
        self.assertFalse(timing.is_impulse({"oracle": "Exile target creature."}))
        self.assertFalse(timing.is_impulse({"oracle": "Look at the top card of your library."}))

    def test_simple_bot_answers_opponents_only(self):
        from showdown.bots.simple_bot import SimpleBot
        counter = spell(0, "Counter", "Instant", api="Counter", forge_would_play=True)
        state = table(active="2-B", phase="MAIN1")
        state["stack_size"] = 1
        state["stack"] = [{"id": 9, "kind": "spell", "controller": "2-B"}]
        bot = SimpleBot()
        self.assertEqual(bot.choose_play({"state": state, "options": [counter]}), 0)
        state["stack"][0]["controller"] = "1-A"     # my own spell: leave it alone
        self.assertIsNone(bot.choose_play({"state": state, "options": [counter]}))
        # A counterspell Forge's AI doesn't want at the end step stays in hand; a draw spell gets cast.
        end_step = table(active="4-D", phase="END_OF_TURN")
        idle = dict(counter, forge_would_play=False)
        draw = spell(1, "Draw", "Instant", api="Draw", forge_would_play=False)
        self.assertIsNone(bot.choose_play({"state": end_step, "options": [idle]}))
        self.assertEqual(bot.choose_play({"state": end_step, "options": [idle, draw]}), 1)
        # Some other player's end step: keep waiting.
        self.assertIsNone(bot.choose_play({"state": table(active="2-B", phase="END_OF_TURN"), "options": [draw]}))

    def test_simple_bot_plays_exiled_cards_first(self):
        from showdown.bots.simple_bot import SimpleBot
        bot = SimpleBot()
        pick = lambda *opts: bot.choose_play({"state": table(), "options": list(opts)})
        self.assertEqual(pick(land(0), land(1, zone="Exile")), 1)
        self.assertEqual(pick(spell(0, cmc=5), spell(1, cmc=2, zone="Exile")), 1)
        # An instant from exile can't wait: it may run out.
        self.assertEqual(pick(spell(2, "Quick", "Instant", zone="Exile")), 2)

    def test_timing_features(self):
        state = table()
        riku = creature(5, 3, oracle=RIKU)
        state["players"][0]["battlefield"].append(riku)
        options = [land(0), spell(1, "Modal"), spell(2, "Counter", "Instant", api="Counter"),
                   land(3, "Island", zone="Exile")]
        feats = features.play_options({"state": state, "options": options})
        self.assertEqual(feats[0]["land_impulse_pending"], 1.0)   # Riku could still exile a land
        self.assertNotIn("land_impulse_pending", feats[3])        # this land IS the exiled one
        self.assertEqual(feats[3]["land_from_exile"], 1.0)
        self.assertEqual(feats[2]["reactive_on_my_main"], 1.0)
        self.assertEqual(feats[4]["pass_with_exile_card"], 1.0)

        impulse = spell(0, "Impulse", "Instant")
        impulse["card"]["oracle"] = "Exile the top card of your library. You may play it this turn."
        feats = features.play_options({"state": table(), "options": [impulse, land(1)]})
        self.assertEqual(feats[0]["impulse_before_land"], 1.0)
        self.assertEqual(feats[1]["land_impulse_pending"], 1.0)

        end_step = table(active="4-D", phase="END_OF_TURN")
        feats = features.play_options({"state": end_step, "options": [spell(0, "Draw", "Instant")]})
        self.assertEqual(feats[0]["instant_end_step_before_mine"], 1.0)
        self.assertEqual(feats[1]["pass_end_step_with_instant"], 1.0)

        respond = table(active="2-B")
        respond["stack_size"] = 1
        respond["stack"] = [{"id": 9, "kind": "spell", "controller": "2-B"}]
        feats = features.play_options({"state": respond, "options": [
            spell(0, "Counter", "Instant", api="Counter", forge_would_play=True)]})
        self.assertEqual(feats[0]["respond_forge_would_play"], 1.0)


class SequencingTest(unittest.TestCase):
    def test_hardened_scales_before_tervigon(self):
        scales = {"index": 0, "card": {"name": "Hardened Scales", "type": "Enchantment", "cmc": 1,
                  "oracle": "If one or more +1/+1 counters would be put on a creature you control, "
                            "that many plus one +1/+1 counters are put on it instead."},
                  "is_land": False, "is_spell": True}
        tervigon = {"index": 1, "card": {"name": "Tervigon", "type": "Creature", "cmc": 4},
                    "is_land": False, "is_spell": True, "max_x": 3}
        bear = {"index": 2, "card": {"name": "Bear", "type": "Creature", "cmc": 4, "oracle": ""},
                "is_land": False, "is_spell": True}
        feats = features.play_options({"state": table(), "options": [scales, tervigon, bear]})
        self.assertEqual(feats[0]["booster_before_x"], 1.0)
        self.assertAlmostEqual(feats[0]["x_left_after"], 2 / 7)   # X 3 -> 2 after Scales
        self.assertAlmostEqual(feats[1]["x_size"], 3 / 7)
        self.assertEqual(feats[2]["kills_the_x"], 1.0)            # the 4-drop eats all the mana
        self.assertNotIn("before_x_creature", feats[1])           # the X creature itself


class GraveyardTest(unittest.TestCase):
    def options(self):
        land = {"index": 0, "card": {"name": "Swamp", "type": "Land", "cmc": 0}, "is_land": True, "is_spell": False}
        bolt = {"index": 1, "card": {"name": "Bolt", "type": "Instant", "cmc": 1}, "is_land": False, "is_spell": True}
        return [land, bolt]

    def test_casualty_before_land_with_bloodghast(self):
        state = table()
        me = state["players"][0]
        me["battlefield"] = [creature(5, 2, graveyard_use="landfall", name="Bloodghast"),
                             {"id": 6, "name": "Anhelo", "type": "Creature", "power": 2, "grants": ["Casualty"]}]
        land, bolt = features.play_options({"state": state, "options": self.options()})[:2]
        self.assertEqual(bolt["casualty_before_land"], 1.0)
        self.assertEqual(land["land_before_casualty"], 1.0)

    def test_land_returns_bloodghast_from_graveyard(self):
        state = table()
        state["players"][0]["graveyard"] = [{"id": 5, "name": "Bloodghast", "graveyard_use": "landfall"}]
        land = features.play_options({"state": state, "options": self.options()})[0]
        self.assertAlmostEqual(land["land_returns_creatures"], 1 / 3)
        self.assertNotIn("land_before_casualty", land)

    def test_discarding_flashback_card_is_not_punished(self):
        bot = BoyBot("tester", learn=False, seed=1)
        mine = table()
        mine["players"][0].update(hand_size=8, library_size=60, graveyard=[])
        bot.choose_play({"state": mine, "options": []})
        later = table(active="2-B")
        later["turn"] = 6
        later["players"][0].update(hand_size=7, library_size=60,
                                   graveyard=[{"id": 9, "name": "Think Twice", "graveyard_use": "keyword"}])
        bot.choose_play({"state": later, "options": []})
        bot.game_over({"winner": "2-B", "players": later["players"]}, "1-A")
        self.assertEqual(bot.result["discarded"], 0)


class BrainTest(unittest.TestCase):
    def test_softmax_sums_to_one(self):
        probs = Brain("t").probabilities("play", [{"simple_prior": 1.0}, {}, {}])
        self.assertAlmostEqual(sum(probs), 1.0)
        self.assertGreater(probs[0], probs[1])

    def test_mulligan_eagerness_stops(self):
        """Joey's real weights made him mulligan forever; deeper mulligans must lose."""
        brain = Brain("joey")
        brain.weights["mulligan"] = {"simple_prior": 1.34, "mulligan": 0.82, "keep": -0.82,
                                     "keep_good_lands": -0.70, "keep_land_distance": -0.25}
        hand = [{"name": "Forest", "type": "Land"}] * 3 + [{"name": "Bear", "type": "Creature"}] * 4
        state = table(); state["players"][0]["hand"] = hand
        def keeps(to_bottom):
            opts = features.mulligan_options({"state": state, "cards_to_bottom_if_kept": to_bottom})
            return brain.choose("mulligan", opts, random.Random(0), explore=False).picked == 0
        self.assertTrue(keeps(2))   # two mulligans deep: keep

    def test_first_mulligan_is_free(self):
        state = table(); state["players"][0]["hand"] = [{"type": "Land"}] * 3 + [{"type": "Creature"}] * 4
        first = features.mulligan_options({"state": state, "cards_to_bottom_if_kept": 0, "mulligans_so_far": 0})
        second = features.mulligan_options({"state": state, "cards_to_bottom_if_kept": 0, "mulligans_so_far": 1})
        self.assertEqual(first[1]["mulligan_deeper"], 0.0)
        self.assertAlmostEqual(second[1]["mulligan_deeper"], 1 / 3)

    def test_mulligans_ignore_the_turn_bonus_baseline(self):
        """A mulligan has no turn, so the usual turn bonus mustn't count against it."""
        brain = Brain("t", games=10, baseline=0.5, bonus_baseline=0.3)
        options = [{"keep": 1.0}, {"mulligan": 1.0}]
        step = brain.choose("mulligan", options, random.Random(0))
        step.pregame = True
        brain.learn([step], reward=0.5)          # an average game, no cards lost
        self.assertTrue(all(w == 0.0 for w in brain.weights["mulligan"].values()))

    def test_mulligans_learn_slower(self):
        fast, slow = Brain("a", games=1), Brain("b", games=1)
        for brain, kind in ((fast, "attack"), (slow, "mulligan")):
            step = brain.choose(kind, [{"x": 1.0}, {}], random.Random(0), explore=False)
            step.pregame = kind == "mulligan"
            brain.learn([step], reward=1.0)
        self.assertAlmostEqual(slow.weights["mulligan"]["x"], fast.weights["attack"]["x"] / 4)

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


class CriticTest(unittest.TestCase):
    def test_bias_alone_is_the_old_running_average(self):
        """With only the bias, the critic must learn exactly like the old baseline did."""
        weights, baseline = {"bias": 0.5}, 0.5
        for reward in (1.0, 0.0, 0.25, 1.5):
            critic.learn(weights, [{"bias": 1.0}] * 7, reward)
            baseline += 0.05 * (reward - baseline)
        self.assertAlmostEqual(weights["bias"], baseline)

    def test_situation_reads_the_table(self):
        state = table()
        state["players"][0]["command"] = [{"name": "Kuja", "commander": True, "type": "Creature"}]
        s = critic.situation(state)
        self.assertEqual(s["deck:Kuja"], 1.0)
        self.assertAlmostEqual(s["my_life"], 30 / 40)
        self.assertAlmostEqual(s["rivals_left"], 1.0)
        for name, value in s.items():
            self.assertTrue(0.0 <= value <= 1.0, name)
        self.assertEqual(critic.situation(None), {"bias": 1.0})

    def test_transformed_commander_is_still_the_same_deck(self):
        state = table()
        state["players"][0]["battlefield"].append({"name": "Trance Kuja", "commander": True, "type": "Creature"})
        self.assertIn("deck:Trance Kuja", critic.situation(state))
        s = critic.situation(state, deck="Kuja")
        self.assertIn("deck:Kuja", s)
        self.assertNotIn("deck:Trance Kuja", s)

    def test_learns_that_one_deck_is_weaker(self):
        weights: dict[str, float] = {}
        weak, strong = {"bias": 1.0, "deck:Weak": 1.0}, {"bias": 1.0, "deck:Strong": 1.0}
        for _ in range(400):
            critic.learn(weights, [weak], 0.1)
            critic.learn(weights, [strong], 0.9)
        self.assertAlmostEqual(critic.expected(weights, weak), 0.1, places=2)
        self.assertAlmostEqual(critic.expected(weights, strong), 0.9, places=2)

    def test_old_brain_moves_over_unchanged(self):
        """A brain saved before the critic starts with its average as the bias,
        so its first game learns exactly like it would have before."""
        old = Brain("t", games=50, baseline=0.6).to_dict()
        del old["critic"]
        brain = Brain.from_dict(old)
        step = brain.choose("attack", [{"x": 1.0}, {}], random.Random(0), explore=False)
        step.situation = {"bias": 1.0, "my_life": 0.5}
        self.assertAlmostEqual(brain.learn([step], reward=1.0), 0.4)
        self.assertGreater(brain.critic["bias"], 0.6)
        self.assertIn("my_life", brain.critic)

    def test_bad_spot_is_not_blamed_on_the_choice(self):
        """When the critic already expects a bad game, losing it teaches nothing."""
        brain = Brain("t", games=50, baseline=0.5, critic={"bias": 0.5, "my_life": 0.0, "low": -0.5})
        step = brain.choose("attack", [{"x": 1.0}, {}], random.Random(0), explore=False)
        step.situation = {"bias": 1.0, "low": 1.0}
        brain.learn([step], reward=0.0)
        self.assertAlmostEqual(brain.weights["attack"].get("x", 0.0), 0.0)

    def test_critic_is_saved(self):
        brain = Brain("t", critic={"bias": 0.4, "deck:Kuja": 0.1})
        self.assertEqual(Brain.from_dict(brain.to_dict()).critic, {"bias": 0.4, "deck:Kuja": 0.1})


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

    def test_name_with_spaces_saves_and_lists(self):
        bot = BoyBot("Nicol Bolas", learn=True, seed=1)
        bot.declare_attackers(attack_decision())
        state = table()
        bot.last_state = state
        bot.game_over({"winner": "2-B", "players": state["players"]}, "1-A")
        self.assertTrue((Path(self.tmp.name) / "Nicol Bolas.json").exists())
        self.assertIn("Nicol Bolas", storage.list_boys())

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

    def test_cards_drawn_and_discarded(self):
        bot = BoyBot("tester", learn=False, seed=1)
        mine = table()
        mine["players"][0].update(hand_size=9, library_size=60)   # our turn, 9 cards in hand
        bot.choose_play({"state": mine, "options": []})
        later = table(active="2-B")
        later["turn"] = 6
        later["players"][0].update(hand_size=7, library_size=58)  # drew 2, then discarded 2 at cleanup
        bot.choose_play({"state": later, "options": []})
        bot.game_over({"winner": "2-B", "players": later["players"]}, "1-A")
        self.assertEqual(bot.result["drawn"], 2)
        self.assertEqual(bot.result["discarded"], 2)
        self.assertAlmostEqual(turn_bonus(0, 0, drawn=2, discarded=2), 0.02 - 0.06)

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

    def test_mulligan_bonus_survives_game_over(self):
        bot = BoyBot("tester_m2", learn=False, seed=1)
        bot.brain.weights["mulligan"] = {"keep": -50.0}
        state = table(); del state["active_player"], state["turn"]
        bot.keep_hand({"state": state, "cards_to_bottom_if_kept": 0, "mulligans_so_far": 0})
        bot.keep_hand({"state": state, "cards_to_bottom_if_kept": 0, "mulligans_so_far": 1})
        steps = list(bot.steps)
        bot.game_over({"winner": "2-B", "players": table()["players"]}, "1-A")
        self.assertEqual([s.bonus for s in steps], [0.0, -0.1])   # first free, second costs a card
        self.assertTrue(all(s.pregame for s in steps))

    def test_forget_resets_one_habit(self):
        """Yugi's real overnight weights: keeps a good 7 only ~30% of the time."""
        brain = storage.load_brain("Yugi")
        brain.weights["mulligan"] = {"mulligan_deeper": -1.71, "simple_prior": 1.33, "keep": -0.99,
                                     "mulligan": 0.99, "keep_good_lands": -0.69, "keep_land_distance": -0.40}
        brain.weights["attack"]["attack_power"] = 0.5
        storage.save_brain(brain)
        state = table(); state["players"][0]["hand"] = [{"type": "Land"}] * 3 + [{"type": "Creature"}] * 4
        opts = features.mulligan_options({"state": state, "cards_to_bottom_if_kept": 0, "mulligans_so_far": 0})
        self.assertLess(brain.probabilities("mulligan", opts)[0], 0.35)
        storage.forget("Yugi", "mulligan")
        storage._brains.clear()
        again = storage.load_brain("Yugi")
        self.assertGreater(again.probabilities("mulligan", opts)[0], 0.85)
        self.assertEqual(again.weights["attack"]["attack_power"], 0.5)   # other habits stay

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
