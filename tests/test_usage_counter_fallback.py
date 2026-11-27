"""Bounded fail-open when the usage counter cannot answer (owner decision
2026-11-27): lexical results only, an in-process per-minute limit, and a
content-free log line per fallback."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from evidence_service.app import create_app
from evidence_service.usage_fallback import FallbackRateLimiter
from test_evidence_service_usage_caps import (
    SESSION_A,
    TOKEN_A,
    WORKSPACE,
    AvailableAccountLifecycleReader,
    Directory,
    FakeJevScorer,
    FakeUsageCounter,
    KeyRepository,
    OpenRecoveryFenceReader,
    SessionEvidence,
    SessionVerifier,
    api_client,
    caps,
    retrieve,
    supabase_settings,
)

SECRET_QUERY = "lexical retrieval confidential"


def fallback_lines(output: str) -> list[dict]:
    lines = []
    for raw in output.splitlines():
        try:
            payload = json.loads(raw)
        except ValueError:
            continue
        if payload.get("event") == "usage_counter_unavailable":
            lines.append(payload)
    return lines


def test_api_serves_lexical_never_calls_jev_and_logs_without_content(capsys: pytest.CaptureFixture[str]) -> None:
    counter = FakeUsageCounter()
    counter.unavailable_kinds.add("retrieval")
    scorer = FakeJevScorer()
    client = api_client(counter, settings=caps(), scorer=scorer)

    response = retrieve(client, query=SECRET_QUERY, use_jev=True)

    assert response.status_code == 200
    assert response.json()["scoring_provider"] == "lexical_stopword_filtered"
    assert response.json()["scoring_fallback_reason"] == "usage_counter_unavailable"
    assert scorer.calls == 0
    output = capsys.readouterr().out
    assert fallback_lines(output) == [{
        "event": "usage_counter_unavailable", "surface": "http", "allowed": True,
        "cause": ["UsageCapsUnavailable"], "upstream_status": None,
    }]
    assert SECRET_QUERY not in "".join(line for line in output.splitlines() if "usage_counter_unavailable" in line)
    assert str(WORKSPACE) not in json.dumps(fallback_lines(output))


def test_the_fallback_is_bounded_per_minute_and_refuses_like_a_minute_cap() -> None:
    counter = FakeUsageCounter()
    counter.unavailable_kinds.add("retrieval")
    repository = KeyRepository()
    client = api_client(counter, settings=caps(retrieval_per_minute=2), repository=repository)

    statuses = [retrieve(client).status_code for _ in range(3)]
    refused = retrieve(client)

    assert statuses == [200, 200, 429]
    assert refused.status_code == 429
    assert refused.json()["detail"]["code"] == "user_rate_limited"
    assert refused.json()["detail"]["window"] == "minute"
    assert int(refused.headers["retry-after"]) >= 1
    assert repository.evidence_reads == 2  # a refused call reads nothing


def test_a_counter_that_answers_still_enforces_its_caps() -> None:
    counter = FakeUsageCounter()
    client = api_client(counter, settings=caps(retrieval_per_minute=1))

    assert retrieve(client).status_code == 200
    assert retrieve(client).status_code == 429


def test_remote_mcp_serves_lexical_when_the_counter_is_unavailable() -> None:
    counter = FakeUsageCounter()
    counter.unavailable_kinds.add("retrieval")
    scorer = FakeJevScorer()
    with api_client(counter, settings=caps(), scorer=scorer, remote_mcp_enabled=True) as client:
        headers = {"Authorization": f"Bearer {TOKEN_A}", "Accept": "application/json, text/event-stream"}
        path = f"/mcp/workspaces/{WORKSPACE}/"
        client.post(path, headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
        })
        called = client.post(path, headers=headers, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "retrieve_evidence", "arguments": {"query": "lexical retrieval"}},
        })

    payload = json.loads(called.json()["result"]["content"][0]["text"])
    assert payload["status"] == "ok"
    assert payload["scoring_fallback_reason"] == "usage_counter_unavailable"
    assert scorer.calls == 0


def test_task_context_serves_lexical_when_the_counter_is_unavailable() -> None:
    counter = FakeUsageCounter()
    counter.unavailable_kinds.add("retrieval")
    scorer = FakeJevScorer()
    app = create_app(
        supabase_settings(),
        token_verifier=SessionVerifier(),
        account_lifecycle_reader=AvailableAccountLifecycleReader(),
        recovery_fence_reader=OpenRecoveryFenceReader(),
        workspace_directory=Directory(),
        evidence_repository=SessionEvidence(),
        retrieval_enabled=True,
        jev_enabled=True,
        jev_scorer=scorer,
        usage_caps_settings=caps(),
        usage_caps_repository=counter,
    )

    async def run() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(
                f"/v1/workspaces/{WORKSPACE}/task-context",
                headers={"Authorization": f"Bearer {SESSION_A}"},
                json={"task": "lexical retrieval", "expected_outcome": "evidence", "top_k": 1},
            )

    response = asyncio.run(run())

    assert response.status_code == 200
    assert response.json()["retrieval"]["scoring_fallback_reason"] == "usage_counter_unavailable"
    assert scorer.calls == 0


def test_limiter_window_and_uncapped_setting() -> None:
    now = [0.0]
    limiter = FallbackRateLimiter(clock=lambda: now[0])

    assert [limiter.allow("k", 2)[0] for _ in range(3)] == [True, True, False]
    assert limiter.allow("other", 2)[0] is True        # keys are independent
    now[0] = 61.0
    assert limiter.allow("k", 2) == (True, 0)           # the minute has passed
    assert all(limiter.allow("free", 0)[0] for _ in range(100))  # 0 = uncapped
