from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID

from fastapi.testclient import TestClient

from evidence_service.app import create_app
from evidence_service.auth import AuthenticatedRequest, Principal
from evidence_service.config import SupabaseSettings
from evidence_service.export_repository import ExportDownload, ExportJob

SUBJECT = UUID("11111111-1111-4111-8111-111111111111")
EXPORT_ID = UUID("22222222-2222-4222-8222-222222222222")
TOKEN = "verified-token"
NOW = datetime(2026, 11, 11, tzinfo=timezone.utc)


class StaticVerifier:
    async def verify(self, token: str) -> Principal:
        assert token == TOKEN
        return Principal(issuer="https://project.supabase.co/auth/v1", subject=SUBJECT)


class AvailableLifecycle:
    async def read_state(self, subject: UUID, access_token: str) -> str:
        assert subject == SUBJECT
        return "available"


class OpenFence:
    async def is_open(self) -> bool:
        return True


class FakeExportRepository:
    def __init__(self):
        self.revoked = False

    async def create_export(self, access_token: str, *, principal_id: UUID, idempotency_key: str):
        assert principal_id == SUBJECT
        return ExportJob(
            export_id=EXPORT_ID,
            status="ready",
            ready_at=NOW,
            expires_at=NOW,
            reused=False,
            grant_token="grant-token",
        )

    async def redeem_export(self, access_token: str, *, export_id: UUID, grant_token: str):
        assert export_id == EXPORT_ID
        if grant_token != "grant-token":
            return None
        return ExportDownload(
            content=b"zip-bytes",
            sha256=hashlib.sha256(b"zip-bytes").hexdigest(),
            size_bytes=len(b"zip-bytes"),
            expires_at=NOW,
        )

    async def revoke_export(self, access_token: str, *, export_id: UUID) -> bool:
        assert export_id == EXPORT_ID
        self.revoked = True
        return True


def make_client() -> tuple[TestClient, FakeExportRepository]:
    settings = SupabaseSettings(
        supabase_url="https://project.supabase.co",
        publishable_key="sb_publishable_test",
        jwt_issuer="https://project.supabase.co/auth/v1",
        jwt_audience="authenticated",
        jwt_algorithms=("ES256",),
    )
    repository = FakeExportRepository()
    app = create_app(
        settings,
        token_verifier=StaticVerifier(),
        account_lifecycle_reader=AvailableLifecycle(),
        recovery_fence_reader=OpenFence(),
        export_repository=repository,
    )
    return TestClient(app), repository


def test_create_download_and_revoke_export() -> None:
    client, repository = make_client()
    headers = {"Authorization": f"Bearer {TOKEN}"}

    created = client.post(
        "/v1/me/exports",
        headers=headers,
        json={"idempotency_key": "export-1"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["export_id"] == str(EXPORT_ID)
    assert body["grant_token"] == "grant-token"
    assert body["download_path"] == f"/v1/me/exports/{EXPORT_ID}/download"

    downloaded = client.get(
        f"/v1/me/exports/{EXPORT_ID}/download",
        headers={**headers, "X-Export-Grant": "grant-token"},
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"zip-bytes"
    assert downloaded.headers["cache-control"] == "private, no-store"
    assert downloaded.headers["content-type"] == "application/zip"
    assert downloaded.headers["content-digest"].startswith("sha-256=:")

    missing_grant = client.get(
        f"/v1/me/exports/{EXPORT_ID}/download", headers=headers
    )
    assert missing_grant.status_code == 404

    revoked = client.post(
        f"/v1/me/exports/{EXPORT_ID}/revoke", headers=headers
    )
    assert revoked.status_code == 200
    assert revoked.json() == {"export_id": str(EXPORT_ID), "revoked": True}
    assert repository.revoked
