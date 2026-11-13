"""How good was a finished game for a Boy? One number, higher is better.

Only rewarding wins would make learning very slow: in a 4-player game a
beginner wins maybe 1 game in 4, and every loss would look the same. So a
loss where you outlived two opponents scores better than dying first.

    win                       +1.0
    each opponent outlived    +0.5 / number of opponents   (all three = +0.5)
    draw                       0.0 extra (the outlived part still counts)

On top of that, each *turn of your own* earns a small bonus for what you did
to your opponents during it:

    each point of damage to opponents   +0.01
    each opponent knocked out           +0.3
    each card you draw                  +0.01   (more cards = more options)
    each card thrown away to hand size  -0.03   (ending a turn with 8+ cards)

The turn bonus only goes to the choices made in that turn. So a good attack
gets credit right away, instead of waiting to see whether the whole game was
won. That gives the brain a much clearer signal about attacks and targets.

Politics (deals) have their own small penalties, see "Politics" below.

This is a good place to experiment. Ideas for later:
  * a bonus for keeping a deal,
  * a small penalty for very long games.
"""

from __future__ import annotations

WIN = 1.0
OUTLIVED = 0.5


def game_reward(won: bool, opponents_outlived: int, opponents: int) -> float:
    reward = WIN if won else 0.0
    if opponents:
        reward += OUTLIVED * opponents_outlived / opponents
    return reward


DAMAGE = 0.01
KNOCKOUT = 0.3


CARD_DRAWN = 0.01
DISCARDED = -0.03


# A card lost to a mulligan (Commander's first mulligan is free, every one
# after that puts a card on the bottom). Bigger than a discard on purpose:
# a mulligan is one choice per game, so its signal must stand out from the
# noise of winning and losing.
MULLIGAN_CARD = -0.1


def mulligan_bonus(cards_lost: int) -> float:
    """Bonus for one mulligan choice: negative for each card it cost."""
    return MULLIGAN_CARD * cards_lost


# --- Politics ---------------------------------------------------------------------
#
# Deals are mostly flavor for whoever is watching, so we don't want a table
# where every turn starts with an offer that gets broken a turn later. Before
# these two penalties, breaking a deal cost a Boy nothing (only the victim's
# trust, for one game) and accepting one cost nothing either, so the Boys
# learned to offer, accept everything (98%) and break freely.
#
#   BROKE_A_DEAL         goes to the attack choice that broke a deal. Breaking
#                        still pays when the attack is worth more than this
#                        (a knockout is +0.3), so a Boy learns to break only
#                        for something big.
#   PARTNER_BROKE_MY_DEAL goes to the "accept" choice of a deal the partner
#                        then broke. Boys learn to decline offers from players
#                        who break deals (they see trust and grudges), which
#                        makes offering-to-break pay less.
#
# Both are the size of one card lost to a mulligan. To turn politics down
# further, make them more negative; set both to 0.0 to switch them off.
# Where they're applied: BoyBot.deal_broken in showdown/bots/boy_bot.py.
# A third knob, not used yet: a cost for an offer that gets declined.
BROKE_A_DEAL = -0.1
PARTNER_BROKE_MY_DEAL = -0.1


def turn_bonus(damage: int, knockouts: int, drawn: int = 0, discarded: int = 0) -> float:
    """Bonus for one of your own turns."""
    return DAMAGE * damage + KNOCKOUT * knockouts + CARD_DRAWN * drawn + DISCARDED * discarded
