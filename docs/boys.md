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

Names may use letters, digits, spaces, `-`, `_` and `'` (up to 32
characters). On the command line, put a name with a space in quotes:
`--bots "boy:Nicol Bolas" simple simple simple`.

A game takes about 45 seconds, and each one runs its own Forge using about
2 GB of memory and 2 CPU cores. `--parallel 2` or `3` is a good choice on a
normal PC. So 100 games is roughly half an hour.

## What a Boy decides

Mulligans, what to play (or pass) when it has priority, the target of the
play it chose (when it has one target), and where each creature attacks.
For targets, Forge's AI suggests one first; a new Boy mostly follows that
suggestion (`forge_suggested`) and learns when to pick something else.
Blocks, triggered abilities' targets and other in-card choices are still
Forge's AI for every bot.

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

**Damage engines.** Power alone misses decks like Kuja, whose 0/1 Wizard
tokens ping every opponent each time he casts a spell. So `showdown/damage.py`
reads Forge's log and writes the last round's damage into the state:
`recent_damage_to` on each player and `recent_damage` on each card. The threat
score uses creature power *or* damage actually dealt, whichever is bigger, and
the Boys' targeting has `hurt_damage_dealer` (starting weight 2.0): removal on
a card that dealt 5+ damage last round counts as much as Forge's suggestion.
This works for any deck: pingers, drains, burn.

**Learning on top of SimpleBot.** Every option has a `simple_prior` feature
that is 1 for SimpleBot's choice, and every brain starts with weight 2.0 on
it. So a new Boy picks SimpleBot's move about two times in three and
something else the rest of the time. If you improve SimpleBot, every Boy's
starting instinct improves with it.

**Mulligans.** Commander's first mulligan is free; every one after that puts
a card on the bottom. The game loop counts mulligans (`mulligans_so_far` in
the decision), a mulligan that costs a card gets a bonus of -0.1, and
`mulligan_deeper` is 0 for the free one. Mulligans also learn at a quarter of
the normal speed: they happen once a game, and at full speed Yugi's weights
wandered until he threw away good 3-land hands 70% of the time.

**Colors.** The bridge sends each card's mana `cost` ("{1}{U}{R}") and the
colors it `produces` ("UB"). `showdown/boys/colors.py` works out which colors
my hand and commander need and which my permanents make. Boys see
`keep_color_coverage` (mulligan: how many spells the lands in hand can pay
for), `land_new_color` (a land drop that adds a missing color) and a new
decision kind, `pick_card`: cards you pick for yourself. That is any search
of your library for lands (Evolving Wilds, Terramorphic Expanse, Cultivate,
Myriad Landscape: no card names in the code, only "searching my library and
every option is a land") and any "look at the top N, put some into your hand"
(Drawn from Dreams, Impulse). Features: `pick_land_new_color`,
`pick_land_only_old_colors`, `pick_land_color_value`, `pick_land_when_short`,
`pick_castable` (a spell my colors can pay for) and `pick_cmc`. When the pick
is a land, SimpleBot takes the one whose colors its cards want most; it also
plays the most useful land from hand. Forge's own AI only fixed colors when it
had no land in hand and nothing to cast, so Anhelo could fetch Island after
Island.

**Commander and extra costs.** There's no rule about when to cast the
commander. Boys see `cast_commander` and `commander_tax`; `cast_commander`
starts at weight 1.0 (a mild liking, since SimpleBot's "biggest spell first"
almost never picks a 3-mana commander), and training moves it from there. Optional extra costs while casting (casualty, offspring...)
are a decision too (`keyword_cost`): Forge's AI pays whenever it can, SimpleBot
only refuses to sacrifice its commander, and Boys see what they'd sacrifice
(`sac_token`, `sac_comes_back` for Bloodghast-like cards, `sac_cheapest_cmc`,
`sac_only_commander`).

**Attack triggers.** Every card's rules text (`oracle`) comes along now. Boys
see `attack_equipped` (an Equipment sits on the attacker) and
`attack_triggers`: how many of my "whenever ... attacks" abilities this attacker
sets off, read from the text ("whenever an equipped creature you control
attacks" needs an equipped attacker, "whenever <name> attacks" only that card).
`attack_triggers` starts at weight 1.0.

**Fixing a bad habit.** To reset one kind of decision for a Boy (back to the
starting weights, everything else kept):

```
python -m showdown.boys.storage forget Yugi mulligan
```

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

**X spells and play order.** SimpleBot always dumps all its mana into the
biggest X creature. The Boys get features to learn when that's wrong:
`x_size` (how big X would be), and for every other play while an X creature
waits: `x_left_after` (X left after this play), `kills_the_x` (no mana left for
X), `counter_booster` and `booster_before_x` (Hardened Scales first, then a
slightly smaller Tervigon that gets an extra counter). They start at weight 0,
so a Boy has to discover them in play.

**Graveyard tricks.** The bridge marks cards that work from the graveyard
(`graveyard_use`: "landfall" for Bloodghast-style returners, "keyword" for
flashback/escape/madness..., "trigger", "ability"), Forge's own
`sac_me`/`discard_me` ratings, and keywords a permanent `grants` (Anhelo grants
"Casualty"). Boy play features: `land_returns_creatures` (a land drop brings a
returner back), `casualty_before_land` (cast the casualty spell first,
sacrificing a returner, while the land drop is still unused) and its opposite
`land_before_casualty`. Discarding a graveyard-useful card to hand size isn't
penalised.

## Things to try (good Python exercises)

- Change the weights in `threat.threat_score()` and see if training improves.
- Change the reward in `reward.py`, e.g. a bonus for knocking a player out.
- Add a feature in `features.py`. The brain picks it up automatically: a new
  name simply starts at weight 0.
- Train four Boys against each other: `--bots boy:Kaiba boy:bob boy:carol boy:dave`.

## Deals

Boys also learn when to offer, accept and break deals: see `docs/deals.md`.
