from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
from collections import deque
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from evidence_service.account_lifecycle import (
    AccountLifecycleReadError,
    SupabaseAccountLifecycleReader,
)
from evidence_service.app import (
    create_app,
    require_authenticated_request,
    require_control_request,
    require_personal_api_key,
)
from evidence_service.auth import AuthenticationRejected, Principal
from evidence_service.config import SupabaseSettings
from evidence_service.deletion_repository import DeletionRequest
from evidence_service.recovery_fence import (
    CloudflareRecoveryFenceReader,
    RecoveryFenceSettings,
)
from evidence_service.tenant_context import AccountProfile, WorkspaceMembership
from account_lifecycle_test_support import OpenRecoveryFenceReader

ISSUER = "https://project-ref.supabase.co/auth/v1"
SUBJECT = UUID("10000000-0000-4000-8000-000000000001")
WORKSPACE = UUID("a1000000-0000-4000-8000-000000000001")
EVIDENCE_A = UUID("e1000000-0000-4000-8000-000000000001")
EVIDENCE_B = UUID("e2000000-0000-4000-8000-000000000002")
EVIDENCE_ID = UUID("e3000000-0000-4000-8000-000000000003")
RELATION_ID = UUID("f1000000-0000-4000-8000-000000000001")
SOURCE = UUID("c1000000-0000-4000-8000-000000000001")
VERSION = UUID("d1000000-0000-4000-8000-000000000001")
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


class ScriptedLifecycleReader:
    def __init__(self, state: str | None = "available", sequence=(), events=None) -> None:
        self.state = state
        self.sequence = deque(sequence)
        self.calls: list[tuple[UUID, str]] = []
        self.events = events

    async def read_state(self, subject_id: UUID, access_token: str) -> str | None:
        self.calls.append((subject_id, access_token))
        if self.events is not None:
            self.events.append("lifecycle")
        value = self.sequence.popleft() if self.sequence else self.state
        if isinstance(value, Exception):
            raise value
        return value


class DirectorySpy:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def list_memberships(self, principal, access_token):
        self.calls.append("list_memberships")
        return []

    async def ensure_personal_workspace(self, principal, access_token):
        self.calls.append("ensure_personal_workspace")
        return WorkspaceMembership(WORKSPACE, "Test workspace", "owner")

    async def get_membership(self, principal, access_token, workspace_id):
        self.calls.append("get_membership")
        if workspace_id != WORKSPACE:
            return None
        return WorkspaceMembership(WORKSPACE, "Test workspace", "owner")

    async def create_organization_account(self, principal, access_token, name):
        self.calls.append("create_organization_account")
        return AccountProfile(account_type="organization", organization_name=name)


class OperationSpy:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def list_records(self, access_token, workspace_id):
        self.calls.append("list_records")
        return []

    def __getattr__(self, name: str):
        async def call(*args, **kwargs):
            self.calls.append(name)
            return None

        return call


class CatalogSpy:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def list_visible_versions(self, workspace_id):
        self.calls.append("list_visible_versions")
        return []

    async def resolve_version(self, workspace_id, source_id, version_id):
        self.calls.append("resolve_version")
        return None


class ReadPathSpy:
    def __init__(self) -> None:
        self.calls = 0

    async def read(self, authenticated_request, *, workspace_id, source_id, version_id):
        self.calls += 1
        manifest = SimpleNamespace(sha256="a" * 64)
        return SimpleNamespace(content=b"protected snapshot bytes", manifest=manifest)


