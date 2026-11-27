"""Per-user usage caps (retrieval calls and the daily JEV budget).

The atomic SQL counter is covered by supabase/tests/user_usage_caps.test.sql.
These tests cover the service wiring with an in-memory counter that follows the
same rules (fixed UTC minute/day windows, refused calls are not counted,
counters are per user), plus the HTTP repository and the settings.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from account_lifecycle_test_support import (
    AvailableAccountLifecycleReader,
    OpenRecoveryFenceReader,
)
from evidence_service.app import create_app
from evidence_service.auth import AuthenticationRejected, Principal
from evidence_service.config import SupabaseSettings
from evidence_service.evidence_repository import EvidenceRecord
from evidence_service.personal_api_key_repository import (
    PersonalApiKeyPage,
    gateway_signature,
)
from evidence_service.tenant_context import WorkspaceMembership
from evidence_service.usage_caps import (
    DEFAULT_JEV_PER_DAY,
    DEFAULT_RETRIEVAL_PER_DAY,
    DEFAULT_RETRIEVAL_PER_MINUTE,
    SupabaseUsageCapsRepository,
    UsageCapsAccessDenied,
    UsageCapsSettings,
    UsageCapsUnavailable,
    UsageDecision,
    usage_gateway_signature,
)

WORKSPACE = UUID("22222222-2222-4222-8222-222222222222")
USER_A = UUID("a0000000-0000-4000-8000-00000000000a")
USER_B = UUID("b0000000-0000-4000-8000-00000000000b")
KEY_A = UUID("66666666-6666-4666-8666-66666666666a")
KEY_B = UUID("66666666-6666-4666-8666-66666666666b")
TOKEN_A = f"evl_pk_{KEY_A}.{'A' * 43}"
TOKEN_B = f"evl_pk_{KEY_B}.{'B' * 43}"
SESSION_A = "verified.session.a"
SESSION_B = "verified.session.b"
ISSUER = "https://project.supabase.co/auth/v1"
RETRIEVE = f"/v1/personal-api-keys/workspaces/{WORKSPACE}/retrieve"
GATEWAY_SECRET = "ab" * 32


def supabase_settings(**overrides) -> SupabaseSettings:
    values = dict(
        supabase_url="https://project.supabase.co",
        publishable_key="sb_publishable_test",
        jwt_issuer=ISSUER,
        jwt_audience="authenticated",
        jwt_algorithms=("ES256",),
    )
    values.update(overrides)
    return SupabaseSettings(**values)


def records() -> list[EvidenceRecord]:
    created = datetime(2026, 11, 12, tzinfo=UTC)
    return [
        EvidenceRecord(
            id=UUID("11111111-1111-4111-8111-111111111111"),
            workspace_id=WORKSPACE,
            content="lexical retrieval evidence",
            source_label="first.md",
            created_at=created,
            updated_at=created,
        ),
        EvidenceRecord(
            id=UUID("77777777-7777-4777-8777-777777777777"),
            workspace_id=WORKSPACE,
            content="lexical retrieval secondary evidence",
            source_label="second.md",
            created_at=created,
            updated_at=created,
        ),
    ]


class KeyRepository:
    """Signed API-key reads; counts how many candidate reads happened."""

    def __init__(self) -> None:
        self.evidence_reads = 0

    async def read_evidence(self, **kwargs):
        self.evidence_reads += 1
        return PersonalApiKeyPage(items=records(), next_cursor=None)

    async def read_relationships(self, **kwargs):
        return PersonalApiKeyPage(items=[], next_cursor=None)


class FakeJevScorer:
    def __init__(self) -> None:
        self.calls = 0

    def score_candidates(self, query, candidates, *, transfer_permit):
        self.calls += 1
        return tuple(0.9 if "secondary" in item.text else 0.1 for item in candidates)


class FakeUsageCounter:
    """Same rules as private.consume_usage, with a controllable clock."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 27, 12, 0, 30, tzinfo=UTC)
        self.counters: dict[tuple[UUID, str, str], tuple[datetime, int]] = {}
        self.calls: list[tuple[str, UUID, str]] = []
        self.unavailable_kinds: set[str] = set()
        self.denied = False
        self.key_owners = {KEY_A: USER_A, KEY_B: USER_B}
        self.session_owners = {SESSION_A: USER_A, SESSION_B: USER_B}

    def _consume(self, user: UUID, kind: str, per_minute: int, per_day: int) -> UsageDecision:
        if self.denied:
            raise UsageCapsAccessDenied("refused")
        if kind in self.unavailable_kinds:
            raise UsageCapsUnavailable("down")
        minute_start = self.now.replace(second=0, microsecond=0)
        day_start = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        windows = {"minute": minute_start, "day": day_start}
        used = {}
        for window, start in windows.items():
            stored_start, stored_used = self.counters.get((user, kind, window), (start, 0))
            used[window] = 0 if stored_start < start else stored_used
        if per_minute and used["minute"] >= per_minute:
            retry = int((minute_start + timedelta(minutes=1) - self.now).total_seconds())
            return UsageDecision(False, "minute", max(1, retry))
        if per_day and used["day"] >= per_day:
            retry = int((day_start + timedelta(days=1) - self.now).total_seconds())
            return UsageDecision(False, "day", max(1, retry))
        for window, start in windows.items():
            self.counters[(user, kind, window)] = (start, used[window] + 1)
        return UsageDecision(True)

    async def consume_for_user(self, access_token, kind, *, per_minute, per_day):
        user = self.session_owners[access_token]
        self.calls.append(("session", user, kind))
        return self._consume(user, kind, per_minute, per_day)

    async def consume_for_api_key(self, *, key_id, key_hash, workspace_id, kind, per_minute, per_day):
        user = self.key_owners[key_id]
        self.calls.append(("api_key", user, kind))
        return self._consume(user, kind, per_minute, per_day)


