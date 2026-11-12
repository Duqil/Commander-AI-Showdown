"""Tests for the Python side that don't need Forge or Java.

A "fake Forge" thread speaks the same protocol over a real socket, so the
game loop, the connection and the bots are all tested for real.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import socket
import threading
import unittest

from showdown.bots.simple_bot import SimpleBot
from showdown.bridge.connection import BridgeConnection, listen
from showdown.bridge.game_runner import ForgeGameError, play
from showdown.decks.convert import parse_line


def make_state(you="1-Zidane", phase="MAIN1", active="1-Zidane", hand=None):
    return {
        "turn": 1, "active_player": active, "phase": phase, "you": you, "stack_size": 0,
        "players": [
            {"name": "1-Zidane", "life": 40, "hand": hand or [], "battlefield": []},
            {"name": "2-Kuja", "life": 31, "battlefield": []},
            {"name": "3-Nelly", "life": 12, "battlefield": []},
        ],
    }


def card(cid, name, cmc=0, type_="Creature", power=None):
    c = {"id": cid, "name": name, "cmc": cmc, "type": type_}
    if power is not None:
        c["power"] = power
    return c


class SimpleBotTest(unittest.TestCase):
    def setUp(self):
        self.bot = SimpleBot()

    def test_plays_land_first(self):
        decision = {"state": make_state(), "options": [
            {"index": 0, "card": card(1, "Big Spell", 6), "is_land": False, "is_spell": True},
            {"index": 1, "card": card(2, "Mountain", 0, "Basic Land"), "is_land": True, "is_spell": False},
        ]}
        self.assertEqual(self.bot.choose_play(decision), 1)

    def test_casts_most_expensive_spell(self):
        decision = {"state": make_state(), "options": [
            {"index": 0, "card": card(1, "Small", 1), "is_land": False, "is_spell": True},
            {"index": 1, "card": card(2, "Big", 5), "is_land": False, "is_spell": True},
        ]}
        self.assertEqual(self.bot.choose_play(decision), 1)

    def test_passes_on_opponents_turn(self):
        decision = {"state": make_state(active="2-Kuja"), "options": [
            {"index": 0, "card": card(1, "Bolt", 1), "is_land": False, "is_spell": True},
        ]}
        self.assertIsNone(self.bot.choose_play(decision))

    def test_attacks_weakest_opponent_with_big_creatures(self):
        decision = {
            "state": make_state(),
            "defenders": [
                {"index": 0, "name": "2-Kuja", "is_player": True},
                {"index": 1, "name": "3-Nelly", "is_player": True},
            ],
            "attackers": [
                {**card(10, "Bear", power=2), "can_attack": [0, 1]},
                {**card(11, "Squirrel", power=1), "can_attack": [0, 1]},
            ],
        }
        self.assertEqual(self.bot.declare_attackers(decision), {"10": 1})

    def test_mulligan(self):
        lands = [card(i, "Mountain", 0, "Basic Land") for i in range(3)]
        spells = [card(10 + i, "Spell", 2, "Instant") for i in range(4)]
        self.assertTrue(self.bot.keep_hand({"state": make_state(hand=lands + spells)}))
        self.assertFalse(self.bot.keep_hand({"state": make_state(hand=(spells * 2)[:7])}))  # 0 lands


class DeckLineTest(unittest.TestCase):
    def test_normal_line(self):
        line = parse_line("1 Zidane, Tantalus Thief (FIN) 405")
        self.assertEqual((line.count, line.name, line.set_code, line.number), (1, "Zidane, Tantalus Thief", "fin", "405"))

    def test_the_list_line(self):
        line = parse_line("1 Vow of Lightning (PLST) CMR-209")
        self.assertEqual((line.set_code, line.number), ("cmr", "209"))

    def test_blank_line(self):
        self.assertIsNone(parse_line(""))


class FakeForgeTest(unittest.TestCase):
    """Run the real game loop against a fake Forge over a real socket."""

    def test_full_exchange(self):
        server = listen()
        port = server.getsockname()[1]
        answers = []

        def fake_forge():
            s = socket.create_connection(("127.0.0.1", port))
            r, w = s.makefile("r"), s.makefile("w")

            def send(msg):
                w.write(json.dumps(msg) + "\n")
                w.flush()

            send({"type": "game_start", "seats": [
                {"seat": 0, "name": "1-Zidane", "controller": "python"},
                {"seat": 1, "name": "2-Kuja", "controller": "forge"},
            ]})
            send({"type": "event", "log": ["Turn 1 (1-Zidane)"]})
            send({"type": "decision", "id": 1, "kind": "choose_play", "player": "1-Zidane",
                  "state": make_state(), "options": [
                      {"index": 0, "card": card(2, "Mountain", 0, "Basic Land"), "is_land": True, "is_spell": False}]})
            answers.append(json.loads(r.readline()))
            send({"type": "decision", "id": 2, "kind": "something_new", "player": "1-Zidane", "state": make_state()})
            answers.append(json.loads(r.readline()))
            send({"type": "game_over", "winner": "1-Zidane", "draw": False, "turns": 9, "players": []})
            s.close()

        t = threading.Thread(target=fake_forge)
        t.start()
        sock, _ = server.accept()
        result = play(BridgeConnection(sock), [SimpleBot(), None])
        t.join()
        server.close()

        self.assertEqual(answers, [{"id": 1, "choice": 0}, {"id": 2, "choice": None}])
        self.assertEqual(result.winner, "1-Zidane")
        self.assertEqual(result.decisions, 2)
        self.assertEqual(result.log, ["Turn 1 (1-Zidane)"])

    def test_mulligans_are_counted(self):
        """The game loop tells bots how many mulligans they took (the first is free)."""
        server = listen()
        port = server.getsockname()[1]
        seen = []

        class Counter(SimpleBot):
            def keep_hand(self, decision):
                seen.append(decision["mulligans_so_far"])
                return len(seen) >= 3   # mulligan twice, then keep

        def fake_forge():
            s = socket.create_connection(("127.0.0.1", port))
            r, w = s.makefile("r"), s.makefile("w")
            send = lambda msg: (w.write(json.dumps(msg) + "\n"), w.flush())
            send({"type": "game_start", "seats": [{"seat": 0, "name": "1-Zidane", "controller": "python"}]})
            for i in range(3):
                send({"type": "decision", "id": i, "kind": "mulligan", "player": "1-Zidane",
                      "state": make_state(), "cards_to_bottom_if_kept": max(0, i - 1)})
                r.readline()
            send({"type": "game_over", "winner": "1-Zidane", "draw": False, "turns": 1, "players": []})
            s.close()

        t = threading.Thread(target=fake_forge)
        t.start()
        sock, _ = server.accept()
        play(BridgeConnection(sock), [Counter()])
        t.join()
        server.close()
        self.assertEqual(seen, [0, 1, 2])

    def test_game_that_broke_inside_forge_is_an_error_not_a_draw(self):
        server = listen()
        port = server.getsockname()[1]

        class Learner(SimpleBot):
            learned = False

            def game_over(self, msg, player_name):
                Learner.learned = True

        def fake_forge():
            s = socket.create_connection(("127.0.0.1", port))
            w = s.makefile("w")
            for msg in ({"type": "game_start", "seats": [{"seat": 0, "name": "1-Zidane"}]},
                        {"type": "game_over", "draw": True, "turns": 7, "players": [],
                         "error": "java.lang.StackOverflowError"}):
                w.write(json.dumps(msg) + "\n")
            w.flush()
            s.close()

        t = threading.Thread(target=fake_forge)
        t.start()
        sock, _ = server.accept()
        with self.assertRaises(ForgeGameError):
            play(BridgeConnection(sock), [Learner()])
        t.join()
        server.close()
        self.assertFalse(Learner.learned)  # Boys mustn't learn from a broken game


if __name__ == "__main__":
    unittest.main()