class DeletionRepositorySpy:
    def __init__(self, request: DeletionRequest) -> None:
        self.request = request
        self.calls: list[tuple[str, UUID]] = []

    async def create_request(self, access_token, *, principal_id, idempotency_key):
        self.calls.append(("create", principal_id))
        return self.request

    async def accept_request(self, access_token, *, request_id):
        self.calls.append(("accept", request_id))
        return self.request

    async def get_request(self, access_token, *, principal_id, request_id):
        self.calls.append(("get", request_id))
        if principal_id != self.request.requester_user_id or request_id != self.request.request_id:
            return None
        return self.request

    async def get_current_request(self, access_token, *, principal_id):
        self.calls.append(("current", self.request.request_id))
        if principal_id != self.request.requester_user_id:
            return None
        return self.request

    async def delete_personal_workspace_data(self, access_token):
        self.calls.append(("delete_data", self.request.request_id))
        return {
            "workspace_id": str(self.request.request_id),
            "evidence_deleted": 1,
            "relationships_deleted": 1,
            "source_grants_deleted": 1,
            "snapshots_deleted": 1,
            "storage_cleanup_queued": 1,
            "storage_cleanup_required": True,
        }

    async def delete_personal_workspace(self, access_token):
        self.calls.append(("delete_workspace", self.request.request_id))
        return {
            "workspace_id": str(self.request.request_id),
            "evidence_deleted": 1,
            "relationships_deleted": 1,
            "source_grants_deleted": 1,
            "snapshots_deleted": 1,
            "storage_cleanup_queued": 1,
            "storage_cleanup_required": True,
            "workspace_deleted": True,
        }


class ScriptedRecoveryFenceReader:
    def __init__(self, sequence=(), *, result=True, events=None) -> None:
        self.sequence = deque(sequence)
        self.result = result
        self.events = events
        self.calls = 0

    async def is_open(self) -> bool | None:
        self.calls += 1
        if self.events is not None:
            self.events.append("recovery")
        value = self.sequence.popleft() if self.sequence else self.result
        if isinstance(value, Exception):
            raise value
        return value


def make_app(
    reader,
    *,
    directory=None,
    repository=None,
    catalog=None,
    read_path=None,
    recovery_fence_reader=None,
    deletion_repository=None,
):
    directory = directory or DirectorySpy()
    repository = repository or OperationSpy()
    catalog = catalog or CatalogSpy()
    read_path = read_path or ReadPathSpy()
    app = create_app(
        settings(),
        token_verifier=StaticVerifier(),
        account_lifecycle_reader=reader,
        recovery_fence_reader=recovery_fence_reader or OpenRecoveryFenceReader(),
        workspace_directory=directory,
        evidence_repository=repository,
        relationship_repository=repository,
        deletion_repository=deletion_repository,
        source_catalog_factory=lambda _token: catalog,
        source_read_path=read_path,
    )
    return app, directory, repository, catalog, read_path


def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def deletion_request(status: str = "approval_pending") -> DeletionRequest:
    from datetime import datetime, timezone

    return DeletionRequest(
        request_id=UUID("71000000-0000-4000-8000-000000000001"),
        requester_user_id=SUBJECT,
        scope_type="account",
        status=status,
        created_at=datetime(2026, 11, 5, tzinfo=timezone.utc),
        accepted_at=(datetime(2026, 11, 5, tzinfo=timezone.utc) if status != "approval_pending" else None),
    )


def test_recovery_fence_runs_before_lifecycle_and_allows_only_explicit_open() -> None:
    events: list[str] = []
    reader = ScriptedLifecycleReader(events=events)
    fence = ScriptedRecoveryFenceReader(events=events)
    app, directory, _, _, _ = make_app(
        reader, recovery_fence_reader=fence
    )

    with TestClient(app) as client:
        response = client.get("/v1/me", headers=auth_headers())

    assert response.status_code == 200
    assert events[:2] == ["recovery", "lifecycle"]
    assert events[2:] == ["recovery", "lifecycle"]
    assert directory.calls == ["list_memberships"]


@pytest.mark.parametrize(
    ("result", "error"),
    [
        (False, None),
        (None, None),
        (True, RuntimeError("fence unavailable")),
    ],
)
def test_unsafe_unknown_or_failed_recovery_fence_denies_before_lifecycle(
    result, error
) -> None:
    lifecycle = ScriptedLifecycleReader()
    fence = OpenRecoveryFenceReader(result=result, error=error)
    app, directory, repository, catalog, read_path = make_app(
        lifecycle, recovery_fence_reader=fence
    )

    with TestClient(app) as client:
        response = client.get("/v1/me", headers=auth_headers())

    assert response.status_code == 503
    assert response.json() == {"detail": "Service is unavailable"}
    assert fence.calls == 1
    assert lifecycle.calls == []
    assert directory.calls == []
    assert repository.calls == []
    assert catalog.calls == []
    assert read_path.calls == 0


