"""Convert a Moxfield-style deck list into a Forge .dck file.

Input (one card per line, commander first):
    1 Zidane, Tantalus Thief (FIN) 405
    1 Vow of Lightning (PLST) CMR-209
    10 Mountain (FIN) 300

Output (Forge's format):
    [metadata]
    Name=Zidane
    [Commander]
    1 Zidane, Tantalus Thief
    [Main]
    1 Vow of Lightning
    10 Mountain

Two wrinkles:
  * Final Fantasy "flavor name" printings (set FCA) use a different name from
    the real card ("Memories of Nibelheim" is really "Stroke of Midnight").
    Forge only knows the real name, so we ask Scryfall by set + collector number.
  * Forge picks the printing itself, so we leave set codes out of the output.

Scryfall answers are cached in decks/scryfall_names.json so we only ask once.

Usage:
    python -m showdown.decks.convert decks/source/zidane.txt decks/forge/zidane.dck
    python -m showdown.decks.convert --all        # every file in decks/source/
    python -m showdown.decks.convert --check      # check every deck's names and card count
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_FILE = REPO_ROOT / "decks" / "scryfall_names.json"

# A regular expression for "<count> <name> (<SET>) <number>".
# (?P<name>...) gives each part a name so we can ask for it later.
LINE_RE = re.compile(r"^\s*(?P<count>\d+)\s+(?P<name>.+?)\s+\((?P<set>\w+)\)\s+(?P<number>\S+)")


@dataclass
class DeckLine:
    count: int
    name: str
    set_code: str
    number: str


def parse_line(line: str) -> DeckLine | None:
    match = LINE_RE.match(line)
    if not match:
        return None
    set_code, number = match["set"], match["number"]
    if "-" in number:  # "The List" style: (PLST) CMR-209 means set CMR, number 209
        set_code, number = number.split("-", 1)
    return DeckLine(int(match["count"]), match["name"], set_code.lower(), number)


def load_cache() -> dict[str, str]:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def scryfall_name(set_code: str, number: str, cache: dict[str, str]) -> str:
    """The card's real (Oracle) name, looked up on Scryfall by set and number."""
    key = f"{set_code}/{number}"
    if key not in cache:
        req = urllib.request.Request(
            # quote() turns special collector numbers such as "162★" into URL-safe text.
            f"https://api.scryfall.com/cards/{set_code}/{urllib.parse.quote(number)}",
            headers={"User-Agent": "CommanderAIShowdown/0.1", "Accept": "application/json"},
        )
        time.sleep(0.1)  # Scryfall asks for at most ~10 requests per second
        with urllib.request.urlopen(req) as resp:
            cache[key] = json.load(resp)["name"]
    return cache[key]


CARDS_FOLDER = REPO_ROOT / "vendor" / "forge" / "forge-gui" / "res" / "cardsfolder"
_forge_names: set[str] | None = None
# Universes Beyond cards can have a second, "Universes Within" name: Forge calls
# the card "Zangief, the Red Cyclone" while Scryfall's Oracle name is
# "Maarika, Brutal Gladiator". Forge lists the other name in the card file as
# "Variant:UniversesWithin:FlavorName:<name>"; we map that name to Forge's.
_forge_aliases: dict[str, str] = {}


def forge_card_names() -> set[str]:
    """Every card name Forge knows (empty if Forge isn't set up yet).

    Each card is a small text file in Forge's cardsfolder whose first line
    is "Name:<card name>". Split cards such as Wear // Tear keep both halves
    in one file (the second half after an "ALTERNATE" line), and Forge's deck
    files name them "Wear // Tear", so for those we store the joined name.
    Reading ~35,000 files takes a few seconds, so we do it once and keep it.
    """
    global _forge_names
    if _forge_names is None:
        _forge_names = set()
        for f in CARDS_FOLDER.rglob("*.txt"):
            text = f.read_text(encoding="utf-8", errors="ignore")
            if not text.startswith("Name:"):
                continue
            names = [l[5:].strip() for l in text.splitlines() if l.startswith("Name:")]
            for l in text.splitlines():
                if l.startswith("Variant:") and ":FlavorName:" in l:
                    _forge_aliases[l.split(":FlavorName:", 1)[1].strip()] = names[0]
            if "AlternateMode:Split" in text and len(names) >= 2:
                _forge_names.add(f"{names[0]} // {names[1]}")
            else:
                _forge_names.add(names[0])
    return _forge_names


