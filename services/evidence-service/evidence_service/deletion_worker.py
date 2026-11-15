"""Continuous individual-account deletion worker.

The worker claims one request at a time with a database lease, drains the
Storage cleanup queue, and processes accepted, retryable, or lease-expired
deletion requests. It connects with the service's privileged database URL
(security-definer purge functions still run as the dedicated worker role) and
uses the Storage/Auth admin credential only from its own environment.

The session advisory lock remains available as a single-instance guard. The
lease/claim RPC is the duplicate-processing guard that survives a lost lock or
a crashed process, and it allows the lock to be disabled deliberately once the
lease path is validated in production.
"""

from __future__ import annotations

import json
import os
import socket
import time
from uuid import UUID, uuid4

from .account_deletion_worker import _connect, run_account_deletion
from .export_cleanup_worker import run_export_cleanup
from .storage_cleanup_worker import run_storage_cleanup
from .source_cleanup_worker import run_source_cleanup
from .workspace_deletion_worker import run_workspace_deletion

LOCK_KEY = "evidence-layer-deletion-worker"
LOCK_TIMEOUT_SECONDS = 180
DEFAULT_LEASE_SECONDS = 300
DEFAULT_MAX_ATTEMPTS = 5
MAX_PROCESSED_PER_CYCLE = 10

CLAIM_SQL = "select private.claim_deletion_request(%s, %s, %s)"
RELEASE_SQL = "select private.release_deletion_claim(%s, %s, %s)"
FAILURE_SQL = "select private.record_deletion_failure(%s, %s, %s, %s)"

# A historical or interrupted manifest freeze can leave a request at
# journal_ack_pending even though the append-only manifest row and its
# manifest_frozen audit event both exist. Promote only that proven state; do
# not touch a request that lacks the durable acknowledgement.
STALE_ACK_SQL = """
update public.data_deletion_requests as request
   set status = 'accepted'
 where request.status = 'journal_ack_pending'
   and request.accepted_at is not null
   and exists (
       select 1
         from public.evidence_deletion_audit as audit
        where audit.request_id = request.request_id
          and audit.action = 'manifest_frozen'
   )
"""


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"[:200]