def test_missing_recovery_fence_denies_before_lifecycle_or_repository() -> None:
    lifecycle = ScriptedLifecycleReader()
    directory = DirectorySpy()
    repository = OperationSpy()
    app = create_app(
        settings(),
        token_verifier=StaticVerifier(),
        account_lifecycle_reader=lifecycle,
        workspace_directory=directory,
        evidence_repository=repository,
        relationship_repository=repository,
    )

    with TestClient(app) as client:
        response = client.get("/v1/me", headers=auth_headers())

    assert response.status_code == 503
    assert response.json() == {"detail": "Service is unavailable"}
    assert lifecycle.calls == []
    assert directory.calls == []
    assert repository.calls == []


def test_recovery_fence_change_at_repository_boundary_blocks_access() -> None:
    lifecycle = ScriptedLifecycleReader()
    fence = ScriptedRecoveryFenceReader(sequence=[True, False])
    app, directory, repository, _, _ = make_app(
        lifecycle, recovery_fence_reader=fence
    )

    with TestClient(app) as client:
        response = client.get("/v1/me", headers=auth_headers())

    assert response.status_code == 503
    assert fence.calls == 2
    assert len(lifecycle.calls) == 1
    assert directory.calls == []
    assert repository.calls == []


@pytest.mark.parametrize(
    ("state", "expected_status"),
    [
        ("available", 200),
        ("revocation_pending", 403),
        ("revoked", 403),
        ("purge_pending", 403),
        ("purge_failed", 403),
        ("purged", 403),
        (None, 403),
        ("future_unknown_state", 403),
    ],
)
def test_each_lifecycle_state_is_checked_at_the_http_request_boundary(
    state: str | None, expected_status: int
) -> None:
    reader = ScriptedLifecycleReader(state=state)
    app, directory, _, _, _ = make_app(reader)

    with TestClient(app) as client:
        response = client.get("/v1/me", headers=auth_headers())

    assert response.status_code == expected_status
    assert reader.calls
    if state != "available":
        assert response.json() == {"detail": "Account unavailable"}
        assert directory.calls == []


def test_lifecycle_read_failure_fails_closed_before_any_repository_call() -> None:
    reader = ScriptedLifecycleReader(sequence=[RuntimeError("database offline")])
    app, directory, repository, _, _ = make_app(reader)

    with TestClient(app) as client:
        response = client.get("/v1/me", headers=auth_headers())

    assert response.status_code == 503
    assert response.json() == {"detail": "Account status is unavailable"}
    assert directory.calls == []
    assert repository.calls == []


def test_status_is_not_cached_between_requests() -> None:
    reader = ScriptedLifecycleReader(state="available")
    app, directory, _, _, _ = make_app(reader)

    with TestClient(app) as client:
        first = client.get("/v1/me", headers=auth_headers())
        reader.state = "revoked"
        second = client.get("/v1/me", headers=auth_headers())

    assert first.status_code == 200
    assert second.status_code == 403
    assert len(reader.calls) == 3  # two reads for the first request, one for the second
    assert directory.calls == ["list_memberships"]


def test_pending_account_denies_evidence_route_before_repository_access() -> None:
    # One request reads lifecycle three times: entry dependency, workspace
    # resolution, and the operation gate. The next request is blocked at entry.
    reader = ScriptedLifecycleReader(
        sequence=["available", "available", "available", "revocation_pending"]
    )
    repository = OperationSpy()
    app, directory, _, catalog, read_path = make_app(reader, repository=repository)

    with TestClient(app) as client:
        first = client.get(f"/v1/workspaces/{WORKSPACE}/evidence", headers=auth_headers())
        second = client.get(f"/v1/workspaces/{WORKSPACE}/evidence", headers=auth_headers())

    assert first.status_code == 200
    assert second.status_code == 403
    assert directory.calls == ["get_membership"]
    # Only the allowed request reached the repository; the blocked one stopped at the gate.
    assert repository.calls == ["list_records"]
    assert catalog.calls == []
    assert read_path.calls == 0


