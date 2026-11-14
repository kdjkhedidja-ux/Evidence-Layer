from __future__ import annotations

import asyncio
import json
from uuid import UUID

import httpx

from evidence_service.config import SupabaseSettings
from evidence_service.task_run_ledger_repository import SupabaseTaskRunLedgerRepository

WORKSPACE = UUID("a1000000-0000-4000-8000-000000000001")
ACTOR = UUID("10000000-0000-4000-8000-000000000001")


def settings() -> SupabaseSettings:
    return SupabaseSettings(
        supabase_url="https://project-ref.supabase.co",
        publishable_key="sb_publishable_test",
        jwt_issuer="https://project-ref.supabase.co/auth/v1",
        jwt_audience="authenticated",
        jwt_algorithms=("RS256",),
    )


def test_ledger_repository_writes_content_free_metrics_with_user_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    async def run() -> None:
        repository = SupabaseTaskRunLedgerRepository(
            settings(), transport=httpx.MockTransport(handler)
        )
        await repository.record(
            "user-access-token",
            workspace_id=WORKSPACE,
            actor_user_id=ACTOR,
            metrics={"run_type": "task_context", "status": "ok", "retrieval_tokens": 120},
        )

    asyncio.run(run())
    request = seen[0]
    assert request.url.path.endswith("/rest/v1/task_run_ledger")
    assert request.headers["apikey"] == "sb_publishable_test"
    assert request.headers["authorization"] == "Bearer user-access-token"
    assert json.loads(request.content) == {
        "workspace_id": str(WORKSPACE),
        "actor_user_id": str(ACTOR),
        "run_type": "task_context",
        "status": "ok",
        "retrieval_tokens": 120,
    }


def test_ledger_repository_lists_newest_first_with_limit() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[{"run_id": "1"}])

    async def run() -> None:
        repository = SupabaseTaskRunLedgerRepository(
            settings(), transport=httpx.MockTransport(handler)
        )
        rows = await repository.list_runs(
            "user-access-token", workspace_id=WORKSPACE, limit=250
        )
        assert rows == [{"run_id": "1"}]

    asyncio.run(run())
    assert seen[0].url.params["workspace_id"] == f"eq.{WORKSPACE}"
    assert seen[0].url.params["order"] == "created_at.desc,run_id.desc"
    assert seen[0].url.params["limit"] == "100"
