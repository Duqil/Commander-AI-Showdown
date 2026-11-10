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
* **Controls.** ⏮ replay from the start, ◀ / ▶ step one event, ⏸ pause,
  ⏭ jump to the latest moment, speed 0.5× to 10×, or **Max** to always show
  the newest moment. Drag the slider to move through the game. Click any log
  line to jump there. Keyboard: Space, ← →, Home, End.
* **New game** plays again with the same decks and bots (new shuffle).
  **Stop** ends a running game. **Decks…** goes back to the setup screen,
  where saved games can be watched again.
* **Board.** Each player has a coloured board: life, cards in hand (✋),
  library (📚), graveyard (🪦, click to see it), exile (🌀), poison and
  commander damage taken. Rows are: commander (while in the command zone),
  creatures, other permanents, lands (identical lands are stacked with ×N),
  and the hand (untick **Hands** to hide them). Tapped cards are turned
  sideways, attackers glow red, blockers blue, and a red "from X" badge marks
  a permanent controlled by someone who doesn't own it (Zidane steals!).
  Hover a card for a big picture.
* **Log.** Forge's game log plus what the Python bots decided (in blue).
  Phase and mana lines are hidden unless you tick "phases & mana".

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
