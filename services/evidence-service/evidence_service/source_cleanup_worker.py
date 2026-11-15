"""Privileged cleanup of abandoned staged source objects.

The catalog row is never deleted. Stale staged versions are marked abandoned,
hidden from clients, excluded from quotas, and queued here. This worker deletes
only the private Storage object with the service-role credential.
"""

from __future__ import annotations

import socket
from urllib.parse import quote
from uuid import uuid4

import httpx

ENQUEUE_SQL = "select private.enqueue_stale_staged_sources(%s, %s)"
CLAIM_SQL = "select out_queue_id, out_object_key, out_attempts from private.claim_source_cleanup(%s, %s)"
COMPLETE_SQL = "select private.complete_source_cleanup(%s, %s)"
FAIL_SQL = "select private.fail_source_cleanup(%s, %s, %s)"
DEFAULT_AGE_DAYS = 14
DEFAULT_LIMIT = 100
DEFAULT_LEASE_SECONDS = 300


def _worker_id() -> str:
    return f"source-cleanup-{socket.gethostname()}-{uuid4()}"[:200]


def delete_stored_object(
    *,
    supabase_url: str,
    service_role_key: str,
    object_key: str,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    if (
        not isinstance(supabase_url, str)
        or not supabase_url.startswith("https://")
        or not isinstance(service_role_key, str)
        or not service_role_key
        or not isinstance(object_key, str)
        or not object_key
    ):
        return False
    try:
        with httpx.Client(
            timeout=10.0,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            response = client.delete(
                f"{supabase_url.rstrip('/')}/storage/v1/object/source-snapshots/{quote(object_key, safe='/')}",
                headers={
                    "apikey": service_role_key,
                    "Authorization": f"Bearer {service_role_key}",
                    "User-Agent": "evidence-layer-source-cleanup/1.0",
                },
            )
        return response.status_code in {200, 204, 404}
    except (httpx.HTTPError, OSError, ValueError):
        return False


def run_source_cleanup(
    connection,
    *,
    supabase_url: str,
    service_role_key: str,
    age_days: int = DEFAULT_AGE_DAYS,
    limit: int = DEFAULT_LIMIT,
    owner: str | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    if not supabase_url or not service_role_key:
        return {"status": "unavailable", "enqueued": 0, "deleted": 0, "failed": 0}
    owner = owner or _worker_id()
    with connection.cursor() as cursor:
        cursor.execute(ENQUEUE_SQL, (age_days, limit))
        row = cursor.fetchone()
    connection.commit()
    enqueued = int(row[0]) if row and row[0] is not None else 0

    deleted = 0
    failed = 0
    claimed = 0
    for _ in range(max(1, min(limit, 1000))):
        with connection.cursor() as cursor:
            cursor.execute(CLAIM_SQL, (owner, lease_seconds))
            claimed_row = cursor.fetchone()
        connection.commit()
        if not claimed_row:
            break
        queue_id, object_key, _attempts = claimed_row
        claimed += 1
        if delete_stored_object(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            object_key=object_key,
            transport=transport,
        ):
            with connection.cursor() as cursor:
                cursor.execute(COMPLETE_SQL, (str(queue_id), owner))
            connection.commit()
            deleted += 1
        else:
            with connection.cursor() as cursor:
                cursor.execute(FAIL_SQL, (str(queue_id), owner, "storage_error"))
            connection.commit()
            failed += 1
    return {
        "status": "ok",
        "enqueued": enqueued,
        "claimed": claimed,
        "deleted": deleted,
        "failed": failed,
    }
