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

Deals, trust and grudges reset every game.

## Who makes deals

| Bot | Deals |
|---|---|
| `simple` | Never breaks a deal. From round 2 on: if someone else leads, asks the next strongest to gang up on the leader; if it leads itself, offers peace to the strongest. Accepts from players it trusts, unless they lead. |
| `random` | Offers a random deal 30% of the time, accepts half, and breaks deals often since it attacks at random. |
| `boy:<name>` | Learns what to offer, what to accept and when breaking pays. Starts out close to SimpleBot. |
| `forge` | Forge's own AI can't make deals: offers to it are declined. |
