"""Read-only workspace quota counters."""

from __future__ import annotations

from uuid import UUID

import httpx

from evidence_service.config import SupabaseSettings


class WorkspaceUsageForbidden(Exception):
    pass


class WorkspaceUsageUnavailable(Exception):
    pass


class SupabaseWorkspaceUsageRepository:
    def __init__(self, settings: SupabaseSettings, *,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    async def usage(self, access_token: str, *, workspace_id: UUID) -> dict:
        if (
            not isinstance(access_token, str)
            or not access_token.strip()
            or type(workspace_id) is not UUID
        ):
            raise WorkspaceUsageForbidden
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
                trust_env=False,
            ) as client:
                response = await client.post(
                    f"{self._settings.supabase_url.rstrip('/')}/rest/v1/rpc/workspace_source_usage",
                    json={"p_workspace_id": str(workspace_id)},
                    headers={
                        "apikey": self._settings.publishable_key,
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            raise WorkspaceUsageUnavailable from exc
        if response.status_code in {401, 403}:
            raise WorkspaceUsageForbidden
        if response.status_code != 200:
            raise WorkspaceUsageUnavailable
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError
            return {
                "version_count": int(payload["version_count"]),
                "total_bytes": int(payload["total_bytes"]),
                "staged_count": int(payload["staged_count"]),
                "pending_invitations": int(payload["pending_invitations"]),
            }
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise WorkspaceUsageUnavailable from exc
