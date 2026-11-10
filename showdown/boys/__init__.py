"""The Boys: bots that learn from the games they play.

    threat.py    who is dangerous at the table (used by bots, deals and features)
    features.py  turns a decision into numbers the brain can score
    brain.py     the learned weights, how options are scored, how it learns
    reward.py    how good a finished game was for a Boy
    storage.py   where brains and training history are saved (boys/ folder)

The bot itself is showdown/bots/boy_bot.py. Training runs through
showdown/batch.py. See docs/boys.md for the big picture.
"""
