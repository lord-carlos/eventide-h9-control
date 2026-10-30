"""Connection lifecycle primitives for the H9 MIDI worker.

Deliberately Qt-free: the state machine and the retry policy are plain
Python so they can be unit-tested without an event loop. The worker
(`h9control.app.ui.qt_worker`) owns the transport and timers; this module
only names the states and decides how long to wait before the next attempt.
"""

from __future__ import annotations

from enum import Enum

#: Reconnect delays in seconds. No jitter: a single local device does not
#: benefit from it and it would make the tests nondeterministic.
DEFAULT_RETRY_DELAYS: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0, 30.0)


class ConnectionState(Enum):
    """Lifecycle state of the worker's MIDI connection."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


class RetryBackoff:
    """Capped retry delays: 1, 2, 5, 10, 30 seconds, then 30 s forever.

    Pure and cheap: `next_delay()` advances the counter, `reset()` starts
    over after a successful connection.
    """

    delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS

    def __init__(self, delays: tuple[float, ...] | None = None) -> None:
        if delays is not None:
            if not delays:
                raise ValueError("RetryBackoff needs at least one delay")
            self.delays = tuple(float(delay) for delay in delays)
        self.attempt = 0

    def delay_for_attempt(self, attempt: int) -> float:
        """Return the delay for `attempt`, capped at the last entry."""
        index = max(0, min(attempt, len(self.delays) - 1))
        return self.delays[index]

    def next_delay(self) -> float:
        """Return the next delay and advance the attempt counter."""
        delay = self.delay_for_attempt(self.attempt)
        self.attempt += 1
        return delay

    def reset(self) -> None:
        """Start the sequence over (called after a successful connect)."""
        self.attempt = 0
