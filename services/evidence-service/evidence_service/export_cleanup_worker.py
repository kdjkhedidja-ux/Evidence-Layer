"""Expire export artifacts and invalidate their grants.

This is a narrow maintenance operation, not a customer-data deletion. The
function is security-definer and owned by the migration owner; the continuous
worker calls it with the same privileged connection used for queue cleanup.
"""

from __future__ import annotations

import os
from typing import Any


def _connect(database_url: str) -> Any:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("psycopg is required for export cleanup") from exc
    return psycopg.connect(database_url)


def run_export_cleanup(*, connection: Any | None = None) -> int:
    owns_connection = connection is None
    if connection is None:
        database_url = os.environ.get("EVIDENCE_EXPORT_CLEANUP_DATABASE_URL", "")
        if not database_url:
            raise RuntimeError("EVIDENCE_EXPORT_CLEANUP_DATABASE_URL is required")
        connection = _connect(database_url)
    try:
        with connection.cursor() as cursor:
            cursor.execute("select private.cleanup_expired_exports()")
            row = cursor.fetchone()
        connection.commit()
    finally:
        if owns_connection:
            connection.close()
    if not row:
        return 0
    return int(row[0])
