"""Workspace invitation endpoints and RPC bridge."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
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
from evidence_service.tenant_context import WorkspaceMembership
from evidence_service.workspace_invitation_repository import (
    Invitation,
    InvitationForbidden,
    SupabaseWorkspaceInvitationRepository,
)

ISSUER = "https://project-ref.supabase.co/auth/v1"
SUBJECT = UUID("10000000-0000-4000-8000-000000000001")
WORKSPACE = UUID("a1000000-0000-4000-8000-000000000001")
INVITATION = UUID("b1000000-0000-4000-8000-000000000001")
TOKEN = "verified.synthetic.jwt"
CREATED = datetime(2026, 11, 15, tzinfo=timezone.utc)
EXPIRES = CREATED + timedelta(days=7)


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


class FakeUsage:
    def __init__(self, pending=0):
        self.pending = pending

    async def usage(self, access_token, *, workspace_id):
        return {
            "version_count": 0,
            "total_bytes": 0,
            "staged_count": 0,
            "pending_invitations": self.pending,
        }


class FakeInvitations:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.revoked: list[UUID] = []
        self.accepted: list[str] = []

    async def create(self, access_token, *, workspace_id, email, token_hash, expires_at):
        self.created.append({
            "workspace_id": workspace_id,
            "email": email,
            "token_hash": token_hash,
            "expires_at": expires_at,
        })
        return Invitation(
            invitation_id=INVITATION, email=email, role="member",
            created_at=CREATED, expires_at=expires_at,
            accepted_at=None, revoked_at=None,
        )

    async def list(self, access_token, *, workspace_id):
        return [
            Invitation(
                invitation_id=INVITATION, email="member@example.test", role="member",
                created_at=CREATED, expires_at=EXPIRES,
                accepted_at=None, revoked_at=None,
            )
        ]

    async def revoke(self, access_token, *, invitation_id):
        self.revoked.append(invitation_id)

    async def accept(self, access_token, *, token_hash):
        self.accepted.append(token_hash)
        return WORKSPACE, "member"


def make_client(*, directory: MembershipDirectory, invitations: FakeInvitations, usage: FakeUsage | None = None):
    def factory(access_token: str):
        return invitations

    usage = usage or FakeUsage()
    app = create_app(
        settings(),
        token_verifier=StaticVerifier(),
        account_lifecycle_reader=AvailableAccountLifecycleReader(),
        recovery_fence_reader=OpenRecoveryFenceReader(),
        workspace_directory=directory,
        workspace_invitation_factory=factory,
        workspace_usage_factory=lambda access_token: usage,
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


def test_owner_creates_an_invitation_with_a_hashed_one_time_token():
    invitations = FakeInvitations()
    client = make_client(directory=MembershipDirectory("owner"), invitations=invitations)
    response = request(
        client,
        "POST",
        f"/v1/workspaces/{WORKSPACE}/invitations",
        payload={"email": "Member@Example.test"},
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["email"] == "member@example.test"
    assert payload["role"] == "member"
    assert len(payload["invite_token"]) > 20
    assert invitations.created[0]["email"] == "member@example.test"
    assert len(invitations.created[0]["token_hash"]) == 64
    assert invitations.created[0]["token_hash"] != payload["invite_token"]


def test_owner_lists_revokes_and_invitee_accepts():
    invitations = FakeInvitations()
    client = make_client(directory=MembershipDirectory("owner"), invitations=invitations)
    listed = request(client, "GET", f"/v1/workspaces/{WORKSPACE}/invitations")
    assert listed.status_code == 200
    assert listed.json()[0]["invitation_id"] == str(INVITATION)

    client = make_client(directory=MembershipDirectory("owner"), invitations=invitations)
    revoked = request(
        client,
        "DELETE",
        f"/v1/workspaces/{WORKSPACE}/invitations/{INVITATION}",
    )
    assert revoked.status_code == 204
    assert invitations.revoked == [INVITATION]

    client = make_client(directory=MembershipDirectory("member"), invitations=invitations)
    accepted = request(
        client,
        "POST",
        "/v1/invitations/accept",
        payload={"token": "raw-invitation-token-value-1234"},
    )
    assert accepted.status_code == 200
    assert accepted.json() == {"workspace_id": str(WORKSPACE), "role": "member"}
    assert len(invitations.accepted[0]) == 64


def test_pending_invitation_quota_blocks_creation():
    invitations = FakeInvitations()
    client = make_client(
        directory=MembershipDirectory("owner"),
        invitations=invitations,
        usage=FakeUsage(pending=50),
    )
    response = request(
        client,
        "POST",
        f"/v1/workspaces/{WORKSPACE}/invitations",
        payload={"email": "member@example.test"},
    )
    assert response.status_code == 429
    assert invitations.created == []


@pytest.mark.parametrize("role", ["member", "admin"])
def test_non_owner_cannot_create_invitations(role: str):
    invitations = FakeInvitations()
    client = make_client(directory=MembershipDirectory(role), invitations=invitations)
    response = request(
        client,
        "POST",
        f"/v1/workspaces/{WORKSPACE}/invitations",
        payload={"email": "member@example.test"},
    )
    assert response.status_code == 403
    assert invitations.created == []


def test_repository_create_and_accept_rpcs():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path.rsplit("/", 1)[-1])
        if request.url.path.endswith("/create_workspace_invitation"):
            return httpx.Response(
                200,
                json={
                    "invitation_id": str(INVITATION),
                    "email": "member@example.test",
                    "role": "member",
                    "created_at": "2026-11-15T00:00:00Z",
                    "expires_at": "2026-11-22T00:00:00Z",
                },
            )
        return httpx.Response(
            200,
            json={"workspace_id": str(WORKSPACE), "role": "member"},
        )

    repository = SupabaseWorkspaceInvitationRepository(
        settings(), transport=httpx.MockTransport(handler)
    )
    invitation = asyncio.run(
        repository.create(
            TOKEN,
            workspace_id=WORKSPACE,
            email="member@example.test",
            token_hash="a" * 64,
            expires_at=EXPIRES,
        )
    )
    assert invitation.invitation_id == INVITATION
    workspace_id, role = asyncio.run(
        repository.accept(TOKEN, token_hash="b" * 64)
    )
    assert workspace_id == WORKSPACE and role == "member"
    assert calls == ["create_workspace_invitation", "accept_workspace_invitation"]


def test_repository_maps_forbidden_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "synthetic"})

    repository = SupabaseWorkspaceInvitationRepository(
        settings(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(InvitationForbidden):
        asyncio.run(repository.list(TOKEN, workspace_id=WORKSPACE))