def test_deletion_request_create_accept_and_status_replay_are_principal_scoped() -> None:
    repository = DeletionRepositorySpy(deletion_request("journal_ack_pending"))
    app, _, _, _, _ = make_app(
        ScriptedLifecycleReader(),
        deletion_repository=repository,
    )

    with TestClient(app) as client:
        created = client.post(
            "/v1/deletion-requests",
            headers=auth_headers(),
            json={"idempotency_key": "synthetic-request-1"},
        )
        accepted = client.post(
            f"/v1/deletion-requests/{repository.request.request_id}/accept",
            headers=auth_headers(),
        )
        status = client.get(
            f"/v1/deletion-requests/{repository.request.request_id}",
            headers=auth_headers(),
        )

    assert created.status_code == 201
    assert accepted.status_code == 200
    assert status.status_code == 200
    assert [kind for kind, _ in repository.calls] == ["create", "accept", "get"]


def test_deletion_request_status_does_not_bypass_pending_data_gate() -> None:
    repository = DeletionRepositorySpy(deletion_request("journal_ack_pending"))
    app, directory, data_repository, catalog, read_path = make_app(
        ScriptedLifecycleReader(state="revocation_pending"),
        deletion_repository=repository,
    )

    with TestClient(app) as client:
        status = client.get(
            f"/v1/deletion-requests/{repository.request.request_id}",
            headers=auth_headers(),
        )
        evidence = client.get(
            f"/v1/workspaces/{WORKSPACE}/evidence",
            headers=auth_headers(),
        )

    assert status.status_code == 200
    assert evidence.status_code == 403
    assert directory.calls == []
    assert data_repository.calls == []
    assert catalog.calls == []
    assert read_path.calls == 0


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/v1/me", None),
        ("PUT", "/v1/me/personal-workspace", None),
        ("GET", f"/v1/workspaces/{WORKSPACE}", None),
        ("GET", f"/v1/workspaces/{WORKSPACE}/sources", None),
        ("GET", f"/v1/workspaces/{WORKSPACE}/evidence", None),
        ("POST", f"/v1/workspaces/{WORKSPACE}/evidence", {"content": "x"}),
        ("GET", f"/v1/workspaces/{WORKSPACE}/evidence/{EVIDENCE_ID}", None),
        (
            "PATCH",
            f"/v1/workspaces/{WORKSPACE}/evidence/{EVIDENCE_ID}",
            {"source_label": "updated"},
        ),
        ("DELETE", f"/v1/workspaces/{WORKSPACE}/evidence/{EVIDENCE_ID}", None),
        ("GET", f"/v1/workspaces/{WORKSPACE}/relationships", None),
        (
            "POST",
            f"/v1/workspaces/{WORKSPACE}/relationships",
            {
                "evidence_id_a": str(EVIDENCE_A),
                "evidence_id_b": str(EVIDENCE_B),
                "relation_type": "supports",
                "rationale": "synthetic rationale",
                "provenance_note": "synthetic note",
            },
        ),
        (
            "PATCH",
            f"/v1/workspaces/{WORKSPACE}/relationships/{RELATION_ID}",
            {"rationale": "updated synthetic rationale"},
        ),
        ("DELETE", f"/v1/workspaces/{WORKSPACE}/relationships/{RELATION_ID}", None),
        (
            "GET",
            f"/v1/workspaces/{WORKSPACE}/sources/{SOURCE}/versions/{VERSION}/content",
            None,
        ),
    ],
)
def test_all_v1_routes_deny_before_repository_catalog_or_storage(
    method: str, path: str, body: dict | None
) -> None:
    reader = ScriptedLifecycleReader(state="revocation_pending")
    app, directory, repository, catalog, read_path = make_app(reader)

    with TestClient(app) as client:
        response = client.request(method, path, headers=auth_headers(), json=body)

    assert response.status_code == 403
    assert directory.calls == []
    assert repository.calls == []
    assert catalog.calls == []
    assert read_path.calls == 0


