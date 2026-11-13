"""The viewer's event model: one list of small dicts that describes a whole game.

Forge talks to Python in "bridge messages" (see docs/bridge.md). The viewer
turns those into a *timeline*: a list of events, oldest first. The browser
plays the timeline back at whatever speed you like, so pausing, rewinding and
speeding up never touch the running game.

Every event is a plain dict with these keys:

    seq       its position in the timeline (0, 1, 2, ...)
    kind      what sort of event it is, one of EVENT_KINDS below
    text      one readable line for the game log
    turn      the turn number when it happened (0 before the game starts)
    player    the player it is mostly about, or None
    category  a finer label, e.g. "LAND" or "COMBAT" for Forge log lines
    state     OPTIONAL: a full snapshot of the table right after this event

Only some events carry a ``state`` (Forge sends one when the table changed).
To draw the table at event N, the browser uses the latest state at or before N.

Adding a new kind of event (for example the planned deals between bots):
  1. Add it to EVENT_KINDS with a one-line explanation.
  2. Create it with make_event("deal_offer", "2-Kuja offers 1-Zidane ...", player=...)
     and append it to the timeline (GameSession.add_event in session.py).
  3. Optionally give it a colour or banner in static/app.js (EVENT_STYLES).
     Unknown kinds still show up in the log, just without special styling.
"""

from __future__ import annotations

from typing import Any

Event = dict[str, Any]

EVENT_KINDS = {
    "game_start": "Seats and decks are known; the game is about to begin.",
    "log": "A line from Forge's game log (category = Forge's type, e.g. LAND, COMBAT).",
    "bot_choice": "A Python bot made a decision (mulligan, attack, ...).",
    "game_over": "The game ended. data has the winner and final life totals.",
    "error": "Something went wrong (Forge crashed, the game was stopped, ...).",
    # Deals between bots (showdown/deals.py):
    "deal_offer": "A bot offers a deal to another bot.",
    "deal_accept": "A deal was accepted.",
    "deal_decline": "A deal was declined.",
    "deal_kept": "A deal ran its full length without being broken.",
    "deal_break": "A bot broke a deal it had made.",
}


def make_event(kind: str, text: str, *, turn: int = 0, player: str | None = None,
               category: str | None = None, state: dict | None = None, **data: Any) -> Event:
    """Build one event dict. ``seq`` is filled in when it joins a timeline.

    The ``*`` in the signature means everything after it must be passed by
    name, e.g. ``make_event("log", "hi", turn=3)``. Extra keyword arguments
    end up in event["data"].
    """
    if kind not in EVENT_KINDS:
        raise ValueError(f"Unknown event kind {kind!r}; add it to EVENT_KINDS first")
    event: Event = {"seq": -1, "kind": kind, "text": text, "turn": turn,
                    "player": player, "category": category or kind.upper()}
    if state is not None:
        event["state"] = state
    if data:
        event["data"] = data
    return event


