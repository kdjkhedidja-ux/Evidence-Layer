"""Private verification-queue worker.

This is not the loopback-only one-shot verifier. It runs in a hosted worker,
claims queued snapshot jobs with a lease, and uses a dedicated non-human Auth
identity. The target is pinned to one exact HTTPS Supabase host; redirects,
proxies, service_role tokens, and arbitrary hosts remain rejected.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

from evidence_layer.snapshot_verifier import (
    DEFAULT_MAX_SNAPSHOT_BYTES,
    DEFAULT_MAX_VERIFY_SECONDS,
    LocalSupabaseVerificationGateway,
    SnapshotVerificationError,
    SnapshotVerificationService,
    SupabaseStorageSnapshotReader,
    VerificationFailure,
    is_valid_verifier_access_token,
)

SUPABASE_URL_ENV = "VERIFICATION_WORKER_SUPABASE_URL"
PUBLISHABLE_KEY_ENV = "VERIFICATION_WORKER_PUBLISHABLE_KEY"
REFRESH_TOKEN_ENV = "VERIFICATION_WORKER_REFRESH_TOKEN"
EMAIL_ENV = "VERIFICATION_WORKER_EMAIL"
PASSWORD_ENV = "VERIFICATION_WORKER_PASSWORD"
ALLOWED_HOST_ENV = "VERIFICATION_WORKER_ALLOWED_HOST"
WORKER_ID_ENV = "VERIFICATION_WORKER_ID"
POLL_SECONDS_ENV = "VERIFICATION_WORKER_POLL_SECONDS"
LEASE_SECONDS_ENV = "VERIFICATION_WORKER_LEASE_SECONDS"

_MAX_RPC_RESPONSE_BYTES = 64 * 1024
_FAILURE_CATEGORIES = {
    VerificationFailure.DIGEST_MISMATCH: "verification_failed",
    VerificationFailure.SIZE_MISMATCH: "verification_failed",
    VerificationFailure.CLAIM_INVALID: "worker_error",
    VerificationFailure.STORAGE_UNAVAILABLE: "storage_error",
    VerificationFailure.TIMEOUT: "transient_error",
    VerificationFailure.NOT_AUTHORISED: "worker_error",
    VerificationFailure.PUBLISH_REJECTED: "worker_error",
    VerificationFailure.INVALID_REQUEST: "worker_error",
    VerificationFailure.NOT_STAGED: "worker_error",
}


class WorkerUnavailable(Exception):
    pass


@dataclass(frozen=True)
class WorkerSettings:
    base_url: str
    publishable_key: str
    refresh_token: str = ""
    email: str = ""
    password: str = ""
    allowed_remote_host: str = ""
    worker_id: str = ""
    poll_seconds: float = 10.0
    lease_seconds: int = 300
    request_timeout_seconds: float = 5.0
    max_snapshot_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES
    max_verify_seconds: float = DEFAULT_MAX_VERIFY_SECONDS

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        if (
            not self.publishable_key
            or not (self.refresh_token or (self.email and self.password))
            or not self.worker_id
            or parsed.scheme != "https"
            or parsed.hostname != self.allowed_remote_host
            or parsed.port not in {None, 443}
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or not 1 <= self.poll_seconds <= 300
            or not 30 <= self.lease_seconds <= 3600
        ):
            raise ValueError("verification worker configuration is invalid")

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "WorkerSettings":
        values = os.environ if environment is None else environment
        base_url = values.get(SUPABASE_URL_ENV, "").strip()
        publishable_key = values.get(PUBLISHABLE_KEY_ENV, "").strip()
        refresh_token = values.get(REFRESH_TOKEN_ENV, "").strip()
        email = values.get(EMAIL_ENV, "").strip()
        password = values.get(PASSWORD_ENV, "").strip()
        allowed_host = values.get(ALLOWED_HOST_ENV, "").strip()
        worker_id = values.get(WORKER_ID_ENV, "").strip()
        if not all((base_url, publishable_key, allowed_host)):
            raise ValueError("verification worker credentials are missing")
        if not refresh_token and not (email and password):
            raise ValueError("verification worker credentials are missing")
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != allowed_host
            or parsed.port not in {None, 443}
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("verification worker target is invalid")
        if not worker_id:
            worker_id = (
                f"verification-worker-{socket.gethostname()}-{os.getpid()}-{uuid4()}"
            )[:200]
        try:
            poll_seconds = float(values.get(POLL_SECONDS_ENV, "10"))
            lease_seconds = int(values.get(LEASE_SECONDS_ENV, "300"))
        except ValueError as exc:
            raise ValueError("verification worker timing is invalid") from exc
        if not 1 <= poll_seconds <= 300 or not 30 <= lease_seconds <= 3600:
            raise ValueError("verification worker timing is out of range")
        return cls(
            base_url=base_url,
            publishable_key=publishable_key,
            refresh_token=refresh_token,
            email=email,
            password=password,
            allowed_remote_host=allowed_host,
            worker_id=worker_id,
            poll_seconds=poll_seconds,
            lease_seconds=lease_seconds,
        )


class SupabaseAuthSession:
    """Refresh-token session for one dedicated non-human verifier identity."""

    def __init__(
        self,
        *,
        base_url: str,
        publishable_key: str,
        refresh_token: str = "",
        email: str = "",
        password: str = "",
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._key = publishable_key
        self._refresh_token = refresh_token
        self._email = email
        self._password = password
        self._timeout = timeout_seconds
        self._transport = transport
        self._access_token: str | None = None
        self._expires_at: datetime | None = None

    async def access_token(self) -> str:
        now = datetime.now(timezone.utc)
        if (
            self._access_token is not None
            and self._expires_at is not None
            and now < self._expires_at - timedelta(seconds=30)
        ):
            return self._access_token
        if self._email and self._password:
            grant = "password"
            body = {"email": self._email, "password": self._password}
        else:
            grant = "refresh_token"
            body = {"refresh_token": self._refresh_token}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/auth/v1/token?grant_type={grant}",
                    headers={
                        "apikey": self._key,
                        "content-type": "application/json",
                        "accept": "application/json",
                    },
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise WorkerUnavailable from exc
        if response.status_code in {400, 401, 403}:
            raise WorkerUnavailable
        if response.status_code != 200 or len(response.content) > _MAX_RPC_RESPONSE_BYTES:
            raise WorkerUnavailable
        try:
            payload = response.json()
            access_token = payload["access_token"]
            refresh_token = payload.get("refresh_token", self._refresh_token)
            expires_in = int(payload.get("expires_in", 3600))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise WorkerUnavailable from exc
        if (
            not is_valid_verifier_access_token(access_token)
            or not isinstance(refresh_token, str)
            or not refresh_token
            or not 60 <= expires_in <= 86400
        ):
            raise WorkerUnavailable
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._expires_at = now + timedelta(seconds=expires_in)
        return access_token


class VerificationQueueClient:
    def __init__(
        self,
        *,
        base_url: str,
        publishable_key: str,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._key = publishable_key
        self._timeout = timeout_seconds
        self._transport = transport

    async def _rpc(self, name: str, access_token: str, body: dict) -> tuple[int, bytes]:
        if not is_valid_verifier_access_token(access_token):
            raise WorkerUnavailable
        headers = {
            "apikey": self._key,
            "authorization": f"Bearer {access_token}",
            "content-type": "application/json",
            "accept": "application/json",
            "accept-encoding": "identity",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/rest/v1/rpc/{name}",
                    headers=headers,
                    content=json.dumps(body).encode("utf-8"),
                ) as response:
                    received = bytearray()
                    async for chunk in response.aiter_bytes():
                        received.extend(chunk)
                        if len(received) > _MAX_RPC_RESPONSE_BYTES:
                            raise WorkerUnavailable
                    return response.status_code, bytes(received)
        except httpx.HTTPError as exc:
            raise WorkerUnavailable from exc

    async def claim(
        self, access_token: str, worker_id: str, lease_seconds: int
    ) -> dict | None:
        status, payload = await self._rpc(
            "claim_snapshot_verification",
            access_token,
            {"p_worker_id": worker_id, "p_lease_seconds": lease_seconds},
        )
        if status in {401, 403}:
            raise WorkerUnavailable
        if status < 200 or status >= 300:
            raise WorkerUnavailable
        try:
            rows = json.loads(payload)
        except ValueError as exc:
            raise WorkerUnavailable from exc
        if not isinstance(rows, list) or len(rows) > 1:
            raise WorkerUnavailable
        if not rows:
            return None
        row = rows[0]
        if not isinstance(row, dict):
            raise WorkerUnavailable
        try:
            UUID(row["out_job_id"])
            UUID(row["out_workspace_id"])
            UUID(row["out_source_id"])
            UUID(row["out_version_id"])
            int(row["out_attempts"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkerUnavailable from exc
        return row

    async def complete(self, access_token: str, job_id: UUID, worker_id: str) -> None:
        status, _ = await self._rpc(
            "complete_snapshot_verification",
            access_token,
            {"p_job_id": str(job_id), "p_worker_id": worker_id},
        )
        if status in {401, 403} or status < 200 or status >= 300:
            raise WorkerUnavailable

    async def fail(
        self,
        access_token: str,
        job_id: UUID,
        worker_id: str,
        category: str,
    ) -> None:
        status, _ = await self._rpc(
            "fail_snapshot_verification",
            access_token,
            {
                "p_job_id": str(job_id),
                "p_worker_id": worker_id,
                "p_category": category,
            },
        )
        if status in {401, 403} or status < 200 or status >= 300:
            raise WorkerUnavailable


class VerificationWorker:
    def __init__(
        self,
        *,
        settings: WorkerSettings,
        auth: SupabaseAuthSession,
        queue: VerificationQueueClient,
        service: SnapshotVerificationService,
    ) -> None:
        self._settings = settings
        self._auth = auth
        self._queue = queue
        self._service = service

    async def run_once(self) -> bool:
        access_token = await self._auth.access_token()
        job = await self._queue.claim(
            access_token, self._settings.worker_id, self._settings.lease_seconds
        )
        if job is None:
            return False
        job_id = UUID(job["out_job_id"])
        workspace_id = UUID(job["out_workspace_id"])
        source_id = UUID(job["out_source_id"])
        version_id = UUID(job["out_version_id"])
        try:
            await self._service.verify_and_publish(
                access_token,
                workspace_id=workspace_id,
                source_id=source_id,
                version_id=version_id,
            )
        except SnapshotVerificationError as error:
            if error.reason is VerificationFailure.NOT_STAGED:
                try:
                    await self._queue.complete(
                        access_token, job_id, self._settings.worker_id
                    )
                    return True
                except WorkerUnavailable:
                    pass
            category = _FAILURE_CATEGORIES.get(error.reason, "worker_error")
            await self._queue.fail(
                access_token, job_id, self._settings.worker_id, category
            )
            print(
                json.dumps(
                    {
                        "event": "verification_failed",
                        "job_id": str(job_id),
                        "category": category,
                    }
                ),
                flush=True,
            )
            return True
        except Exception:
            await self._queue.fail(
                access_token, job_id, self._settings.worker_id, "worker_error"
            )
            return True
        await self._queue.complete(access_token, job_id, self._settings.worker_id)
        print(
            json.dumps({"event": "verification_published", "job_id": str(job_id)}),
            flush=True,
        )
        return True

    async def run_forever(self) -> None:
        while True:
            try:
                worked = await self.run_once()
            except WorkerUnavailable:
                worked = False
            except Exception:
                worked = False
            if not worked:
                await asyncio.sleep(self._settings.poll_seconds)


def build_worker(settings: WorkerSettings) -> VerificationWorker:
    gateway = LocalSupabaseVerificationGateway(
        base_url=settings.base_url,
        publishable_key=settings.publishable_key,
        timeout_seconds=settings.request_timeout_seconds,
        total_timeout_seconds=settings.max_verify_seconds,
        allowed_remote_host=settings.allowed_remote_host,
    )
    reader = SupabaseStorageSnapshotReader(
        base_url=settings.base_url,
        publishable_key=settings.publishable_key,
        max_snapshot_bytes=settings.max_snapshot_bytes,
        timeout_seconds=settings.request_timeout_seconds,
        total_timeout_seconds=settings.max_verify_seconds,
        allowed_remote_host=settings.allowed_remote_host,
    )
    service = SnapshotVerificationService(
        gateway=gateway,
        reader=reader,
        max_snapshot_bytes=settings.max_snapshot_bytes,
        max_verify_seconds=settings.max_verify_seconds,
    )
    return VerificationWorker(
        settings=settings,
        auth=SupabaseAuthSession(
            base_url=settings.base_url,
            publishable_key=settings.publishable_key,
            refresh_token=settings.refresh_token,
            email=settings.email,
            password=settings.password,
            timeout_seconds=settings.request_timeout_seconds,
        ),
        queue=VerificationQueueClient(
            base_url=settings.base_url,
            publishable_key=settings.publishable_key,
            timeout_seconds=settings.request_timeout_seconds,
        ),
        service=service,
    )


def main() -> int:
    try:
        settings = WorkerSettings.from_environment()
        worker = build_worker(settings)
    except (TypeError, ValueError):
        print(json.dumps({"event": "worker_configuration_invalid"}), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "event": "worker_started",
                "worker_id": settings.worker_id,
                "poll_seconds": settings.poll_seconds,
                "lease_seconds": settings.lease_seconds,
            }
        ),
        flush=True,
    )
    try:
        asyncio.run(worker.run_forever())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI dispatch
    raise SystemExit(main())
