from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
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
from evidence_service.relationship_repository import EvidenceRelationship
from evidence_service.tenant_context import WorkspaceMembership

ISSUER = "https://project-ref.supabase.co/auth/v1"
SUBJECT = UUID("10000000-0000-4000-8000-000000000001")
OTHER_SUBJECT = UUID("20000000-0000-4000-8000-000000000002")
WORKSPACE_A = UUID("a1000000-0000-4000-8000-000000000001")
WORKSPACE_B = UUID("b3000000-0000-4000-8000-000000000003")
EVIDENCE_A = UUID("e1000000-0000-4000-8000-000000000001")
EVIDENCE_B = UUID("e2000000-0000-4000-8000-000000000002")
RELATION_ID = UUID("f1000000-0000-4000-8000-000000000001")
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
    def __init__(self) -> None:
        self.roles = {
            (SUBJECT, WORKSPACE_A): "owner",
            (OTHER_SUBJECT, WORKSPACE_A): "member",
        }

    async def list_memberships(self, principal: Principal, access_token: str):
        raise AssertionError("not used")

    async def get_membership(
        self, principal: Principal, access_token: str, workspace_id: UUID
    ) -> WorkspaceMembership | None:
        role = self.roles.get((principal.subject, workspace_id))
        if role is None:
            return None
        return WorkspaceMembership(workspace_id, "Workspace", role)


class RelationshipRepositorySpy:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.relationship = EvidenceRelationship(
            id=RELATION_ID,
            workspace_id=WORKSPACE_A,
            evidence_id_a=EVIDENCE_A,
            evidence_id_b=EVIDENCE_B,
            relation_type="contextualizes",
            rationale="The first record gives context to the second.",
            provenance_note="Manual comparison of both original records.",
            created_by=SUBJECT,
            created_at=datetime(2026, 11, 2, tzinfo=UTC),
            updated_by=SUBJECT,
            updated_at=datetime(2026, 11, 2, tzinfo=UTC),
        )

    async def list_relationships(self, access_token: str, workspace_id: UUID):
        self.calls.append(("list", access_token, workspace_id))
        return [self.relationship] if workspace_id == WORKSPACE_A else []

    async def create_relationship(
        self, access_token, workspace_id, *, evidence_id_a, evidence_id_b,
        relation_type, rationale, provenance_note,
    ):
        self.calls.append((
            "create", access_token, workspace_id, evidence_id_a, evidence_id_b,
            relation_type, rationale, provenance_note,
        ))
        self.relationship = replace(
            self.relationship,
            evidence_id_a=evidence_id_a,
            evidence_id_b=evidence_id_b,
            relation_type=relation_type,
            rationale=rationale,
            provenance_note=provenance_note,
        )
        return self.relationship

    async def update_relationship(self, access_token, workspace_id, relationship_id, changes):
        self.calls.append(("update", access_token, workspace_id, relationship_id, changes))
        if relationship_id != RELATION_ID:
            return None
        self.relationship = replace(self.relationship, **changes)
        return self.relationship

    async def delete_relationship(self, access_token, workspace_id, relationship_id):
        self.calls.append(("delete", access_token, workspace_id, relationship_id))
        return relationship_id == RELATION_ID


