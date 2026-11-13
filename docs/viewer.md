# The browser viewer

```bash
python -m showdown.viewer               # opens http://127.0.0.1:8000
python -m showdown.viewer --port 8080   # if 8000 is taken
python -m showdown.viewer --no-browser  # don't open a tab automatically
```

Stop it with Ctrl+C. No extra Python packages are needed: the server uses only
the standard library. Forge must be built first (`./scripts/setup_forge.sh`).

The viewer needs a small addition to the Java bridge. If you set up Forge
before the viewer existed, rebuild the bridge once (takes seconds, in Git Bash):

```bash
./scripts/build_bridge.sh
```

## Using it

* **Setup screen.** Pick a deck and a bot for each seat (seats 3 and 4 can be
  empty for a 2 or 3 player game). Leave the seed empty for a random game, or
  give a number to replay the same shuffle with different bots.
* **A new game opens paused** on the opening hands. Press ▶ (or Space) to start.
* **Controls.** ⏮ replay from the start, ◀ / ▶ step one event, ⏸ pause,
  ⏭ jump to the latest moment, speed 0.5× to 4×, or **Max** to always show
  the newest moment. Drag the slider to move through the game. Click any log
  line to jump there. Keyboard: Space, ← →, Home, End.
* **Size.** − and + change the card size in steps of 8. **Fit** keeps the
  cards as big as possible without any board needing to scroll, and adjusts as
  cards come and go. The viewer remembers your choice.
* **New game** plays again with the same decks and bots (new shuffle).
  **Stop** ends a running game. **Main Menu** goes back to the setup screen,
  where the **Saved games** tab lists finished games to watch again.
* **Round and turn.** The top left shows e.g. "Round 9 · Turn 35 · 3-Nelly ·
  Combat". A round is over once every player still in the game has had a
  turn, so after a knockout a round is shorter.
