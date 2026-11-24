"""Fixes from Adam's replay notes (2026-11-24): partners, ward, instant removal,
board wipes, gang-ups on the weakest, tapped lands, bounce lands. No Forge needed."""

from __future__ import annotations

import unittest

from showdown import deals
from showdown.boys import features, mana, removal, threat
from showdown.bots.simple_bot import SimpleBot, land_priority
from showdown.deals import DealBook


def land(cid, produces="G", tapped=False, oracle=""):
    return {"id": cid, "name": f"L{cid}", "type": "Land", "cmc": 0, "produces": produces,
            "tapped": tapped, "oracle": oracle}


def creature(cid, power, toughness=None, controller="2-B", cmc=3, oracle="", **extra):
    return {"id": cid, "name": f"C{cid}", "type": "Creature", "cmc": cmc, "power": power,
            "toughness": power if toughness is None else toughness, "controller": controller,
            "oracle": oracle, **extra}


def table(my_lands=2, boards=None, hand=None, lives=(30, 40, 20, 25)):
    names = ["1-A", "2-B", "3-C", "4-D"]
    boards = boards or {}
    players = []
    for n, life in zip(names, lives):
        bf = list(boards.get(n, []))
        if n == "1-A":
            bf += [land(100 + i, "W") for i in range(my_lands)]
        players.append({"name": n, "life": life, "hand_size": len(hand or []) if n == "1-A" else 3,
                        "battlefield": bf, "hand": list(hand or []) if n == "1-A" else []})
    return {"turn": 9, "active_player": "1-A", "you": "1-A", "phase": "MAIN1", "stack_size": 0,
            "players": players}


SWORDS = {"id": 60, "name": "Swords", "type": "Instant", "cmc": 1,
          "oracle": "Exile target creature. Its controller gains life equal to its power."}
WARDED = creature(70, 3, oracle="Ward {2}\nWhenever an opponent casts a spell, ...")


class WardTest(unittest.TestCase):
    def test_ward_cost(self):
        self.assertEqual(removal.ward_cost(WARDED), 2)
        self.assertEqual(removal.ward_cost({"oracle": "Flying\nWard {1}{U}"}), 2)
        self.assertEqual(removal.ward_cost({"oracle": "Ward—Pay 3 life."}), 0)  # not mana
        self.assertEqual(removal.ward_cost({"oracle": "Flying"}), 0)

    def test_target_with_unpayable_ward(self):
        state = table(my_lands=2, boards={"2-B": [WARDED], "3-C": [creature(71, 2, controller="3-C")]})
        decision = {"kind": "choose_targets", "state": state, "api": "ChangeZone",
                    "params": {"Origin": "Battlefield", "Destination": "Exile"},
                    "description": "Exile target creature.", "source": SWORDS, "suggested": [0], "min": 1, "max": 1,
                    "candidates": [dict(WARDED, index=0, is_player=False), dict(creature(71, 2, controller="3-C"),
                                                                             index=1, is_player=False)]}
        feats = features.target_options(decision)
        self.assertEqual(feats[0]["target_ward_unpayable"], 1.0)  # 2 lands - 1 for Swords = 1 < ward 2
        self.assertEqual(feats[1]["target_ward_unpayable"], 0.0)
        self.assertEqual(SimpleBot().choose_targets(decision), [1])  # Forge's warded pick is swapped
        state["players"][0]["battlefield"].append(land(200, "W"))  # 3 lands: 2 spare, ward paid
        self.assertEqual(features.target_options(decision)[0]["target_ward_unpayable"], 0.0)
        self.assertIsNone(SimpleBot().choose_targets(decision))

    def test_waits_to_cast_removal_into_ward(self):
        state = table(my_lands=2, boards={"2-B": [WARDED]}, hand=[SWORDS])
        option = {"index": 0, "is_spell": True, "card": SWORDS, "description": ""}
        self.assertIn("removal_ward_unpayable", removal.removal_features(option, state, True, deals.removes))
        decision = {"state": state, "options": [option]}
        self.assertTrue(SimpleBot().keep_for_later(option, decision))


class InstantRemovalTest(unittest.TestCase):
    def test_no_rush_and_keeping_mana_up(self):
        state = table(my_lands=3, boards={"2-B": [creature(70, 3)]},
                      hand=[SWORDS, {"id": 61, "name": "Big", "type": "Sorcery", "cmc": 3, "oracle": "Draw three cards."}])
        swords = {"index": 0, "is_spell": True, "card": SWORDS, "description": ""}
        self.assertIn("instant_removal_no_rush", removal.removal_features(swords, state, True, deals.removes))
        big = {"index": 1, "is_spell": True, "card": state["players"][0]["hand"][1], "description": ""}
        self.assertEqual(removal.taps_out_holding_removal(big, state, True, deals.removes),
                         {"taps_out_holding_removal": 1.0})
        # Something hits me hard: removing it now is fine.
        state["players"][1]["battlefield"][0]["recent_damage"] = 5
        self.assertNotIn("instant_removal_no_rush", removal.removal_features(swords, state, True, deals.removes))