def test_worker_http_failure_at_real_request_boundary_blocks_protected_access() -> None:
    requests: list[httpx.Request] = []

    def worker_response(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            nonce = request.headers["x-recovery-nonce"]
            return httpx.Response(
                200,
                json={
                    "state": "open",
                    "generation": 1,
                    "sequence": 7,
                    "state_version": 7,
                    "initialized": True,
                    "verified": True,
                    "request_nonce": nonce,
                    "checkpoint_id": "synthetic-checkpoint",
                },
            )
        return httpx.Response(503, json={"error": "recovery unavailable"})

    worker_client = httpx.AsyncClient(transport=httpx.MockTransport(worker_response))
    recovery_reader = CloudflareRecoveryFenceReader(
        RecoveryFenceSettings(
            url="http://127.0.0.1:8787",
            key_id="reader-test",
            hmac_key=b"synthetic-reader-secret-that-is-at-least-32-bytes",
        ),
        client=worker_client,
    )
    lifecycle = ScriptedLifecycleReader()
    app, directory, repository, catalog, read_path = make_app(
        lifecycle, recovery_fence_reader=recovery_reader
    )

    with TestClient(app) as client:
        response = client.get("/v1/me", headers=auth_headers())
    asyncio.run(worker_client.aclose())

    assert response.status_code == 503
    assert response.json() == {"detail": "Service is unavailable"}
    # One open read, then a persistent 503 at the repository boundary: the
    # reader retries that transient failure once (fresh nonce) and still
    # fails closed.
    assert len(requests) == 3
    nonces = {request.headers["x-recovery-nonce"] for request in requests}
    signatures = {request.headers["x-recovery-signature"] for request in requests}
    assert len(nonces) == 3
    assert len(signatures) == 3
    assert len(lifecycle.calls) == 1
    assert directory.calls == []
    assert repository.calls == []
    assert catalog.calls == []
    assert read_path.calls == 0


def test_worker_http_reader_does_not_cache_open_between_requests() -> None:
    calls: list[httpx.Request] = []
    statuses = deque(["open", "closed"])

    def worker_response(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        nonce = request.headers["x-recovery-nonce"]
        return httpx.Response(
            200,
            json={
                "state": statuses.popleft(),
                "generation": 3,
                "sequence": len(calls),
                "state_version": len(calls),
                "initialized": True,
                "verified": True,
                "request_nonce": nonce,
                "checkpoint_id": f"checkpoint-{len(calls)}",
            },
        )

    worker_client = httpx.AsyncClient(transport=httpx.MockTransport(worker_response))
    reader = CloudflareRecoveryFenceReader(
        RecoveryFenceSettings(
            url="http://127.0.0.1:8787",
            key_id="reader-test",
            hmac_key=b"synthetic-reader-secret-that-is-at-least-32-bytes",
        ),
        client=worker_client,
    )

    async def verify_two_fresh_reads() -> tuple[bool, bool]:
        first = await reader.is_open()
        second = await reader.is_open()
        await worker_client.aclose()
        return first, second

    assert asyncio.run(verify_two_fresh_reads()) == (True, False)
    assert len(calls) == 2
    assert calls[0].headers["x-recovery-nonce"] != calls[1].headers["x-recovery-nonce"]

    for request in calls:
        timestamp = request.headers["x-recovery-timestamp"]
        nonce = request.headers["x-recovery-nonce"]
        payload = (
            f"GET\nhttp://127.0.0.1:8787\n/v1/recovery\nreader-test\n{timestamp}\n{nonce}"
        ).encode()
        expected = base64.urlsafe_b64encode(
            hmac.new(
                b"synthetic-reader-secret-that-is-at-least-32-bytes",
                payload,
                hashlib.sha256,
            ).digest()
        ).decode().rstrip("=")
        assert request.headers["x-recovery-signature"] == expected


def test_new_v1_routes_must_bind_the_lifecycle_dependency_and_healthz_is_public() -> None:
    routes = [route for route in make_app(ScriptedLifecycleReader())[0].routes if isinstance(route, APIRoute)]
    protected = [route for route in routes if route.path.startswith("/v1/")]
    public = [route for route in routes if not route.path.startswith("/v1/")]

    def has_auth_dependency(dependant) -> bool:
        return any(
            dependency.call in {require_authenticated_request, require_control_request, require_personal_api_key}
            or has_auth_dependency(dependency)
            for dependency in dependant.dependencies
        )

    # 28 data routes, 4 deletion control routes (create/accept/status/current),
    # 2 personal deletion routes, the organization account route, 6
    # organization management routes, 3 personal API key management routes,
    # 2 personal API key read routes, and 3 narrowed export routes. All require
    # a verified credential; the read routes use the dedicated personal-key
    # dependency plus the DB gate.
    assert len(protected) == 58
    assert all(has_auth_dependency(route.dependant) for route in protected)
    assert [(route.path, route.methods) for route in public] == [
        ("/healthz", {"GET"})
    ]


def test_database_failure_between_initial_gate_and_data_operation_blocks_access() -> None:
    reader = ScriptedLifecycleReader(
        sequence=["available", RuntimeError("database offline")]
    )
    app, directory, repository, _, _ = make_app(reader)

    with TestClient(app) as client:
        response = client.get("/v1/me", headers=auth_headers())

    assert response.status_code == 503
    assert response.json() == {"detail": "Account status is unavailable"}
    assert directory.calls == []
    assert repository.calls == []


def test_state_change_after_initial_gate_blocks_workspace_repository_access() -> None:
    reader = ScriptedLifecycleReader(sequence=["available", "revocation_pending"])
    app, directory, repository, _, _ = make_app(reader)

    with TestClient(app) as client:
        response = client.get("/v1/me", headers=auth_headers())

    assert response.status_code == 403
    assert response.json() == {"detail": "Account unavailable"}
    assert directory.calls == []
    assert repository.calls == []


def test_database_failure_at_catalog_boundary_does_not_construct_catalog() -> None:
    reader = ScriptedLifecycleReader(
        sequence=["available", "available", RuntimeError("database offline")]
    )
    directory = DirectorySpy()
    repository = OperationSpy()
    catalog_calls: list[str] = []

    def catalog_factory(_token):
        catalog_calls.append("created")
        return CatalogSpy()

    app = create_app(
        settings(),
        token_verifier=StaticVerifier(),
        account_lifecycle_reader=reader,
        recovery_fence_reader=OpenRecoveryFenceReader(),
        workspace_directory=directory,
        evidence_repository=repository,
        relationship_repository=repository,
        source_catalog_factory=catalog_factory,
    )

    with TestClient(app) as client:
        response = client.get(
            f"/v1/workspaces/{WORKSPACE}/sources", headers=auth_headers()
        )

    assert response.status_code == 503
    assert directory.calls == ["get_membership"]
    assert repository.calls == []
    assert catalog_calls == []


def test_database_failure_at_storage_boundary_prevents_snapshot_read() -> None:
    reader = ScriptedLifecycleReader(
        sequence=["available", "available", RuntimeError("database offline")]
    )
    app, directory, repository, _, read_path = make_app(reader)

    with TestClient(app) as client:
        response = client.get(
            f"/v1/workspaces/{WORKSPACE}/sources/{SOURCE}/versions/{VERSION}/content",
            headers=auth_headers(),
        )

    assert response.status_code == 503
    assert directory.calls == ["get_membership"]
    assert repository.calls == []
    assert read_path.calls == 0


def test_final_snapshot_gate_withholds_body_and_digest() -> None:
    reader = ScriptedLifecycleReader(
        sequence=["available", "available", "available", "revoked"]
    )
    app, _, _, _, read_path = make_app(reader)

    with TestClient(app) as client:
        response = client.get(
            f"/v1/workspaces/{WORKSPACE}/sources/{SOURCE}/versions/{VERSION}/content",
            headers=auth_headers(),
        )

    assert response.status_code == 403
    assert response.content == b'{"detail":"Account unavailable"}'
    assert "content-digest" not in response.headers
    assert read_path.calls == 1


def test_supabase_reader_uses_one_primary_user_scoped_request_per_read() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, json=[{"state": "available"}])

    reader = SupabaseAccountLifecycleReader(
        settings(), transport=httpx.MockTransport(handler)
    )

    async def run() -> None:
        assert await reader.read_state(SUBJECT, TOKEN) == "available"
        assert await reader.read_state(SUBJECT, TOKEN) == "available"

    asyncio.run(run())

    assert len(observed) == 2
    for request in observed:
        assert request.url.host == "project-ref.supabase.co"
        assert request.url.path == "/rest/v1/account_lifecycle"
        assert request.url.params["select"] == "state"
        assert request.url.params["subject_id"] == f"eq.{SUBJECT}"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.headers["apikey"] == settings().publishable_key


@pytest.mark.parametrize(
    "payload",
    [
        [],
        [{"state": "future_unknown_state"}],
    ],
)
def test_supabase_reader_reports_missing_or_unknown_state_for_gate_to_deny(payload) -> None:
    reader = SupabaseAccountLifecycleReader(
        settings(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=payload)
        ),
    )

    result = asyncio.run(reader.read_state(SUBJECT, TOKEN))
    assert result is None if not payload else result == "future_unknown_state"


