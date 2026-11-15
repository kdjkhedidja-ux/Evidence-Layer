import asyncio
import base64
import json
from datetime import datetime, timezone
from uuid import UUID

import httpx
import pytest

from evidence_layer.snapshot_verifier import (
    LocalSupabaseVerificationGateway,
    SnapshotVerificationError,
    SupabaseStorageSnapshotReader,
    VerificationFailure,
)
from snapshot_verifier_service.worker import (
    SupabaseAuthSession,
    VerificationQueueClient,
    VerificationWorker,
    WorkerSettings,
    WorkerUnavailable,
)

PROJECT_HOST = "project-ref.supabase.co"
BASE_URL = f"https://{PROJECT_HOST}"
API_KEY = "sb_publishable_worker_test_key"
WORKSPACE = UUID("a1000000-0000-4000-8000-000000000001")
SOURCE = UUID("c1000000-0000-4000-8000-000000000001")
VERSION = UUID("d1000000-0000-4000-8000-000000000001")
JOB = UUID("e1000000-0000-4000-8000-000000000001")


def _jwt(role: str = "authenticated") -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"role": role}).encode()).decode().rstrip("=")
    return f"e30.{payload}.signature"


def _settings(**override) -> WorkerSettings:
    values = dict(
        base_url=BASE_URL,
        publishable_key=API_KEY,
        refresh_token="synthetic-refresh-token",
        allowed_remote_host=PROJECT_HOST,
        worker_id="worker-test",
        poll_seconds=1,
        lease_seconds=60,
    )
    values.update(override)
    return WorkerSettings(**values)


def test_hosted_gateway_and_reader_accept_only_the_exact_https_host():
    LocalSupabaseVerificationGateway(
        base_url=BASE_URL,
        publishable_key=API_KEY,
        allowed_remote_host=PROJECT_HOST,
    )
    SupabaseStorageSnapshotReader(
        base_url=BASE_URL,
        publishable_key=API_KEY,
        allowed_remote_host=PROJECT_HOST,
    )
    for bad_url in (
        f"http://{PROJECT_HOST}",
        "https://other-host.supabase.co",
        f"https://{PROJECT_HOST}:8443",
        f"https://{PROJECT_HOST}/path",
    ):
        with pytest.raises(ValueError):
            LocalSupabaseVerificationGateway(
                base_url=bad_url,
                publishable_key=API_KEY,
                allowed_remote_host=PROJECT_HOST,
            )
        with pytest.raises(ValueError):
            SupabaseStorageSnapshotReader(
                base_url=bad_url,
                publishable_key=API_KEY,
                allowed_remote_host=PROJECT_HOST,
            )


def test_worker_settings_require_an_exact_https_host():
    settings = _settings()
    assert settings.allowed_remote_host == PROJECT_HOST
    with pytest.raises(ValueError):
        _settings(base_url="https://other-host.supabase.co")
    with pytest.raises(ValueError):
        _settings(allowed_remote_host="")
    with pytest.raises(ValueError):
        _settings(lease_seconds=5)


def test_auth_session_refreshes_once_and_uses_the_rotated_token():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": _jwt("authenticated"),
                "refresh_token": "rotated-refresh-token",
                "expires_in": 3600,
            },
        )

    session = SupabaseAuthSession(
        base_url=BASE_URL,
        publishable_key=API_KEY,
        refresh_token="initial-refresh-token",
        transport=httpx.MockTransport(handler),
    )
    first = asyncio.run(session.access_token())
    second = asyncio.run(session.access_token())
    assert first == second
    assert len(requests) == 1
    assert requests[0].url.path == "/auth/v1/token"
    assert b"initial-refresh-token" in requests[0].content


def test_auth_session_rejects_service_role_tokens():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": _jwt("service_role"),
                "refresh_token": "rotated",
                "expires_in": 3600,
            },
        )

    session = SupabaseAuthSession(
        base_url=BASE_URL,
        publishable_key=API_KEY,
        refresh_token="initial",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(WorkerUnavailable):
        asyncio.run(session.access_token())


def test_queue_claim_uses_the_worker_rpc_and_parses_the_job():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=[
                {
                    "out_job_id": str(JOB),
                    "out_workspace_id": str(WORKSPACE),
                    "out_source_id": str(SOURCE),
                    "out_version_id": str(VERSION),
                    "out_attempts": 1,
                }
            ],
        )

    queue = VerificationQueueClient(
        base_url=BASE_URL,
        publishable_key=API_KEY,
        transport=httpx.MockTransport(handler),
    )
    job = asyncio.run(queue.claim("token", "worker-test", 60))
    assert job["out_job_id"] == str(JOB)
    assert seen["path"] == "/rest/v1/rpc/claim_snapshot_verification"
    assert seen["body"] == {"p_worker_id": "worker-test", "p_lease_seconds": 60}


class FakeAuth:
    async def access_token(self) -> str:
        return "token"


class FakeQueue:
    def __init__(self) -> None:
        self.claimed = {
            "out_job_id": str(JOB),
            "out_workspace_id": str(WORKSPACE),
            "out_source_id": str(SOURCE),
            "out_version_id": str(VERSION),
            "out_attempts": 1,
        }
        self.completed: list[UUID] = []
        self.failures: list[tuple[UUID, str]] = []

    async def claim(self, access_token, worker_id, lease_seconds):
        job, self.claimed = self.claimed, None
        return job

    async def complete(self, access_token, job_id, worker_id):
        self.completed.append(job_id)

    async def fail(self, access_token, job_id, worker_id, category):
        self.failures.append((job_id, category))


class FakeService:
    def __init__(self, error: SnapshotVerificationError | None = None) -> None:
        self.error = error

    async def verify_and_publish(self, access_token, **kwargs):
        if self.error is not None:
            raise self.error
        return object()


def test_worker_publishes_then_completes_the_job():
    queue = FakeQueue()
    worker = VerificationWorker(
        settings=_settings(), auth=FakeAuth(), queue=queue, service=FakeService()
    )
    assert asyncio.run(worker.run_once()) is True
    assert queue.completed == [JOB]
    assert queue.failures == []


def test_worker_maps_digest_mismatch_to_terminal_failure():
    queue = FakeQueue()
    worker = VerificationWorker(
        settings=_settings(),
        auth=FakeAuth(),
        queue=queue,
        service=FakeService(
            SnapshotVerificationError(VerificationFailure.DIGEST_MISMATCH)
        ),
    )
    assert asyncio.run(worker.run_once()) is True
    assert queue.completed == []
    assert queue.failures == [(JOB, "verification_failed")]


def test_worker_completes_when_the_snapshot_was_already_published():
    queue = FakeQueue()
    worker = VerificationWorker(
        settings=_settings(),
        auth=FakeAuth(),
        queue=queue,
        service=FakeService(SnapshotVerificationError(VerificationFailure.NOT_STAGED)),
    )
    assert asyncio.run(worker.run_once()) is True
    assert queue.completed == [JOB]
    assert queue.failures == []


def test_worker_returns_false_when_the_queue_is_empty():
    queue = FakeQueue()
    queue.claimed = None
    worker = VerificationWorker(
        settings=_settings(), auth=FakeAuth(), queue=queue, service=FakeService()
    )
    assert asyncio.run(worker.run_once()) is False
