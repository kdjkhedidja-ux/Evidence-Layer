"""Workspace sources index endpoint: membership-gated, RLS-scoped listing."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

import httpx

from account_lifecycle_test_support import (
    AvailableAccountLifecycleReader,
    OpenRecoveryFenceReader,
)
from evidence_service.app import create_app
from evidence_service.auth import AuthenticationRejected, Principal
from evidence_service.config import SupabaseSettings
from evidence_service.secure_snapshot_read import (
    SnapshotState,
    SourceSnapshotManifest,
    snapshot_object_key,
)
from evidence_service.supabase_source_catalog import SourceCatalogUnavailable
from evidence_service.tenant_context import WorkspaceMembership

ISSUER = "https://project-ref.supabase.co/auth/v1"
SUBJECT = UUID("10000000-0000-4000-8000-000000000001")
WORKSPACE = UUID("a1000000-0000-4000-8000-000000000001")
OTHER_WORKSPACE = UUID("b3000000-0000-4000-8000-000000000003")
SOURCE = UUID("c1000000-0000-4000-8000-000000000001")
VERSION = UUID("d1000000-0000-4000-8000-000000000001")
VERSION_OLDER = UUID("d1000000-0000-4000-8000-0000000000aa")
OWNER = UUID("e1000000-0000-4000-8000-000000000001")
TOKEN = "verified.synthetic.jwt"
SHA256 = "a" * 64


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
        return Principal(ISSUER, SUBJECT)


class MembershipDirectory:
    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow

    async def list_memberships(self, principal, access_token):
        raise AssertionError("not used")

    async def get_membership(self, principal, access_token, workspace_id):
        if self.allow and principal.subject == SUBJECT and workspace_id == WORKSPACE:
            return WorkspaceMembership(WORKSPACE, "A workspace", "owner")
        return None


def manifest(version: UUID = VERSION, *, state=SnapshotState.PUBLISHED) -> SourceSnapshotManifest:
    return SourceSnapshotManifest(
        workspace_id=WORKSPACE,
        source_id=SOURCE,
        owner_id=OWNER,
        version_id=version,
        object_key=snapshot_object_key(WORKSPACE, SOURCE, version),
        sha256=SHA256,
        size_bytes=25,
        state=state,
        created_at=datetime(2026, 10, 22, tzinfo=timezone.utc),
    )


class FakeCatalog:
    def __init__(self, manifests=None, error: Exception | None = None) -> None:
        self.manifests = manifests if manifests is not None else [manifest()]
        self.error = error
        self.calls: list[tuple[str, UUID]] = []

    async def list_visible_versions(self, workspace_id):
        self.calls.append((self.bound_token, workspace_id))
        if self.error is not None:
            raise self.error
        return self.manifests


def make_client(*, directory, catalog: FakeCatalog):
    def factory(access_token: str) -> FakeCatalog:
        catalog.bound_token = access_token
        return catalog

    app = create_app(
        settings(),
        token_verifier=StaticVerifier(),
        account_lifecycle_reader=AvailableAccountLifecycleReader(),
        recovery_fence_reader=OpenRecoveryFenceReader(),
        workspace_directory=directory,
        source_catalog_factory=factory,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def get(client, path, token=TOKEN):
    async def run():
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with client:
            return await client.get(path, headers=headers)

    return asyncio.run(run())


def test_sources_index_lists_visible_versions_for_a_member():
    catalog = FakeCatalog(
        [manifest(), manifest(VERSION_OLDER, state=SnapshotState.STAGED)]
    )
    client = make_client(directory=MembershipDirectory(), catalog=catalog)

    response = get(client, f"/v1/workspaces/{WORKSPACE}/sources")

    assert response.status_code == 200
    payload = response.json()
    assert payload == [
        {
            "source_id": str(SOURCE),
            "version_id": str(VERSION),
            "state": "published",
            "sha256": SHA256,
            "size_bytes": 25,
            "created_at": "2026-10-22T00:00:00Z",
        },
        {
            "source_id": str(SOURCE),
            "version_id": str(VERSION_OLDER),
            "state": "staged",
            "sha256": SHA256,
            "size_bytes": 25,
            "created_at": "2026-10-22T00:00:00Z",
        },
    ]
    # The request-scoped catalog is bound to the caller's token, and the
    # listing is scoped to the resolved workspace only.
    assert catalog.calls == [(TOKEN, WORKSPACE)]


def test_sources_index_empty_when_nothing_is_visible():
    catalog = FakeCatalog([])
    client = make_client(directory=MembershipDirectory(), catalog=catalog)

    response = get(client, f"/v1/workspaces/{WORKSPACE}/sources")

    assert response.status_code == 200
    assert response.json() == []


def test_sources_index_never_leaks_object_keys():
    catalog = FakeCatalog()
    client = make_client(directory=MembershipDirectory(), catalog=catalog)

    response = get(client, f"/v1/workspaces/{WORKSPACE}/sources")

    assert response.status_code == 200
    assert "object_key" not in response.text
    assert "workspace/" not in response.text


def test_sources_index_requires_authentication():
    catalog = FakeCatalog()
    client = make_client(directory=MembershipDirectory(), catalog=catalog)

    response = get(client, f"/v1/workspaces/{WORKSPACE}/sources", token=None)

    assert response.status_code == 401
    assert catalog.calls == []


def test_sources_index_rejects_invalid_token():
    catalog = FakeCatalog()
    client = make_client(directory=MembershipDirectory(), catalog=catalog)

    response = get(client, f"/v1/workspaces/{WORKSPACE}/sources", token="bad")

    assert response.status_code == 401
    assert catalog.calls == []


def test_sources_index_hides_workspace_from_non_member():
    catalog = FakeCatalog()
    client = make_client(
        directory=MembershipDirectory(allow=False), catalog=catalog
    )

    response = get(client, f"/v1/workspaces/{WORKSPACE}/sources")

    assert response.status_code == 404
    assert catalog.calls == [], "a non-member must not reach the catalog"


def test_sources_index_other_workspace_is_not_resolvable():
    catalog = FakeCatalog()
    client = make_client(directory=MembershipDirectory(), catalog=catalog)

    response = get(client, f"/v1/workspaces/{OTHER_WORKSPACE}/sources")

    assert response.status_code == 404
    assert catalog.calls == []


def test_sources_index_catalog_failure_is_unavailable():
    catalog = FakeCatalog(error=SourceCatalogUnavailable)
    client = make_client(directory=MembershipDirectory(), catalog=catalog)

    response = get(client, f"/v1/workspaces/{WORKSPACE}/sources")

    assert response.status_code == 503
    assert "sha256" not in response.text


def test_sources_index_rejects_non_uuid_workspace_id():
    catalog = FakeCatalog()
    client = make_client(directory=MembershipDirectory(), catalog=catalog)

    response = get(client, "/v1/workspaces/not-a-uuid/sources")

    assert response.status_code in {404, 422}
    assert catalog.calls == []