def test_supabase_reader_fails_closed_for_non_success_or_malformed_response() -> None:
    responses = [
        httpx.Response(503),
        httpx.Response(200, json={"state": "available"}),
        httpx.Response(200, json=[{"state": None}]),
        httpx.Response(200, json=[{"state": "available"}, {"state": "available"}]),
    ]

    for response in responses:
        reader = SupabaseAccountLifecycleReader(
            settings(),
            transport=httpx.MockTransport(lambda _request, current=response: current),
        )
        with pytest.raises(AccountLifecycleReadError):
            asyncio.run(reader.read_state(SUBJECT, TOKEN))


def test_personal_data_deletion_route_returns_accepted_and_queues_storage() -> None:
    repository = DeletionRepositorySpy(deletion_request())
    app, _, _, _, _ = make_app(
        ScriptedLifecycleReader(), deletion_repository=repository
    )

    with TestClient(app) as client:
        response = client.post("/v1/me/data-deletions", headers=auth_headers())

    assert response.status_code == 202
    payload = response.json()
    assert payload["storage_cleanup_required"] is True
    assert payload["workspace_deleted"] is False
    assert ("delete_data", repository.request.request_id) in repository.calls


def test_personal_workspace_deletion_route_returns_accepted() -> None:
    repository = DeletionRepositorySpy(deletion_request())
    app, _, _, _, _ = make_app(
        ScriptedLifecycleReader(), deletion_repository=repository
    )

    with TestClient(app) as client:
        response = client.delete("/v1/me/personal-workspace", headers=auth_headers())

    assert response.status_code == 202
    assert response.json()["workspace_deleted"] is True
    assert ("delete_workspace", repository.request.request_id) in repository.calls


