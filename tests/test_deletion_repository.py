from __future__ import annotations

import json
import asyncio
from uuid import UUID

import httpx
from evidence_service.config import SupabaseSettings
from evidence_service.deletion_repository import SupabaseDeletionRepository


PRINCIPAL = UUID("71000000-0000-4000-8000-000000000001")
REQUEST = UUID("72000000-0000-4000-8000-000000000001")


def settings() -> SupabaseSettings:
    return SupabaseSettings(
        supabase_url="https://project-ref.supabase.co",
        publishable_key="sb_publishable_synthetic_test_key",
        jwt_issuer="https://project-ref.supabase.co/auth/v1",
        jwt_audience="authenticated",
        jwt_algorithms=("RS256",),
    )


def row(status: str = "approval_pending") -> dict[str, str | None]:
    return {
        "request_id": str(REQUEST),
        "requester_user_id": str(PRINCIPAL),
        "scope_type": "account",
        "status": status,
        "created_at": "2026-11-05T04:00:00+00:00",
        "accepted_at": None if status == "approval_pending" else "2026-11-05T04:01:00+00:00",
    }


def test_create_conflict_replays_existing_request() -> None:
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(409, request=request)
        return httpx.Response(200, json=[row()], request=request)

    repository = SupabaseDeletionRepository(settings(), transport=httpx.MockTransport(handler))
    request = asyncio.run(
        repository.create_request(
            "verified-token", principal_id=PRINCIPAL, idempotency_key="same-key"
        )
    )

    assert request.request_id == REQUEST
    assert calls[0].url.path == "/rest/v1/data_deletion_requests"
    assert calls[1].url.params["idempotency_key"] == "eq.same-key"
    assert calls[0].headers["authorization"] == "Bearer verified-token"


def test_accept_uses_authenticated_rpc_and_status_is_requester_filtered() -> None:
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/rpc/accept_account_deletion_v3"):
            return httpx.Response(200, json=[row("journal_ack_pending")], request=request)
        return httpx.Response(200, json=[], request=request)

    repository = SupabaseDeletionRepository(settings(), transport=httpx.MockTransport(handler))
    accepted = asyncio.run(repository.accept_request("verified-token", request_id=REQUEST))
    status = asyncio.run(
        repository.get_request(
            "verified-token", principal_id=PRINCIPAL, request_id=REQUEST
        )
    )

    assert accepted.status == "journal_ack_pending"
    assert status is None
    assert calls[0].url.path == "/rest/v1/rpc/accept_account_deletion_v3"
    assert json.loads(calls[0].content) == {"p_request_id": str(REQUEST)}
    assert calls[1].url.params["requester_user_id"] == f"eq.{PRINCIPAL}"


def test_current_request_orders_by_created_at_and_limits_one() -> None:
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path == "/rest/v1/data_deletion_requests"
        assert request.url.params["requester_user_id"] == f"eq.{PRINCIPAL}"
        assert request.url.params["order"] == "created_at.desc"
        assert request.url.params["limit"] == "1"
        return httpx.Response(200, json=[row()])

    repository = SupabaseDeletionRepository(
        settings(), transport=httpx.MockTransport(handler)
    )
    result = asyncio.run(
        repository.get_current_request("synthetic-token", principal_id=PRINCIPAL)
    )
    assert result is not None
    assert result.request_id == REQUEST
    assert len(calls) == 1
