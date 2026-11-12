# The Forge ↔ Python bridge

## The idea

Forge asks a `PlayerController` object every time a player must decide
something. There are about 150 kinds of question (choose targets, order
blockers, pick a mode, scry, ...).

`PythonPlayerController` **extends Forge's own AI controller** and overrides a
few of those questions so they go to Python. Every question we don't override
is still answered by Forge's AI. This lets us move decisions to Python one at
a time, starting with the ones that matter most.

## Decisions Python makes today

| kind | When | Python returns | `None` means |
|---|---|---|---|
| `mulligan` | Opening hand | `true` keep / `false` mulligan | Forge AI decides |
| `choose_play` | You have priority and at least one legal play | option `index` | pass priority |
| `choose_targets` | Right after you chose a play that targets, once per targeting part | list of candidate `index`es | keep Forge AI's `suggested` targets |
| `attack` | Your declare-attackers step, if anything can attack | `{"<card id>": defender index}` | Forge AI attacks |
| `fetch_land` | Searching your library for a land (Evolving Wilds; Myriad Landscape asks twice) | a candidate `index` (-1 = none, only if `optional`) | Forge AI's `suggested` land |

Still answered by Forge's AI: targets and X values of a chosen play, mana
payment, blocks, combat damage order, all "choose a card/mode/player" effects,
scry, discard, and so on. These are the next candidates to move over.

Safety nets in the Java side:
* Options are pre-filtered to legal plays (timing + can pay the cost).
* An attack that breaks a rule (for example a goaded creature that must
  attack) is thrown away and Forge's AI attacks instead. It logs
  `[bridge] Python's attack broke an attack rule` to `forge.log`.
* If Python keeps choosing a play Forge can't complete (no legal target), the
  player passes after 15 tries instead of looping forever.

## Wire format

Local TCP socket, one JSON object per line. Python listens, starts Java, Java
connects back.

Java → Python:

```json
{"type": "game_start", "seats": [{"seat": 0, "name": "1-Zidane", "deck": "Zidane", "controller": "python"}, ...]}
{"type": "event", "log": ["Turn: Turn 3 (1-Zidane)", "Land: 1-Zidane played Mountain (50)"],
 "entries": [{"type": "TURN", "text": "Turn 3 (1-Zidane)"}, {"type": "LAND", "text": "1-Zidane played Mountain (50)"}],
 "state": {...}}
{"type": "decision", "id": 12, "kind": "choose_play", "player": "1-Zidane",
 "state": {...}, "options": [{"index": 0, "card": {...}, "description": "...", "is_land": true, "is_spell": false,
              "is_ability": false, "forge_would_play": true}]}
{"type": "game_over", "winner": "4-Kuja", "draw": false, "turns": 26, "players": [{"name": "...", "life": -1, "lost": true}]}
```

`is_ability` marks activated abilities; `api` is Forge's effect type (e.g.
`ChangeZone` for Evolving Wilds); `forge_would_play` says whether Forge's own
AI would make that play right now, a hint the bots can use.

Python → Java (only in reply to a decision):

```json
{"id": 12, "choice": 0}
```

Events are sent as the game happens (by `SpectatorFeed.java`, which listens
to Forge's event bus), not only before a decision. Their `state` is a
spectator snapshot with every hand in it, for the browser viewer, and is only
included when the table changed. Never hand an event's `state` to a bot.

A decision's `state` is the game from the deciding player's point of view: turn, phase
(`MAIN1`, `COMBAT_DECLARE_ATTACKERS`, ...), whose turn it is, and for every
player their life, poison, commander damage taken, hand size, battlefield,
graveyard, exile and command zone, plus the `stack` (each item has an `id`, a
`kind`: `spell`, `ability` or `trigger`, and its `targets` as `{"card": id}`,
`{"player": name}` or `{"spell": stack id}`). Cards also say whether
they are tapped, a token, a commander, attacking or blocking, what they are
attached to, their counters, their `owner` vs `controller`, their mana `cost`
("{1}{U}{R}") and the colors they `produces` ("UB"). Only
your own `hand` is included, so bots can't peek. `turns` counts each player's
turn separately (4 players × 5 rounds = 20 turns).

## Adding a new decision

1. In `PythonPlayerController.java`, override the Forge method (see
   `forge-game/.../PlayerController.java` for the list), build a JSON message
   and call `ask("my_kind", extra)`. Fall back to `super.method(...)` when the
   answer is `null`.
2. Rebuild: `./scripts/build_bridge.sh`.
3. In `showdown/bots/base.py`, add a method with a `None` default, and route
   `"my_kind"` to it in `game_runner.answer_decision`.
