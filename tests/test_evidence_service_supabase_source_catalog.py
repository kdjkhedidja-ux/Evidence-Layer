from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

import httpx
import pytest

from evidence_service.auth import AuthenticationRejected, Principal
from evidence_service.config import SupabaseSettings
from evidence_service.secure_snapshot_read import (
    SnapshotState,
    SourceSnapshotManifest,
    VerifiedSnapshot,
    snapshot_object_key,
)
from evidence_service.supabase_source_catalog import (
    SourceAccessPolicyUnavailable,
    SourceCatalogUnavailable,
    SupabaseSnapshotCatalog,
    SupabaseSourceAccessPolicy,
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


def settings() -> SupabaseSettings:
    return SupabaseSettings(
        supabase_url="https://project-ref.supabase.co",
        publishable_key="sb_publishable_synthetic_test_key",
        jwt_issuer=ISSUER,
        jwt_audience="authenticated",
        jwt_algorithms=("RS256",),
    )


def published_row() -> dict[str, object]:
    return {
        "workspace_id": str(WORKSPACE),
        "source_id": str(SOURCE),
        "version_id": str(VERSION),
        "object_key": snapshot_object_key(WORKSPACE, SOURCE, VERSION),
        "sha256": SHA256,
        "size_bytes": 5,
        "state": "published",
        "created_by": str(OWNER),
        "created_at": "2026-10-22T00:00:00+00:00",
    }


def test_catalog_resolves_published_version_with_user_token() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, json=[published_row()])

    catalog = SupabaseSnapshotCatalog(
        settings(), TOKEN, transport=httpx.MockTransport(handler)
    )
    manifest = asyncio.run(catalog.resolve_version(WORKSPACE, SOURCE, VERSION))

    assert manifest is not None
    assert manifest.workspace_id == WORKSPACE
    assert manifest.source_id == SOURCE
    assert manifest.version_id == VERSION
    assert manifest.owner_id == OWNER
    assert manifest.sha256 == SHA256
    assert manifest.state is SnapshotState.PUBLISHED

    request = observed[0]
    assert request.url.path == "/rest/v1/source_snapshots"
    assert request.url.params["workspace_id"] == f"eq.{WORKSPACE}"
    assert request.url.params["source_id"] == f"eq.{SOURCE}"
    assert request.url.params["version_id"] == f"eq.{VERSION}"
    assert request.url.params["state"] == "eq.published"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["apikey"] == settings().publishable_key
    assert "service_role" not in request.headers["apikey"]


def test_catalog_returns_none_when_version_is_not_visible() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=[])

    catalog = SupabaseSnapshotCatalog(
        settings(), TOKEN, transport=httpx.MockTransport(handler)
    )
    result = asyncio.run(catalog.resolve_version(WORKSPACE, SOURCE, VERSION))
    assert result is None


def test_catalog_fails_closed_on_non_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500, json={"error": "hidden"})

    catalog = SupabaseSnapshotCatalog(
        settings(), TOKEN, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(SourceCatalogUnavailable):
        asyncio.run(catalog.resolve_version(WORKSPACE, SOURCE, VERSION))


def test_catalog_rejects_malformed_row() -> None:
    bad = published_row()
    bad["sha256"] = "not-a-digest"

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=[bad])

    catalog = SupabaseSnapshotCatalog(
        settings(), TOKEN, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(SourceCatalogUnavailable):
        asyncio.run(catalog.resolve_version(WORKSPACE, SOURCE, VERSION))


def test_catalog_cannot_publish() -> None:
    catalog = SupabaseSnapshotCatalog(
        settings(),
        TOKEN,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])),
    )
    manifest = SourceSnapshotManifest(
        workspace_id=WORKSPACE,
        source_id=SOURCE,
        owner_id=OWNER,
        version_id=VERSION,
        object_key=snapshot_object_key(WORKSPACE, SOURCE, VERSION),
        sha256=SHA256,
        size_bytes=5,
        state=SnapshotState.PUBLISHED,
        created_at=datetime(2026, 10, 22, tzinfo=timezone.utc),
    )
    with pytest.raises(SourceCatalogUnavailable):
        asyncio.run(catalog.publish_if_absent(manifest))


def test_acl_grants_read_when_row_exists() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, json=[{"workspace_id": str(WORKSPACE)}])

    acl = SupabaseSourceAccessPolicy(
        settings(), TOKEN, transport=httpx.MockTransport(handler)
    )
    allowed = asyncio.run(acl.may_read(SUBJECT, WORKSPACE, SOURCE, VERSION))
    assert allowed is True

    request = observed[0]
    assert request.url.path == "/rest/v1/source_grants"
    assert request.url.params["workspace_id"] == f"eq.{WORKSPACE}"
    assert request.url.params["source_id"] == f"eq.{SOURCE}"
    assert request.url.params["principal_id"] == f"eq.{SUBJECT}"
    assert request.url.params["grant_type"] == "eq.read"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"


def test_acl_denies_when_no_grant_row() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=[])

    acl = SupabaseSourceAccessPolicy(
        settings(), TOKEN, transport=httpx.MockTransport(handler)
    )
    allowed = asyncio.run(acl.may_read(SUBJECT, WORKSPACE, SOURCE, VERSION))
    assert allowed is False


def test_acl_fails_closed_on_non_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(503, json={})

    acl = SupabaseSourceAccessPolicy(
        settings(), TOKEN, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(SourceAccessPolicyUnavailable):
        asyncio.run(acl.may_read(SUBJECT, WORKSPACE, SOURCE, VERSION))



def staged_row() -> dict[str, object]:
    row = dict(published_row())
    row["state"] = "staged"
    return row


def test_list_visible_versions_returns_published_and_staged_rows() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, json=[published_row(), staged_row()])

    catalog = SupabaseSnapshotCatalog(
        settings(), TOKEN, transport=httpx.MockTransport(handler)
    )
    manifests = asyncio.run(catalog.list_visible_versions(WORKSPACE))

    assert [m.state for m in manifests] == [SnapshotState.PUBLISHED, SnapshotState.STAGED]
    assert all(m.workspace_id == WORKSPACE for m in manifests)
    request = observed[0]
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["apikey"] == "sb_publishable_synthetic_test_key"
    assert "workspace_id=eq." in str(request.url)


def test_list_visible_versions_empty_list_is_not_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    catalog = SupabaseSnapshotCatalog(
        settings(), TOKEN, transport=httpx.MockTransport(handler)
    )
    assert asyncio.run(catalog.list_visible_versions(WORKSPACE)) == []


def test_list_visible_versions_fails_closed_on_malformed_row() -> None:
    bad = dict(published_row())
    bad["sha256"] = "not-a-digest"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[published_row(), bad])

    catalog = SupabaseSnapshotCatalog(
        settings(), TOKEN, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(SourceCatalogUnavailable):
        asyncio.run(catalog.list_visible_versions(WORKSPACE))


def test_list_visible_versions_fails_closed_on_non_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "denied"})

    catalog = SupabaseSnapshotCatalog(
        settings(), TOKEN, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(SourceCatalogUnavailable):
        asyncio.run(catalog.list_visible_versions(WORKSPACE))
