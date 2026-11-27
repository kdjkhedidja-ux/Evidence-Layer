"""Usage-counter calls overlap the work they gate (owner conditions, 2026-11-27):

* the retrieval count runs concurrently with the candidate read, and a request
  the counter refuses (429) returns no evidence even if the read completed;
* the JEV budget call runs while the lexical ranking is computed, but always
  completes before any JEV call, and a refused budget means no JEV call.
"""

from __future__ import annotations

import asyncio
import time

from test_evidence_service_usage_caps import (
    USER_A,
    FakeJevScorer,
    FakeUsageCounter,
    KeyRepository,
    api_client,
    caps,
    retrieve,
)

DELAY = 0.3


class SlowCounter(FakeUsageCounter):
    def __init__(self, delays: dict[str, float]) -> None:
        super().__init__()
        self.delays = delays
        self.finished: dict[str, float] = {}

    async def consume_for_api_key(self, *, key_id, key_hash, workspace_id, kind, per_minute, per_day):
        await asyncio.sleep(self.delays.get(kind, 0))
        decision = await super().consume_for_api_key(
            key_id=key_id, key_hash=key_hash, workspace_id=workspace_id,
            kind=kind, per_minute=per_minute, per_day=per_day,
        )
        self.finished[kind] = time.perf_counter()
        return decision


class SlowRepository(KeyRepository):
    def __init__(self, delay: float) -> None:
        super().__init__()
        self.delay = delay
        self.completed = 0

    async def read_evidence(self, **kwargs):
        await asyncio.sleep(self.delay)
        page = await super().read_evidence(**kwargs)
        self.completed += 1
        return page


class TimedScorer(FakeJevScorer):
    def __init__(self) -> None:
        super().__init__()
        self.called_at: float | None = None

    def score_candidates(self, query, candidates, *, transfer_permit):
        self.called_at = time.perf_counter()
        return super().score_candidates(query, candidates, transfer_permit=transfer_permit)


def test_the_read_overlaps_the_retrieval_count() -> None:
    counter = SlowCounter({"retrieval": DELAY})
    repository = SlowRepository(DELAY)
    client = api_client(counter, settings=caps(), repository=repository)

    started = time.perf_counter()
    response = retrieve(client, use_jev=False)
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert elapsed < 2 * DELAY * 0.9, f"read and count ran one after the other ({elapsed:.2f}s)"


def test_a_refused_request_returns_no_evidence_even_if_the_read_completed() -> None:
    counter = SlowCounter({"retrieval": DELAY})  # the read (no delay) finishes first
    repository = SlowRepository(0)
    client = api_client(counter, settings=caps(retrieval_per_minute=1), repository=repository)

    assert retrieve(client, use_jev=False).status_code == 200
    refused = retrieve(client, use_jev=False)

    assert refused.status_code == 429
    body = refused.json()
    assert set(body) == {"detail"} and body["detail"]["code"] == "user_rate_limited"
    assert "evidence" not in refused.text
    assert repository.completed == 2  # the concurrent read did complete; its result was discarded


def test_the_jev_budget_completes_before_the_jev_call() -> None:
    counter = SlowCounter({"jev": DELAY})
    scorer = TimedScorer()
    client = api_client(counter, settings=caps(), scorer=scorer, repository=KeyRepository())

    response = retrieve(client, use_jev=True)

    assert response.status_code == 200
    assert response.json()["scoring_provider"] == "jev"
    assert scorer.called_at is not None
    assert counter.finished["jev"] <= scorer.called_at


def test_a_refused_jev_budget_means_no_jev_call() -> None:
    counter = SlowCounter({"jev": DELAY})
    scorer = TimedScorer()
    client = api_client(counter, settings=caps(jev_per_day=1), scorer=scorer, repository=KeyRepository())

    assert retrieve(client, use_jev=True).json()["scoring_provider"] == "jev"
    second = retrieve(client, use_jev=True).json()

    assert second["scoring_provider"] == "lexical_stopword_filtered"
    assert second["scoring_fallback_reason"] == "jev_budget_exceeded"
    assert scorer.calls == 1
    assert counter.calls.count(("api_key", USER_A, "jev")) == 2


def test_no_jev_budget_is_spent_when_nothing_can_match() -> None:
    counter = SlowCounter({})
    client = api_client(counter, settings=caps(), repository=KeyRepository())

    retrieve(client, query="zzzz qqqq xxxx", use_jev=True)

    assert ("api_key", USER_A, "jev") not in counter.calls