def caps(**overrides) -> UsageCapsSettings:
    values = dict(enabled=True, retrieval_per_minute=30, retrieval_per_day=500, jev_per_day=200)
    values.update(overrides)
    return UsageCapsSettings(**values)


def api_client(
    counter: FakeUsageCounter,
    *,
    settings: UsageCapsSettings,
    scorer: FakeJevScorer | None = None,
    repository: KeyRepository | None = None,
    remote_mcp_enabled: bool = False,
) -> TestClient:
    app = create_app(
        supabase_settings(),
        recovery_fence_reader=OpenRecoveryFenceReader(),
        personal_api_key_repository=repository or KeyRepository(),
        retrieval_enabled=True,
        jev_enabled=True,
        jev_scorer=scorer or FakeJevScorer(),
        remote_mcp_enabled=remote_mcp_enabled,
        usage_caps_settings=settings,
        usage_caps_repository=counter,
    )
    return TestClient(app)


def retrieve(client: TestClient, token: str = TOKEN_A, **body):
    payload = {"query": "lexical retrieval", "top_k": 1, "max_tokens": 100, "max_chars": 1000}
    payload.update(body)
    return client.post(RETRIEVE, headers={"Authorization": f"Bearer {token}"}, json=payload)


# --- API-key path ---------------------------------------------------------


def test_caps_are_inert_until_enabled() -> None:
    counter = FakeUsageCounter()
    scorer = FakeJevScorer()
    client = api_client(counter, settings=UsageCapsSettings(), scorer=scorer)

    for _ in range(3):
        assert retrieve(client).json()["scoring_provider"] == "jev"
    assert counter.calls == []
    assert scorer.calls == 3


def test_minute_cap_returns_documented_429_before_reading_candidates() -> None:
    counter = FakeUsageCounter()
    repository = KeyRepository()
    client = api_client(counter, settings=caps(retrieval_per_minute=2), repository=repository)

    assert retrieve(client).status_code == 200
    assert retrieve(client).status_code == 200
    refused = retrieve(client)

    assert refused.status_code == 429
    assert refused.json() == {
        "detail": {
            "code": "user_rate_limited",
            "window": "minute",
            "retry_after_seconds": 30,
            "message": "Per-user retrieval limit reached for this minute.",
        }
    }
    assert refused.headers["Retry-After"] == "30"
    assert repository.evidence_reads == 2


def test_refused_calls_are_not_counted_and_a_new_minute_resets_the_cap() -> None:
    counter = FakeUsageCounter()
    client = api_client(counter, settings=caps(retrieval_per_minute=1))

    assert retrieve(client).status_code == 200
    assert retrieve(client).status_code == 429
    assert retrieve(client).status_code == 429
    assert counter.counters[(USER_A, "retrieval", "minute")][1] == 1

    counter.now += timedelta(seconds=30)
    assert retrieve(client).status_code == 200


