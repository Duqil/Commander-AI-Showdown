# Deals between bots

Turn deals on with `--deals`:

```bash
python -m showdown.run_game --deals
python -m showdown.batch --bots boy:Kaiba simple simple simple --games 50 --deals
```

Forge knows nothing about deals; they live in Python (`showdown/deals.py`).
Every offer, acceptance, refusal and broken deal is written to the game log
as a line starting with `Deal:` and shows up in the browser viewer's timeline.

## The menu

| Deal | Means |
|---|---|
| `peace` | "Let's not attack each other for 2 rounds." |
| `gang_up` | "Let's both attack *X* for 2 rounds" (and not each other). |

A round is every player taking one turn, so 2 rounds at a 4-player table is
8 game turns.

## When deals happen

At the start of each turn, the active player's bot may offer **one** deal to
one opponent. That opponent's bot accepts or declines. Bots see their deals,
trust and grudges in every decision (`decision["deals"]`).

## Breaking a deal

Attacking someone you have a deal with breaks it. Python notices by itself,
because Python decides attacks. A broken deal is a big event:

- **Revenge:** the breaker goes on the victim's grudge list for the rest of
  the game. SimpleBot attacks grudges first and never deals with them again;
  Boys start out wanting revenge (`attack_grudge`) and learn how much.
- **Reputation:** the victim's trust in the breaker drops by 0.5, everyone
  else's by 0.25. SimpleBot refuses offers from anyone below 0.5.

## What breaking costs a Boy

Trust and grudges only last one game, so on their own they didn't stop the
Boys from offering every turn, accepting almost everything and breaking deals
freely. Two small reward penalties (in `showdown/boys/reward.py`, under "Politics") fix that:

| Constant | Who pays | Why |
|---|---|---|
| `BROKE_A_DEAL` (-0.1) | the attack choice that broke a deal | breaking only pays for something big (a knockout is +0.3) |
| `PARTNER_BROKE_MY_DEAL` (-0.1) | the "accept" choice of a deal the partner then broke | Boys learn to say no to players who break deals |

### Offering isn't free either

After those two, offering still cost nothing, so the Boys offered every turn
(17 to 45 offers a game, from turn 1) and almost every offer got a "yes".

| Constant | Who pays | Why |
|---|---|---|
| `OFFER_MADE` (-0.02) | the "offer a deal this turn?" choice, when it says yes | a deal has to be worth asking for |
| `OFFER_DECLINED` (-0.05) | the same choice and the "which deal" choice, when the answer is no | asking someone who won't say yes costs more |

And a few starting dislikes (in `showdown/boys/brain.py`, `DEFAULT_WEIGHTS`).
They're where a fresh Boy starts; training can change them:

| Feature | Starts at | Meaning |
|---|---|---|
| `offer_quiet_table` | -2.0 | offering while the whole table has less than 3 creature power |
| `gang_pointless` / `accept_gang_pointless` | -3.0 | a gang-up where the two partners have less than 3 power between them |
| `accept_low_trust` | -4.0 | 1 - trust in whoever offers: someone who broke a deal with you this game (trust 0.5) starts at -2.0 |
| `gang_against_runaway` / `accept_against_runaway` | +2.0 | a gang-up on a player far ahead of everyone (threat 1.5x the next player: a big board or a big life lead) |
| `offer_vs_runaway_leader` | +1.0 | offering at all while someone is running away with the game |

The dislikes are tied to how much the partners can fight with, not to the
turn number, so a gang-up late in the game with real creatures on the table
doesn't get them.

**Doesn't accepting already depend on trust?** For SimpleBot, yes: it refuses
anyone below 0.5. Boys only *saw* trust as one signal among many and learned
to mostly ignore it. And everyone starts each game at full trust, so trust
can only matter after someone breaks a deal. `accept_low_trust` makes that
moment count. Trust and grudges still last one game only.

Make the constants more negative to turn politics down further, or set them
to 0.0 to switch them off. `BoyBot.deal_broken` (in `showdown/bots/boy_bot.py`) is
where they're applied; the game runner calls it for both partners whenever a
deal breaks.

## Winning trust back

Trust (0 to 1) can recover during a game:

- **Kept deal:** when a deal runs its full length unbroken, both partners'
  trust in each other goes up by 0.1 (logged as "kept their deal"). Only
  if the deal was *tested*: at some point one partner had a creature with
  power that could have attacked the other, and didn't. Peace in round 2
  with empty boards proves nothing, so it earns nothing.
- **Helping:** attacking the agreed gang-up target raises your partner's
  trust in you by 0.05.

Grudges don't fade yet. All the numbers are at the top of `showdown/deals.py`.

Deals, trust and grudges reset every game.

## Who makes deals

| Bot | Deals |
|---|---|
| `simple` | Never breaks a deal. Only offers from round 3 on, and only once the strongest opponent has at least 3 power on the board. Then: if someone else leads, asks the next strongest to gang up on the leader; if it leads itself, offers peace to the strongest. Accepts from players it trusts, unless they lead. |
| `random` | Offers a random deal 30% of the time, accepts half, and breaks deals often since it attacks at random. |
| `boy:<name>` | Learns whether to offer this turn (`offer_whether`), then which deal (`offer`), what to accept and when breaking pays. Starts out close to SimpleBot. Also sees how far into the game it is and how much power is on the table, so it can learn that early deals don't pay. |
| `forge` | Forge's own AI can't make deals: offers to it are declined. |
