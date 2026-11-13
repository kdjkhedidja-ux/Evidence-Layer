from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from uuid import UUID

import httpx

from account_lifecycle_test_support import (
    AvailableAccountLifecycleReader,
    OpenRecoveryFenceReader,
)
from evidence_layer.source_access import (
    SourceReadDenial,
    SourceReadDenialReason,
    SourceReadError,
)
from evidence_service.app import create_app
from evidence_service.auth import AuthenticationRejected, AuthenticatedRequest, Principal
from evidence_service.config import SupabaseSettings
from evidence_service.secure_snapshot_read import (
    SnapshotState,
    SourceSnapshotManifest,
    VerifiedSnapshot,
    snapshot_object_key,
)
from evidence_service.tenant_context import WorkspaceMembership

ISSUER = "https://project-ref.supabase.co/auth/v1"
SUBJECT = UUID("10000000-0000-4000-8000-000000000001")
WORKSPACE = UUID("a1000000-0000-4000-8000-000000000001")
SOURCE = UUID("c1000000-0000-4000-8000-000000000001")
VERSION = UUID("d1000000-0000-4000-8000-000000000001")
OWNER = UUID("e1000000-0000-4000-8000-000000000001")
TOKEN = "verified.synthetic.jwt"
SHA256 = "a" * 64
CONTENT = b"verified snapshot bytes\n"


def settings() -> SupabaseSettings:
    return SupabaseSettings(
        supabase_url="https://project-ref.supabase.co",
        publishable_key="sb_publishable_synthetic_test_key",
        jwt_issuer=ISSUER,
        jwt_audience="authenticated",
        jwt_algorithms=("RS256",),
    )


class StaticVerifier:
    async def verify(self, token: str) -> Principal:
        if token != TOKEN:
            raise AuthenticationRejected
        return Principal(ISSUER, SUBJECT, "aal2")


class MembershipDirectory:
    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow

    async def list_memberships(self, principal, access_token):
        raise AssertionError("not used")

    async def get_membership(self, principal, access_token, workspace_id):
        if self.allow and principal.subject == SUBJECT and workspace_id == WORKSPACE:
            return WorkspaceMembership(WORKSPACE, "A workspace", "owner")
        return None


def manifest() -> SourceSnapshotManifest:
    return SourceSnapshotManifest(
        workspace_id=WORKSPACE,
        source_id=SOURCE,
        owner_id=OWNER,
        version_id=VERSION,
        object_key=snapshot_object_key(WORKSPACE, SOURCE, VERSION),
        sha256=SHA256,
        size_bytes=len(CONTENT),
        state=SnapshotState.PUBLISHED,
        created_at=datetime(2026, 10, 22, tzinfo=timezone.utc),
    )


class FakeReadPath:
    def __init__(self, result=None, error: SourceReadError | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[AuthenticatedRequest, UUID, UUID, UUID]] = []

    async def read(self, authenticated_request, *, workspace_id, source_id, version_id):
        self.calls.append((authenticated_request, workspace_id, source_id, version_id))
        if self.error is not None:
            raise self.error
        return self.result


def make_client(*, directory, read_path, recovery_fence_reader=None):
    app = create_app(
        settings(),
        token_verifier=StaticVerifier(),
        account_lifecycle_reader=AvailableAccountLifecycleReader(),
        recovery_fence_reader=recovery_fence_reader or OpenRecoveryFenceReader(),
        workspace_directory=directory,
        source_read_path=read_path,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def url() -> str:
    return f"/v1/workspaces/{WORKSPACE}/sources/{SOURCE}/versions/{VERSION}/content"


def test_authorized_member_receives_verified_content() -> None:
    async def run() -> None:
        read_path = FakeReadPath(
            result=VerifiedSnapshot(manifest=manifest(), content=CONTENT)
        )
        async with make_client(
            directory=MembershipDirectory(), read_path=read_path
        ) as client:
            response = await client.get(url(), headers={"Authorization": f"Bearer {TOKEN}"})

        assert response.status_code == 200
        assert response.content == CONTENT
        assert response.headers["content-digest"] == f"sha-256=:{SHA256}:"
        assert len(read_path.calls) == 1
        assert read_path.calls[0][1:] == (WORKSPACE, SOURCE, VERSION)


    asyncio.run(run())


def test_recovery_fence_closing_at_final_snapshot_boundary_withholds_body_and_digest() -> None:
    class ClosingRecoveryFence:
        def __init__(self) -> None:
            self.statuses = deque([True, True, True, False])
            self.calls = 0

        async def is_open(self) -> bool | None:
            self.calls += 1
            return self.statuses.popleft()

    async def run() -> None:
        fence = ClosingRecoveryFence()
        read_path = FakeReadPath(
            result=VerifiedSnapshot(manifest=manifest(), content=CONTENT)
        )
        async with make_client(
            directory=MembershipDirectory(),
            read_path=read_path,
            recovery_fence_reader=fence,
        ) as client:
            response = await client.get(url(), headers={"Authorization": f"Bearer {TOKEN}"})

        assert response.status_code == 503
        assert response.content != CONTENT
        assert response.json() == {"detail": "Service is unavailable"}
        assert "content-digest" not in response.headers
        assert "location" not in response.headers
        assert fence.calls == 4
        assert len(read_path.calls) == 1

    asyncio.run(run())


def test_source_read_denied_returns_403() -> None:
    async def run() -> None:
        read_path = FakeReadPath(
            error=SourceReadError(SourceReadDenial(SourceReadDenialReason.ACCESS_DENIED))
        )
        async with make_client(
            directory=MembershipDirectory(), read_path=read_path
        ) as client:
            response = await client.get(url(), headers={"Authorization": f"Bearer {TOKEN}"})

        assert response.status_code == 403


    asyncio.run(run())


def test_nonmember_returns_404() -> None:
    async def run() -> None:
        read_path = FakeReadPath(result=VerifiedSnapshot(manifest=manifest(), content=CONTENT))
        async with make_client(
            directory=MembershipDirectory(allow=False), read_path=read_path
        ) as client:
            response = await client.get(url(), headers={"Authorization": f"Bearer {TOKEN}"})

        assert response.status_code == 404
        assert read_path.calls == []


    asyncio.run(run())


def test_missing_version_returns_404() -> None:
    async def run() -> None:
        read_path = FakeReadPath(
            error=SourceReadError(
                SourceReadDenial(SourceReadDenialReason.VERSION_MISMATCH)
            )
        )
        async with make_client(
            directory=MembershipDirectory(), read_path=read_path
        ) as client:
            response = await client.get(url(), headers={"Authorization": f"Bearer {TOKEN}"})

        assert response.status_code == 404


    asyncio.run(run())
