from __future__ import annotations

from uuid import UUID


class AvailableAccountLifecycleReader:
    """Explicit test dependency; never used by the production app factory."""

    def __init__(self) -> None:
        self.states: dict[UUID, str | None] = {}
        self.calls: list[tuple[UUID, str]] = []
        self.error: Exception | None = None

    async def read_state(self, subject_id: UUID, access_token: str) -> str | None:
        self.calls.append((subject_id, access_token))
        if self.error is not None:
            raise self.error
        return self.states.get(subject_id, "available")


class OpenRecoveryFenceReader:
    """Synthetic open fence for tests; never used by the production app."""

    def __init__(self, *, result: bool | None = True, error: Exception | None = None, events=None) -> None:
        self.result = result
        self.error = error
        self.events = events
        self.calls = 0

    async def is_open(self) -> bool | None:
        self.calls += 1
        if self.events is not None:
            self.events.append("recovery")
        if self.error is not None:
            raise self.error
        return self.result
