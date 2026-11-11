# Development notes

The [README](../README.md) covers installing and running a game. This page is
for working on the code.

## How it fits together

```
 Python (this repo)                     Java (Forge + forge-bridge/)
 ------------------                     ----------------------------
 showdown/run_game.py  starts  ───────► BridgeMain: loads decks, starts a game
 game_runner.py        ◄─── "decision" PythonPlayerController: "keep this hand?"
   └─ bots/simple_bot.py  ───► answer   "what do you play?" "who attacks?"
                       ◄─── "event"     game log lines
                       ◄─── "game_over" winner, life totals
```

Forge knows every card and every rule. Whenever a Python-controlled player
must decide something, Forge sends the question over a local socket and waits
for the answer. Details are in [bridge.md](bridge.md).

## Layout

| Path | What |
|---|---|
| `forge-bridge/` | The Java side: a Forge player controller that asks Python |
| `showdown/bridge/` | Python side of the connection and the game loop |
| `showdown/bots/` | Bots. Start with `simple_bot.py` |
| `showdown/boys/` | The Boys' brains, features, threat assessment and reward ([boys.md](boys.md)) |
| `showdown/batch.py` | Plays many games without watching; used for training |
| `showdown/viewer/` | The browser viewer (web server + page in `static/`) |
| `showdown/decks/` | Deck converter (uses Scryfall for flavor-name cards) |
| `decks/` | Deck lists, source and Forge format |
| `scripts/` | Setup and build scripts |

## All `run_game` options

| Option | Meaning |
|---|---|
| `--decks A B ...` | 2 to 4 deck names from `decks/forge/` (default: all four) |
| `--bots A B ...` | one per deck: `simple`, `random`, `forge` or `boy:<name>` (default: all `simple`) |
| `--seed N` | random seed, passed to Forge and the random bot |
| `--quiet` | print only the result, not the game log |
| `--forge-log PATH` | where Forge's own output goes (default `forge.log`) |

`python -m showdown.run_game --help` prints the same list.

## Decks

Deck lists live in `decks/source/` (Moxfield export format). Convert them to
Forge's format with:

```bash
python -m showdown.decks.convert --all     # decks/source/*.txt -> decks/forge/*.dck
python -m showdown.decks.convert --check   # 100 cards each, every name matches Scryfall and Forge
```

To add a deck: drop the export into `decks/source/`, run the command, and pass
its name to `--decks`.

## Tests

```bash
python -m unittest discover -s tests -v    # no Java needed: uses a fake Forge
```

## Where setup puts files

`scripts/setup_forge.sh` installs nothing system-wide. Everything goes in
`vendor/` (ignored by git, about 1.3 GB):

| Folder | What |
|---|---|
| `vendor/forge/` | Forge's source code and the built engine (pinned commit, see the script) |
| `vendor/m2/` | Libraries Maven downloads to build Forge (normally `~/.m2`) |
| `vendor/forge-data/` | Forge's settings and cache, created on the first game (normally `%APPDATA%\Forge` or `~/.forge`) |
| `vendor/card-images/` | Card images the browser viewer downloads from Scryfall |

If only the bridge's Java code changed, `./scripts/build_bridge.sh` rebuilds
just that, without rebuilding Forge.
