"""A small web server: the browser page talks to Python through it.

Uses only Python's standard library (``http.server``), so nothing to install.

What the browser can ask for (an "API"):

    GET  /                          the page itself (static/index.html)
    GET  /static/<file>             the page's JavaScript and CSS
    GET  /api/decks                 deck names in decks/forge/ and bot types
    GET  /api/game                  the current game's status
    GET  /api/game/events?since=N   timeline events from position N on
    POST /api/game/new              start a game. Body: {"decks": [...], "bots": [...], "seed": 7, "deals": true}
    POST /api/game/stop             stop the running game
    GET  /api/saved                 saved games in games/
    POST /api/saved/load            watch a saved game again. Body: {"id": "..."}
    GET  /api/card-image?name=X     a card picture (tokens: &token=1, and &colors=W&pt=2/2&oracle=...)
    POST /api/batch/start           run many games without watching. Body: {"decks", "bots",
                                    "games": 20, "parallel": 1, "seed", "save_games", "learn", "deals"}
    GET  /api/batch                 progress and results of that batch
    GET  /api/stats?source=batch    win rates per deck and bot (source is optional)
    POST /api/batch/stop            start no more games. Body {"now": true} also ends running ones

The browser asks for new events about twice a second ("polling"). Simple, and
plenty fast for a local app.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from showdown.batch import bots_available
from showdown.boys.storage import VALID_NAME
from showdown.run_game import DECK_DIR
from showdown.viewer.batch_job import BatchJob
from showdown.viewer.card_images import CardImages
from showdown.viewer import stats_page
from showdown.viewer.session import GAMES_DIR, GameConfig, GameSession, list_saved_games, new_game_id

STATIC_DIR = Path(__file__).parent / "static"


def valid_bot(name: str) -> bool:
    """simple / random / forge, or "boy:<name>" (a new name creates a new Boy)."""
    if name.startswith("boy:"):
        return bool(VALID_NAME.match(name.removeprefix("boy:")))
    return name in ("simple", "random", "forge")
CONTENT_TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
                 ".svg": "image/svg+xml", ".png": "image/png"}


class ViewerState:
    """Everything the server remembers between requests. One game at a time."""

    def __init__(self) -> None:
        self.session: GameSession | None = None
        self.batch: BatchJob | None = None
        self.images = CardImages()
        self.lock = threading.Lock()

    def start_game(self, config: GameConfig) -> GameSession:
        with self.lock:
            if self.session:
                self.session.stop()  # only one Forge at a time
            self.session = GameSession(config, new_game_id(config))
            self.session.start()
            return self.session


STATE = ViewerState()


class Handler(BaseHTTPRequestHandler):
    """``http.server`` creates one Handler per request and calls do_GET / do_POST."""

    # --- small helpers ---------------------------------------------------------

    def send_json(self, data, status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_bytes(body, "application/json", status)

    def send_bytes(self, body: bytes, content_type: str, status: int = 200, cache: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Card images never change, so the browser may keep them; everything else it must re-ask.
        self.send_header("Cache-Control", "max-age=604800" if cache else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def log_message(self, format, *args) -> None:
        pass  # don't print a line for every request (there are many)

    # --- routes ----------------------------------------------------------------

    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)  # "?since=5" -> {"since": ["5"]}
        path = url.path

        if path == "/":
            return self.serve_static("index.html")
        if path.startswith("/static/"):
            return self.serve_static(path.removeprefix("/static/"))
        if path == "/api/decks":
            decks = sorted(p.stem for p in DECK_DIR.glob("*.dck"))
            return self.send_json({"decks": decks, "bots": bots_available()})
        if path == "/api/batch":
            return self.send_json(STATE.batch.summary() if STATE.batch else {"status": "none"})
        if path == "/api/stats":
            return self.send_json(stats_page.summary(query.get("source", [None])[0]))
        if path == "/api/game":
            return self.send_json(STATE.session.summary() if STATE.session else {"status": "none"})
        if path == "/api/game/events":
            if not STATE.session:
                return self.send_json({"events": [], "status": "none"})
            since = int(query.get("since", ["0"])[0])
            return self.send_json({"events": STATE.session.events_since(since),
                                   **STATE.session.summary()})
        if path == "/api/saved":
            return self.send_json({"games": list_saved_games()})
        if path == "/api/card-image":
            name = query.get("name", [""])[0]
            token = query.get("token", ["0"])[0] == "1"
            details = {k: query[k][0] for k in ("colors", "pt", "oracle") if k in query}
            data = STATE.images.get(name, token, details) if name else None
            if data is None:
                return self.send_json({"error": "no image"}, 404)
            return self.send_bytes(data, "image/jpeg", cache=True)
        self.send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self.read_json()

        if path == "/api/game/new":
            decks, bots = body.get("decks", []), body.get("bots", [])
            if not 2 <= len(decks) <= 4 or len(bots) != len(decks):
                return self.send_json({"error": "Pick 2 to 4 decks, one bot each"}, 400)
            if not all(valid_bot(b) for b in bots):
                return self.send_json({"error": "Bots must be simple, random, forge or boy:<name>"}, 400)
            seed = body.get("seed")
            config = GameConfig(decks=decks, bots=bots, seed=int(seed) if seed not in (None, "") else None,
                                deals=bool(body.get("deals")))
            return self.send_json(STATE.start_game(config).summary())
        if path == "/api/batch/start":
            decks, bots = body.get("decks", []), body.get("bots", [])
            if not 2 <= len(decks) <= 4 or len(bots) != len(decks) or not all(valid_bot(b) for b in bots):
                return self.send_json({"error": "Pick 2 to 4 decks with a valid bot each"}, 400)
            if STATE.batch and STATE.batch.status in ("running", "stopping"):
                return self.send_json({"error": "A batch is already running"}, 409)
            games = max(1, min(int(body.get("games") or 10), 10000))
            parallel = max(1, min(int(body.get("parallel") or 1), 5))
            seed = body.get("seed")
            STATE.batch = BatchJob(decks, bots, games, parallel,
                                   seed=int(seed) if seed not in (None, "") else None,
                                   save_games=bool(body.get("save_games")), learn=bool(body.get("learn", True)),
                                   deals=bool(body.get("deals")))
            STATE.batch.start()
            return self.send_json(STATE.batch.summary())
        if path == "/api/batch/stop":
            if STATE.batch:
                STATE.batch.stop(now=bool(body.get("now")))
            return self.send_json({"ok": True})
        if path == "/api/game/stop":
            if STATE.session:
                STATE.session.stop()
            return self.send_json({"ok": True})
        if path == "/api/saved/load":
            file = GAMES_DIR / f"{Path(body.get('id', '')).name}.json.gz"  # .name blocks "../" tricks
            if not file.exists():
                return self.send_json({"error": "no such saved game"}, 404)
            with STATE.lock:
                if STATE.session:
                    STATE.session.stop()
                STATE.session = GameSession.load(file)
            return self.send_json(STATE.session.summary())
        self.send_json({"error": "not found"}, 404)

    def serve_static(self, name: str) -> None:
        file = (STATIC_DIR / name).resolve()
        # Only serve files that really are inside static/.
        if STATIC_DIR.resolve() not in file.parents or not file.is_file():
            return self.send_json({"error": "not found"}, 404)
        self.send_bytes(file.read_bytes(), CONTENT_TYPES.get(file.suffix, "application/octet-stream"))


def serve(port: int = 8000, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Create the server. ThreadingHTTPServer answers each request in its own thread."""
    return ThreadingHTTPServer((host, port), Handler)
