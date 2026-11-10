# Commander AI Showdown

Four bots play a game of Commander (EDH) while you watch. You pick the decks;
[Forge](https://github.com/Card-Forge/forge) enforces the rules, and our Python
bots make the decisions.

**Status:** games run in the terminal. A browser viewer with card art is on the way.

---

## 1. Install these (once)

| Program | Version | Where to get it | Check it works |
|---|---|---|---|
| **Git** | any | [git-scm.com](https://git-scm.com/downloads) (includes Git Bash) | `git --version` |
| **Python** | 3.10 or newer | [python.org](https://www.python.org/downloads/) | `python --version` |
| **Java JDK** | 21 | [Temurin 21 from adoptium.net](https://adoptium.net/temurin/releases/?version=21) | `java -version` |
| **Maven** | 3.9 or newer | [maven.apache.org](https://maven.apache.org/download.cgi) ("Binary zip archive") | `mvn -v` |

Tips for Windows:

- **Python:** tick **"Add python.exe to PATH"** on the installer's first screen.
- **Java:** in the Temurin installer, turn on **"Set JAVA_HOME variable"**. Use
  Java 21; newer versions (like 25) are untested.
- **Maven** has no installer. Unzip it somewhere you'll keep it (for example
  `C:\Tools\apache-maven-3.9.x`), then add its `bin` folder to your PATH:
  Start menu, search **"environment variables"**, open
  *Edit the system environment variables* > *Environment Variables* >
  select **Path** under *User variables* > *Edit* > *New*, paste the path to
  `...\apache-maven-3.9.x\bin`, and click OK on every window.

Close and reopen your terminal after installing, then run the four "check"
commands above. Each should print a version number.

No Python packages are needed.

## 2. Get the code (once)

Open a terminal. In VS Code: menu **Terminal > New Terminal** (or `` Ctrl+` ``;
on a Swedish keyboard `Ctrl+Ö`). Then click the small arrow next to `+` in the
terminal panel and pick **Git Bash**. All commands below are run in Git Bash.

```bash
git clone https://github.com/duqil/commander-ai-showdown.git
cd commander-ai-showdown
```

## 3. Build the rules engine (once)

```bash
./scripts/setup_forge.sh
```

This downloads and builds Forge. It takes **5 to 15 minutes** the first time.
Everything it creates stays in the `vendor/` folder inside the project (about
1.3 GB); nothing is installed anywhere else. Delete `vendor/` to undo it.

It's done when it prints `>> Done.`

## 4. Run a game

```bash
python -m showdown.run_game
```

(If `python` isn't found on Windows, use `py -m showdown.run_game`.)

You'll see the game log scroll by and the winner at the end. Some variations:

```bash
python -m showdown.run_game --seed 7                       # set the random seed
python -m showdown.run_game --bots simple forge simple forge  # Forge's AI plays seats 2 and 4
python -m showdown.run_game --decks zidane kuja --quiet    # 2 players, result only
```

Decks: `zidane`, `anhelo`, `nelly`, `kuja`. Bots: `simple` (our bot), `forge`
(Forge's own AI), `random`.

## 5. Getting updates

```bash
git pull
./scripts/setup_forge.sh      # only needed if Java files (forge-bridge/ or scripts/) changed
```

Running the setup again is always safe; it's much faster the second time.

---

## More

- [docs/development.md](docs/development.md): how the code fits together,
  adding decks, running tests, where files go.
- [docs/bridge.md](docs/bridge.md): how Python and Forge talk to each other.
