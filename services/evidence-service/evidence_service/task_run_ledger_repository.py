"""Content-free, workspace-scoped task-run ledger repository."""

from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

import httpx

from evidence_service.config import SupabaseSettings


class TaskRunLedgerUnavailable(Exception):
    pass


class SupabaseTaskRunLedgerRepository:
    def __init__(self, settings: SupabaseSettings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    async def record(
        self,
        access_token: str,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        metrics: Mapping[str, Any],
    ) -> None:
        payload = {"workspace_id": str(workspace_id), "actor_user_id": str(actor_user_id), **dict(metrics)}
        response = await self._request("POST", access_token, json=payload, headers={"Prefer": "return=minimal"})
        if response.status_code not in (200, 201, 204):
            raise TaskRunLedgerUnavailable(f"ledger record failed: {response.status_code}")

    async def list_runs(self, access_token: str, *, workspace_id: UUID, limit: int = 100) -> list[dict[str, Any]]:
        response = await self._request(
            "GET",
            access_token,
            params={
                "select": "run_id,workspace_id,run_type,status,created_at,retrieval_latency_ms,retrieval_tokens,retrieval_chars,selected_count,chunk_count,relationship_count,scoring_provider,fallback_reason,use_jev,agent_provider,agent_model,agent_input_tokens,agent_output_tokens,agent_total_tokens,agent_cost_usd,agent_latency_ms,quality_status,quality_score",
                "workspace_id": f"eq.{workspace_id}",
                "order": "created_at.desc,run_id.desc",
                "limit": str(max(1, min(limit, 100))),
            },
        )
        if response.status_code != 200:
            raise TaskRunLedgerUnavailable(f"ledger list failed: {response.status_code}")
        data = response.json()
        if not isinstance(data, list):
            raise TaskRunLedgerUnavailable("ledger list returned invalid data")
        return [row for row in data if isinstance(row, dict)]

    async def _request(self, method: str, access_token: str, *, params: Mapping[str, str] | None = None, json: Any = None, headers: Mapping[str, str] | None = None) -> httpx.Response:
        request_headers = {
            "apikey": self._settings.publishable_key,
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        if headers:
            request_headers.update(headers)
        url = f"{self._settings.supabase_url.rstrip('/')}/rest/v1/task_run_ledger"
        try:
            async with httpx.AsyncClient(timeout=20, transport=self._transport, trust_env=False) as client:
                return await client.request(method, url, params=params, json=json, headers=request_headers)
        except httpx.HTTPError as exc:
            raise TaskRunLedgerUnavailable("ledger request failed") from exc