def test_personal_deletion_is_denied_before_repository_when_account_blocked() -> None:
    repository = DeletionRepositorySpy(deletion_request())
    app, _, _, _, _ = make_app(
        ScriptedLifecycleReader(state="revocation_pending"),
        deletion_repository=repository,
    )

    with TestClient(app) as client:
        data = client.post("/v1/me/data-deletions", headers=auth_headers())
        workspace = client.delete("/v1/me/personal-workspace", headers=auth_headers())

    assert data.status_code == 403
    assert workspace.status_code == 403
    assert repository.calls == []


def test_current_deletion_request_returns_the_requester_request() -> None:
    repository = DeletionRepositorySpy(deletion_request())
    app, _, _, _, _ = make_app(
        ScriptedLifecycleReader(), deletion_repository=repository
    )

    with TestClient(app) as client:
        response = client.get("/v1/me/deletion-request", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["request_id"] == str(repository.request.request_id)
    assert ("current", repository.request.request_id) in repository.calls


def test_create_organization_account_upgrades_the_authenticated_account() -> None:
    app, directory, _, _, _ = make_app(ScriptedLifecycleReader())

    with TestClient(app) as client:
        response = client.post(
            "/v1/me/organization",
            json={"name": "Acme Evidence"},
            headers=auth_headers(),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["account_type"] == "organization"
    assert payload["organization_name"] == "Acme Evidence"
    assert payload["can_manage_workspace"] is True
    assert "create_organization_account" in directory.calls
