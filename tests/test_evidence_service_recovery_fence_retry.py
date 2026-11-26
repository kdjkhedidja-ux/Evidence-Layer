"""Regression tests for the Recovery Fence retry (MCP "Service is unavailable").

A single dropped connection, timeout, 429 or 5xx from the external fence used
to fail the request immediately. The reader now retries exactly once, with a
fresh nonce and signature, and only when no authoritative answer arrived. A
delivered answer (closed, reconciling, unverified, wrong nonce, 4xx) is never
retried, so the fail-closed guarantee is unchanged.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from evidence_service import recovery_fence
from evidence_service.recovery_fence import (
    CloudflareRecoveryFenceReader,
    RecoveryFenceSettings,
    RecoveryFenceTransientError,
    RecoveryFenceUnavailable,
)

URL = "http://127.0.0.1:8787"


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery_fence, "RECOVERY_FENCE_RETRY_DELAY_SECONDS", 0)


def fence_payload(request: httpx.Request, state: str = "open", **overrides) -> dict:
    payload = {
        "state": state,
        "generation": 1,
        "sequence": 1,
        "state_version": 1,
        "initialized": True,
        "verified": True,
        "request_nonce": request.headers["x-recovery-nonce"],
        "checkpoint_id": "synthetic-checkpoint",
    }
    payload.update(overrides)
    return payload


def read_with(responses: list) -> tuple[object, list[httpx.Request]]:
    """Run one ``is_open`` call; each item answers one attempt.

    An item is either a callable ``request -> httpx.Response`` or an exception
    instance raised by the transport.
    """
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item(request)

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        reader = CloudflareRecoveryFenceReader(
            RecoveryFenceSettings(
                url=URL,
                key_id="reader-test",
                hmac_key=b"synthetic-reader-secret-that-is-at-least-32-bytes",
            ),
            client=client,
        )
        try:
            return await reader.is_open()
        except RecoveryFenceUnavailable as exc:
            return exc
        finally:
            await client.aclose()

    return asyncio.run(run()), seen


@pytest.mark.parametrize(
    "first_failure",
    [
        lambda request: httpx.Response(503),
        lambda request: httpx.Response(502),
        lambda request: httpx.Response(429),
        httpx.ConnectTimeout("synthetic connect timeout"),
        httpx.ReadTimeout("synthetic read timeout"),
        httpx.ConnectError("synthetic connection reset"),
    ],
    ids=["503", "502", "429", "connect-timeout", "read-timeout", "connect-error"],
)
def test_one_transient_failure_is_retried_with_a_fresh_nonce(first_failure) -> None:
    result, seen = read_with(
        [first_failure, lambda request: httpx.Response(200, json=fence_payload(request))]
    )

    assert result is True
    assert len(seen) == 2
    assert seen[0].headers["x-recovery-nonce"] != seen[1].headers["x-recovery-nonce"]
    assert seen[0].headers["x-recovery-signature"] != seen[1].headers["x-recovery-signature"]


def test_persistent_transient_failure_stops_after_two_attempts_and_fails_closed() -> None:
    result, seen = read_with(
        [lambda request: httpx.Response(503), httpx.ReadTimeout("synthetic read timeout")]
    )

    assert isinstance(result, RecoveryFenceTransientError)
    assert len(seen) == 2


@pytest.mark.parametrize("state", ["closed", "reconciling"])
def test_authoritative_non_open_answer_is_not_retried(state: str) -> None:
    result, seen = read_with(
        [lambda request: httpx.Response(200, json=fence_payload(request, state=state))]
    )

    assert result is False
    assert len(seen) == 1


@pytest.mark.parametrize(
    "answer",
    [
        lambda request: httpx.Response(401),
        lambda request: httpx.Response(403),
        lambda request: httpx.Response(200, json=fence_payload(request, verified=False)),
        lambda request: httpx.Response(200, json=fence_payload(request, request_nonce="stale")),
        lambda request: httpx.Response(200, json=fence_payload(request, state="unknown")),
        lambda request: httpx.Response(200, content=b"not json"),
    ],
    ids=["401", "403", "unverified", "nonce-mismatch", "unknown-state", "not-json"],
)
def test_delivered_but_untrusted_answer_is_not_retried(answer) -> None:
    result, seen = read_with([answer])

    assert isinstance(result, RecoveryFenceUnavailable)
    assert not isinstance(result, RecoveryFenceTransientError)
    assert len(seen) == 1