def acquire_single_instance_lock(connection, *, timeout_seconds: int = LOCK_TIMEOUT_SECONDS) -> bool:
    """Hold a session advisory lock so two worker replicas cannot race."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        with connection.cursor() as cursor:
            cursor.execute(
                "select pg_try_advisory_lock(hashtext(%s))", (LOCK_KEY,)
            )
            row = cursor.fetchone()
        if row and row[0]:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(2)


def reconcile_stale_manifest_ack(connection) -> int:
    with connection.cursor() as cursor:
        cursor.execute(STALE_ACK_SQL)
        repaired = cursor.rowcount
    connection.commit()
    return repaired


def claim_next_request(
    connection,
    owner: str,
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> dict | None:
    with connection.cursor() as cursor:
        cursor.execute(CLAIM_SQL, (owner, lease_seconds, max_attempts))
        row = cursor.fetchone()
    connection.commit()
    if not row or not isinstance(row[0], dict):
        return None
    return row[0]


def release_claim(
    connection, request_id: UUID, owner: str, status: str
) -> dict | None:
    with connection.cursor() as cursor:
        cursor.execute(RELEASE_SQL, (str(request_id), owner, status))
        row = cursor.fetchone()
    connection.commit()
    return row[0] if row and isinstance(row[0], dict) else None


def record_failure(
    connection,
    request_id: UUID,
    owner: str,
    category: str,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> dict | None:
    with connection.cursor() as cursor:
        cursor.execute(
            FAILURE_SQL, (str(request_id), owner, category, max_attempts)
        )
        row = cursor.fetchone()
    connection.commit()
    return row[0] if row and isinstance(row[0], dict) else None


def _failure_category(result: dict) -> str:
    if result.get("failure_category") == "verification_failed":
        return "verification_failed"
    if result.get("failure") == "verification_failed":
        return "verification_failed"
    return "worker_error"


def process_pending(
    connection,
    *,
    owner: str | None = None,
    limit: int = MAX_PROCESSED_PER_CYCLE,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> dict:
    repaired = reconcile_stale_manifest_ack(connection)
    exports_cleaned = run_export_cleanup(connection=connection)
    storage = run_storage_cleanup(limit=500, connection=connection)
    source_cleanup = run_source_cleanup(
        connection,
        supabase_url=os.environ.get("SUPABASE_URL", ""),
        service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
        age_days=_int_env("SOURCE_CLEANUP_AGE_DAYS", 14, 1, 365),
        limit=_int_env("SOURCE_CLEANUP_LIMIT", 100, 1, 1000),
    )
    workspace_deletion = run_workspace_deletion(
        connection,
        limit=_int_env("WORKSPACE_DELETION_LIMIT", 10, 1, 100),
    )
    owner = owner or worker_id()
    processed = 0
    accounts: list[dict] = []
    while processed < max(1, min(limit, MAX_PROCESSED_PER_CYCLE)):
        claimed = claim_next_request(
            connection,
            owner,
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
        )
        if not claimed:
            break
        processed += 1
        request_id = claimed["request_id"]
        if not isinstance(request_id, UUID):
            request_id = UUID(str(request_id))
        try:
            result = run_account_deletion(request_id, connection=connection)
        except Exception as exc:  # keep the worker alive; retry or alert later
            try:
                connection.rollback()
            except Exception:
                pass
            result = {
                "status": "worker_error",
                "subject_id": None,
                "error": type(exc).__name__,
            }
            result["failure_record"] = record_failure(
                connection,
                request_id,
                owner,
                "worker_error",
                max_attempts=max_attempts,
            )
        else:
            if result.get("status") == "purged":
                result["release"] = release_claim(
                    connection, request_id, owner, "purged"
                )
            else:
                result["failure_record"] = record_failure(
                    connection,
                    request_id,
                    owner,
                    _failure_category(result),
                    max_attempts=max_attempts,
                )
        result.setdefault("request_id", str(request_id))
        result["claim"] = claimed
        accounts.append(result)
    return {
        "repaired_stale_ack": repaired,
        "exports_cleaned": exports_cleaned,
        "storage": storage,
        "source_cleanup": source_cleanup,
        "workspace_deletion": workspace_deletion,
        "accounts": accounts,
    }


def main() -> int:
    interval = _int_env("DELETION_WORKER_INTERVAL_SECONDS", 60, 5, 3600)
    lease_seconds = _int_env(
        "DELETION_WORKER_LEASE_SECONDS", DEFAULT_LEASE_SECONDS, 30, 3600
    )
    max_attempts = _int_env(
        "DELETION_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS, 1, 20
    )
    single_instance = os.environ.get(
        "DELETION_WORKER_SINGLE_INSTANCE", "true"
    ).strip().lower() not in {"0", "false", "no"}
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    connection = _connect(database_url)
    if single_instance and not acquire_single_instance_lock(connection):
        print(json.dumps({"event": "worker_lock_unavailable"}), flush=True)
        return 2
    owner = worker_id()
    print(
        json.dumps(
            {
                "event": "worker_started",
                "interval_seconds": interval,
                "lease_seconds": lease_seconds,
                "max_attempts": max_attempts,
                "single_instance": single_instance,
                "worker_id": owner,
            }
        ),
        flush=True,
    )
    while True:
        try:
            result = process_pending(
                connection,
                owner=owner,
                lease_seconds=lease_seconds,
                max_attempts=max_attempts,
            )
            result["event"] = "worker_cycle"
            print(json.dumps(result, default=str), flush=True)
        except Exception as exc:  # keep the worker alive; retry next interval
            try:
                connection.rollback()
            except Exception:
                pass
            print(
                json.dumps(
                    {
                        "event": "worker_error",
                        "error": type(exc).__name__,
                        "message": str(exc)[:200],
                    }
                ),
                flush=True,
            )
        time.sleep(interval)


if __name__ == "__main__":  # pragma: no cover - CLI dispatch
    raise SystemExit(main())
