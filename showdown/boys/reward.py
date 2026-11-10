"""How good was a finished game for a Boy? One number, higher is better.

Only rewarding wins would make learning very slow: in a 4-player game a
beginner wins maybe 1 game in 4, and every loss would look the same. So a
loss where you outlived two opponents scores better than dying first.

    win                       +1.0
    each opponent outlived    +0.5 / number of opponents   (all three = +0.5)
    draw                       0.0 extra (the outlived part still counts)

This is a good place to experiment. Ideas for later:
  * a bonus for eliminating a player yourself,
  * a bonus for keeping a deal, or for breaking one at the right moment,
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
