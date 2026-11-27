"""Bounded fail-open for the per-user usage counter.

When the durable counter (``private.consume_usage``) cannot answer, a
retrieval is served with lexical scoring only (JEV never runs without a
verified budget), limited in this process by the same per-minute cap. A full
database outage still fails, because the evidence reads fail too.

The limiter is per process and per minute only: it bounds an outage, it does
not replace the durable minute/day counters.
"""

from __future__ import annotations

import json
import time
from collections import deque
from threading import Lock

from evidence_service.error_cause import describe_cause

FALLBACK_REASON = "usage_counter_unavailable"
_WINDOW_SECONDS = 60.0
_MAX_KEYS = 10_000


class CounterUnavailableForJev(Exception):
    """Raised by the JEV budget hook while the usage counter is unavailable."""


async def refuse_jev_budget() -> bool:
    raise CounterUnavailableForJev


class FallbackRateLimiter:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._events: dict[str, deque[float]] = {}
        self._lock = Lock()

    def allow(self, key: str, per_minute: int) -> tuple[bool, int]:
        """(allowed, retry_after_seconds). ``per_minute == 0`` means uncapped."""
        now = self._clock()
        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and now - events[0] >= _WINDOW_SECONDS:
                events.popleft()
            if per_minute and len(events) >= per_minute:
                return False, max(1, int(_WINDOW_SECONDS - (now - events[0])) + 1)
            events.append(now)
            if len(self._events) > _MAX_KEYS:
                for stale in [k for k, v in self._events.items() if not v or now - v[-1] >= _WINDOW_SECONDS]:
                    del self._events[stale]
            return True, 0


def log_counter_fallback(*, surface: str, allowed: bool, exc: BaseException | None) -> None:
    """Content-free: surface, decision and exception class names only."""
    print(json.dumps({
        "event": FALLBACK_REASON,
        "surface": surface,
        "allowed": allowed,
        **describe_cause(exc),
    }, sort_keys=True), flush=True)