* **Board.** Each player has a coloured board. Its top line holds the name,
  the bot, cards in hand (✋), library (📚), graveyard (🪦, click to see it),
  exile (🌀), poison and commander damage taken, then the life total. Rows are:
  creatures, other permanents, lands (small strips showing just the art, to
  save room; identical lands are stacked with ×N; tapped ones are darker),
  and the hand (untick **Hands** to hide them). Tapped cards are turned
  sideways, attackers glow red, blockers blue, and
  a coloured outline (in the owner's seat colour) marks a permanent controlled
  by someone who doesn't own it (Zidane steals!). The commander, while waiting
  in the command zone, is a small picture of its art next to the life total; a gold
  "+2" on it is the command tax.
  Creatures with +1/+1 counters show green stats (red for -1/-1); hover for
  the details. A purple badge shows a choice made for the card, like the
  colour of a Thriving land. Seats go clockwise from the top left.
  Hover a card for a big picture.
* **Casting and targeting.** A spell that is cast is shown big in the middle
  of its caster's board for a moment. A permanent (creature, artifact, ...)
  then flies to the spot where it will land; an instant or sorcery shrinks into
  the Stack box, where it waits to resolve (faster at higher speeds, skipped at
  Max). While a spell or ability
  with targets is on the stack, a red arrow points from it to each target
  (a card, or a player's life total). The arrow for the item that resolves
  next is bright, older ones are faint.
* **Deals.** Tick **Bots make deals** in the main menu (for watched games and
  Run many games) and bots can offer each other peace or to gang up on
  someone (see docs/deals.md; the fold-out under the checkbox says how each
  bot type handles deals). Every offer, answer and broken deal pops up as a
  big icon between the two boards with what was said: 🤝? offer, 🤝 accept,
  ✋ decline, 💔 broken (which also gets a red banner). The **Politics** tab
  next to the log shows the deals that are on right now and grudges, and
  every offer, answer and break so far.
* **Combat.** A red arrow goes from each attacker to the middle of the board
  it attacks, ending in a sword. A blocker shows a shield and a blue arrow to
  the creature it blocks. Once an attacker is blocked, its red arrow goes
  away so only the blue one is left.
* **Triggers.** While a triggered ability waits on the stack, the card it
  comes from glows gold with a small TRIGGER tag.
* **Log.** Forge's game log plus what the Python bots decided (in blue),
  and choices Forge doesn't log itself ("2-Anhelo chose Red for Thriving Moor").
  Phase and mana lines are only in the **Phases & mana** tab.

## Stats

The **Stats** tab in the main menu shows win rates per deck, per bot and per
deck + bot, from every finished game: watched games, **Run many games** and
`python -m showdown.run_game`. Pick where the games came from in the dropdown.
The line on each bar is a fair share (25% with 4 players), so a bar past the
line wins more than an even match would. The numbers live in
`stats/results.csv` in the repo folder (not shared through git); delete that
file to start counting again.

## Run many games (no viewing)

Under **Run many games** in the main menu: pick how many games and how many
at once (1 to 5; each one is a Forge using about one CPU core and up to
2 GB of memory), then **Run games**. It uses the
decks and bots chosen above, plays the games without showing them, and shows
a progress bar, wins per seat and, for Boys, their average reward. "Boys learn"
lets Boys train from these games; untick it to just test them. "Save games for
replay" puts every game under **Saved games**, so you can watch the
interesting ones.

Two buttons stop a batch: **Stop after the running games** starts no new games
and lets the ones being played finish (they count). **Stop now** ends
everything straight away; unfinished games are thrown away and nobody learns
from them. On the command line, Ctrl+C stops right away too.

When a batch ends you get confetti and a small summary card, and the
browser tab title starts with "✓ Games done" until you look at it.

Pick **New Boy…** in a bot dropdown to name a new learning bot (spaces are
fine, like "Nicol Bolas"); it's created the first time it plays. The same runner works from the command line:
`python -m showdown.batch --help`.

## Start-up time

Clicking **Start game** launches a fresh Forge. To make that quicker, Forge
reads a card's rules only when the game needs that card (instead of all
~34,000 before every game), and Java keeps its prepared classes in
`vendor/forge-data/forge-classes.jsa`. Both are set up by
`showdown/bridge/forge_process.py`. The first game after `build_bridge.sh`
is a little slower while Java writes that file again.

**Run many games** goes further: each parallel slot keeps its Forge running
and asks it for the next game, so the rules load once per slot, and Java,
which speeds up as it runs, stays warm. In a test with 5 games in a row this
took 205 s instead of 235 s, with exactly the same results per seed. A slot
starts a fresh Forge every 25 games, and after any game that crashed or was
stopped (`Forge` in `showdown/bridge/game_runner.py`).

**Games that never end.** An infinite loop, or a board so big the game
crawls, used to stall a whole run. Now Forge ends a game as a **draw** after
200 turns in total, or after 15 minutes of Forge's own thinking time (time
spent waiting for Python, like a paused viewer, doesn't count). The batch
output then says e.g. `draw (turn limit (200 turns))`; Boys learn from it like
any draw. See `forge-bridge/.../GameLimits.java`. As a last resort, Run many
games kills a game still going after 30 minutes and counts it as crashed.
The table snapshot sent to the bots is also built in one pass over combat
now, so boards with thousands of tokens no longer slow every decision down.

## Where files go

| Folder | What |
|---|---|
| `vendor/card-images/` | Card pictures from Scryfall, downloaded once each (about 100 KB per card) |
| `games/` | Every finished game, compressed (about 100-200 KB each) |

Both are ignored by git. Delete them any time.

Card art uses the exact printing from your deck lists in `decks/source/`
(including the Final Fantasy flavor-name versions). Cards that aren't in a
list are looked up by name, tokens among Scryfall's tokens. If a picture
can't be found you see a plain text card instead.

## How it works

```
 Forge (Java)                     Python                                 Browser
 ------------                     ------                                 -------
 SpectatorFeed  ── "event" ──►  game_runner.play(on_message=...)
 (log lines + table snapshot)     └─ session.GameSession  (game thread)
                                       └─ events.TimelineBuilder
                                            -> timeline: [event, event, ...]
                                  server.py  ◄── GET /api/game/events?since=N ── app.js (every 0.5 s)
                                                                                 plays the timeline
                                                                                 at the chosen speed
```

1. `SpectatorFeed.java` listens to Forge's event bus and sends Python every
   new log line, plus a snapshot of the whole table whenever it changed. The
   snapshot includes every hand (it's for the spectator; bots still only see
   their own hand).
2. `session.py` runs the game in a background thread and turns those messages
   into a **timeline** with `events.TimelineBuilder`.
3. `server.py` hands the timeline to the browser in pieces.
4. `static/app.js` keeps a playhead and walks through the timeline at the
   chosen speed. To draw the table it uses the latest snapshot at or before
   the playhead. Pausing or rewinding never touches the game itself.

## Adding a new kind of event

The timeline is a list of plain dicts (`seq`, `kind`, `text`, `turn`,
`player`, `category`, optional `state` and `data`). See
`showdown/viewer/events.py`. To show something new, for example the planned
deals between bots:

1. Add the kind to `EVENT_KINDS` in `events.py` (`deal_offer`,
   `deal_accept`, `deal_decline` and `deal_break` are already reserved).
2. Create it with `make_event("deal_offer", "2-Kuja offers 1-Zidane a 3-turn truce", player="2-Kuja", deal=...)`
   and add it with `GameSession.add_event(...)`.
3. Optionally style it in `EVENT_STYLES` at the top of `static/app.js`
   (log colour, a banner in the middle of the screen, how long playback
   pauses on it). The deal kinds already have a pink log colour and a banner.
