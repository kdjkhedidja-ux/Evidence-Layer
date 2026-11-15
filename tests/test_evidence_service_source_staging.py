"""Owner-scoped source staging endpoint and repository."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
import pytest

from account_lifecycle_test_support import (
    AvailableAccountLifecycleReader,
    OpenRecoveryFenceReader,
)
from evidence_service.app import create_app
from evidence_service.auth import AuthenticationRejected, Principal
from evidence_service.config import SupabaseSettings
from evidence_service.secure_snapshot_read import snapshot_object_key
from evidence_service.source_staging_repository import (
    SourceStageConflict,
    SourceStageForbidden,
    SourceStageRejected,
    SourceStageUnavailable,
    StagedSource,
    SupabaseSourceStagingRepository,
)
from evidence_service.tenant_context import WorkspaceMembership

ISSUER = "https://project-ref.supabase.co/auth/v1"
SUBJECT = UUID("10000000-0000-4000-8000-000000000001")
WORKSPACE = UUID("a1000000-0000-4000-8000-000000000001")
SOURCE = UUID("c1000000-0000-4000-8000-000000000001")
TOKEN = "verified.synthetic.jwt"
CONTENT = "hello"
DIGEST = hashlib.sha256(CONTENT.encode()).hexdigest()


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
    def __init__(self, role: str = "owner") -> None:
        self.role = role

    async def list_memberships(self, principal, access_token):
        raise AssertionError("not used")

    async def get_membership(self, principal, access_token, workspace_id):
        if principal.subject == SUBJECT and workspace_id == WORKSPACE:
            return WorkspaceMembership(WORKSPACE, "A workspace", self.role)
        return None


class FakeStaging:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.bound_token = None

    async def stage(self, access_token, **kwargs):
        self.bound_token = access_token
        self.calls.append(kwargs)
        return StagedSource(
            workspace_id=kwargs["workspace_id"],
            source_id=kwargs["source_id"],
            version_id=kwargs["version_id"],
            object_key=kwargs["object_key"],
            sha256=kwargs["sha256"],
            size_bytes=kwargs["size_bytes"],
            created_at=datetime(2026, 11, 15, tzinfo=timezone.utc),
        )


def make_client(*, directory: MembershipDirectory, staging: FakeStaging):
    def factory(access_token: str):
        staging.bound_token = access_token
        return staging

    app = create_app(
        settings(),
        token_verifier=StaticVerifier(),
        account_lifecycle_reader=AvailableAccountLifecycleReader(),
        recovery_fence_reader=OpenRecoveryFenceReader(),
        workspace_directory=directory,
        source_staging_factory=factory,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def post(client, payload, token=TOKEN):
    async def run():
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with client:
            return await client.post(
                f"/v1/workspaces/{WORKSPACE}/sources", json=payload, headers=headers
            )

    return asyncio.run(run())


def test_owner_stages_a_source_version_without_content_leakage():
    staging = FakeStaging()
    client = make_client(directory=MembershipDirectory("owner"), staging=staging)

    response = post(client, {"content": CONTENT})

    assert response.status_code == 201
    payload = response.json()
    version_id = UUID(payload["version_id"])
    assert payload["source_id"]
    assert payload["state"] == "staged"
    assert payload["sha256"] == DIGEST
    assert payload["size_bytes"] == len(CONTENT.encode())
    assert payload["bucket"] == "source-snapshots"
    assert payload["object_key"] == snapshot_object_key(
        WORKSPACE, UUID(payload["source_id"]), version_id
    )
    assert CONTENT not in response.text
    assert staging.bound_token == TOKEN
    assert staging.calls[0]["sha256"] == DIGEST
    assert staging.calls[0]["size_bytes"] == len(CONTENT.encode())


def test_owner_can_pin_an_existing_source_id():
    staging = FakeStaging()
    client = make_client(directory=MembershipDirectory("owner"), staging=staging)

    response = post(client, {"content": CONTENT, "source_id": str(SOURCE)})

    assert response.status_code == 201
    payload = response.json()
    assert payload["source_id"] == str(SOURCE)
    assert staging.calls[0]["source_id"] == SOURCE


@pytest.mark.parametrize("role", ["member", "admin"])
def test_non_owner_cannot_stage(role: str):
    staging = FakeStaging()
    client = make_client(directory=MembershipDirectory(role), staging=staging)

    response = post(client, {"content": CONTENT})

    assert response.status_code == 403
    assert staging.calls == []


def test_staging_requires_authentication():
    staging = FakeStaging()
    client = make_client(directory=MembershipDirectory("owner"), staging=staging)

    response = post(client, {"content": CONTENT}, token=None)

    assert response.status_code == 401


def test_repository_posts_a_staged_row_with_the_user_token():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        seen["apikey"] = request.headers["apikey"]
        seen["payload"] = request.content
        version = version_id
        return httpx.Response(
            201,
            json=[{
                "workspace_id": str(WORKSPACE),
                "source_id": str(SOURCE),
                "version_id": str(version),
                "object_key": object_key,
                "sha256": DIGEST,
                "size_bytes": 5,
                "state": "staged",
                "created_at": "2026-11-15T00:00:00Z",
            }],
        )

    version_id = uuid4()
    object_key = snapshot_object_key(WORKSPACE, SOURCE, version_id)
    repository = SupabaseSourceStagingRepository(
        settings(), transport=httpx.MockTransport(handler)
    )
    staged = asyncio.run(
        repository.stage(
            TOKEN,
            workspace_id=WORKSPACE,
            source_id=SOURCE,
            version_id=version_id,
            object_key=object_key,
            sha256=DIGEST,
            size_bytes=5,
        )
    )

    assert staged.version_id == version_id
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/rest/v1/source_snapshots")
    assert seen["authorization"] == f"Bearer {TOKEN}"
    assert seen["apikey"] == "sb_publishable_synthetic_test_key"
    assert b'"state":"staged"' in seen["payload"]


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (403, SourceStageForbidden),
        (409, SourceStageConflict),
        (422, SourceStageRejected),
        (500, SourceStageUnavailable),
    ],
)
def test_repository_maps_failures(status: int, error: type[Exception]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "synthetic"})

    repository = SupabaseSourceStagingRepository(
        settings(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(error):
        asyncio.run(
            repository.stage(
                TOKEN,
                workspace_id=WORKSPACE,
                source_id=SOURCE,
                version_id=uuid4(),
                object_key=snapshot_object_key(WORKSPACE, SOURCE, uuid4()),
                sha256=DIGEST,
                size_bytes=5,
            )
        )
