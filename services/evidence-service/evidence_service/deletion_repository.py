"""User-scoped deletion request and manifest status access."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

import httpx

from evidence_service.config import SupabaseSettings


class DeletionRequestUnavailable(Exception):
    """The deletion request Data API operation could not complete safely."""


@dataclass(frozen=True)
class DeletionRequest:
    request_id: UUID
    requester_user_id: UUID
    scope_type: str
    status: str
    created_at: datetime
    accepted_at: datetime | None


class DeletionRepository(Protocol):
    async def create_request(
        self, access_token: str, *, principal_id: UUID, idempotency_key: str
    ) -> DeletionRequest: ...

    async def accept_request(
        self, access_token: str, *, request_id: UUID
    ) -> DeletionRequest: ...

    async def get_request(
        self, access_token: str, *, principal_id: UUID, request_id: UUID
    ) -> DeletionRequest | None: ...

    async def delete_personal_workspace_data(
        self, access_token: str
    ) -> dict[str, object]: ...

    async def delete_personal_workspace(
        self, access_token: str
    ) -> dict[str, object]: ...


class SupabaseDeletionRepository:
    def __init__(
        self,
        settings: SupabaseSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._base_url = settings.supabase_url
        self._publishable_key = settings.publishable_key
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    def _headers(self, access_token: str) -> dict[str, str]:
        return {
            "apikey": self._publishable_key,
            "authorization": f"Bearer {access_token}",
            "content-type": "application/json",
        }

    async def create_request(
        self, access_token: str, *, principal_id: UUID, idempotency_key: str
    ) -> DeletionRequest:
        request_id = uuid4()
        payload = {
            "request_id": str(request_id),
            "requester_user_id": str(principal_id),
            "scope_type": "account",
            "scope_id": str(principal_id),
            "idempotency_key": idempotency_key,
        }
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self._base_url}/rest/v1/data_deletion_requests",
                    headers={
                        **self._headers(access_token),
                        "prefer": "return=representation",
                    },
                    json=payload,
                )
            if response.status_code == 409:
                existing = await self._find_by_idempotency(
                    access_token, principal_id=principal_id, idempotency_key=idempotency_key
                )
                if existing is not None:
                    return existing
            return self._parse_single_response(response)
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            raise DeletionRequestUnavailable from exc

    async def accept_request(
        self, access_token: str, *, request_id: UUID
    ) -> DeletionRequest:
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self._base_url}/rest/v1/rpc/accept_account_deletion_v2",
                    headers=self._headers(access_token),
                    json={"p_request_id": str(request_id)},
                )
            return self._parse_single_response(response)
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            raise DeletionRequestUnavailable from exc

    async def get_request(
        self, access_token: str, *, principal_id: UUID, request_id: UUID
    ) -> DeletionRequest | None:
        try:
            async with self._client() as client:
                response = await client.get(
                    f"{self._base_url}/rest/v1/data_deletion_requests",
                    headers=self._headers(access_token),
                    params={
                        "select": "request_id,requester_user_id,scope_type,status,created_at,accepted_at",
                        "request_id": f"eq.{request_id}",
                        "requester_user_id": f"eq.{principal_id}",
                    },
                )
            if response.status_code != 200:
                raise DeletionRequestUnavailable
            rows = response.json()
            if not isinstance(rows, list):
                raise DeletionRequestUnavailable
            if not rows:
                return None
            if len(rows) != 1:
                raise DeletionRequestUnavailable
            return self._parse_row(rows[0])
        except DeletionRequestUnavailable:
            raise
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            raise DeletionRequestUnavailable from exc

    async def _find_by_idempotency(
        self, access_token: str, *, principal_id: UUID, idempotency_key: str
    ) -> DeletionRequest | None:
        async with self._client() as client:
            response = await client.get(
                f"{self._base_url}/rest/v1/data_deletion_requests",
                headers=self._headers(access_token),
                params={
                    "select": "request_id,requester_user_id,scope_type,status,created_at,accepted_at",
                    "requester_user_id": f"eq.{principal_id}",
                    "idempotency_key": f"eq.{idempotency_key}",
                },
            )
        if response.status_code != 200:
            raise DeletionRequestUnavailable
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            return None
        if len(rows) != 1:
            raise DeletionRequestUnavailable
        return self._parse_row(rows[0])

    async def delete_personal_workspace_data(
        self, access_token: str
    ) -> dict[str, object]:
        return await self._personal_deletion_rpc(
            access_token, "delete_personal_workspace_data"
        )

    async def delete_personal_workspace(
        self, access_token: str
    ) -> dict[str, object]:
        return await self._personal_deletion_rpc(
            access_token, "delete_personal_workspace"
        )

    async def _personal_deletion_rpc(
        self, access_token: str, function_name: str
    ) -> dict[str, object]:
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self._base_url}/rest/v1/rpc/{function_name}",
                    headers=self._headers(access_token),
                    json={},
                )
            if response.status_code not in {200, 201}:
                raise DeletionRequestUnavailable
            payload = response.json()
            if not isinstance(payload, dict):
                raise DeletionRequestUnavailable
            return payload
        except DeletionRequestUnavailable:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise DeletionRequestUnavailable from exc

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=self._transport,
        )

    def _parse_single_response(self, response: httpx.Response) -> DeletionRequest:
        if response.status_code not in {200, 201}:
            raise DeletionRequestUnavailable
        payload = response.json()
        if isinstance(payload, list):
            if len(payload) != 1:
                raise DeletionRequestUnavailable
            payload = payload[0]
        if not isinstance(payload, dict):
            raise DeletionRequestUnavailable
        return self._parse_row(payload)

    def _parse_row(self, row: dict) -> DeletionRequest:
        return DeletionRequest(
            request_id=UUID(str(row["request_id"])),
            requester_user_id=UUID(str(row["requester_user_id"])),
            scope_type=str(row["scope_type"]),
            status=str(row["status"]),
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
            accepted_at=(
                datetime.fromisoformat(str(row["accepted_at"]).replace("Z", "+00:00"))
                if row.get("accepted_at") is not None
                else None
            ),
        )
