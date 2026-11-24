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

Mulligans, what to play (or pass) when it has priority (in any step,
combat included), the target of the play it chose (when it has one target),
and where each creature attacks.
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
critic.py     what reward did we *expect* from the spot each choice was made in?
   │
   ▼
brain.py      REINFORCE: after a better-than-expected game, strengthen the
              features of the options you picked; after a worse one, weaken them
```

**The critic** (`showdown/boys/critic.py`) guesses the reward from a few
numbers about the table: turn, my life, my hand, my lands, opponents left,
their average life, my share of the table's hitting power, and which deck
I'm playing (`deck:<commander>`). It is a weighted sum like the brain's
scores and learns after every game. Choices get credit for doing better
than expected *from that spot*, so losing on a weak deck or from 8 life
isn't blamed on the choices. Its weights are saved under `"critic"` in each
Boy's file; `bias` is the average reward, and old brains start with their
old average there, so they carry on unchanged.

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

**Who to attack.** Early on, every Boy learned to pile onto the weakest player
(`target_threat_rank` and `target_weak` grew strongly positive): no blockers
means easy damage, and SimpleBot hit whoever had the lowest life. The leader
then ran away. Now damage in the turn bonus is weighted by the victim's threat
(x1.5 for the biggest threat, x0.5 for the smallest, see `reward.py`), and
SimpleBot finishes off a player it can kill this turn, otherwise attacks the
biggest threat. Knockouts still count in full.
This works for any deck: pingers, drains, burn.

**Attacking into blockers.** A 2/2 swinging at a player with an untapped 3/4
just dies for nothing. SimpleBot now skips a target when one of its untapped
creatures would kill the attacker and survive, unless the attack can finish
that player, it was promised (revenge, push back, a gang-up), or we bring more
attackers than they have blockers. It tries the next target instead, or stays
home. The Boys get inputs to weigh blocks themselves (`features.attack_options`):

| Input | Meaning | Starts at |
|---|---|---|
| `target_bad_block` | a blocker kills it and survives | learned |
| `target_double_block` | no single blocker kills it, two together do | 0 |
| `target_trade_block` | a blocker kills it but dies too | 0 |
| `target_outnumber` | our attackers minus their blockers, -1..1 | 0.5 |
| `target_blockers_outnumbered` | more attackers than blockers | 0 |
| `attack_valuable_at_risk` | commander, 4+ power, our biggest or equipped, and a bad or double block waits | -0.75 |
| `attack_last_creatures_at_risk` | same, and we have 2 creatures or fewer | -0.5 |

The starting pulls are mild on purpose, so the Boys think about blocks
without getting scared of attacking. Besides power and toughness, flying/reach
and deathtouch are read from the cards' keyword lines (`threat.has_keyword`);
first strike, menace and keywords granted by other cards are not.

**Removal that works** (`showdown/boys/removal.py`, Adam's replay notes 2026-11-24):

- *Ward.* "Ward {2}" is read from the text. A target whose ward we couldn't
  pay after casting the spell gets `target_ward_unpayable` (-3.0), and
  SimpleBot picks another one. Casting removal while the creature it would
  go for has such ward gets `removal_ward_unpayable` (-1.5): better to wait
  for the mana. SimpleBot waits.
- *Instant removal.* Cast in my own main phase with nothing urgent (no high
  danger, nothing dealt 3+ damage last round): `instant_removal_no_rush`
  (-1.0). A sorcery that leaves too little mana for the instant removal I
  hold, while an opponent has a creature: `taps_out_holding_removal` (-0.75).
- *Board wipes.* Destroy/exile all creatures, all creatures get -X/-X, N
  damage to each creature, each player sacrifices a creature: we count what
  each player would lose (mana value + power, double for a commander).
  `wipe_my_share` (-2.0), `wipe_good_trade` (+1.0: the leader loses more than
  me), `wipe_partner_share` (-0.5). SimpleBot holds a wipe when its own share
  is half or more.

**Lands.** On a turn where the extra mana wouldn't be used before my next
turn, a land that enters tapped goes first, so the untapped ones are ready
later; Evolving Wilds and the like count as tapped lands (`mana.slow_land`).
"Used before my next turn" also counts casting a spell now while keeping an
instant up. A bounce land with 8+ cards in hand, when another land could be
played, means a discard: `land_bounce_full_hand` (-1.5), and SimpleBot plays
the other land.

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
commander, and no single "commander" weight either: commanders differ too
much. `showdown/boys/commander.py` reads each commander's *traits* from its
card (cheap, big body, spell engine and which spells feed it, draws cards,
makes tokens, attack trigger, grows when recast, gets cheaper) and crosses
them with the situation for the option "cast my commander": `cmdr_engine_fuel`
(an engine x the spells in hand it would power up), `cmdr_big_late`,
`cmdr_big_open_board`, `cmdr_cheap_early`, `cmdr_draws_low_hand`,
`cmdr_grows_recast` and more. Once it's out, other spells get
`feeds_my_commander` (an instant with Anhelo out). On top, `cmdr_is_<name>` is
a small learned adjustment for that one commander (it starts at 0; it's
learned, never a rule). `cast_commander` starts at 1.0, `commander_tax` at 0.
Optional extra costs while casting (casualty, offspring...)
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

**Combat tricks.** A Boy gets priority in every step, also mid-combat, so it
can cast instants and use abilities after blockers are declared. The table
snapshot says which creatures attack whom and which block what;
`showdown/boys/combat.py` turns that into "my unblocked attackers", "my
blocked attackers", "creatures blocking mine", "creatures attacking me" and
so on (before blocks, nothing counts as unblocked yet). A play made during
combat gets `play_in_combat`, `trick_my_unblocked` (damage is sure to land),
`trick_my_blocked` (a fight to win), `trick_defending` and
`trick_unblocked_at_me`; passing then gets `pass_in_fight`. Its target gets
`help_my_unblocked` (a pump, or Hunter's Insight's "draw per damage"),
`help_my_blocked`, `help_my_blocker`, `hurt_blocker_of_mine` (clear the way),
`hurt_attacker_at_me` and `hurt_unblocked_at_me`. They all start at 0, so a
Boy discovers tricks by trying them. Whether a target is helped or hurt also
reads who may be targeted (`ValidTgts`): "creature you control" counts as
help even when the text mentions damage. No card names anywhere.
On someone else's turn, `trick_my_blocker` marks a fight where my creature
blocks (pump it), and `trick_forge_would_play` says Forge's AI likes the trick.

**Instant speed.** (`showdown/boys/timing.py`) An instant doesn't belong in
your own main phase: keep the mana open and use it when it matters. Plays get
`instant_on_my_main` and `reactive_on_my_main` (a counterspell, pump or
protection spell cast with nothing going on: usually wasted),
`instant_end_step_before_mine` (the end of the turn right before mine: the
mana untaps next anyway), `respond_to_opponent` and `respond_forge_would_play`
(an opponent's spell or ability is on top of the stack), and `reactive_spell`.
Passing at that end step while an instant is castable gets
`pass_end_step_with_instant`. "Reactive" is read from what the spell does
(Forge's effect type: Counter, Pump, Protection...), not from names.
SimpleBot now does the simple version: it holds instants and flash cards in
its main phase, answers opponents and casts tricks when Forge's AI says yes,
and at the end of the turn before its own casts what it kept (reactive cards
only if Forge's AI likes them).

**Impulse draws.** "Exile the top card of your library. Until the end of your
next turn, you may play it." Those cards run out. The bridge says which zone a
playable card is in, so plays get `from_exile` and `land_from_exile`, and
passing my main phase with one still playable gets `pass_with_exile_card`.
SimpleBot plays cards from exile before cards in hand. And the land drop:
an impulse draw might turn up a land, which you can only play if you haven't
played one yet. `impulse_play` and `impulse_before_land` mark an impulse
play while the land drop is free; `land_impulse_pending` marks playing a land
from hand while an impulse draw (a card, or a "whenever you cast" ability I
control, like Riku of Many Paths) could still come. A Boy learns the order
itself; SimpleBot still plays its land first.

**Fixing a bad habit.** To reset one kind of decision for a Boy (back to the
starting weights, everything else kept):

```
python -m showdown.boys.storage forget Yugi mulligan
```

## Reading a brain

Open `boys/Kaiba.json`. The `"critic"` block says what the Boy expects:
for example `"deck:Swarmlord": -0.2` means games on Swarmlord usually go 0.2
worse than average, and a positive `my_power_share` means having the
biggest board usually pays off. Weights are listed strongest first for each kind of
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

## Card memory: a generalist that learns single cards

Every feature above describes what a card *is* (a creature, an X spell...),
so it works for any deck. Card memory adds one learned number per card on top
(`showdown/boys/cards.py`), for example:

```json
"cards": {
  "play": {
    "cast:lightning_greaves": 0.31,
    "cast:lightning_greaves@early": 0.12,
    ...
  },
  "target": {"hurt:sol_ring": 0.2, ...}
}
```

The verbs are `cast`, `use` (an ability), `pick` (from a search),
`hurt`/`help` (as a target) and `attack`. `@early` means rounds 1-3 and `@late`
means round 8 on. Every card starts at 0, so on day one the Boy plays exactly
like before, and he learns on his own which cards are better or worse than the
general rules think. Nothing is written by hand for any card.

### Situations and roles

A card's value depends on when and how it's used. Lightning Bolt in my own
main phase at a player is a different play from Lightning Bolt on an
opponent's turn at an attacking creature. So card memory also has:

- **Situations** for `cast` and `use`: `@their_turn`, `@combat` and `@respond`
  (something is on the stack), e.g. `cast:lightning_bolt@combat`.
- **Roles**: what the card does, read from Forge's own description of its
  effects (`DealDamage` becomes `damage`, a `-3/-3` Pump becomes `shrink`,
  exiling from the battlefield becomes `exile`...). A card gets every role
  that fits; a modal spell gets the roles of the modes Forge's AI chose.
  Roles also get the situations and stages: `role:damage@combat`,
  `role:pump@their_turn`, `role:draw@early`.
- **Aim**: where a targeted effect goes: `aim:lightning_bolt>player`,
  `aim:damage>creature`, `aim:damage>commander`, `aim:destroy>mine`.
  Kinds are `player`, `me`, `commander`, `creature`, `mine` and `permanent`.

Roles are shared by every card with the same effect, so a lesson learned with
one burn spell carries over to every burn spell in every deck, including
cards the Boy has never seen. This needs the bridge from the same version
(`scripts/build_bridge.sh`): with an older bridge the roles are simply missing.

Which modes a modal spell uses is still Forge's AI's choice. The bridge logs
each pick in the game log ("Detail: 1-Riku modes for Over the Edge: chose ...
(not: ...)"), so we can see how good those picks are.

Card numbers learn at half speed and lose 0.5% once per game they come up in
(`CARD_RATE`, `CARD_SHRINK` in brain.py), so a single lucky game can't give a
rarely seen card a strong opinion.

It's off for every Boy unless switched on. Make a copy of a trained Boy with
card memory, so the original stays as it was for comparison:

```bash
python -m showdown.boys.storage copy Kaiba "Kaiba Prime" --card-memory
```

Several seats can play the same Boy at once and they all learn into one brain,
so a table of three Kaiba Primes and one other bot learns three times as fast:

```bash
python -m showdown.batch --decks zuko terra nahiri kuja \
    --bots "boy:Kaiba Prime" "boy:Kaiba Prime" "boy:Kaiba Prime" boy:Yugi --games 100 --parallel 3
```

## Things to try (good Python exercises)

- Change the weights in `threat.threat_score()` and see if training improves.
- Change the reward in `reward.py`, e.g. a bonus for knocking a player out.
- Add a feature in `features.py`. The brain picks it up automatically: a new
  name simply starts at weight 0.
- Train four Boys against each other: `--bots boy:Kaiba boy:bob boy:carol boy:dave`.

## Deals

Boys also learn when to offer, accept and break deals: see `docs/deals.md`.
