"""PostgREST RPC bridge for shared-workspace deletion approvals."""

from __future__ import annotations

from uuid import UUID

import httpx

from evidence_service.config import SupabaseSettings


class WorkspaceDeletionForbidden(Exception):
    pass


class WorkspaceDeletionUnavailable(Exception):
    pass


class SupabaseWorkspaceDeletionRepository:
    def __init__(self, settings: SupabaseSettings, *,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    async def _rpc(self, name: str, access_token: str, payload: dict) -> dict | None:
        if not isinstance(access_token, str) or not access_token.strip():
            raise WorkspaceDeletionForbidden
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
                trust_env=False,
            ) as client:
                response = await client.post(
                    f"{self._settings.supabase_url.rstrip('/')}/rest/v1/rpc/{name}",
                    json=payload,
                    headers={
                        "apikey": self._settings.publishable_key,
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            raise WorkspaceDeletionUnavailable from exc
        if response.status_code in {401, 403}:
            raise WorkspaceDeletionForbidden
        if response.status_code != 200:
            raise WorkspaceDeletionUnavailable
        if not response.content:
            return None
        try:
            payload = response.json()
        except ValueError as exc:
            raise WorkspaceDeletionUnavailable from exc
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise WorkspaceDeletionUnavailable
        return payload

    async def request(self, access_token: str, *, workspace_id: UUID) -> dict:
        return await self._rpc(
            "request_workspace_deletion", access_token,
            {"p_workspace_id": str(workspace_id)},
        ) or {}

    async def status(self, access_token: str, *, workspace_id: UUID) -> dict | None:
        return await self._rpc(
            "workspace_deletion_status", access_token,
            {"p_workspace_id": str(workspace_id)},
        )

    async def approve(self, access_token: str, *, request_id: UUID) -> dict:
        return await self._rpc(
            "approve_workspace_deletion", access_token,
            {"p_request_id": str(request_id)},
        ) or {}

    async def withdraw(self, access_token: str, *, request_id: UUID) -> dict:
        return await self._rpc(
            "withdraw_workspace_deletion_approval", access_token,
            {"p_request_id": str(request_id)},
        ) or {}

    async def cancel(self, access_token: str, *, request_id: UUID) -> dict:
        return await self._rpc(
            "cancel_workspace_deletion", access_token,
            {"p_request_id": str(request_id)},
        ) or {}
