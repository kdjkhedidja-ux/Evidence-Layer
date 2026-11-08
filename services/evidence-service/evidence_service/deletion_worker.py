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
from .storage_cleanup_worker import run_storage_cleanup

PENDING_SQL = """
select request_id
  from public.data_deletion_requests
 where status in ('accepted', 'purge_pending')
 order by created_at
 limit 10
"""


def process_pending(connection) -> dict:
    storage = run_storage_cleanup(limit=500, connection=connection)
    with connection.cursor() as cursor:
        cursor.execute(PENDING_SQL)
        rows = cursor.fetchall()
    accounts = [
        run_account_deletion(row[0], connection=connection)
        for row in rows
    ]
    return {"storage": storage, "accounts": accounts}


def main() -> int:
    interval = int(os.environ.get("DELETION_WORKER_INTERVAL_SECONDS", "60"))
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    connection = _connect(database_url)
    while True:
        try:
            print(json.dumps(process_pending(connection), default=str), flush=True)
        except Exception as exc:  # keep the worker alive; retry next interval
            try:
                connection.rollback()
            except Exception:
                pass
            print(
                json.dumps(
                    {"error": type(exc).__name__, "message": str(exc)[:200]}
                ),
                flush=True,
            )
        time.sleep(interval)


if __name__ == "__main__":  # pragma: no cover - CLI dispatch
    raise SystemExit(main())
