from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from evidence_service.app import create_app
from evidence_service.auth import AuthenticationRejected, Principal
from evidence_service.config import SupabaseSettings
from evidence_service.evidence_repository import EvidenceRecord
from evidence_service.tenant_context import WorkspaceMembership

ISSUER = "https://project-ref.supabase.co/auth/v1"
SUBJECT_A = UUID("10000000-0000-4000-8000-000000000001")
WORKSPACE_A = UUID("a1000000-0000-4000-8000-000000000001")
WORKSPACE_B = UUID("b3000000-0000-4000-8000-000000000003")
EVIDENCE_ID = UUID("e1000000-0000-4000-8000-000000000001")
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
        return Principal(ISSUER, SUBJECT_A)


class MembershipDirectory:
    def __init__(self) -> None:
        self.lookups: list[tuple[UUID, UUID, str]] = []

    async def list_memberships(self, principal: Principal, access_token: str):
        raise AssertionError("not used by evidence routes")

    async def get_membership(
        self, principal: Principal, access_token: str, workspace_id: UUID
    ) -> WorkspaceMembership | None:
        self.lookups.append((principal.subject, workspace_id, access_token))
        if principal.subject == SUBJECT_A and workspace_id == WORKSPACE_A:
            return WorkspaceMembership(WORKSPACE_A, "A workspace", "owner")
        return None


class EvidenceRepositorySpy:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.record = EvidenceRecord(
            id=EVIDENCE_ID,
            workspace_id=WORKSPACE_A,
            content="private evidence",
            source_label="source",
            created_at=datetime(2026, 10, 18, tzinfo=UTC),
            updated_at=datetime(2026, 10, 18, tzinfo=UTC),
        )

    async def list_records(self, access_token: str, workspace_id: UUID):
        self.calls.append(("list", access_token, workspace_id))
        return [self.record]

    async def get_record(
        self, access_token: str, workspace_id: UUID, evidence_id: UUID
    ):
        self.calls.append(("get", access_token, workspace_id, evidence_id))
        return self.record if evidence_id == EVIDENCE_ID else None

    async def create_record(
        self,
        access_token: str,
        workspace_id: UUID,
        *,
        content: str,
        source_label: str | None,
    ):
        self.calls.append(
            ("create", access_token, workspace_id, content, source_label)
        )
        self.record = replace(
            self.record,
            content=content,
            source_label=source_label,
            workspace_id=workspace_id,
        )
        return self.record

    async def update_record(
        self,
        access_token: str,
        workspace_id: UUID,
        evidence_id: UUID,
        changes,
    ):
        self.calls.append(("update", access_token, workspace_id, evidence_id, changes))
        if evidence_id != EVIDENCE_ID:
            return None
        self.record = replace(self.record, **changes)
        return self.record

    async def delete_record(
        self, access_token: str, workspace_id: UUID, evidence_id: UUID
    ) -> bool:
        self.calls.append(("delete", access_token, workspace_id, evidence_id))
        return evidence_id == EVIDENCE_ID


def make_client(directory: MembershipDirectory, repository: EvidenceRepositorySpy):
    app = create_app(
        settings(),
        token_verifier=StaticVerifier(),
        workspace_directory=directory,
        evidence_repository=repository,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def test_active_member_evidence_crud_uses_verified_membership_context() -> None:
    async def run() -> None:
        directory = MembershipDirectory()
        repository = EvidenceRepositorySpy()
        async with make_client(directory, repository) as client:
            headers = {"Authorization": f"Bearer {TOKEN}"}
            listed = await client.get(
                f"/v1/workspaces/{WORKSPACE_A}/evidence", headers=headers
            )
            created = await client.post(
                f"/v1/workspaces/{WORKSPACE_A}/evidence",
                headers=headers,
                json={"content": "new private evidence", "source_label": "test"},
            )
            fetched = await client.get(
                f"/v1/workspaces/{WORKSPACE_A}/evidence/{EVIDENCE_ID}",
                headers=headers,
            )
            updated = await client.patch(
                f"/v1/workspaces/{WORKSPACE_A}/evidence/{EVIDENCE_ID}",
                headers=headers,
                json={"source_label": "relabelled"},
            )
            deleted = await client.delete(
                f"/v1/workspaces/{WORKSPACE_A}/evidence/{EVIDENCE_ID}",
                headers=headers,
            )

        assert [listed.status_code, created.status_code, fetched.status_code,
                updated.status_code, deleted.status_code] == [200, 201, 200, 200, 204]
        assert listed.json()[0]["workspace_id"] == str(WORKSPACE_A)
        assert created.json()["content"] == "new private evidence"
        assert fetched.json()["id"] == str(EVIDENCE_ID)
        assert updated.json()["source_label"] == "relabelled"
        assert deleted.content == b""
        assert directory.lookups == [
            (SUBJECT_A, WORKSPACE_A, TOKEN),
            (SUBJECT_A, WORKSPACE_A, TOKEN),
            (SUBJECT_A, WORKSPACE_A, TOKEN),
            (SUBJECT_A, WORKSPACE_A, TOKEN),
            (SUBJECT_A, WORKSPACE_A, TOKEN),
        ]
        assert [call[0] for call in repository.calls] == [
            "list", "create", "get", "update", "delete"
        ]
        assert all(call[1] == TOKEN and call[2] == WORKSPACE_A for call in repository.calls)
        assert repository.calls[1] == (
            "create", TOKEN, WORKSPACE_A, "new private evidence", "test"
        )
        assert repository.calls[3][-1] == {"source_label": "relabelled"}

    asyncio.run(run())


@pytest.mark.parametrize(
    ("method", "path_suffix", "body"),
    [
        ("GET", "/evidence", None),
        ("POST", "/evidence", {"content": "forged"}),
        ("GET", f"/evidence/{EVIDENCE_ID}", None),
        ("PATCH", f"/evidence/{EVIDENCE_ID}", {"content": "forged"}),
        ("DELETE", f"/evidence/{EVIDENCE_ID}", None),
    ],
)
def test_nonmember_cannot_read_or_mutate_another_workspace(
    method: str, path_suffix: str, body: dict[str, str] | None
) -> None:
    async def run() -> None:
        directory = MembershipDirectory()
        repository = EvidenceRepositorySpy()
        async with make_client(directory, repository) as client:
            response = await client.request(
                method,
                f"/v1/workspaces/{WORKSPACE_B}{path_suffix}",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json=body,
            )
        assert response.status_code == 404
        assert response.json() == {"detail": "Workspace not found"}
        assert directory.lookups == [(SUBJECT_A, WORKSPACE_B, TOKEN)]
        assert repository.calls == []

    asyncio.run(run())


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_caller_cannot_supply_evidence_workspace_ownership(method: str) -> None:
    async def run() -> None:
        directory = MembershipDirectory()
        repository = EvidenceRepositorySpy()
        async with make_client(directory, repository) as client:
            path = f"/v1/workspaces/{WORKSPACE_A}/evidence"
            if method == "PATCH":
                path += f"/{EVIDENCE_ID}"
            response = await client.request(
                method,
                path,
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={
                    "content": "attempt to set owner",
                    "workspace_id": str(WORKSPACE_B),
                },
            )
        assert response.status_code == 422
        assert repository.calls == []

    asyncio.run(run())
