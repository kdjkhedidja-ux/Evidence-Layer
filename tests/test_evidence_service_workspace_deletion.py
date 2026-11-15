"""C3 shared-workspace deletion approval endpoints."""

from __future__ import annotations

import asyncio
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
from evidence_service.workspace_deletion_repository import (
    SupabaseWorkspaceDeletionRepository,
    WorkspaceDeletionForbidden,
)

ISSUER = "https://project-ref.supabase.co/auth/v1"
SUBJECT = UUID("10000000-0000-4000-8000-000000000001")
WORKSPACE = UUID("a1000000-0000-4000-8000-000000000001")
REQUEST = UUID("b1000000-0000-4000-8000-000000000001")
TOKEN = "verified.synthetic.jwt"


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


class FakeDeletion:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def request(self, access_token, *, workspace_id):
        self.calls.append(("request", str(workspace_id)))
        return {
            "request_id": str(REQUEST), "workspace_id": str(workspace_id),
            "status": "pending", "owner_count": 2, "approved_count": 1,
        }

    async def status(self, access_token, *, workspace_id):
        self.calls.append(("status", str(workspace_id)))
        return {
            "request_id": str(REQUEST), "workspace_id": str(workspace_id),
            "status": "pending", "owner_count": 2, "approved_count": 1,
            "owners": [{"user_id": str(SUBJECT), "approved": True}],
        }

    async def approve(self, access_token, *, request_id):
        self.calls.append(("approve", str(request_id)))
        return {"request_id": str(request_id), "status": "approved"}

    async def withdraw(self, access_token, *, request_id):
        self.calls.append(("withdraw", str(request_id)))
        return {"request_id": str(request_id), "status": "pending"}

    async def cancel(self, access_token, *, request_id):
        self.calls.append(("cancel", str(request_id)))
        return {"request_id": str(request_id), "status": "cancelled"}


def make_client(*, directory: MembershipDirectory, deletion: FakeDeletion):
    def factory(access_token: str):
        return deletion

    app = create_app(
        settings(),
        token_verifier=StaticVerifier(),
        account_lifecycle_reader=AvailableAccountLifecycleReader(),
        recovery_fence_reader=OpenRecoveryFenceReader(),
        workspace_directory=directory,
        workspace_deletion_factory=factory,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def request(client, method, path, token=TOKEN):
    async def run():
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with client:
            return await client.request(method, path, headers=headers)

    return asyncio.run(run())


def test_owner_runs_the_full_approval_workflow():
    deletion = FakeDeletion()
    base = f"/v1/workspaces/{WORKSPACE}/deletion-request"
    for method, path, expected in (
        ("POST", base, "pending"),
        ("GET", base, "pending"),
        ("POST", f"{base}/{REQUEST}/approve", "approved"),
        ("POST", f"{base}/{REQUEST}/withdraw", "pending"),
        ("POST", f"{base}/{REQUEST}/cancel", "cancelled"),
    ):
        client = make_client(
            directory=MembershipDirectory("owner"), deletion=deletion
        )
        response = request(client, method, path)
        assert response.status_code == 200
        assert response.json()["status"] == expected
    assert [call[0] for call in deletion.calls] == [
        "request", "status", "approve", "withdraw", "cancel"
    ]


@pytest.mark.parametrize("role", ["member", "admin"])
def test_non_owner_cannot_manage_workspace_deletion(role: str):
    deletion = FakeDeletion()
    client = make_client(directory=MembershipDirectory(role), deletion=deletion)
    response = request(
        client, "POST", f"/v1/workspaces/{WORKSPACE}/deletion-request"
    )
    assert response.status_code == 403
    assert deletion.calls == []


def test_workspace_deletion_requires_authentication():
    deletion = FakeDeletion()
    client = make_client(directory=MembershipDirectory("owner"), deletion=deletion)
    response = request(
        client, "GET", f"/v1/workspaces/{WORKSPACE}/deletion-request", token=None
    )
    assert response.status_code == 401


def test_repository_maps_forbidden_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "synthetic"})

    repository = SupabaseWorkspaceDeletionRepository(
        settings(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(WorkspaceDeletionForbidden):
        asyncio.run(repository.status(TOKEN, workspace_id=WORKSPACE))
