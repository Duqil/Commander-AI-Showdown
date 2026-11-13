"""Tests for the browser viewer's Python side. No Java, no network needed.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from showdown.viewer.card_images import CardImages, file_name, load_deck_printings
from showdown.viewer.events import EVENT_KINDS, TimelineBuilder, describe_choice, make_event
from showdown.viewer.session import GameSession


def state(turn=1, active="1-Zidane", life=40):
    return {"turn": turn, "active_player": active, "phase": "MAIN1", "stack": [],
            "players": [{"name": "1-Zidane", "life": life}, {"name": "2-Kuja", "life": 40}]}


class TimelineBuilderTest(unittest.TestCase):
    def setUp(self):
        self.b = TimelineBuilder()

    def test_game_start(self):
        [ev] = self.b.feed({"type": "game_start", "seats": [{"seat": 0, "name": "1-Zidane", "controller": "python"}]})
        self.assertEqual(ev["kind"], "game_start")
        self.assertEqual(ev["data"]["seats"][0]["name"], "1-Zidane")

    def test_log_lines_get_categories_and_the_last_one_gets_the_state(self):
        events = self.b.feed({"type": "event", "log": ["x", "y"], "state": state(turn=3),
                              "entries": [{"type": "LAND", "text": "played Mountain"},
                                          {"type": "STACK_ADD", "text": "cast Bolt"}]})
        self.assertEqual([e["category"] for e in events], ["LAND", "STACK_ADD"])
        self.assertNotIn("state", events[0])
        self.assertEqual(events[1]["state"]["turn"], 3)
        self.assertEqual(events[1]["turn"], 3)

    def test_old_bridge_without_entries(self):
        [ev] = self.b.feed({"type": "event", "log": ["Turn 1"]})
        self.assertEqual(ev["text"], "Turn 1")

    def test_state_without_log_lines_still_becomes_an_event(self):
        [ev] = self.b.feed({"type": "event", "log": [], "entries": [], "state": state()})
        self.assertEqual(ev["category"], "STATE")
        self.assertIn("state", ev)

    def test_a_new_choice_on_a_card_is_logged_once(self):
        st = state()
        st["players"][0]["battlefield"] = [{"id": 5, "name": "Thriving Bluff", "controller": "1-Zidane",
                                            "chosen": {"color": "blue"}}]
        msg = {"type": "event", "log": [], "entries": [], "state": st}
        events = self.b.feed(msg)
        self.assertEqual(events[-1]["category"], "CHOICE")
        self.assertEqual(events[-1]["text"], "1-Zidane chose Blue for Thriving Bluff")
        self.assertEqual([e for e in self.b.feed(msg) if e["category"] == "CHOICE"], [])  # not again

    def test_answer_becomes_bot_choice(self):
        decision = {"kind": "mulligan", "player": "1-Zidane"}
        [ev] = self.b.feed({"type": "answer", "decision": decision, "choice": True, "bot": "simple"})
        self.assertEqual(ev["kind"], "bot_choice")
        self.assertIn("keeps its hand", ev["text"])

    def test_game_over(self):
        [ev] = self.b.feed({"type": "game_over", "winner": "2-Kuja", "draw": False, "turns": 20, "players": []})
        self.assertIn("2-Kuja wins", ev["text"])
        self.assertEqual(ev["data"]["winner"], "2-Kuja")

    def test_unknown_message_type_is_ignored(self):
        self.assertEqual(self.b.feed({"type": "something_new"}), [])


class DescribeChoiceTest(unittest.TestCase):
    def test_attack(self):
        decision = {"kind": "attack",
                    "attackers": [{"id": 10, "name": "Bear"}, {"id": 11, "name": "Goblin"}],
                    "defenders": [{"index": 0, "name": "2-Kuja"}, {"index": 1, "name": "3-Nelly"}]}
        self.assertEqual(describe_choice(decision, {"10": 1, "11": 1}), "attacks with Bear, Goblin at 3-Nelly")
        self.assertEqual(describe_choice(decision, {}), "decides not to attack")

    def test_extra_costs_and_picks_are_shown(self):
        """Forge's own log never mentions casualty, so the bot's choice is the only record."""
        cost = {"kind": "keyword_cost", "keyword": "Casualty:2", "spell": {"name": "Some Charm"}}
        self.assertEqual(describe_choice(cost, None), "pays Casualty 2 for Some Charm")  # Forge's choice: pay
        self.assertEqual(describe_choice(cost, 1), "pays Casualty 2 for Some Charm")
        self.assertEqual(describe_choice(cost, 0), "skips Casualty 2 for Some Charm")
        pick = {"kind": "pick_card", "candidates": [{"index": 0, "name": "Island"}, {"index": 1, "name": "Swamp"}]}
        self.assertEqual(describe_choice(pick, 1), "picks Swamp")
        self.assertIsNone(describe_choice(pick, None))

    def test_passing_is_not_shown(self):
        self.assertIsNone(describe_choice({"kind": "choose_play"}, None))
        self.assertIsNone(describe_choice({"kind": "choose_play"}, 0))


class EventTest(unittest.TestCase):
    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            make_event("no_such_kind", "hi")

    def test_deal_kinds_are_reserved(self):
        self.assertIn("deal_offer", EVENT_KINDS)
        self.assertEqual(make_event("deal_offer", "x", deal="truce")["data"], {"deal": "truce"})


class SessionTest(unittest.TestCase):
    def test_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            import showdown.viewer.session as session_module
            old = session_module.GAMES_DIR
            session_module.GAMES_DIR = Path(tmp)
            try:
                s = GameSession(None, "test-game")
                s.add_event(make_event("game_start", "go"))
                s.add_event(make_event("log", "hello", state=state()))
                path = s.save()
                loaded = GameSession.load(path)
            finally:
                session_module.GAMES_DIR = old
        self.assertEqual([e["seq"] for e in loaded.events], [0, 1])
        self.assertEqual(loaded.events_since(1)[0]["text"], "hello")
        self.assertEqual(loaded.status, "finished")


class CardImagesTest(unittest.TestCase):
    def test_file_names_are_safe(self):
        name = file_name("Zidane, Tantalus Thief // ../evil")
        self.assertTrue(name.endswith(".jpg"))
        self.assertNotIn("/", name)
        self.assertNotEqual(file_name("A"), file_name("token:A"))

    def test_printings_come_from_deck_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "d.txt").write_text("1 Lightning Bolt (2X2) 117\n1 Fire // Ice (MH2) 290\n", encoding="utf-8")
            printings = load_deck_printings(Path(tmp))
        self.assertEqual(printings["Lightning Bolt"], ("2x2", "117"))
        self.assertEqual(printings["Fire"], ("mh2", "290"))  # front face also works

    def test_cached_image_is_served_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            images = CardImages(Path(tmp))
            Path(tmp, file_name("Sol Ring")).write_bytes(b"jpeg!")
            self.assertEqual(images.get("Sol Ring"), b"jpeg!")

    def test_offline_is_not_remembered_as_missing(self):
        from unittest import mock
        from showdown.viewer.card_images import ScryfallUnreachable
        with tempfile.TemporaryDirectory() as tmp:
            images = CardImages(Path(tmp))
            with mock.patch.object(images, "_fetch", side_effect=ScryfallUnreachable("offline")):
                self.assertIsNone(images.get("Sol Ring"))
            self.assertNotIn("Sol Ring", images._missing)  # try again once we're back online
            with mock.patch.object(images, "_fetch", return_value=None):  # 404: Scryfall doesn't know it
                self.assertIsNone(images.get("Not A Card"))
            self.assertIn("Not A Card", images._missing)

    def test_network_errors_become_unreachable(self):
        from unittest import mock
        from showdown.viewer.card_images import ScryfallUnreachable
        images = CardImages(Path(tempfile.gettempdir()) / "no-such-cache")
        for error in (urllib.error.URLError("no network"), TimeoutError(),
                      urllib.error.HTTPError("u", 429, "slow down", None, None)):
            with mock.patch("urllib.request.urlopen", side_effect=error):
                with self.assertRaises(ScryfallUnreachable):
                    images._fetch("https://api.scryfall.com/x")
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 404, "nope", None, None)):
            self.assertIsNone(images._fetch("https://api.scryfall.com/x"))


class TokenScoreTest(unittest.TestCase):
    def test_rules_text_picks_the_right_knight(self):
        from showdown.viewer.card_images import token_score
        vigilance = {"name": "Knight", "colors": ["W"], "power": "2", "toughness": "2", "oracle_text": "Vigilance"}
        haste = {"name": "Knight", "colors": ["W"], "power": "2", "toughness": "2",
                 "oracle_text": "Haste\nProtection from white (This creature can't be blocked by white creatures.)"}
        wanted = {"colors": "W", "pt": "2/2", "oracle": "Vigilance"}
        self.assertGreater(token_score(vigilance, "Knight", wanted), token_score(haste, "Knight", wanted))


class BotNameTest(unittest.TestCase):
    def test_valid_bots(self):
        from showdown.viewer.server import valid_bot
        for name in ["simple", "random", "forge", "boy:alice", "boy:new_one-2", "boy:Nicol Bolas",
                     "boy:Urza's Heir", "boy:K"]:
            self.assertTrue(valid_bot(name), name)
        for name in ["", "boy:", "boy:../evil", "smart", "boy: Bolas", "boy:Bolas ", "boy:a/b", "boy:Bolas."]:
            self.assertFalse(valid_bot(name), name)


class BatchStopTest(unittest.TestCase):
    def test_stop_now_aborts_running_games(self):
        from showdown.viewer.batch_job import BatchJob
        job = BatchJob(["a", "b"], ["simple", "simple"], games=3)  # not started: no Forge needed
        job.stop()
        self.assertTrue(job._stop.is_set())
        self.assertFalse(job._abort.is_set())  # plain stop lets running games finish
        job.stop(now=True)
        self.assertTrue(job._abort.is_set())
        self.assertEqual(job.status, "stopping")
        job.stop(now=True, wait=1)  # never started: nothing to wait for, and no error


class SessionStopTest(unittest.TestCase):
    def test_stop_reaches_forge_and_seats_get_their_own_seeds(self):
        from unittest import mock
        import showdown.viewer.session as session_module
        from showdown.bridge.game_runner import GameAborted
        from showdown.viewer.session import GameConfig
        seen = {}

        def fake_run_game(seats, abort=None, **kwargs):
            seen["abort"], seen["seeds"] = abort, [s.bot.rng.random() for s in seats]
            raise GameAborted("stopped")

        session = GameSession(GameConfig(["a", "b"], ["random", "random"], seed=5), "test")
        with mock.patch.object(session_module, "run_game", fake_run_game):
            session._run()
        self.assertIs(seen["abort"], session._stop)  # Stop can kill Forge right away
        self.assertNotEqual(seen["seeds"][0], seen["seeds"][1])  # not the same dice
        self.assertEqual(session.status, "stopped")


class SessionLoadingTest(unittest.TestCase):
    def test_a_bot_that_cannot_be_made_shows_an_error_instead_of_hanging(self):
        from unittest import mock
        import showdown.viewer.session as session_module
        from showdown.viewer.session import GameConfig

        def broken_bot(*args, **kwargs):
            raise ValueError("brain file is broken")

        session = GameSession(GameConfig(["a", "b"], ["boy:X", "boy:Y"]), "test")
        with mock.patch.object(session_module, "make_bot", broken_bot), mock.patch("traceback.print_exc"):
            session._run()
        self.assertEqual(session.status, "error")  # before, the status stayed "starting" forever
        self.assertIn("brain file is broken", session.events[-1]["text"])

    def test_the_clock_moves_the_bar_between_steps_but_never_to_100(self):
        from showdown.viewer.session import GameConfig
        session = GameSession(GameConfig(["a", "b"], ["random", "random"]), "test")
        session.loading = {"percent": 5, "text": "Loading card rules"}
        session._expected_seconds = 10
        session._started_at -= 5  # 5 of about 10 seconds gone
        self.assertEqual(session.summary()["loading"]["percent"], 50)
        session._started_at -= 60  # much slower than last time
        self.assertEqual(session.summary()["loading"]["percent"], 97)

    def test_loading_progress_goes_forward_and_ends_with_the_first_table(self):
        from unittest import mock
        import showdown.viewer.session as session_module
        from showdown.viewer.session import GameConfig
        seen = []

        def fake_run_game(seats, on_message=None, **kwargs):
            on_message({"type": "loading", "percent": 50, "text": "Loading card rules"})
            on_message({"type": "loading", "percent": 40, "text": "late message"})  # out of order: ignored
            seen.append(session.summary()["loading"])
            on_message({"type": "game_start", "seats": []})
            on_message({"type": "event", "log": [], "state": {"turn": 0}})
            seen.append(session.summary()["loading"])
            raise session_module.GameStopped()

        session = GameSession(GameConfig(["a", "b"], ["random", "random"]), "test")
        with mock.patch.object(session_module, "run_game", fake_run_game), \
                mock.patch.object(session_module, "expected_loading_seconds", lambda: None), \
                mock.patch.object(session_module, "remember_loading_seconds") as remember:
            session._run()
        remember.assert_called_once()  # the next game's bar can use this game's time
        self.assertEqual((seen[0]["percent"], seen[0]["text"]), (50, "Loading card rules"))
        self.assertIn("seconds", seen[0])
        self.assertIsNone(seen[1])  # the table is here: no more loading bar


class StatsPageTest(unittest.TestCase):
    def test_groups_and_fair_share(self):
        from unittest import mock
        from showdown.viewer import stats_page
        rows = []
        for game, winner in (("g1", 0), ("g2", 0), ("g3", 1)):
            for seat, (deck, bot) in enumerate([("zidane", "boy:kaiba"), ("kuja", "simple")]):
                rows.append({"game_id": game, "deck": deck, "bot": bot, "won": int(seat == winner),
                             "draw": 0, "turns": 20, "source": "batch" if game != "g3" else "viewer",
                             "time": "2026-11-11 12:00:00"})
        with mock.patch.object(stats_page.stats, "read_results", return_value=rows):
            s = stats_page.summary()
            self.assertEqual(s["games"], 3)
            self.assertEqual(s["fair_share"], 0.5)  # 2 players in these games
            kaiba = next(r for r in s["bots"] if r["name"] == "boy:kaiba")
            self.assertEqual((kaiba["games"], kaiba["wins"]), (3, 2))
            self.assertEqual(stats_page.summary("viewer")["games"], 1)


class ServerTest(unittest.TestCase):
    """Start the real web server on a free port and ask it things."""

    @classmethod
    def setUpClass(cls):
        from showdown.viewer.server import serve
        cls.server = serve(port=0)  # port 0 = any free port
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
            return r.status, r.read()

    def test_page_and_decks(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Commander AI Showdown", body)
        decks = json.loads(self.get("/api/decks")[1])
        self.assertIn("zidane", decks["decks"])

    def test_static_files_cannot_escape_the_folder(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/static/../server.py")
        self.assertEqual(ctx.exception.code, 404)

    def test_no_batch_yet_and_bad_batch_is_refused(self):
        import showdown.viewer.server as server
        if server.STATE.batch is None:
            self.assertEqual(json.loads(self.get("/api/batch")[1])["status"], "none")
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/batch/start",
                                     data=b'{"decks": ["zidane", "kuja"], "bots": ["simple", "boy:../x"]}', method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)

    def post(self, path, body: bytes):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        return ctx.exception.code

    def test_unknown_decks_and_bad_numbers_are_refused(self):
        self.assertEqual(self.post("/api/game/new", b'{"decks": ["zidane", "../../x"], "bots": ["simple", "simple"]}'), 400)
        self.assertEqual(self.post("/api/batch/start", b'{"decks": ["zidane", "nope"], "bots": ["simple", "simple"]}'), 400)
        self.assertEqual(self.post("/api/batch/start",
                                   b'{"decks": ["zidane", "kuja"], "bots": ["simple", "simple"], "games": "lots"}'), 400)
        self.assertEqual(self.post("/api/game/new", b'not json'), 400)

    def test_bad_new_game_is_refused(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/game/new", data=b'{"decks": ["zidane"]}',
                                     method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
