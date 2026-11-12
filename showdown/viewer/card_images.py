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

import difflib
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


class ScryfallUnreachable(Exception):
    """We couldn't ask Scryfall right now (offline, timeout, too many requests).

    Not the same as "Scryfall doesn't know this card": that answer is saved in
    missing.json for good, while this one is only for now, so we try again later.
    """


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

    def get(self, name: str, token: bool = False, details: dict | None = None) -> bytes | None:
        """The JPEG for this card, or None if Scryfall has no picture for it.

        ``details`` (tokens only) helps pick the right one of many tokens with
        the same name: {"colors": "W", "pt": "2/2", "oracle": "Vigilance"}.
        """
        # "token-v2:" because v1 cached the wrong face of double-faced tokens (a Dinosaur for Treasure).
        # "token-v3:" adds the details, so a 2/2 Knight with vigilance and one with haste get their own pictures.
        details = {k: v for k, v in (details or {}).items() if v} if token else {}
        if details:
            key = "token-v3:" + name + "|" + "|".join(f"{k}={details[k]}" for k in sorted(details))
        else:
            key = ("token-v2:" if token else "") + name
        path = self.cache_dir / file_name(key)
        if path.exists():
            return path.read_bytes()
        if key in self._missing:
            return None
        with self._lock:
            if path.exists():  # another browser request fetched it while we waited
                return path.read_bytes()
            try:
                data = self._download(name, token, details)
            except ScryfallUnreachable:
                return None  # the browser shows a text card; we'll try again next time
            self.cache_dir.mkdir(parents=True, exist_ok=True)  # created on first use
            if data is None:
                self._missing.add(key)
                (self.cache_dir / MISSING_FILE.name).write_text(json.dumps(sorted(self._missing), indent=1), encoding="utf-8")
                return None
            path.write_bytes(data)
            return data

    # --- talking to Scryfall -------------------------------------------------

    def _download(self, name: str, token: bool, details: dict | None = None) -> bytes | None:
        wanted = re.sub(r"\s+Token$", "", name)  # Forge says "Treasure Token", Scryfall "Treasure"
        for url in self._candidate_urls(name, token):
            image_url = self._image_url(url, wanted, details)
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

    def _image_url(self, card_url: str, wanted: str = "", details: dict | None = None) -> str | None:
        """Ask Scryfall for a card (or a search) and pick its picture's URL.

        ``wanted`` is the name we're after. A token search for "Treasure" also
        finds double-faced tokens like "Dinosaur // Treasure", so we prefer an
        exact match, and on a double-faced card we show the matching face.
        """
        raw = self._fetch(card_url)
        if raw is None:
            return None
        card = json.loads(raw)
        if card.get("object") == "list":  # a search result: pick the best card
            results = card.get("data") or []
            if not results:
                return None
            # The best match on name, colours, power/toughness and rules text.
            # max() keeps the first of equal scores, so Scryfall's order breaks ties.
            # A single-faced token gets a small bonus over the matching face of a double-faced one.
            card = max(results, key=lambda c: token_score(best_face(c, wanted), wanted, details or {})
                       + (0 if c.get("card_faces") else 1))
        if "image_uris" in card:
            return card["image_uris"]["normal"]
        face = best_face(card, wanted)
        return face.get("image_uris", {}).get("normal")

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
            raise ScryfallUnreachable(f"Scryfall answered {e.code}") from e  # e.g. 429 "slow down"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ScryfallUnreachable(str(e)) from e  # offline or no answer in time


def best_face(card: dict, wanted: str) -> dict:
    """The face of a double-faced card with the name we want (otherwise the front), or the card itself."""
    faces = card.get("card_faces") or []
    if not faces:
        return card
    return next((f for f in faces if f.get("name") == wanted), faces[0])


def rules_text(text: str) -> str:
    """Rules text made comparable: no reminder text in (brackets), lower case, single spaces."""
    text = re.sub(r"\([^)]*\)", "", text or "")
    text = text.lower().replace("this token", "this creature").replace("this artifact", "this creature")
    return " ".join(text.split()).strip(" .")


def token_score(face: dict, wanted: str, details: dict) -> float:
    """How well a Scryfall token (one face) matches what Forge created. Higher is better."""
    score = 2.0 if face.get("name") == wanted else 0.0
    if "colors" in details:
        colors = "".join(c for c in "WUBRG" if c in (face.get("colors") or []))
        score += 4 if colors == details["colors"] else 0
    if "pt" in details:
        score += 4 if f"{face.get('power')}/{face.get('toughness')}" == details["pt"] else 0
    if details:
        # Similar rules text counts most: it tells "vigilance" from "haste, protection from white".
        ours, theirs = rules_text(details.get("oracle", "")), rules_text(face.get("oracle_text", ""))
        score += 8 * difflib.SequenceMatcher(None, ours, theirs).ratio()
    return score


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
