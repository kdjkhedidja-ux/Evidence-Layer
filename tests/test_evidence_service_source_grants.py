"""Owner-managed source read grants."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

import httpx
import pytest

from account_lifecycle_test_support import (
    AvailableAccountLifecycleReader,
    OpenRecoveryFenceReader,
)
from evidence_service.app import create_app
from evidence_service.auth import AuthenticationRejected, Principal
from evidence_service.config import SupabaseSettings
from evidence_service.source_grant_repository import (
    SourceGrant,
    SourceGrantConflict,
    SourceGrantForbidden,
    SourceGrantRejected,
    SourceGrantUnavailable,
    SupabaseSourceGrantRepository,
)
from evidence_service.tenant_context import WorkspaceMembership

ISSUER = "https://project-ref.supabase.co/auth/v1"
SUBJECT = UUID("10000000-0000-4000-8000-000000000001")
WORKSPACE = UUID("a1000000-0000-4000-8000-000000000001")
SOURCE = UUID("c1000000-0000-4000-8000-000000000001")
PRINCIPAL = UUID("10000000-0000-4000-8000-000000000002")
TOKEN = "verified.synthetic.jwt"
CREATED = datetime(2026, 11, 15, tzinfo=timezone.utc)


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


class FakeGrants:
    def __init__(self) -> None:
        self.listed: list[tuple[UUID, UUID]] = []
        self.created: list[tuple[UUID, UUID, UUID]] = []
        self.revoked: list[tuple[UUID, UUID, UUID]] = []

    async def list(self, access_token, *, workspace_id, source_id):
        self.listed.append((workspace_id, source_id))
        return [
            SourceGrant(
                principal_id=PRINCIPAL,
                grant_type="read",
                granted_by=SUBJECT,
                created_at=CREATED,
            )
        ]

    async def grant(self, access_token, *, workspace_id, source_id, principal_id):
        self.created.append((workspace_id, source_id, principal_id))
        return SourceGrant(
            principal_id=principal_id,
            grant_type="read",
            granted_by=SUBJECT,
            created_at=CREATED,
        )

    async def revoke(self, access_token, *, workspace_id, source_id, principal_id):
        self.revoked.append((workspace_id, source_id, principal_id))


def make_client(*, directory: MembershipDirectory, grants: FakeGrants):
    def factory(access_token: str):
        return grants

    app = create_app(
        settings(),
        token_verifier=StaticVerifier(),
        account_lifecycle_reader=AvailableAccountLifecycleReader(),
        recovery_fence_reader=OpenRecoveryFenceReader(),
        workspace_directory=directory,
        source_grant_factory=factory,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def request(client, method, path, *, payload=None, token=TOKEN):
    async def run():
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with client:
            return await client.request(method, path, json=payload, headers=headers)

    return asyncio.run(run())


def test_owner_lists_grants():
    grants = FakeGrants()
    client = make_client(directory=MembershipDirectory("owner"), grants=grants)
    response = request(
        client, "GET", f"/v1/workspaces/{WORKSPACE}/sources/{SOURCE}/grants"
    )
    assert response.status_code == 200
    assert response.json()[0]["principal_id"] == str(PRINCIPAL)
    assert grants.listed == [(WORKSPACE, SOURCE)]


def test_owner_creates_and_revokes_a_grant():
    grants = FakeGrants()
    client = make_client(directory=MembershipDirectory("owner"), grants=grants)
    created = request(
        client,
        "POST",
        f"/v1/workspaces/{WORKSPACE}/sources/{SOURCE}/grants",
        payload={"principal_id": str(PRINCIPAL)},
    )
    assert created.status_code == 201
    assert created.json()["grant_type"] == "read"
    assert grants.created == [(WORKSPACE, SOURCE, PRINCIPAL)]

    client = make_client(directory=MembershipDirectory("owner"), grants=grants)
    revoked = request(
        client,
        "DELETE",
        f"/v1/workspaces/{WORKSPACE}/sources/{SOURCE}/grants/{PRINCIPAL}",
    )
    assert revoked.status_code == 204
    assert grants.revoked == [(WORKSPACE, SOURCE, PRINCIPAL)]


@pytest.mark.parametrize("role", ["member", "admin"])
def test_non_owner_cannot_manage_grants(role: str):
    grants = FakeGrants()
    client = make_client(directory=MembershipDirectory(role), grants=grants)
    response = request(
        client, "GET", f"/v1/workspaces/{WORKSPACE}/sources/{SOURCE}/grants"
    )
    assert response.status_code == 403
    assert grants.listed == []


def test_grant_management_requires_authentication():
    grants = FakeGrants()
    client = make_client(directory=MembershipDirectory("owner"), grants=grants)
    response = request(
        client,
        "GET",
        f"/v1/workspaces/{WORKSPACE}/sources/{SOURCE}/grants",
        token=None,
    )
    assert response.status_code == 401


def test_repository_lists_and_filters_grants():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json=[{
                "principal_id": str(PRINCIPAL),
                "grant_type": "read",
                "granted_by": str(SUBJECT),
                "created_at": "2026-11-15T00:00:00Z",
            }],
        )

    repository = SupabaseSourceGrantRepository(
        settings(), transport=httpx.MockTransport(handler)
    )
    grants = asyncio.run(
        repository.list(TOKEN, workspace_id=WORKSPACE, source_id=SOURCE)
    )
    assert grants[0].principal_id == PRINCIPAL
    assert seen["method"] == "GET"
    assert seen["params"]["workspace_id"] == f"eq.{WORKSPACE}"
    assert seen["params"]["source_id"] == f"eq.{SOURCE}"


def test_repository_posts_and_deletes_a_grant():
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, dict(request.url.params)))
        if request.method == "POST":
            return httpx.Response(
                201,
                json=[{
                    "principal_id": str(PRINCIPAL),
                    "grant_type": "read",
                    "granted_by": str(SUBJECT),
                    "created_at": "2026-11-15T00:00:00Z",
                }],
            )
        return httpx.Response(204)

    repository = SupabaseSourceGrantRepository(
        settings(), transport=httpx.MockTransport(handler)
    )
    grant = asyncio.run(
        repository.grant(
            TOKEN,
            workspace_id=WORKSPACE,
            source_id=SOURCE,
            principal_id=PRINCIPAL,
        )
    )
    assert grant.principal_id == PRINCIPAL
    asyncio.run(
        repository.revoke(
            TOKEN,
            workspace_id=WORKSPACE,
            source_id=SOURCE,
            principal_id=PRINCIPAL,
        )
    )
    assert calls == [
        ("POST", {}),
        ("DELETE", {
            "workspace_id": f"eq.{WORKSPACE}",
            "source_id": f"eq.{SOURCE}",
            "principal_id": f"eq.{PRINCIPAL}",
        }),
    ]


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (403, SourceGrantForbidden),
        (409, SourceGrantConflict),
        (422, SourceGrantRejected),
        (500, SourceGrantUnavailable),
    ],
)
def test_repository_maps_grant_failures(status: int, error: type[Exception]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "synthetic"})

    repository = SupabaseSourceGrantRepository(
        settings(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(error):
        asyncio.run(
            repository.grant(
                TOKEN,
                workspace_id=WORKSPACE,
                source_id=SOURCE,
                principal_id=PRINCIPAL,
            )
        )
