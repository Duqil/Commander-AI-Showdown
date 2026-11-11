"""Talking to Forge: one JSON object per line over a TCP socket.

This file is the Python half of BridgeConnection.java. Read both side by side
to see the whole protocol.

Python concepts used here:
  * ``socket``  - the standard library's network connections.
  * ``json``    - turning Python dicts into text and back.
  * A *generator* (``messages()``) - a function with ``yield`` that hands out
    messages one at a time, so the caller can write ``for msg in conn.messages():``.
"""

from __future__ import annotations

import json
import socket
from typing import Any, Iterator


class BridgeConnection:
    """One open connection to a running Forge game."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        # makefile() wraps the socket in a file-like object, so we can read
        # it line by line just like reading a text file.
        self._reader = sock.makefile("r", encoding="utf-8")
        self._writer = sock.makefile("w", encoding="utf-8")

    def messages(self) -> Iterator[dict[str, Any]]:
        """Yield every message Forge sends, until it closes the connection."""
        for line in self._reader:
            line = line.strip()
            if line:  # skip blank lines
                yield json.loads(line)

    def answer(self, decision_id: int, choice: Any) -> None:
        """Reply to a decision. ``choice=None`` means "let Forge's AI decide"."""
        self._writer.write(json.dumps({"id": decision_id, "choice": choice}) + "\n")
        self._writer.flush()  # send now, don't wait for the buffer to fill

    def send(self, message: dict[str, Any]) -> None:
        """Send Forge a message that isn't an answer (e.g. "next_game" or "quit")."""
        self._writer.write(json.dumps(message) + "\n")
        self._writer.flush()

    def settimeout(self, seconds: float) -> None:
        self._sock.settimeout(seconds)

    def close(self) -> None:
        for f in (self._reader, self._writer, self._sock):
            try:
                f.close()
            except OSError:
                pass


def listen() -> socket.socket:
    """Open a server socket on a free local port. Forge will connect to it."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))  # port 0 = "any free port, you pick"
    server.listen(1)
    return server
