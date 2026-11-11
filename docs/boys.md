# The Boys: bots that learn

A Boy is a bot with a name and a brain. It plays like SimpleBot at first, and
after every training game it adjusts its brain a little based on how the game
went. Over many games it should drift towards whatever actually works.

## Quick start

```bash
# Train Kaiba in seat 1 against three SimpleBots, 2 games at a time
python -m showdown.batch --bots boy:Kaiba simple simple simple --games 50 --parallel 2

# How is he doing? Win rate and average reward per block of 20 games
python -m showdown.batch --report

# Watch him play in the terminal (he doesn't learn from watched games)
python -m showdown.run_game --bots boy:Kaiba simple simple simple
```

A new name creates a new Boy. Its brain is saved in `boys/Kaiba.json` after
every game; delete that file to start it over. `boys/history.csv` has one
row per Boy per training game. The `boys/` folder is ignored by git.

A game takes about 45 seconds, and each one runs its own Forge using about
2 GB of memory and 2 CPU cores. `--parallel 2` or `3` is a good choice on a
normal PC. So 100 games is roughly half an hour.

## What a Boy decides

The same three things as SimpleBot: mulligans, what to play (or pass) when it
has priority, and where each creature attacks. Blocks and targets are still
Forge's AI for every bot. Moving them to Python is a later step.

## How it works

```
decision from Forge
   │
   ▼
features.py   each option becomes named numbers:
              {"attack": 1, "target_is_leader": 1, "target_life": 0.3, "simple_prior": 1, ...}
   │
   ▼
brain.py      score = sum of weight × number; softmax turns scores into chances;
              pick one (usually a high scorer, sometimes not) and remember it
   │
   ▼  ... game ends ...
reward.py     win = +1, plus +0.5 × share of opponents you outlived
   │
   ▼
brain.py      REINFORCE: after a better-than-usual game, strengthen the
              features of the options you picked; after a worse one, weaken them
```

**Threat assessment** (`showdown/boys/threat.py`) gives the attack features
their meaning: who is the leader (biggest board, most cards, most life), who
is weak right now, and who could be killed with the power we have. The Boys
learn how much each of those should matter, for example "focus the leader",
"ignore a player with nothing on board", or "finish off the player at 4 life".

**Learning on top of SimpleBot.** Every option has a `simple_prior` feature
that is 1 for SimpleBot's choice, and every brain starts with weight 2.0 on
it. So a new Boy picks SimpleBot's move about two times in three and
something else the rest of the time. If you improve SimpleBot, every Boy's
starting instinct improves with it.

## Reading a brain

Open `boys/Kaiba.json`. Weights are listed strongest first for each kind of
decision. A positive weight means "I like options that have this", negative
means "I avoid them". For example, after training you might see:

```json
"attack": {
  "simple_prior": 1.83,
  "target_killable": 0.41,
  "target_is_leader": 0.22,
  "target_weak": -0.18,
  ...
}
```

## Things to try (good Python exercises)

- Change the weights in `threat.threat_score()` and see if training improves.
- Change the reward in `reward.py`, e.g. a bonus for knocking a player out.
- Add a feature in `features.py`. The brain picks it up automatically: a new
  name simply starts at weight 0.
- Train four Boys against each other: `--bots boy:Kaiba boy:bob boy:carol boy:dave`.

## Next: deals

Deals (non-aggression for N turns, ganging up on the leader) are the next
step. They will be extra options for the Boys, with the threat assessment
above deciding who "the leader" is, and they'll show up in the viewer's log.
