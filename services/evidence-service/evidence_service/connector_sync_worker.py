"""Background GitHub connector sync.

The worker uses the service-role PostgREST credential to read connected GitHub
connections and write their normalized documents. It never logs document
content or credentials.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from evidence_service.config import SupabaseSettings
from evidence_service.connector_repository import ConnectorRepository
from evidence_service.github_app_sync_service import (
    GitHubAppSyncError,
    GitHubAppSyncService,
)


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def build_worker() -> tuple[ConnectorRepository, GitHubAppSyncService, int, str]:
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    app_id = os.environ.get("GITHUB_APP_ID") or os.environ.get("GIT_APP_ID", "")
    private_key = (
        os.environ.get("GITHUB_APP_PRIVATE_KEY")
        or os.environ.get("GIT_APP_PRIVATE_KEY", "")
    )
    if not all((supabase_url, service_key, app_id, private_key)):
        raise ValueError("connector worker configuration is incomplete")
    settings = SupabaseSettings(
        supabase_url=supabase_url,
        publishable_key=service_key,
        jwt_issuer=f"{supabase_url}/auth/v1",
        jwt_audience="authenticated",
        jwt_algorithms=("RS256",),
    )
    repository = ConnectorRepository(settings, service_mode=True)
    service = GitHubAppSyncService(repository, app_id=app_id, private_key=private_key)
    interval = _int_env("CONNECTOR_SYNC_INTERVAL_SECONDS", 900, 60, 86400)
    return repository, service, interval, service_key


async def run_once(
    repository: ConnectorRepository,
    service: GitHubAppSyncService,
    *,
    access_token: str,
    limit: int = 100,
) -> dict:
    connections = await repository.list_syncable_connections(
        access_token, provider="github", limit=limit
    )
    results = []
    for connection in connections:
        try:
            result = await service.sync(
                access_token,
                connection.connection_id,
                limit=_int_env("CONNECTOR_DOCUMENT_LIMIT", 10, 1, 100),
            )
            results.append({
                "connection_id": str(connection.connection_id),
                "status": "ok",
                "listed": result.listed,
                "ingested": result.ingested,
                "unchanged": result.unchanged,
                "skipped": result.skipped,
            })
        except GitHubAppSyncError:
            results.append({
                "connection_id": str(connection.connection_id),
                "status": "error",
            })
        except Exception:
            results.append({
                "connection_id": str(connection.connection_id),
                "status": "error",
            })
    return {"event": "connector_sync_cycle", "connections": len(connections), "results": results}


async def main() -> int:
    try:
        repository, service, interval, service_key = build_worker()
    except (TypeError, ValueError):
        print(json.dumps({"event": "connector_worker_configuration_invalid"}), file=sys.stderr)
        return 2
    print(
        json.dumps({"event": "connector_worker_started", "interval_seconds": interval}),
        flush=True,
    )
    while True:
        try:
            result = await run_once(repository, service, access_token=service_key)
            print(json.dumps(result), flush=True)
        except Exception:
            print(json.dumps({"event": "connector_worker_error"}), flush=True)
        await asyncio.sleep(interval)


if __name__ == "__main__":  # pragma: no cover - CLI dispatch
    raise SystemExit(asyncio.run(main()))