class PartnerTest(unittest.TestCase):
    def test_harm_on_partner_breaks_the_deal(self):
        book = DealBook(["1-A", "2-B", "3-C", "4-D"])
        book.deals.append(deals.Deal(id=1, kind="gang_up", a="1-A", b="2-B", against="3-C", made_turn=8,
                                     expires_turn=12))
        bear = creature(50, 2, controller="2-B")
        decision = {"kind": "choose_targets", "api": "Destroy", "params": {}, "state": table(),
                    "source": {"name": "Doom Blade"}, "candidates": [dict(bear, index=0)], "suggested": [0]}
        msgs = book.note_target("1-A", decision, [0])
        self.assertEqual([m["event"] for m in msgs], ["break"])
        self.assertIn("by aiming Doom Blade at C50", msgs[0]["text"])
        self.assertEqual(msgs[0]["deal"]["broken_how"], "harm")

    def test_partner_features_and_simple_bot(self):
        state = table(boards={"2-B": [creature(50, 5)], "3-C": [creature(51, 2, controller="3-C")]})
        decision = {"kind": "choose_targets", "state": state, "api": "Destroy", "params": {},
                    "description": "Destroy target creature.", "source": {"name": "Doom Blade", "cmc": 2},
                    "deals": {"protected": ["2-B"]}, "suggested": [0], "min": 1, "max": 1,
                    "candidates": [dict(creature(50, 5), index=0, is_player=False),
                                   dict(creature(51, 2, controller="3-C"), index=1, is_player=False)]}
        feats = features.target_options(decision)
        self.assertEqual(feats[0]["hurt_partner"], 1.0)
        self.assertEqual(feats[1]["hurt_partner"], 0.0)
        self.assertEqual(SimpleBot().choose_targets(decision), [1])


class WipeTest(unittest.TestCase):
    WRATH = {"id": 80, "name": "Wrath", "type": "Sorcery", "cmc": 4, "oracle": "Destroy all creatures. They can't be regenerated."}

    def test_losses(self):
        mine = [creature(1, 2, controller="1-A", cmc=2)]
        theirs = [creature(10, 6, cmc=6), creature(11, 4, cmc=4)]
        state = table(boards={"1-A": mine, "2-B": theirs})
        losses = removal.wipe_losses(self.WRATH, state)
        self.assertEqual(losses["1-A"], 4)
        self.assertEqual(losses["2-B"], 20)
        option = {"is_spell": True, "card": self.WRATH}
        f = removal.wipe_features(option, state, {"protected": []})
        self.assertAlmostEqual(f["wipe_my_share"], 4 / 24)
        self.assertEqual(f["wipe_good_trade"], 1.0)
        f = removal.wipe_features(option, state, {"protected": ["2-B"]})
        self.assertAlmostEqual(f["wipe_partner_share"], 20 / 24)

    def test_sacrifice_and_damage(self):
        state = table(boards={"1-A": [creature(1, 2, controller="1-A", cmc=2)],
                              "2-B": [creature(10, 6, cmc=6), creature(11, 1, cmc=1)]})
        edict = {"oracle": "Each player sacrifices a creature."}
        self.assertEqual(removal.wipe_losses(edict, state)["2-B"], 2)  # they give up the 1/1
        quake = {"oracle": "Pyroclasm deals 2 damage to each creature."}
        self.assertEqual(removal.wipe_losses(quake, state)["2-B"], 2)  # only the 1/1 dies
        self.assertIsNone(removal.wipe_losses(SWORDS, state))

    def test_simple_bot_holds_a_wipe_that_hurts_itself_most(self):
        state = table(boards={"1-A": [creature(1, 6, controller="1-A", cmc=6)], "2-B": [creature(10, 1, cmc=1)]})
        option = {"index": 0, "is_spell": True, "card": self.WRATH, "description": ""}
        self.assertTrue(SimpleBot().keep_for_later(option, {"state": state, "options": [option]}))