def test_daily_cap_reports_the_day_window_and_resets_at_utc_midnight() -> None:
    counter = FakeUsageCounter()
    client = api_client(counter, settings=caps(retrieval_per_minute=0, retrieval_per_day=1))

    assert retrieve(client).status_code == 200
    refused = retrieve(client)
    assert refused.status_code == 429
    assert refused.json()["detail"]["window"] == "day"
    assert refused.json()["detail"]["retry_after_seconds"] == 12 * 3600 - 30

    counter.now += timedelta(days=1)
    assert retrieve(client).status_code == 200


def test_exhausted_jev_budget_keeps_the_whole_lexical_result() -> None:
    counter = FakeUsageCounter()
    scorer = FakeJevScorer()
    client = api_client(counter, settings=caps(jev_per_day=1), scorer=scorer)

    first = retrieve(client).json()
    assert first["scoring_provider"] == "jev"
    assert first["evidence"][0]["source_label"] == "second.md"

    over_budget = retrieve(client)
    lexical_only = retrieve(client, use_jev=False).json()

    assert over_budget.status_code == 200
    body = over_budget.json()
    assert body["scoring_provider"] == "lexical_stopword_filtered"
    assert body["scoring_fallback_reason"] == "jev_budget_exceeded"
    assert scorer.calls == 1
    # Exactly the lexical ranking: same records, same scores, nothing from JEV.
    assert body["evidence"] == lexical_only["evidence"]


def test_unverifiable_jev_budget_falls_back_to_lexical_without_an_error() -> None:
    counter = FakeUsageCounter()
    counter.unavailable_kinds.add("jev")
    scorer = FakeJevScorer()
    client = api_client(counter, settings=caps(), scorer=scorer)

    response = retrieve(client)

    assert response.status_code == 200
    assert response.json()["scoring_provider"] == "lexical_stopword_filtered"
    assert response.json()["scoring_fallback_reason"] == "jev_budget_unavailable"
    assert scorer.calls == 0


def test_jev_budget_is_only_spent_when_jev_would_run() -> None:
    counter = FakeUsageCounter()
    client = api_client(counter, settings=caps())

    retrieve(client, use_jev=False)
    retrieve(client, query="nothing matches this query at all")

    assert ("api_key", USER_A, "jev") not in counter.calls


def test_one_user_over_the_cap_does_not_affect_another() -> None:
    counter = FakeUsageCounter()
    scorer = FakeJevScorer()
    client = api_client(counter, settings=caps(retrieval_per_minute=1, jev_per_day=1), scorer=scorer)

    assert retrieve(client, TOKEN_A).json()["scoring_provider"] == "jev"
    assert retrieve(client, TOKEN_A).status_code == 429

    other = retrieve(client, TOKEN_B)
    assert other.status_code == 200
    assert other.json()["scoring_provider"] == "jev"
    assert scorer.calls == 2


def test_unavailable_usage_counter_serves_lexical_within_the_fallback_limit() -> None:
    """Owner decision 2026-11-27: no longer fail closed (503). Serve lexical
    results, never JEV, bounded per minute in-process. Details in
    tests/test_usage_counter_fallback.py."""
    counter = FakeUsageCounter()
    counter.unavailable_kinds.add("retrieval")
    repository = KeyRepository()
    scorer = FakeJevScorer()
    client = api_client(counter, settings=caps(), repository=repository, scorer=scorer)

    response = retrieve(client, use_jev=True)

    assert response.status_code == 200
    body = response.json()
    assert body["scoring_provider"] == "lexical_stopword_filtered"
    assert body["scoring_fallback_reason"] == "usage_counter_unavailable"
    assert body["selected_count"] >= 1
    assert scorer.calls == 0
    assert repository.evidence_reads == 1


def test_refused_usage_proof_maps_to_an_invalid_key() -> None:
    counter = FakeUsageCounter()
    counter.denied = True
    client = api_client(counter, settings=caps())

    response = retrieve(client)

    assert response.status_code == 401
    assert response.json() == {"detail": "Personal API key is invalid or revoked"}


