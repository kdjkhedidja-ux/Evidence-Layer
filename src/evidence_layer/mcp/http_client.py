"""HTTP client for the authenticated Evidence Layer retrieve/document API."""

from __future__ import annotations

from typing import Any

import httpx


class EvidenceApiError(Exception):
    pass


class EvidenceApiClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        workspace_id: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._workspace_id = workspace_id
        self._transport = transport
        self._timeout = timeout_seconds

    def _request(self, method: str, path: str, *, json: object | None = None) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                response = client.request(
                    method,
                    f"{self._base_url}{path}",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json=json,
                )
        except httpx.HTTPError as exc:
            raise EvidenceApiError("Evidence API request failed") from exc
        if response.status_code >= 300:
            raise EvidenceApiError(f"Evidence API returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise EvidenceApiError("Evidence API returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise EvidenceApiError("Evidence API returned an invalid response")
        return payload

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        max_tokens: int = 1200,
        max_chars: int = 6000,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/personal-api-keys/workspaces/{self._workspace_id}/retrieve",
            json={
                "query": query,
                "top_k": top_k,
                "max_tokens": max_tokens,
                "max_chars": max_chars,
            },
        )

    def get_document(self, evidence_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/v1/personal-api-keys/workspaces/{self._workspace_id}/evidence/{evidence_id}",
        )
