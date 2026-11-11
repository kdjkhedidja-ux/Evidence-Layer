"""Drain the personal-workspace Storage cleanup queue.

This is a dedicated worker, not part of the HTTP request path. It uses a
privileged database connection to claim queue rows and a Storage admin
credential to delete the exact object keys. The service and browser never
receive these credentials or the object keys.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any
from urllib.parse import quote

import httpx

QUEUE_TABLE = "public.data_deletion_storage_queue"
BUCKET = "source-snapshots"
SUCCESS_STATUSES = {200, 204, 404}


def delete_storage_object(
    client: httpx.Client,
    base_url: str,
    service_key: str,
    object_key: str,
) -> int:
    url = (
        f"{base_url.rstrip('/')}/storage/v1/object/{BUCKET}/"
        f"{quote(object_key, safe='/')}"
    )
    response = client.delete(
        url,
        headers={
            "apikey": service_key,
            "authorization": f"Bearer {service_key}",
        },
    )
    if response.status_code == 400:
        # Supabase Storage reports a missing object as HTTP 400 with a
        # statusCode of 404 and error/code not_found. Treat it as an
        # idempotent delete so one stale queue row cannot block the queue.
        try:
            payload = response.json()
        except Exception:
            payload = {}
        if (
            payload.get("error") == "not_found"
            or payload.get("code") == "NoSuchKey"
            or str(payload.get("statusCode")) == "404"
        ):
            return 404
    return response.status_code


def _connect(database_url: str) -> Any:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError(
            "psycopg is required only in the dedicated storage cleanup worker"
        ) from exc
    return psycopg.connect(database_url)


def run_storage_cleanup(
    *,
    limit: int = 50,
    dry_run: bool = False,
    connection: Any | None = None,
    client: httpx.Client | None = None,
) -> dict[str, int]:
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500")

    database_url = os.environ.get("EVIDENCE_STORAGE_CLEANUP_DATABASE_URL", "")
    base_url = os.environ.get("SUPABASE_URL", "")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

    if connection is None:
        if not database_url:
            raise RuntimeError("EVIDENCE_STORAGE_CLEANUP_DATABASE_URL is required")
        connection = _connect(database_url)
    if client is None and not dry_run:
        if not base_url or not service_key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
        client = httpx.Client(timeout=20.0)

    processed = completed = failed = 0
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                select id, object_key
                  from {QUEUE_TABLE}
                 where completed_at is null
                 order by created_at
                 limit %s
                 for update skip locked
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            for row in rows:
                processed += 1
                if dry_run:
                    continue
                assert client is not None
                status = delete_storage_object(client, base_url, service_key, row[1])
                if status in SUCCESS_STATUSES:
                    cursor.execute(
                        f"""
                        update {QUEUE_TABLE}
                           set completed_at = pg_catalog.clock_timestamp(),
                               last_error = null
                         where id = %s
                        """,
                        (row[0],),
                    )
                    completed += 1
                else:
                    cursor.execute(
                        f"""
                        update {QUEUE_TABLE}
                           set attempts = attempts + 1,
                               last_error = %s
                         where id = %s
                        """,
                        (f"storage delete HTTP {status}", row[0]),
                    )
                    failed += 1
        if dry_run:
            connection.rollback()
        else:
            connection.commit()
    finally:
        if client is not None:
            client.close()

    return {"processed": processed, "completed": completed, "failed": failed}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete queued personal-workspace Storage objects"
    )
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_storage_cleanup(limit=args.limit, dry_run=args.dry_run), sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI dispatch
    raise SystemExit(main())
