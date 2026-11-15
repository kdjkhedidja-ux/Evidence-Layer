"""Read-only verifier for deletion reconciliation on a disposable restore target.

This script does not restore anything and never runs against production by
default. Point ``RESTORE_VERIFY_DATABASE_URL`` at a disposable PostgreSQL
restore, then run:

    PYTHONPATH=services/evidence-service \
      python -m evidence_service.restore_reconciliation

It checks every terminal ``purged`` request for resurrected account-owned
workspaces, memberships, source grants, evidence, snapshots, pending Storage
cleanup rows, and (when the restored database has an ``auth`` schema) the Auth
identity. Exit codes: 0 clean, 2 resurrected data, 1 check failed.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

REQUESTS_SQL = """
select request_id, scope_id
  from public.data_deletion_requests
 where status = 'purged'
 order by created_at
 limit %s
"""

COUNTS_SQL = """
with owned as (
    select workspace.id
      from public.workspaces as workspace
     where workspace.personal_owner_id = %s
        or exists (
            select 1
              from public.organizations as organization
             where organization.id = workspace.organization_id
               and organization.owner_user_id = %s
        )
        or (
            exists (
                select 1
                  from public.workspace_memberships as owner_membership
                 where owner_membership.workspace_id = workspace.id
                   and owner_membership.user_id = %s
                   and owner_membership.role = 'owner'
                   and owner_membership.status = 'active'
            )
            and 1 = (
                select count(*)
                  from public.workspace_memberships as owner_count
                 where owner_count.workspace_id = workspace.id
                   and owner_count.role = 'owner'
                   and owner_count.status = 'active'
            )
            and 0 = (
                select count(*)
                  from public.workspace_memberships as member_count
                 where member_count.workspace_id = workspace.id
                   and member_count.role = 'member'
                   and member_count.status = 'active'
            )
        )
)
select
    (select count(*) from owned)::int as workspaces,
    (select count(*) from public.workspace_memberships
      where user_id = %s)::int as memberships,
    (select count(*) from public.source_grants
      where principal_id = %s)::int as source_grants,
    (select count(*) from public.evidence_records
      where workspace_id in (select id from owned))::int as evidence,
    (select count(*) from public.source_snapshots
      where workspace_id in (select id from owned))::int as snapshots,
    (select count(*) from public.data_deletion_storage_queue
      where workspace_id in (select id from owned)
        and completed_at is null)::int as pending_storage
"""

COUNT_NAMES = (
    "workspaces",
    "memberships",
    "source_grants",
    "evidence",
    "snapshots",
    "pending_storage",
)


def _connect(database_url: str) -> Any:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("psycopg is required for restore verification") from exc
    return psycopg.connect(database_url)


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def verify_restored_deletion(connection: Any, *, limit: int = 1000) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            "select pg_catalog.to_regclass('auth.users') is not null"
        )
        auth_schema_present = bool(cursor.fetchone()[0])
        cursor.execute(REQUESTS_SQL, (limit,))
        requests = cursor.fetchall()

        failures: list[dict[str, Any]] = []
        for request_id, subject_id in requests:
            auth_user_present = None
            if auth_schema_present:
                cursor.execute(
                    "select count(*) from auth.users where id = %s", (subject_id,)
                )
                auth_user_present = int(cursor.fetchone()[0]) > 0
            cursor.execute(
                COUNTS_SQL,
                (subject_id, subject_id, subject_id, subject_id, subject_id),
            )
            row = cursor.fetchone()
            counts = {name: int(row[index]) for index, name in enumerate(COUNT_NAMES)}
            resurrected = {
                name: value for name, value in counts.items() if value > 0
            }
            if auth_user_present:
                resurrected["auth_identity"] = 1
            if resurrected:
                failures.append(
                    {
                        "request_id": str(request_id),
                        "subject_id": str(subject_id),
                        "resurrected": resurrected,
                        "auth_checked": auth_schema_present,
                    }
                )

    return {
        "event": "restore_deletion_reconciliation",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "resurrected" if failures else "clean",
        "requests_checked": len(requests),
        "auth_schema_present": auth_schema_present,
        "failures": failures,
    }


def main() -> int:
    database_url = os.environ.get("RESTORE_VERIFY_DATABASE_URL", "").strip()
    if not database_url:
        print(
            json.dumps(
                {
                    "event": "restore_deletion_reconciliation_error",
                    "error": "RESTORE_VERIFY_DATABASE_URL is required",
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 1
    limit = _int_env("RESTORE_VERIFY_LIMIT", 1000, 1, 100000)
    connection = None
    try:
        connection = _connect(database_url)
        result = verify_restored_deletion(connection, limit=limit)
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0 if result["status"] == "clean" else 2
    except Exception as exc:  # keep the CLI usable for drill automation
        print(
            json.dumps(
                {
                    "event": "restore_deletion_reconciliation_error",
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


if __name__ == "__main__":  # pragma: no cover - CLI dispatch
    raise SystemExit(main())
