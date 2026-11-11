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