def make_client(directory: MembershipDirectory, repository: RelationshipRepositorySpy):
    app = create_app(
        settings(),
        token_verifier=StaticVerifier(),
        account_lifecycle_reader=AvailableAccountLifecycleReader(),
        recovery_fence_reader=OpenRecoveryFenceReader(),
        workspace_directory=directory,
        relationship_repository=repository,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def test_owner_can_create_update_and_delete_explicit_relationship() -> None:
    async def run() -> None:
        repository = RelationshipRepositorySpy()
        async with make_client(MembershipDirectory(), repository) as client:
            headers = {"Authorization": f"Bearer {TOKEN}"}
            created = await client.post(
                f"/v1/workspaces/{WORKSPACE_A}/relationships",
                headers=headers,
                json={
                    "evidence_id_a": str(EVIDENCE_B),
                    "evidence_id_b": str(EVIDENCE_A),
                    "relation_type": "contradicts",
                    "rationale": "The stored claims differ.",
                    "provenance_note": "Reviewed both source-linked records.",
                },
            )
            updated = await client.patch(
                f"/v1/workspaces/{WORKSPACE_A}/relationships/{RELATION_ID}",
                headers=headers,
                json={"rationale": "Updated after a second review."},
            )
            deleted = await client.delete(
                f"/v1/workspaces/{WORKSPACE_A}/relationships/{RELATION_ID}",
                headers=headers,
            )
        assert [created.status_code, updated.status_code, deleted.status_code] == [201, 200, 204]
        assert created.json()["evidence_id_a"] == str(EVIDENCE_A)
        assert created.json()["relation_type"] == "contradicts"
        assert updated.json()["rationale"] == "Updated after a second review."
        assert repository.calls[0][0] == "create"
        assert repository.calls[0][3:5] == (EVIDENCE_A, EVIDENCE_B)
        assert [call[0] for call in repository.calls] == ["create", "update", "delete"]

    asyncio.run(run())


def test_members_may_read_but_cannot_mutate_relationships() -> None:
    async def run() -> None:
        directory = MembershipDirectory()
        directory.roles[(SUBJECT, WORKSPACE_A)] = "member"
        repository = RelationshipRepositorySpy()
        async with make_client(directory, repository) as client:
            headers = {"Authorization": f"Bearer {TOKEN}"}
            listed = await client.get(
                f"/v1/workspaces/{WORKSPACE_A}/relationships", headers=headers
            )
            created = await client.post(
                f"/v1/workspaces/{WORKSPACE_A}/relationships",
                headers=headers,
                json={
                    "evidence_id_a": str(EVIDENCE_A),
                    "evidence_id_b": str(EVIDENCE_B),
                    "relation_type": "supports",
                    "rationale": "A reason.",
                    "provenance_note": "A documented basis.",
                },
            )
            updated = await client.patch(
                f"/v1/workspaces/{WORKSPACE_A}/relationships/{RELATION_ID}",
                headers=headers,
                json={"rationale": "Attempted unauthorized update."},
            )
            deleted = await client.delete(
                f"/v1/workspaces/{WORKSPACE_A}/relationships/{RELATION_ID}",
                headers=headers,
            )
        assert listed.status_code == 200 and len(listed.json()) == 1
        assert [created.status_code, updated.status_code, deleted.status_code] == [403, 403, 403]
        assert [call[0] for call in repository.calls] == ["list"]

    asyncio.run(run())


@pytest.mark.parametrize(
    ("method", "path_suffix", "body"),
    [
        ("GET", "", None),
        ("POST", "", {"evidence_id_a": str(EVIDENCE_A), "evidence_id_b": str(EVIDENCE_B),
                      "relation_type": "supports", "rationale": "reason", "provenance_note": "basis"}),
        ("PATCH", f"/{RELATION_ID}", {"rationale": "forged"}),
        ("DELETE", f"/{RELATION_ID}", None),
    ],
)
def test_nonmember_cannot_reach_relationship_repository(method, path_suffix, body) -> None:
    async def run() -> None:
        repository = RelationshipRepositorySpy()
        async with make_client(MembershipDirectory(), repository) as client:
            response = await client.request(
                method,
                f"/v1/workspaces/{WORKSPACE_B}/relationships{path_suffix}",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json=body,
            )
        assert response.status_code == 404
        assert repository.calls == []

    asyncio.run(run())


def test_invalid_relationship_payload_is_rejected_before_repository_call() -> None:
    async def run() -> None:
        repository = RelationshipRepositorySpy()
        async with make_client(MembershipDirectory(), repository) as client:
            response = await client.post(
                f"/v1/workspaces/{WORKSPACE_A}/relationships",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={
                    "evidence_id_a": str(EVIDENCE_A),
                    "evidence_id_b": str(EVIDENCE_A),
                    "relation_type": "invented",
                    "rationale": "not valid",
                    "provenance_note": "no basis",
                },
            )
        assert response.status_code == 422
        assert repository.calls == []

    asyncio.run(run())


def test_relationship_routes_require_verified_authentication() -> None:
    async def run() -> None:
        repository = RelationshipRepositorySpy()
        async with make_client(MembershipDirectory(), repository) as client:
            response = await client.get(f"/v1/workspaces/{WORKSPACE_A}/relationships")
        assert response.status_code == 401
        assert repository.calls == []

    asyncio.run(run())