def test_remote_mcp_reports_the_per_user_cap() -> None:
    counter = FakeUsageCounter()
    with api_client(counter, settings=caps(retrieval_per_minute=1), remote_mcp_enabled=True) as client:
        headers = {
            "Authorization": f"Bearer {TOKEN_A}",
            "Accept": "application/json, text/event-stream",
        }
        path = f"/mcp/workspaces/{WORKSPACE}/"
        client.post(path, headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}},
        })

        def call(request_id: int) -> dict:
            response = client.post(path, headers=headers, json={
                "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                "params": {"name": "retrieve_evidence", "arguments": {"query": "lexical retrieval"}},
            })
            assert response.status_code == 200
            return json.loads(response.json()["result"]["content"][0]["text"])

        assert call(2)["status"] == "ok"
        refused = call(3)

    assert refused["error"]["code"] == "user_rate_limited"
    assert refused["error"]["window"] == "minute"
    assert refused["error"]["retry_after_seconds"] == 30


# --- Signed-in web path (task-context) -------------------------------------


class SessionVerifier:
    async def verify(self, token: str) -> Principal:
        subjects = {SESSION_A: USER_A, SESSION_B: USER_B}
        if token not in subjects:
            raise AuthenticationRejected
        return Principal(ISSUER, subjects[token], "aal2")


class Directory:
    async def list_memberships(self, principal, access_token):
        raise AssertionError("not used")

    async def get_membership(self, principal, access_token, workspace_id):
        if workspace_id != WORKSPACE or principal.subject not in {USER_A, USER_B}:
            return None
        return WorkspaceMembership(workspace_id, "Workspace", "owner")


class SessionEvidence:
    async def list_records(self, access_token, workspace_id):
        return records() if workspace_id == WORKSPACE else []


def test_task_context_counts_per_signed_in_user_and_falls_back_on_jev_budget() -> None:
    counter = FakeUsageCounter()
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
        usage_caps_settings=caps(retrieval_per_minute=2, jev_per_day=1),
        usage_caps_repository=counter,
    )

    async def run() -> list[httpx.Response]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            async def post(session: str) -> httpx.Response:
                return await client.post(
                    f"/v1/workspaces/{WORKSPACE}/task-context",
                    headers={"Authorization": f"Bearer {session}"},
                    json={
                        "task": "lexical retrieval",
                        "expected_outcome": "evidence",
                        "top_k": 1,
                    },
                )

            return [await post(SESSION_A), await post(SESSION_A), await post(SESSION_A), await post(SESSION_B)]

    first, second, third, other = asyncio.run(run())

    assert first.json()["retrieval"]["scoring_provider"] == "jev"
    assert second.json()["retrieval"]["scoring_fallback_reason"] == "jev_budget_exceeded"
    assert third.status_code == 429
    assert third.json()["detail"]["code"] == "user_rate_limited"
    assert other.status_code == 200
    assert other.json()["retrieval"]["scoring_provider"] == "jev"
    assert ("session", USER_A, "retrieval") in counter.calls


# --- Settings ---------------------------------------------------------------


def test_settings_default_to_disabled_with_conservative_caps() -> None:
    settings = UsageCapsSettings.from_environment({})

    assert settings.enabled is False
    assert (DEFAULT_RETRIEVAL_PER_MINUTE, DEFAULT_RETRIEVAL_PER_DAY, DEFAULT_JEV_PER_DAY) == (30, 500, 200)
    assert settings.limits("retrieval") == (30, 500)
    assert settings.limits("jev") == (0, 200)


def test_settings_read_overrides_and_reject_invalid_values() -> None:
    settings = UsageCapsSettings.from_environment({
        "EVIDENCE_USAGE_CAPS_ENABLED": "true",
        "EVIDENCE_USER_RETRIEVAL_PER_MINUTE": "10",
        "EVIDENCE_USER_RETRIEVAL_PER_DAY": "-5",
        "EVIDENCE_USER_JEV_PER_DAY": "lots",
    })

    assert settings.enabled is True
    assert settings.retrieval_per_minute == 10
    assert settings.retrieval_per_day == 500
    assert settings.jev_per_day == 200
    assert UsageCapsSettings.from_environment({"EVIDENCE_USAGE_CAPS_ENABLED": "yes"}).enabled is False
    assert UsageCapsSettings.from_environment({"EVIDENCE_USER_JEV_PER_DAY": "0"}).jev_per_day == 0


