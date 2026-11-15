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
from evidence_service.evidence_repository import EvidenceRecord
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
        self.relationships = [self.relationship]

    async def list_relationships(self, access_token: str, workspace_id: UUID):
        self.calls.append(("list", access_token, workspace_id))
        return self.relationships if workspace_id == WORKSPACE_A else []

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


class TaskContextEvidenceRepository:
    def __init__(self) -> None:
        now = datetime(2026, 11, 2, tzinfo=UTC)
        self.records = [
            EvidenceRecord(
                id=EVIDENCE_A,
                workspace_id=WORKSPACE_A,
                content="Evidence Layer release process and deployment checks.",
                source_label="release.md",
                created_at=now,
                updated_at=now,
            ),
            EvidenceRecord(
                id=EVIDENCE_B,
                workspace_id=WORKSPACE_A,
                content="Emergency procedures for reverting a deployment.",
                source_label="rollback.md",
                created_at=now,
                updated_at=now,
            ),
            EvidenceRecord(
                id=UUID("e3000000-0000-4000-8000-000000000003"),
                workspace_id=WORKSPACE_A,
                content="A second-hop record must not be expanded.",
                source_label="second-hop.md",
                created_at=now,
                updated_at=now,
            ),
        ]

    async def list_records(self, access_token: str, workspace_id: UUID):
        assert access_token == TOKEN
        return [record for record in self.records if record.workspace_id == workspace_id]


