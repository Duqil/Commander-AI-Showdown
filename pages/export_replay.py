"""Turn a saved game into a replay for the GitHub Pages site.

The local viewer saves every finished game in games/ (one .json.gz file each).
This script copies one of them into pages/replays/ and adds two things the
website needs, because there is no Python server behind it:

  * a title and a short description (shown in the list of replays), and
  * the Scryfall picture URL of every card in the game. The local viewer asks
    Scryfall while you watch; the website can't, so we look them up now, once.

Usage (from the project folder):

    python -m pages.export_replay games/2026-11-12_140000_kuja-garnet-anhelo-swarmlord.json.gz \\
        --title "The Boys, with deals" --about "Kaiba (Kuja), Tristan (Garnet), ..."

Python concepts used here:
  * ``gzip.open``     - read and write compressed files as if they were normal text.
  * a *generator*     - ``walk_cards`` uses ``yield`` to hand out cards one by one.
  * ``re.sub``        - replace text with a pattern (here: drop " Token" at the end).
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
from pathlib import Path

from showdown.viewer.card_images import CardImages, ScryfallUnreachable

REPLAY_DIR = Path(__file__).parent / "replays"


def walk_cards(value):
    """Every card dict anywhere inside a table snapshot (battlefield, hands, stack, ...).

    A card is a dict with a "name" and an "id". Players have names too, but no id.
    The function calls itself on everything inside lists and dicts ("recursion").
    """
    if isinstance(value, dict):
        if "name" in value and "id" in value:
            yield value
        for inner in value.values():
            yield from walk_cards(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from walk_cards(inner)


def card_key(card: dict) -> str:
    """The name the website looks a picture up by. pages/replay.js builds the same key.

    Tokens with the same name can look different (a white 2/2 Knight with
    vigilance, a red one with haste), so for tokens the key also has the
    colours, power/toughness and rules text.
    """
    if not card.get("token"):
        return card["name"]
    pt = f"{card['base_power']}/{card['base_toughness']}" if card.get("base_power") is not None else ""
    return "|".join([card["name"], card.get("colors") or "", pt, card.get("oracle") or ""])


def token_details(card: dict) -> dict:
    """The same hints the local viewer sends to pick the right token picture."""
    details = {"colors": card.get("colors"), "oracle": card.get("oracle")}
    if card.get("base_power") is not None:
        details["pt"] = f"{card['base_power']}/{card['base_toughness']}"
    return {k: v for k, v in details.items() if v}


def find_image_urls(events: list[dict]) -> dict[str, str]:
    """{card key: picture URL} for every card that appears in the game."""
    cards: dict[str, dict] = {}
    for event in events:
        for card in walk_cards(event.get("state", {})):
            cards.setdefault(card_key(card), card)

    images = CardImages()  # the same Scryfall lookup the local viewer uses
    urls: dict[str, str] = {}
    for i, (key, card) in enumerate(sorted(cards.items()), 1):
        token = bool(card.get("token"))
        wanted = re.sub(r"\s+Token$", "", card["name"])
        details = token_details(card) if token else None
        try:
            for candidate in images._candidate_urls(card["name"], token):
                url = images._image_url(candidate, wanted, details)
                if url:
                    urls[key] = url
                    break
        except ScryfallUnreachable as e:
            print(f"  Scryfall didn't answer for {card['name']}: {e}")
        print(f"  {i}/{len(cards)} {card['name']}{'' if key in urls else '  (no picture)'}")
    return urls


def export(game_file: Path, title: str, about: str) -> Path:
    with gzip.open(game_file, "rt", encoding="utf-8") as f:
        game = json.load(f)
    print(f"Looking up card pictures for {game_file.name}...")
    game["title"] = title
    game["about"] = about
    game["images"] = find_image_urls(game["events"])
    REPLAY_DIR.mkdir(exist_ok=True)
    out = REPLAY_DIR / game_file.name
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(game, f, separators=(",", ":"))  # no spaces: a smaller file
    print(f"Saved {out} ({out.stat().st_size // 1024} KB)")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("game", type=Path, help="a saved game from games/")
    parser.add_argument("--title", required=True, help="short name shown in the replay list")
    parser.add_argument("--about", default="", help="one line: who played which deck")
    args = parser.parse_args()
    export(args.game, args.title, args.about)


if __name__ == "__main__":
    main()