# --- HTTP repository ----------------------------------------------------------


def run_repository(handler, call):
    repository = SupabaseUsageCapsRepository(
        supabase_settings(personal_api_key_gateway_secret=GATEWAY_SECRET),
        transport=httpx.MockTransport(handler),
    )
    return asyncio.run(call(repository))


def test_api_key_usage_request_binds_the_caps_into_the_gateway_proof() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"allowed": True, "window": None, "retry_after_seconds": 0})

    key_hash = "1" * 64
    decision = run_repository(handler, lambda repository: repository.consume_for_api_key(
        key_id=KEY_A, key_hash=key_hash, workspace_id=WORKSPACE, kind="jev", per_minute=0, per_day=200,
    ))

    assert decision == UsageDecision(True)
    request = seen[0]
    assert request.url.path == "/rest/v1/rpc/consume_personal_api_key_usage"
    assert "authorization" not in request.headers
    assert json.loads(request.content) == {
        "p_key_id": str(KEY_A), "p_workspace_id": str(WORKSPACE),
        "p_kind": "jev", "p_per_minute": 0, "p_per_day": 200,
    }
    timestamp = int(request.headers["x-evidence-api-key-gateway-timestamp"])
    nonce = UUID(request.headers["x-evidence-api-key-gateway-nonce"])
    message = "\n".join(("v1-usage", "jev", str(KEY_A), str(WORKSPACE), key_hash,
                         str(timestamp), str(nonce), "0", "200"))
    expected = hmac.new(bytes.fromhex(GATEWAY_SECRET), message.encode(), hashlib.sha256).hexdigest()
    assert request.headers["x-evidence-api-key-gateway-proof"] == expected
    # A different cap yields a different proof; a read proof never matches.
    assert usage_gateway_signature(
        GATEWAY_SECRET, kind="jev", key_id=KEY_A, workspace_id=WORKSPACE, key_hash=key_hash,
        timestamp=timestamp, nonce=nonce, per_minute=0, per_day=10_000,
    ) != expected
    assert gateway_signature(
        GATEWAY_SECRET, resource="evidence", key_id=KEY_A, workspace_id=WORKSPACE, key_hash=key_hash,
        timestamp=timestamp, nonce=nonce, limit=1, cursor_created_at=None, cursor_id=None,
    ) != expected


def test_session_usage_request_uses_the_callers_own_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"allowed": False, "window": "minute", "retry_after_seconds": 12})

    decision = run_repository(handler, lambda repository: repository.consume_for_user(
        SESSION_A, "retrieval", per_minute=30, per_day=500,
    ))

    assert decision == UsageDecision(False, "minute", 12)
    assert seen[0].url.path == "/rest/v1/rpc/consume_user_usage"
    assert seen[0].headers["authorization"] == f"Bearer {SESSION_A}"


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(401), UsageCapsAccessDenied),
        (httpx.Response(403), UsageCapsAccessDenied),
        (httpx.Response(500), UsageCapsUnavailable),
        (httpx.Response(404), UsageCapsUnavailable),
        (httpx.Response(200, content=b"not json"), UsageCapsUnavailable),
        (httpx.Response(200, json={"allowed": "yes"}), UsageCapsUnavailable),
        (httpx.Response(200, json={"allowed": False, "window": "week", "retry_after_seconds": 5}), UsageCapsUnavailable),
    ],
    ids=["401", "403", "500", "missing-function", "not-json", "bad-allowed", "bad-window"],
)
def test_repository_fails_closed_on_untrusted_answers(response, error) -> None:
    with pytest.raises(error):
        run_repository(lambda request: response, lambda repository: repository.consume_for_user(
            SESSION_A, "retrieval", per_minute=30, per_day=500,
        ))


def test_api_key_usage_without_gateway_secret_is_unavailable() -> None:
    repository = SupabaseUsageCapsRepository(supabase_settings())
    with pytest.raises(UsageCapsUnavailable):
        asyncio.run(repository.consume_for_api_key(
            key_id=KEY_A, key_hash="1" * 64, workspace_id=WORKSPACE,
            kind="retrieval", per_minute=30, per_day=500,
        ))