class TimelineBuilder:
    """Turns bridge messages into timeline events, one message at a time.

    Usage:
        builder = TimelineBuilder()
        for msg in messages_from_forge:
            for event in builder.feed(msg):
                timeline.append(event)
    """

    def __init__(self) -> None:
        self.turn = 0
        self.controllers: dict[str, str] = {}  # player name -> "python" / "forge"
        self.seen_choices: dict[int, dict] = {}  # card id -> its "chosen" dict, last time we looked

    def feed(self, msg: dict) -> list[Event]:
        kind = msg.get("type")
        # getattr finds the method called e.g. "_on_event" for type "event".
        handler = getattr(self, f"_on_{kind}", None)
        return handler(msg) if handler else []

    # --- one method per bridge message type --------------------------------

    def _on_game_start(self, msg: dict) -> list[Event]:
        seats = msg.get("seats", [])
        for s in seats:
            self.controllers[s["name"]] = s.get("controller", "?")
        names = ", ".join(s["name"] for s in seats)
        return [make_event("game_start", f"Game starts: {names}", seats=seats)]

    def _on_event(self, msg: dict) -> list[Event]:
        state = msg.get("state")
        if state:
            self.turn = state.get("turn", self.turn)
        # Newer bridges send "entries" (text + category); older ones only "log".
        entries = msg.get("entries") or [{"type": "INFORMATION", "text": t} for t in msg.get("log", [])]
        events = [make_event("log", e["text"], turn=self.turn, category=e["type"],
                             player=state.get("active_player") if state else None)
                  for e in entries]
        if state:
            if events:
                events[-1]["state"] = state  # the table as it is after these lines
            else:
                events.append(make_event("log", "", turn=self.turn, category="STATE", state=state))
            events += self._new_choices(state)
        return events

    def _new_choices(self, state: dict) -> list[Event]:
        """Log choices like "choose a color" (Thriving lands), which Forge's own log leaves out.

        Each card in the snapshot may carry a "chosen" dict. We remember what we
        saw last time and write a log line whenever a card's choice is new.
        """
        events = []
        for player in state.get("players", []):
            for card in player.get("battlefield", []):
                chosen = card.get("chosen")
                if not chosen or self.seen_choices.get(card["id"]) == chosen:
                    continue
                self.seen_choices[card["id"]] = chosen
                who = card.get("controller", player["name"])
                # e.g. {"color": "red"} -> "Red"; {"type": "Wizard"} -> "Wizard"
                what = ", ".join(str(v).title() if k == "color" else str(v) for k, v in chosen.items())
                events.append(make_event("log", f"{who} chose {what} for {card['name']}",
                                         turn=self.turn, player=who, category="CHOICE"))
        return events

    def _on_answer(self, msg: dict) -> list[Event]:
        decision, choice = msg["decision"], msg["choice"]
        text = describe_choice(decision, choice)
        if text is None:
            return []
        player = decision.get("player")
        return [make_event("bot_choice", f"{player} ({msg.get('bot')}) {text}",
                           turn=self.turn, player=player, category=decision.get("kind", "").upper())]

    def _on_deal(self, msg: dict) -> list[Event]:
        """A deal was offered, accepted, declined or broken (see showdown/deals.py)."""
        deal = msg.get("deal", {})
        player = msg.get("breaker") or (deal.get("b") if msg["event"] in ("accept", "decline") else deal.get("a"))
        return [make_event(f"deal_{msg['event']}", msg["text"], turn=msg.get("turn", self.turn),
                           player=player, category="DEAL", deal=deal)]

    def _on_game_over(self, msg: dict) -> list[Event]:
        if msg.get("draw"):
            text = f"Game over after {msg.get('turns')} turns: draw"
        else:
            text = f"Game over after {msg.get('turns')} turns: {msg.get('winner')} wins!"
        return [make_event("game_over", text, turn=self.turn, player=msg.get("winner"),
                           winner=msg.get("winner"), draw=msg.get("draw", False),
                           players=msg.get("players", []))]


def describe_choice(decision: dict, choice: Any) -> str | None:
    """A short sentence about what a bot decided, or None if not worth showing.

    Passing priority happens hundreds of times a game, so it's left out. Plays
    already show up in Forge's own log ("cast X"), so they're left out too.
    """
    kind = decision.get("kind")
    if kind == "keyword_cost":
        # Casualty, offspring, ...: "Casualty:2" -> "Casualty 2". The number
        # matters: a spell can have two (its own Casualty 3 and Anhelo's
        # Casualty 2). No answer (None) means "do what Forge's AI does", and
        # Forge pays whenever it can.
        keyword = str(decision.get("keyword", "an extra cost")).replace(":", " ")
        spell = decision.get("spell", {}).get("name", "a spell")
        return f"pays {keyword} for {spell}" if choice is None or choice else f"skips {keyword} for {spell}"
    if choice is None:
        return None  # "let Forge decide" or "pass"
    if kind == "mulligan":
        return "keeps its hand" if choice else "mulligans"
    if kind == "attack":
        if not choice:
            return "decides not to attack"
        names = {str(a["id"]): a["name"] for a in decision.get("attackers", [])}
        defenders = {d["index"]: d["name"] for d in decision.get("defenders", [])}
        # Group attackers by who they attack: {"2-Kuja": ["Bear", "Goblin"]}
        by_target: dict[str, list[str]] = {}
        for card_id, target in choice.items():
            by_target.setdefault(defenders.get(target, "?"), []).append(names.get(card_id, card_id))
        parts = [f"{', '.join(cards)} at {target}" for target, cards in by_target.items()]
        return "attacks with " + "; ".join(parts)
    if kind == "pick_card":
        picked = next((c["name"] for c in decision.get("candidates", []) if c.get("index") == choice), None)
        return f"picks {picked}" if picked else None
    return None
