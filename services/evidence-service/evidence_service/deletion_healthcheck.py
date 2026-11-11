"""Read-only deletion-queue health signal for alerts and operator checks.

Exit codes:
- 0: healthy
- 2: at least one alert condition
- 1: check failed (connection/query)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

HEALTH_SQL = """
select
    count(*) filter (where status = 'purge_failed')::int as purge_failed,
    count(*) filter (where status = 'purge_pending')::int as purge_pending,
    coalesce(
        extract(epoch from now() - min(created_at) filter (where status = 'purge_pending')),
        0
    )::int as oldest_purge_pending_seconds,
    count(*) filter (where status = 'accepted')::int as accepted,
    coalesce(
        extract(epoch from now() - min(created_at) filter (where status = 'accepted')),
        0
    )::int as oldest_accepted_seconds,
    count(*) filter (where status = 'journal_ack_pending')::int as journal_ack_pending,
    (
        select count(*)::int
          from public.data_deletion_storage_queue
         where completed_at is null
           and created_at < now() - make_interval(mins => %s)
    ) as stale_storage_queue
  from public.data_deletion_requests;
"""


def _connect(database_url: str) -> Any:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("psycopg is required for deletion health checks") from exc
    return psycopg.connect(database_url)


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def read_deletion_health(connection: Any) -> dict[str, Any]:
    queue_minutes = _int_env("DELETION_QUEUE_WARN_MINUTES", 30)
    pending_minutes = _int_env("DELETION_PURGE_PENDING_WARN_MINUTES", 10)
    accepted_minutes = _int_env("DELETION_ACCEPTED_WARN_MINUTES", 10)
    with connection.cursor() as cursor:
        cursor.execute(HEALTH_SQL, (queue_minutes,))
        row = cursor.fetchone()
    if not row:
        raise RuntimeError("deletion health query returned no row")
    metrics = {
        "purge_failed": int(row[0]),
        "purge_pending": int(row[1]),
        "oldest_purge_pending_seconds": int(row[2]),
        "accepted": int(row[3]),
        "oldest_accepted_seconds": int(row[4]),
        "journal_ack_pending": int(row[5]),
        "stale_storage_queue": int(row[6]),
    }
    alerts: list[str] = []
    if metrics["purge_failed"] > 0:
        alerts.append("purge_failed")
    if metrics["journal_ack_pending"] > 0:
        alerts.append("journal_ack_pending")
    if metrics["oldest_purge_pending_seconds"] > pending_minutes * 60:
        alerts.append("purge_pending_age")
    if metrics["oldest_accepted_seconds"] > accepted_minutes * 60:
        alerts.append("accepted_age")
    if metrics["stale_storage_queue"] > 0:
        alerts.append("storage_queue_age")
    return {
        "event": "deletion_health",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "alert" if alerts else "ok",
        "alerts": alerts,
        "metrics": metrics,
        "thresholds": {
            "purge_pending_minutes": pending_minutes,
            "accepted_minutes": accepted_minutes,
            "storage_queue_minutes": queue_minutes,
        },
    }


def main() -> int:
    database_url = os.environ.get("DELETION_HEALTHCHECK_DATABASE_URL") or os.environ.get(
        "DATABASE_URL", ""
    )
    if not database_url:
        print(json.dumps({"event": "deletion_health_error", "error": "database_url_missing"}))
        return 1
    connection = None
    try:
        connection = _connect(database_url)
        result = read_deletion_health(connection)
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0 if result["status"] == "ok" else 2
    except Exception as exc:  # keep the CLI usable for cron/monitoring
        print(
            json.dumps(
                {
                    "event": "deletion_health_error",
                    "error": type(exc).__name__,
                    "message": str(exc)[:200],
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
