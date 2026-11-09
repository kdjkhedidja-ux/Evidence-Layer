"""Full individual-account deletion worker.

Sequence:
  1. claim and run the existing scoped purge slice;
  2. prepare owned workspaces/snapshots and enqueue Storage keys;
  3. drain the Storage queue;
  4. delete the Supabase Auth identity through the Admin API;
  5. mark the request and lifecycle `purged`.

The worker never runs inside the HTTP service and never receives browser
credentials. It requires a privileged database connection and a Storage/Auth
admin credential in its own environment.
"""

from __future__ import annotations

import argparse
import json
import os
from uuid import UUID

import httpx

from .storage_cleanup_worker import run_storage_cleanup


def _connect(database_url: str):
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError(
            "psycopg is required only in the dedicated account deletion worker"
        ) from exc
    return psycopg.connect(database_url)


def _call_json(cursor, statement: str, params: tuple):
    cursor.execute(statement, params)
    row = cursor.fetchone()
    return row[0] if row else None


def delete_auth_identity(
    client: httpx.Client, base_url: str, service_key: str, subject_id: str
) -> int:
    response = client.delete(
        f"{base_url.rstrip('/')}/auth/v1/admin/users/{subject_id}",
        headers={
            "apikey": service_key,
            "authorization": f"Bearer {service_key}",
        },
    )
    return response.status_code


def run_account_deletion(
    request_id: UUID,
    *,
    connection=None,
    storage_client: httpx.Client | None = None,
    auth_client: httpx.Client | None = None,
) -> dict:
    database_url = os.environ.get("EVIDENCE_PURGE_DATABASE_URL", "")
    base_url = os.environ.get("SUPABASE_URL", "")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not base_url or not service_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")

    if connection is None:
        if not database_url:
            raise RuntimeError("EVIDENCE_PURGE_DATABASE_URL is required")
        connection = _connect(database_url)

    owns_auth_client = auth_client is None
    if auth_client is None:
        auth_client = httpx.Client(timeout=20.0)

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "select status, requester_user_id from public.data_deletion_requests where request_id = %s",
                (str(request_id),),
            )
            row = cursor.fetchone()
            if not row:
                raise RuntimeError("deletion request not found")
            status, subject_id = row[0], str(row[1])
            if status == "purged":
                return {"status": "purged", "subject_id": subject_id, "already_purged": True}
            if status not in ("accepted", "purge_pending"):
                raise RuntimeError(f"deletion request is not runnable ({status})")

            if status == "accepted":
                cursor.execute("set local role account_deletion_worker")
                slice_result = _call_json(
                    cursor,
                    "select private.run_account_deletion_slice(%s)",
                    (str(request_id),),
                )
                connection.commit()
                if not isinstance(slice_result, dict) or slice_result.get("status") != "purge_pending":
                    return {
                        "status": (slice_result or {}).get("status", "failed")
                        if isinstance(slice_result, dict) else "failed",
                        "subject_id": subject_id,
                        "stage": "scoped_slice",
                    }

            cursor.execute(
                "select exists(select 1 from public.evidence_deletion_audit "
                "where request_id = %s and action = 'account_prepared')",
                (str(request_id),),
            )
            marker_row = cursor.fetchone()
            already_prepared = bool(marker_row and marker_row[0])
            if already_prepared:
                prepared = {"status": "prepared"}
            else:
                cursor.execute("set local role account_deletion_worker")
                prepared = _call_json(
                    cursor,
                    "select private.prepare_individual_account_purge_v2(%s)",
                    (str(request_id),),
                )
                connection.commit()
            if not isinstance(prepared, dict) or prepared.get("status") != "prepared":
                return {
                    "status": "purge_pending",
                    "subject_id": subject_id,
                    "stage": "prepare",
                    "failure": "prepare_failed",
                }

        storage_result = run_storage_cleanup(
            limit=500,
            connection=connection,
            client=storage_client,
        )
        if storage_result.get("failed", 0) > 0:
            return {
                "status": "purge_pending",
                "subject_id": subject_id,
                "stage": "storage_cleanup",
                "failure": "storage_cleanup_failed",
                **storage_result,
            }

        with connection.cursor() as cursor:
            cursor.execute("set local role account_deletion_worker")
            lifecycle = _call_json(
                cursor,
                "select private.delete_account_lifecycle_for_purge(%s)",
                (str(request_id),),
            )
            connection.commit()
        if not isinstance(lifecycle, dict):
            return {
                "status": "purge_pending",
                "subject_id": subject_id,
                "stage": "lifecycle_cleanup",
                "failure": "lifecycle_cleanup_failed",
            }

        auth_status = delete_auth_identity(
            auth_client, base_url, service_key, subject_id
        )
        if auth_status not in (200, 204, 404):
            return {
                "status": "purge_pending",
                "subject_id": subject_id,
                "stage": "auth_delete",
                "failure": f"auth_delete_http_{auth_status}",
            }

        with connection.cursor() as cursor:
            cursor.execute("set local role account_deletion_worker")
            marked = _call_json(
                cursor,
                "select private.mark_account_purged_v2(%s)",
                (str(request_id),),
            )
            connection.commit()
        if not isinstance(marked, dict) or marked.get("status") != "purged":
            return {
                "status": "purge_pending",
                "subject_id": subject_id,
                "stage": "mark_purged",
                "failure": "mark_purged_failed",
            }

        return {
            "status": "purged",
            "subject_id": subject_id,
            "storage": storage_result,
            "auth_status": auth_status,
        }
    finally:
        if owns_auth_client:
            auth_client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one full individual-account deletion")
    parser.add_argument("request_id", type=UUID)
    args = parser.parse_args()
    print(json.dumps(run_account_deletion(args.request_id), sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI dispatch
    raise SystemExit(main())
