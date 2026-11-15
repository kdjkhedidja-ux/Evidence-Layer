"""Executes approved shared-workspace deletion requests.

Approval, owner-set revision, and the withdrawal window are enforced by the
database functions. This worker only drains requests whose window has elapsed.
"""

from __future__ import annotations

DUE_SQL = """
select request_id from private.workspace_deletion_requests
 where status = 'approved'
   and execute_after is not null
   and execute_after <= pg_catalog.clock_timestamp()
 order by execute_after
 limit %s
 for update skip locked
"""
EXECUTE_SQL = "select private.execute_workspace_deletion(%s)"
DEFAULT_LIMIT = 10


def run_workspace_deletion(connection, *, limit: int = DEFAULT_LIMIT) -> dict:
    with connection.cursor() as cursor:
        cursor.execute(DUE_SQL, (max(1, min(limit, 100)),))
        request_ids = [row[0] for row in cursor.fetchall()]
    connection.commit()
    executed = 0
    stale = 0
    for request_id in request_ids:
        with connection.cursor() as cursor:
            cursor.execute(EXECUTE_SQL, (str(request_id),))
            row = cursor.fetchone()
        connection.commit()
        if row and isinstance(row[0], dict):
            if row[0].get("status") == "executed":
                executed += 1
            elif row[0].get("status") == "stale":
                stale += 1
    return {
        "status": "ok",
        "due": len(request_ids),
        "executed": executed,
        "stale": stale,
    }
