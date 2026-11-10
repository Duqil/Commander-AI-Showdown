"""Card pictures from Scryfall, saved on disk so each one is downloaded only once.

Images are kept in vendor/card-images/ (inside this project, ignored by git).
Delete that folder to clear the cache.

Which picture? If a card is in one of your deck lists in decks/source/, we
fetch exactly that printing (set + collector number), so you see the art you
chose, including Final Fantasy flavor-name versions. Otherwise we ask
Scryfall for the card by name. Tokens are looked up among Scryfall's tokens.

Scryfall asks API users to:
  * send a User-Agent and an Accept header,
  * make no more than about 10 requests per second.
We wait at least 0.1 s between requests to respect that.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from showdown.decks.convert import parse_line
from showdown.run_game import REPO_ROOT

CACHE_DIR = REPO_ROOT / "vendor" / "card-images"
MISSING_FILE = CACHE_DIR / "missing.json"  # names Scryfall didn't know, so we don't ask again
SOURCE_DECKS = REPO_ROOT / "decks" / "source"
HEADERS = {"User-Agent": "CommanderAIShowdown/0.1 (local hobby viewer)", "Accept": "*/*"}
API = "https://api.scryfall.com"


class CardImages:
    def __init__(self, cache_dir: Path = CACHE_DIR) -> None:
        self.cache_dir = cache_dir
        self._lock = threading.Lock()   # one download at a time (rate limit)
        self._last_request = 0.0
        self._missing: set[str] = set()
        missing_file = cache_dir / MISSING_FILE.name
        if missing_file.exists():
            self._missing = set(json.loads(missing_file.read_text(encoding="utf-8")))
        self._printings = load_deck_printings(SOURCE_DECKS)

    def get(self, name: str, token: bool = False) -> bytes | None:
        """The JPEG for this card, or None if Scryfall has no picture for it."""
        key = ("token:" if token else "") + name
        path = self.cache_dir / file_name(key)
        if path.exists():
            return path.read_bytes()
        if key in self._missing:
            return None
        with self._lock:
            if path.exists():  # another browser request fetched it while we waited
                return path.read_bytes()
            data = self._download(name, token)
            self.cache_dir.mkdir(parents=True, exist_ok=True)  # created on first use
            if data is None:
                self._missing.add(key)
                (self.cache_dir / MISSING_FILE.name).write_text(json.dumps(sorted(self._missing), indent=1), encoding="utf-8")
                return None
            path.write_bytes(data)
            return data

    # --- talking to Scryfall -------------------------------------------------

    def _download(self, name: str, token: bool) -> bytes | None:
        for url in self._candidate_urls(name, token):
            image_url = self._image_url(url)
            if image_url:
                return self._fetch(image_url)
        return None

    def _candidate_urls(self, name: str, token: bool) -> list[str]:
        """Scryfall card URLs to try, best first."""
        urls = []
        if not token and name in self._printings:
            set_code, number = self._printings[name]
            urls.append(f"{API}/cards/{set_code}/{urllib.parse.quote(number)}")
        if token:
            # Forge calls tokens e.g. "Treasure Token" or "Wizard Token"; Scryfall says "Treasure".
            base = re.sub(r"\s+Token$", "", name)
            query = f'!"{base}" t:token'
            urls.append(f"{API}/cards/search?" + urllib.parse.urlencode({"q": query, "include_extras": "true"}))
        urls.append(f"{API}/cards/named?" + urllib.parse.urlencode({"exact": name}))
        return urls

    def _image_url(self, card_url: str) -> str | None:
        """Ask Scryfall for a card (or a search) and pick its picture's URL."""
        raw = self._fetch(card_url)
        if raw is None:
            return None
        card = json.loads(raw)
        if card.get("object") == "list":  # a search result: take the first card
            if not card.get("data"):
                return None
            card = card["data"][0]
        if "image_uris" in card:
            return card["image_uris"]["normal"]
        if card.get("card_faces"):  # double-faced cards: show the front
            return card["card_faces"][0].get("image_uris", {}).get("normal")
        return None

    def _fetch(self, url: str) -> bytes | None:
        wait = 0.1 - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        request = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # Scryfall doesn't know it
            raise
        except urllib.error.URLError:
            return None  # offline: the browser shows a text card instead


def file_name(key: str) -> str:
    """A safe file name for any card name: readable start + a short hash."""
    readable = re.sub(r"[^A-Za-z0-9]+", "_", key)[:60]
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    return f"{readable}_{digest}.jpg"


def load_deck_printings(folder: Path) -> dict[str, tuple[str, str]]:
    """{card name: (set code, collector number)} from every deck list in ``folder``."""
    names_file = REPO_ROOT / "decks" / "scryfall_names.json"  # flavor name -> real name
    real_names = json.loads(names_file.read_text(encoding="utf-8")) if names_file.exists() else {}
    printings: dict[str, tuple[str, str]] = {}
    for deck in sorted(folder.glob("*.txt")):
        for line_text in deck.read_text(encoding="utf-8").splitlines():
            line = parse_line(line_text)
            if line is None:
                continue
            # Forge uses the real name, so store the printing under that name.
            name = real_names.get(f"{line.set_code}/{line.number}", line.name)
            printings.setdefault(name, (line.set_code, line.number))
            if " // " in name:  # double-faced: Forge names the card after the face showing
                printings.setdefault(name.split(" // ")[0], (line.set_code, line.number))
    return printings
