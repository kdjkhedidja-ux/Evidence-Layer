"""Continuous individual-account deletion worker.

The worker drains the Storage cleanup queue and processes accepted or
retryable deletion requests. It connects with the service's privileged database
URL (security-definer purge functions still run as the dedicated worker role)
and uses the Storage/Auth admin credential only from its own environment.
"""

from __future__ import annotations

import json
import os
import time
from .account_deletion_worker import _connect, run_account_deletion
from .export_cleanup_worker import run_export_cleanup
from .storage_cleanup_worker import run_storage_cleanup

LOCK_KEY = "evidence-layer-deletion-worker"
LOCK_TIMEOUT_SECONDS = 180

PENDING_SQL = """
select request_id
  from public.data_deletion_requests
 where status in ('accepted', 'purge_pending')
 order by created_at
 limit 10
"""

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


def process_pending(connection) -> dict:
    repaired = reconcile_stale_manifest_ack(connection)
    exports_cleaned = run_export_cleanup(connection=connection)
    storage = run_storage_cleanup(limit=500, connection=connection)
    with connection.cursor() as cursor:
        cursor.execute(PENDING_SQL)
        rows = cursor.fetchall()
    accounts = [
        run_account_deletion(row[0], connection=connection)
        for row in rows
    ]
    return {
        "repaired_stale_ack": repaired,
        "exports_cleaned": exports_cleaned,
        "storage": storage,
        "accounts": accounts,
    }


def main() -> int:
    interval = int(os.environ.get("DELETION_WORKER_INTERVAL_SECONDS", "60"))
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    connection = _connect(database_url)
    if not acquire_single_instance_lock(connection):
        print(json.dumps({"event": "worker_lock_unavailable"}), flush=True)
        return 2
    print(
        json.dumps({"event": "worker_started", "interval_seconds": interval}),
        flush=True,
    )
    while True:
        try:
            result = process_pending(connection)
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