class GangUpOnWeakestTest(unittest.TestCase):
    def board(self):
        # 2-B big, 3-C stuck with nothing, 4-D medium. 3-C is the weakest.
        return table(boards={"2-B": [creature(10, 6), creature(11, 5)],
                             "4-D": [creature(30, 3, controller="4-D")],
                             "1-A": [creature(1, 3, controller="1-A")]}, lives=(30, 40, 30, 30))

    def test_punching_down(self):
        s = self.board()
        self.assertTrue(threat.punching_down(s, "1-A", "3-C"))
        self.assertFalse(threat.punching_down(s, "1-A", "2-B"))

    def test_gang_up_on_the_weakest_is_disliked_not_banned(self):
        s = self.board()
        view = {"kind": "offer_deal", "player": "1-A", "state": s,
                "deals": DealBook(["1-A", "2-B", "3-C", "4-D"]).view_for("1-A", s)}
        feats, offers = features.offer_options(view)
        on_c = [f for f, o in zip(feats, offers) if o["type"] == "gang_up" and o["against"] == "3-C"]
        on_b = [f for f, o in zip(feats, offers) if o["type"] == "gang_up" and o["against"] == "2-B"]
        self.assertTrue(on_c)  # still an option the Boys can learn about
        self.assertTrue(all(f["gang_against_weakest"] == 1.0 for f in on_c))
        self.assertTrue(all(f["gang_against_weakest"] == 0.0 for f in on_b))

    def test_answers(self):
        s = self.board()
        s["you"] = "4-D"
        book = DealBook(["1-A", "2-B", "3-C", "4-D"])
        view = {"state": s, "offer": {"a": "2-B", "kind": "gang_up", "against": "3-C", "rounds": 2},
                "deals": book.view_for("4-D", s)}
        self.assertFalse(SimpleBot().answer_deal(view))
        self.assertEqual(features.answer_options(view)[0]["accept_against_weakest"], 1.0)

    def test_two_v_two_offer_features(self):
        s = self.board()
        book = DealBook(["1-A", "2-B", "3-C", "4-D"])
        book.deals.append(deals.Deal(id=1, kind="gang_up", a="2-B", b="4-D", against="1-A", made_turn=8,
                                     expires_turn=12))
        view = {"kind": "offer_deal", "player": "1-A", "state": s, "deals": book.view_for("1-A", s)}
        self.assertEqual(view["deals"]["ganging_me"], ["2-B", "4-D"])
        feats, offers = features.offer_options(view)
        by = {(o["to"], o.get("against")): f for f, o in zip(feats, offers) if o["type"] == "gang_up"}
        self.assertEqual(by[("3-C", "2-B")]["gang_outsider_vs_ganger"], 1.0)  # the 2v2
        self.assertEqual(by[("3-C", "2-B")]["gang_to_ganging_me"], 0.0)


class LandTest(unittest.TestCase):
    WILDS = land(20, "", oracle="{T}, Sacrifice Evolving Wilds: Search your library for a basic land card, "
                                "put it onto the battlefield tapped, then shuffle.")
    TAPLAND = land(21, "UR", oracle="Swiftwater Cliffs enters tapped.\n{T}: Add {U} or {R}.")
    BASIC = land(22, "R")
    BOUNCE = land(23, "RG", oracle="Gruul Turf enters tapped.\nWhen Gruul Turf enters, return a land you control "
                                   "to its owner's hand.\n{T}: Add {R}{G}.")

    def opts(self, *lands):
        return [{"index": i, "card": c, "is_land": True, "is_spell": False, "description": ""} for i, c in enumerate(lands)]

    def test_wilds_counts_as_tapped(self):
        self.assertTrue(mana.slow_land(self.WILDS))
        self.assertFalse(mana.slow_land(self.BASIC))

    def test_quiet_turn_prefers_a_tapped_land(self):
        for slow in (self.TAPLAND, self.WILDS):
            me = {"name": "1-A", "battlefield": [], "hand": [slow, self.BASIC], "hand_size": 2}
            opts = self.opts(slow, self.BASIC)
            best = max(opts, key=lambda o: land_priority(o, me, opts))
            self.assertEqual(best["card"]["id"], slow["id"])

    def test_keeping_an_instant_up_needs_the_untapped_land(self):
        two_drop = {"id": 40, "name": "Bear", "type": "Creature", "cmc": 2, "cost": "{1}{R}", "oracle": ""}
        bolt = {"id": 41, "name": "Bolt", "type": "Instant", "cmc": 1, "cost": "{R}", "oracle": "Bolt deals 3 damage to any target."}
        me = {"name": "1-A", "battlefield": [land(1, "R"), land(2, "R")], "hand": [two_drop, bolt, self.TAPLAND, self.BASIC]}
        self.assertEqual(mana.land_fit(self.TAPLAND, me), {"tapland_costs_a_play": 1.0})

    def test_bounce_land_with_a_full_hand(self):
        hand = [self.BOUNCE, self.BASIC] + [{"id": 50 + i, "type": "Sorcery", "cmc": 9} for i in range(6)]
        me = {"name": "1-A", "battlefield": [land(1, "R")], "hand": hand, "hand_size": 8}
        opts = self.opts(self.BOUNCE, self.BASIC)
        best = max(opts, key=lambda o: land_priority(o, me, opts))
        self.assertEqual(best["card"]["id"], self.BASIC["id"])
        state = {"turn": 5, "active_player": "1-A", "you": "1-A", "phase": "MAIN1", "stack_size": 0,
                 "players": [dict(me, life=40)]}
        feats = features.play_options({"state": state, "options": opts})
        self.assertEqual(feats[0].get("land_bounce_full_hand"), 1.0)
        self.assertFalse(feats[1].get("land_bounce_full_hand"))


if __name__ == "__main__":
    unittest.main()
