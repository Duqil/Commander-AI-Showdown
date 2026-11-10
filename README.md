# Commander AI Showdown

Teaching bots to play Commander. Four bots sit down with four decks; you pick
the decks and watch. Later: a browser viewer with card images, and learning
bots ("The Boys").

**Status:** step 1, the bridge. Python can play full 4-player Commander games
inside [Forge](https://github.com/Card-Forge/forge), which enforces all the rules.

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
for the answer. Details are in [docs/bridge.md](docs/bridge.md).

## Setup (once)

You need **Java 17+** (21 recommended), **Maven**, **git** and **Python 3.10+**.
No Python packages are needed; everything uses the standard library.

```bash
./scripts/setup_forge.sh      # clones + builds Forge into vendor/ (5-15 min), then the bridge
```

On **Windows**, run this in **Git Bash** (it comes with Git). In VS Code: open the
terminal (Ctrl+`), click the arrow next to `+` and pick "Git Bash".

**Where files go.** The script installs nothing system-wide. Everything it
downloads or builds stays in `vendor/` inside this folder (ignored by git, about 1.3 GB):

| Folder | What |
|---|---|
| `vendor/forge/` | Forge's source code and the built engine |
| `vendor/m2/` | Libraries Maven downloads to build Forge (normally `~/.m2`) |
| `vendor/forge-data/` | Forge's settings and cache, created on the first game (normally `%APPDATA%\Forge` or `~/.forge`) |

Delete `vendor/` to undo it all. Java, Maven, git and Python you install yourself.

## Play a game

```bash
python -m showdown.run_game                                   # 4 decks, all SimpleBot
python -m showdown.run_game --bots simple forge random forge --seed 7
python -m showdown.run_game --decks zidane kuja --quiet       # 2 players, result only
```

`forge` means Forge's own AI plays that seat, a good opponent to measure our bots against.

## Decks

Deck lists live in `decks/source/` (Moxfield export format). Convert them to
Forge's format with:

```bash
python -m showdown.decks.convert --all     # decks/source/*.txt -> decks/forge/*.dck
```

To add a deck: drop the export into `decks/source/`, run the command, and pass
its name to `--decks`.

## Tests

```bash
python -m unittest discover -s tests -v    # no Java needed: uses a fake Forge
```

## Layout

| Path | What |
|---|---|
| `forge-bridge/` | The Java side: a Forge player controller that asks Python |
| `showdown/bridge/` | Python side of the connection and the game loop |
| `showdown/bots/` | Bots. Start with `simple_bot.py` |
| `showdown/decks/` | Deck converter (uses Scryfall for flavor-name cards) |
| `decks/` | Deck lists, source and Forge format |
| `scripts/` | Setup and build scripts |
