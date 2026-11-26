from __future__ import annotations

import asyncio
from types import SimpleNamespace

from evidence_service.github_app_sync_service import GitHubSyncResult
from uuid import UUID

from evidence_service.connector_repository import ConnectorConnection
from evidence_service.connector_sync_worker import run_once
from evidence_service.github_app_sync_service import GitHubAppSyncError

USER = UUID("11111111-1111-4111-8111-111111111111")
CONNECTION_A = UUID("22222222-2222-4222-8222-222222222222")
CONNECTION_B = UUID("33333333-3333-4333-8333-333333333333")


def connection(connection_id: UUID) -> ConnectorConnection:
    return ConnectorConnection(
        connection_id=connection_id,
        user_id=USER,
        provider="github",
        account_email="acme",
        scopes=("contents:read",),
        status="connected",
        last_sync_at=None,
        last_error_category=None,
        access_token_expires_at=None,
        provider_config={"installation_id": "123"},
        workspace_id=UUID("44444444-4444-4444-8444-444444444444"),
    )


class FakeRepository:
    async def list_syncable_connections(self, access_token, *, provider, limit):
        assert access_token == "service-role"
        assert provider == "github"
        return [connection(CONNECTION_A), connection(CONNECTION_B)]


class FakeService:
    async def sync(self, access_token, connection_id, *, limit):
        assert access_token == "service-role"
        if connection_id == CONNECTION_B:
            raise GitHubAppSyncError("synthetic")
        return GitHubSyncResult(
            connection_id=str(connection_id), listed=3, ingested=1, skipped=0,
            next_cursor=None, unchanged=2, pending=0, not_indexed=1,
            not_indexed_files=(("0123456789abcdef", "too_large"),),
        )


def test_worker_syncs_each_connected_github_connection():
    result = asyncio.run(
        run_once(
            FakeRepository(),  # type: ignore[arg-type]
            FakeService(),  # type: ignore[arg-type]
            access_token="service-role",
        )
    )
    assert result["connections"] == 2
    assert result["results"][0] == {
        "connection_id": str(CONNECTION_A),
        "status": "ok",
        "listed": 3,
        "ingested": 1,
        "unchanged": 2,
        "skipped": 0,
        "pending": 0,
        "not_indexed": 1,
        # Hash + reason only: no plain path or content reaches the logs.
        "not_indexed_files": [{"path_sha256": "0123456789abcdef", "reason": "too_large"}],
    }
    assert result["results"][1] == {
        "connection_id": str(CONNECTION_B),
        "status": "error",
    }


def test_worker_reports_accounts_being_deleted_as_skipped():
    from evidence_service.github_app_sync_service import GitHubAccountUnavailable

    class DeletingService:
        async def sync(self, access_token, connection_id, *, limit):
            raise GitHubAccountUnavailable("synthetic")

    result = asyncio.run(
        run_once(
            FakeRepository(),  # type: ignore[arg-type]
            DeletingService(),  # type: ignore[arg-type]
            access_token="service-role",
        )
    )
    assert [item["status"] for item in result["results"]] == [
        "skipped_account_unavailable",
        "skipped_account_unavailable",
    ]
