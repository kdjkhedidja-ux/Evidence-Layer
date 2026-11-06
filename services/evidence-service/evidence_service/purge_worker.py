"""Dedicated-process entrypoint for the bounded account purge slice.

The process must connect as the NOLOGIN account_deletion_worker role through an
operator-managed local database credential. It is never imported by the HTTP
application and never receives browser or MCP credentials.
"""

from __future__ import annotations

import argparse
import json
import os
from uuid import UUID


def run_account_deletion_slice(request_id: UUID) -> dict:
    database_url = os.environ.get("EVIDENCE_PURGE_DATABASE_URL")
    if not database_url:
        raise RuntimeError("EVIDENCE_PURGE_DATABASE_URL is required")

    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("psycopg is required only in the dedicated worker process") from exc

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select private.run_account_deletion_slice(%s)",
                (str(request_id),),
            )
            result = cursor.fetchone()
            if not result or not isinstance(result[0], dict):
                raise RuntimeError("worker returned no structured result")
            connection.commit()
            return result[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one bounded account deletion slice")
    parser.add_argument("request_id", type=UUID)
    args = parser.parse_args()
    print(json.dumps(run_account_deletion_slice(args.request_id), sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI dispatch
    raise SystemExit(main())
