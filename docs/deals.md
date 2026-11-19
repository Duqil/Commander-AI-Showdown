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
| `peace` | "Let's not attack each other for N rounds." |
| `gang_up` | "Let's both attack *X* for N rounds" (and not each other). |
| `removal` | "I'll remove something of *X*'s. In return: peace for N rounds." |
| `gift` | "I'll give you something (a card, a Treasure...). In return: peace for N rounds." |

**Plain peace is on ice for now** (Adam, 2026-11-19): it never felt right, so
games run without `peace` offers while we watch how the other deals get used.
Bots are told the menu without it, and an offer of it is refused. Favours
still come with peace, since that's what the offerer gets in return. To turn
plain peace back on, set `PEACE_DEALS = True` at the top of
`showdown/deals.py`, or start with the environment variable
`SHOWDOWN_PEACE_DEALS=1` (`python -m showdown.batch --deals --peace` does that).

The offerer picks N: 1, 2, 3 or 4 rounds (`"rounds"` in the offer, 2 if a bot
doesn't say). A gang-up can also last until *X* is out of the game
(`"rounds": 0`). A round is every player taking one turn, so 2 rounds at a
4-player table is 8 game turns.

A deal also ends early, with no blame, when:

- one of its two players is out of the game,
- a gang-up's target is out (the job is done: it counts as kept),
- the two partners are the only players left (no peace in the final two).

And once only two players are left, nobody can offer a new deal either.

These show up as "deal is over" lines in the log.

## When deals happen

At the start of each turn, the active player's bot may offer **one** deal to
one opponent. That opponent's bot accepts or declines. Bots see their deals,
trust and grudges in every decision (`decision["deals"]`).

## Favours: removal or a gift, for peace

`removal` and `gift` are peace with a payment on top. The offerer pays first:

| | Removal for a favour | Gift for a favour |
|---|---|---|
| Offered only when | the offerer holds a card whose text removes ("destroy target", "exile target", "deals 3 damage to any target"; not a blink like "exile target creature you control") | the offerer holds a card whose text gives an opponent something ("target opponent creates/draws/gains...") |
| Paid when | the offerer really sets *X* back (see below) | the offerer aims something helpful at the partner (Loran's draw, Generous Plunderer's Treasure) |
| Deadline | 1 or 2 rounds, the offerer picks (`PAY_LENGTHS`; 2 if a bot doesn't say) | same |
| Not paid in time | counts as breaking the deal: penalty, grudge, revenge | same |

The deadline counts from the deal, even if the peace itself runs out first:
the favour is still owed.

**What pays a removal.** Removing one 1/1 isn't fair payment. On the
offerer's own turn, or with a card the offerer aimed at it, any one of these:

- removing *X*'s commander, a 3+ power creature or a 3+ mana non-land,
- removing *X*'s biggest creature (2+ power), or *X*'s biggest artifact,
  enchantment or planeswalker (2+ mana),
- removing an engine piece: a card that dealt 2+ damage to players lately
  (`ENGINE_DAMAGE`), or one that rewards casting spells,
- a wipe: 3 of *X*'s non-lands gone in the offerer's turn (`WIPE_COUNT`),
- or *X*'s threat score falling by a fifth during the offerer's turn.

**Named removals.** An offer can also name one card: "I'll remove Kefka from
Kuja". The card named is *X*'s biggest threat (`biggest_threat_card`: damage it
dealt lately counts most, then a commander, power and mana cost), and only
cards whose removal is a real setback can be named. Only removing that exact
card pays. If it leaves play some other way (someone else kills it, it's
stolen or flickered) the deal just ends, with no blame. Boys see whether one of
their removal cards can hit it (`removal_named_can_hit` +0.5, read from text);
accepting likes a named promise a little (`accept_named` +0.3), more so when
the card has been dealing damage (`accept_named_engine` +0.5).

Paying is logged ("Kuja removed Zangief's Krenko (their biggest creature) for
Garnet, as promised") and shown as a 🎁 moment in the viewer. Until then the Politics
tab says who owes what. Card texts are read with `REMOVAL_TEXT` / `GIFT_TEXT`
at the top of `showdown/deals.py`: no card names anywhere.

Boys start out liking to pay what they promised (`pay_removal`, `pay_gift`
+3.0) and find a favour a bit more tempting than plain peace (`accept_removal`,
`accept_gift` +1.0). They like a 2-round deadline a little when offering
(`removal_pay_slow`, `gift_pay_slow` +0.3) and a little less when accepting
(`accept_pay_slow` -0.3). SimpleBot never offers favours but can accept them.

## Sharing plans: a recurring gift, handed out in turn

A player with a gift that comes back every turn (a card on the battlefield
with gift text, like Generous Plunderer or Loran of the Third Path) can
announce a sharing plan instead of making an offer:

> Zidane will share Generous Plunderer in turn: Anhelo, then Nelly, then Kuja

- A gift to the next player in line raises their trust a little (+0.05) and
  is logged "as planned".
- A gift out of turn (to pay a gift favour, say) costs trust with whoever
  was skipped (-0.1): "Nelly was skipped and trusts Zidane less".
- Players in someone's plan go easy on them: SimpleBot attacks them last,
  Boys start out disliking `attack_my_sharer` (-1.0).
- SimpleBot always announces a plan when it can, and follows it. Boys learn
  whether to (`offer_share`) and start out following it (`share_planned` +2.0).
- The plan ends when the gift card leaves play.

To let the bots choose who gets a triggered gift ("target opponent creates a
tapped Treasure"), the bridge now asks Python for the targets of triggered
abilities that can only target players (`choose_targets` with `"trigger":
true`). Everything else about triggers is still Forge's AI. After pulling
this, run `scripts/build_bridge.sh` once.

## Breaking a deal

Attacking someone you have a deal with breaks it. Python notices by itself,
because Python decides attacks.

So does agreeing to a new deal that goes against an old one (Adam's call,
2026-11-18). It breaks the old deal the moment the new one is accepted:

| Old deal | New deal | Who breaks what |
|---|---|---|
| Nelly has peace with Zangief | Nelly joins a gang-up on Zangief | Nelly breaks the peace with Zangief |
| Nelly and Zidane gang up on Zangief | Nelly makes peace (or teams up) with Zangief | Nelly breaks the gang-up with Zidane |

It counts for both new partners, the one who offered and the one who said
yes. A declined offer breaks nothing. The Boys see `offer_breaks_deal` and
`accept_breaks_deal` (both start at -2.0), so they can learn when switching
sides is worth it.

A broken deal is a big event:

- **Grudge:** the breaker goes on the victim's grudge list for the rest of
  the game. SimpleBot never deals with them again.
- **Revenge, once:** the victim owes the breaker one revenge (see below).
- **Reputation:** the victim's trust in the breaker drops by 0.5, everyone
  else's by 0.25. SimpleBot refuses offers from anyone below 0.5.

## Revenge: one real setback, then it's over

Real players usually pay a betrayal back, but going all-in for the rest of
the game is overkill. So the victim owes **one** revenge, and it's done as
soon as the victim has really set the breaker back by any of these
(numbers at the top of `showdown/deals.py`):

| Setback | Counts when |
|---|---|
| Damage | the victim deals the breaker 5+ damage in one game turn (`REVENGE_DAMAGE`) |
| Removal | the victim removes one of the breaker's important things: their commander, a creature with 3+ power, or any non-land costing 3+ (`important()`). It counts on the victim's own turn, or any time the victim aimed something harmful at that card. |
| Board hit | the breaker's threat score (`threat.threat_score`) drops by a fifth during the victim's turn (`REVENGE_THREAT_DROP`) |

Only the victim's own actions count. When it's done, the viewer shows it
(banner, a ⚔ moment between the two boards, and a log line):
"Zidane removed Krenko from Zangief. Zidane has taken revenge, and it's over."

While revenge is owed, bots see the breaker in `decision["deals"]["revenge"]`:
SimpleBot attacks them first, and Boys start out liking `attack_revenge` and
`hurt_revenge_target` (+1.5 each). After that, the grudge stays (no new
deals, lower trust) but the hunt is over. All of this lasts one game.

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

## Who gains more?

A deal means "we don't attack each other". Whether that's a good trade
depends on both sides (`threat.deal_gains`):

- **my gain:** how much of my life the partner could take in one go (their
  damage output / my life, at most 1.0), now held back,
- **their gain:** the same the other way around, times how big a threat they
  are next to the biggest threat at the table. Helping the leader counts a
  lot; helping someone far behind hardly counts.

Boys see both when answering (`accept_my_gain` +2.0, `accept_their_gain` -2.0)
and when offering (`peace_my_gain` / `peace_their_gain` +2/-2, `gang_my_gain` /
`gang_their_gain` +1/-1). SimpleBot says no when the offerer would gain
clearly more than it does (`GAIN_MARGIN` 0.05 in `showdown/bots/simple_bot.py`).
That's the main reason for a "no" now: peace with someone who can't really
hurt you, but whom you could hurt, is a bad trade.

## Who makes deals

| Bot | Deals |
|---|---|
| `simple` | Never breaks a deal. Offers last 2 rounds. Only offers from round 3 on, and only once the strongest opponent has at least 3 power on the board. Then: if someone else leads, asks the next strongest to gang up on the leader; if it leads itself, offers peace to the strongest. Accepts from players it trusts, unless they lead or would gain clearly more than it does. |
| `random` | Offers a random deal (random length) 30% of the time, accepts half, and breaks deals often since it attacks at random. |
| `boy:<name>` | Learns whether to offer this turn (`offer_whether`), then which deal and how long (`offer`), what to accept and when breaking pays. Starts out close to SimpleBot. Also sees how far into the game it is and how much power is on the table, so it can learn that early deals don't pay. |
| `forge` | Forge's own AI can't make deals: offers to it are declined. |