def split_card_name(front: str) -> str | None:
    """'Wear' -> 'Wear // Tear' if Forge has it as a split card, else None."""
    for name in forge_card_names():
        if name.startswith(front + " // "):
            return name
    return None


def forge_name(line: DeckLine, cache: dict[str, str]) -> str:
    # Double-faced cards are written "Front / Back"; Forge wants just the front.
    # Split cards (Wear / Tear) are the exception: Forge wants "Wear // Tear".
    name = line.name.split(" / ")[0]
    known = forge_card_names()
    # Flavor-name printings (all of set FCA, and some others such as
    # "Paradise Chocobo" = Birds of Paradise) carry a name Forge doesn't know.
    # Ask Scryfall for the real name by set + collector number.
    if known and name not in known and (split := split_card_name(name)):
        return split
    if line.set_code == "fca" or (known and name not in known):
        name = scryfall_name(line.set_code, line.number, cache).split(" // ")[0]
        name = _forge_aliases.get(name, name)
        if known and name not in known:
            name = split_card_name(name) or name
    return name


def unknown_cards(target: Path) -> list[str]:
    """Cards in a converted .dck file that Forge doesn't have."""
    known = forge_card_names()
    if not known:
        return []
    names = [l.split(" ", 1)[1] for l in target.read_text(encoding="utf-8").splitlines()
             if l[:1].isdigit() and " " in l]
    return [n for n in names if n not in known]


def convert(source: Path, target: Path, deck_name: str | None = None) -> int:
    cache = load_cache()
    lines = [p for p in (parse_line(l) for l in source.read_text(encoding="utf-8").splitlines()) if p]
    if not lines:
        raise ValueError(f"No cards found in {source}")

    commander, main = lines[0], lines[1:]
    deck_name = deck_name or source.stem.capitalize()
    out = ["[metadata]", f"Name={deck_name}", "[Commander]", f"1 {forge_name(commander, cache)}", "[Main]"]
    out += [f"{l.count} {forge_name(l, cache)}" for l in main]

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    CACHE_FILE.write_text(json.dumps(cache, indent=1, sort_keys=True) + "\n")
    return sum(l.count for l in lines)


def check(source: Path) -> list[str]:
    """Problems with a deck list: wrong card count, names that don't match the
    printing's real name on Scryfall, or cards Forge doesn't have."""
    cache = load_cache()
    lines = [p for p in (parse_line(l) for l in source.read_text(encoding="utf-8").splitlines()) if p]
    problems = []
    total = sum(l.count for l in lines)
    if total != 100:
        problems.append(f"{total} cards (a Commander deck has 100)")
    known = forge_card_names()
    for l in lines:
        ours = forge_name(l, cache)
        real = scryfall_name(l.set_code, l.number, cache).split(" // ")[0]
        if ours.split(" // ")[0] != real and _forge_aliases.get(real) != ours:
            problems.append(f"{l.name}: converted to '{ours}' but the printing is '{real}'")
        if known and ours not in known:
            problems.append(f"Forge doesn't have '{ours}'")
    CACHE_FILE.write_text(json.dumps(cache, indent=1, sort_keys=True) + "\n")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", nargs="?", type=Path)
    parser.add_argument("target", nargs="?", type=Path)
    parser.add_argument("--all", action="store_true", help="convert every deck in decks/source/")
    parser.add_argument("--check", action="store_true",
                        help="check every deck in decks/source/ against Scryfall and Forge (slow the first time)")
    args = parser.parse_args()

    if args.check:
        for source in sorted((REPO_ROOT / "decks" / "source").glob("*.txt")):
            problems = check(source)
            print(f"{source.stem}: {'OK' if not problems else ''}")
            for problem in problems:
                print(f"    {problem}")
        return

    if args.all:
        jobs = [(p, REPO_ROOT / "decks" / "forge" / f"{p.stem}.dck") for p in sorted((REPO_ROOT / "decks" / "source").glob("*.txt"))]
    elif args.source and args.target:
        jobs = [(args.source, args.target)]
    else:
        parser.error("give SOURCE and TARGET, or --all")

    for source, target in jobs:
        total = convert(source, target)
        note = "" if total == 100 else "  <-- a Commander deck should have 100 cards"
        print(f"{source.name} -> {target}  ({total} cards){note}")
        for name in unknown_cards(target):
            print(f"    Forge doesn't know '{name}' (not implemented yet, or a typo)")


if __name__ == "__main__":
    main()