def make_client(
    directory: MembershipDirectory,
    repository: RelationshipRepositorySpy,
    *,
    evidence_repository=None,
    retrieval_enabled: bool = False,
    task_run_ledger_repository=None,
    task_ledger_enabled: bool = False,
):
    app = create_app(
        settings(),
        token_verifier=StaticVerifier(),
        account_lifecycle_reader=AvailableAccountLifecycleReader(),
        recovery_fence_reader=OpenRecoveryFenceReader(),
        workspace_directory=directory,
        relationship_repository=repository,
        evidence_repository=evidence_repository,
        retrieval_enabled=retrieval_enabled,
        task_run_ledger_repository=task_run_ledger_repository,
        task_ledger_enabled=task_ledger_enabled,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


class TaskRunLedgerSpy:
    def __init__(self) -> None:
        self.records: list[dict] = []
        self.listed: list[dict] = []

    async def record(self, access_token: str, *, workspace_id, actor_user_id, metrics):
        self.records.append(
            {
                "access_token": access_token,
                "workspace_id": workspace_id,
                "actor_user_id": actor_user_id,
                "metrics": metrics,
            }
        )

    async def list_runs(self, access_token: str, *, workspace_id, limit: int = 100):
        self.listed.append({"access_token": access_token, "workspace_id": workspace_id, "limit": limit})
        return [
            {
                "run_id": "91000000-0000-4000-8000-000000000001",
                "workspace_id": str(WORKSPACE_A),
                "run_type": "task_context",
                "status": "ok",
                "created_at": "2026-11-14T20:00:00Z",
                "retrieval_latency_ms": 21,
                "retrieval_tokens": 120,
                "retrieval_chars": 480,
                "selected_count": 2,
                "chunk_count": 3,
                "relationship_count": 1,
                "scoring_provider": "jev",
                "fallback_reason": None,
                "use_jev": True,
                "agent_provider": None,
                "agent_model": None,
                "agent_input_tokens": None,
                "agent_output_tokens": None,
                "agent_total_tokens": None,
                "agent_cost_usd": None,
                "agent_latency_ms": None,
                "quality_status": None,
                "quality_score": None,
            }
        ]


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


def test_task_context_retrieval_uses_expected_outcome_and_explicit_links() -> None:
    async def run() -> None:
        relationship_repository = RelationshipRepositorySpy()
        second_hop_id = UUID("e3000000-0000-4000-8000-000000000003")
        relationship_repository.relationships.append(
            replace(
                relationship_repository.relationship,
                id=UUID("f2000000-0000-4000-8000-000000000002"),
                evidence_id_a=EVIDENCE_B,
                evidence_id_b=second_hop_id,
                relation_type="supports",
            )
        )
        evidence_repository = TaskContextEvidenceRepository()
        async with make_client(
            MembershipDirectory(),
            relationship_repository,
            evidence_repository=evidence_repository,
            retrieval_enabled=True,
        ) as client:
            response = await client.post(
                f"/v1/workspaces/{WORKSPACE_A}/task-context",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={
                    "task": "review Evidence Layer release",
                    "expected_outcome": "identify mitigation strategies",
                    "context_requirements": "focus operational controls",
                    "top_k": 2,
                    "max_tokens": 400,
                    "max_chars": 1600,
                },
            )
        assert response.status_code == 200
        body = response.json()
        assert body["task"] == "review Evidence Layer release"
        assert body["expected_outcome"] == "identify mitigation strategies"
        assert "focus operational controls" in body["retrieval"]["query"]
        selected_ids = {item["evidence_id"] for item in body["retrieval"]["evidence"]}
        assert selected_ids == {str(EVIDENCE_A), str(EVIDENCE_B)}
        assert body["retrieval"]["matched_chunk_count"] == 1
        assert body["relationships"] == [
            {
                "id": str(RELATION_ID),
                "evidence_id_a": str(EVIDENCE_A),
                "evidence_id_b": str(EVIDENCE_B),
                "relation_type": "contextualizes",
                "rationale": "The first record gives context to the second.",
                "provenance_note": "Manual comparison of both original records.",
            }
        ]

    asyncio.run(run())


def test_task_context_caps_relationship_expansion_at_three_records() -> None:
    async def run() -> None:
        relationship_repository = RelationshipRepositorySpy()
        evidence_repository = TaskContextEvidenceRepository()
        direct_ids = [
            UUID("e2000000-0000-4000-8000-000000000002"),
            UUID("e3000000-0000-4000-8000-000000000003"),
            UUID("e4000000-0000-4000-8000-000000000004"),
            UUID("e5000000-0000-4000-8000-000000000005"),
        ]
        now = datetime(2026, 11, 2, tzinfo=UTC)
        evidence_repository.records.extend(
            EvidenceRecord(
                id=record_id,
                workspace_id=WORKSPACE_A,
                content=f"Unmatched context {index} for the incident.",
                source_label=f"context-{index}.md",
                created_at=now,
                updated_at=now,
            )
            for index, record_id in enumerate(direct_ids[1:], start=2)
        )
        relationship_repository.relationships = [
            replace(
                relationship_repository.relationship,
                id=UUID(f"f{index:03d}0000-0000-4000-8000-00000000000{index}"),
                evidence_id_a=EVIDENCE_A,
                evidence_id_b=record_id,
            )
            for index, record_id in enumerate(direct_ids, start=1)
        ]
        async with make_client(
            MembershipDirectory(),
            relationship_repository,
            evidence_repository=evidence_repository,
            retrieval_enabled=True,
        ) as client:
            response = await client.post(
                f"/v1/workspaces/{WORKSPACE_A}/task-context",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={
                    "task": "review Evidence Layer release",
                    "expected_outcome": "identify mitigation strategies",
                    "context_requirements": "focus operational controls",
                    "top_k": 5,
                    "max_tokens": 400,
                    "max_chars": 1600,
                },
            )
        assert response.status_code == 200
        body = response.json()
        selected_ids = {item["evidence_id"] for item in body["retrieval"]["evidence"]}
        assert selected_ids == {str(EVIDENCE_A), *(str(item) for item in direct_ids[:3])}
        assert len(body["relationships"]) == 3

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


def test_task_context_writes_and_lists_server_ledger_without_content() -> None:
    async def run() -> None:
        ledger = TaskRunLedgerSpy()
        async with make_client(
            MembershipDirectory(),
            RelationshipRepositorySpy(),
            evidence_repository=TaskContextEvidenceRepository(),
            retrieval_enabled=True,
            task_run_ledger_repository=ledger,
            task_ledger_enabled=True,
        ) as client:
            headers = {"Authorization": f"Bearer {TOKEN}"}
            response = await client.post(
                f"/v1/workspaces/{WORKSPACE_A}/task-context",
                headers=headers,
                json={
                    "task": "review Evidence Layer release",
                    "expected_outcome": "identify mitigation strategies",
                    "top_k": 2,
                    "max_tokens": 400,
                    "max_chars": 1600,
                },
            )
            listed = await client.get(f"/v1/workspaces/{WORKSPACE_A}/task-runs", headers=headers)
        assert response.status_code == 200
        assert listed.status_code == 200
        assert len(ledger.records) == 1
        record = ledger.records[0]
        assert record["workspace_id"] == WORKSPACE_A
        assert record["actor_user_id"] == SUBJECT
        assert record["metrics"]["run_type"] == "task_context"
        assert record["metrics"]["selected_count"] == 2
        assert record["metrics"]["use_jev"] is True
        assert "task" not in record["metrics"]
        assert listed.json()[0]["run_id"] == "91000000-0000-4000-8000-000000000001"
        assert listed.json()[0]["scoring_provider"] == "jev"

    asyncio.run(run())
