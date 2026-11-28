"""The neural add-on: lets a Boy learn inputs that only matter *together*.

Why
---
The brain's score (brain.py) adds up one number per input:

    score = weight[a] * a + weight[b] * b + ...

So every input counts the same, whatever the other inputs say. It can't learn
"attacking the leader is good, but NOT into a blocker that kills my creature"
if the plain "leader" plus is bigger than the plain "bad block" minus. What's
missing is a number for the two *together*.

How
---
Every input gets a small list of SIZE learned numbers, its "taste". For every
two inputs that are both on for an option, the add-on multiplies their tastes
number by number and adds that up (a "dot product"):

    together(a, b) = taste[a][0] * taste[b][0] + ... + taste[a][3] * taste[b][3]

Tastes that point the same way give a plus, opposite tastes a minus. So
"leader" and "bad block" can learn tastes that clash, and the pair gets its
own minus on top of the two plain weights. The add-on's part of the score is
the sum over all pairs:

    gut feeling = sum of together(a, b) * a * b, over every pair of inputs a, b
    score       = (the old score) + gut feeling

This is the smallest kind of neural network that learns combinations (it is
called a "factorization machine": one layer, no squashing). We first tried
the textbook kind with hidden units and tanh, but on a test table (tests/test_net.py)
it got stuck 3 times out of 5, while this one learned it every time.

Why tastes and not one number per pair? There are thousands of pairs, and
most turn up rarely. With tastes, what "leader" learns with one input helps
with others too, and the whole add-on stays a few thousand numbers.

Starting point
--------------
Tastes start as tiny random numbers (the same ones every time, so runs can
be repeated), so a new add-on adds almost nothing: a Boy plays like before.

Learning
--------
Same REINFORCE rule as the weights (brain.py): after a better-than-expected
choice, nudge every number so the picked option's score goes up compared with
the average option. For taste[a][j] the nudge is a * (sum over the other
inputs b of taste[b][j] * b), for the picked option minus the average option.
Plain Python, no numpy or PyTorch.

Card memory stays separate: card numbers (cards.py) and SimpleBot's hint
are not inputs here, so a Boy still learns his own opinion of each card exactly
as before, and the gut feeling is his own, not SimpleBot's.
"""

from __future__ import annotations

import zlib

from showdown.boys import cards

SIZE = 4            # numbers per input's taste
RATE = 5.0          # the add-on learns 5x faster than the weights (it starts tiny, see tests/test_net.py)
START_SIZE = 0.1    # starting tastes are random between -0.1 and +0.1
LIMIT = 3.0         # no taste number may grow beyond +-3 (keeps it calm)
HINT = "simple_prior"   # SimpleBot's pick: not an input (see above)

Features = dict[str, float]
Tastes = dict[str, list[float]]   # one kind of decision: input name -> taste

_start_cache: dict[tuple[str, str], list[float]] = {}


def _starting(kind: str, name: str) -> list[float]:
    """The starting taste of one input: tiny, random-looking, always the same."""
    key = (kind, name)
    if key not in _start_cache:
        # crc32 turns text into a number; the same text always gives the same number.
        _start_cache[key] = [((zlib.crc32(f"{kind}|{name}|{j}".encode()) % 2001) / 1000 - 1) * START_SIZE
                             for j in range(SIZE)]
    return _start_cache[key]


def inputs(features: Features) -> Features:
    """The inputs the add-on looks at: every feature that is on, except card memory and the hint."""
    return {k: max(-3.0, min(3.0, v)) for k, v in features.items()
            if v and k != HINT and not cards.is_card_feature(k)}


def taste(tastes: Tastes | None, kind: str, name: str) -> list[float]:
    return (tastes or {}).get(name) or _starting(kind, name)


def _sums(tastes: Tastes | None, kind: str, x: Features) -> list[float]:
    """sum(taste[a][j] * a) over all inputs, for each j."""
    total = [0.0] * SIZE
    for name, value in x.items():
        t = taste(tastes, kind, name)
        for j in range(SIZE):
            total[j] += t[j] * value
    return total


def score(tastes: Tastes | None, kind: str, features: Features) -> float:
    """The gut feeling for one option: every pair of inputs, multiplied together.

    Trick: (sum of all)^2 = (every pair twice) + (each one with itself), so
    the sum over pairs = ((sum of all)^2 - sum of each one squared) / 2.
    That is one pass over the inputs instead of one per pair.
    """
    x = inputs(features)
    total = _sums(tastes, kind, x)
    squares = sum((t * value) ** 2 for name, value in x.items() for t in taste(tastes, kind, name))
    return (sum(s * s for s in total) - squares) / 2


def pairs(tastes: Tastes | None, kind: str, features: Features) -> dict[tuple[str, str], float]:
    """The gut feeling split into pairs, for the Reasons tab: (a, b) -> its part of the score."""
    x = sorted(inputs(features).items())
    out = {}
    for i, (a, va) in enumerate(x):
        ta = taste(tastes, kind, a)
        for b, vb in x[i + 1:]:
            out[(a, b)] = sum(p * q for p, q in zip(ta, taste(tastes, kind, b))) * va * vb
    return out


def learn(tastes: Tastes, kind: str, options: list[Features], probs: list[float], picked: int,
          rate: float) -> None:
    """One REINFORCE nudge for one remembered choice (``rate`` already holds the advantage)."""
    rate *= RATE
    xs = [inputs(f) for f in options]
    totals = [_sums(tastes, kind, x) for x in xs]
    # How the gut feeling of option i moves when taste[a][j] moves:
    #   a * (total[j] - taste[a][j] * a)      (all the *other* inputs, not a itself)
    old = {name: list(taste(tastes, kind, name)) for name in set().union(*xs)}

    def slope(i: int, name: str, j: int) -> float:
        value = xs[i].get(name, 0.0)
        return value * (totals[i][j] - old[name][j] * value) if value else 0.0

    for name in old:
        t = tastes.setdefault(name, list(old[name]))
        for j in range(SIZE):
            g = slope(picked, name, j) - sum(p * slope(i, name, j) for i, p in enumerate(probs) if p)
            if g:
                t[j] = max(-LIMIT, min(LIMIT, t[j] + rate * g))


def to_dict(nets: dict[str, Tastes]) -> dict:
    return {kind: {k: [round(v, 4) for v in t] for k, t in sorted(tastes.items())}
            for kind, tastes in nets.items()}


def from_dict(data: dict) -> dict[str, Tastes]:
    return {kind: {k: list(t) for k, t in tastes.items()} for kind, tastes in data.items()}
