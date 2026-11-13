"""PostgREST persistence for connector connections/documents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx

from evidence_service.config import SupabaseSettings

CONNECTION_SELECT = (
    "connection_id,user_id,provider,account_email,scopes,status,"
    "last_sync_at,last_error_category,access_token_expires_at,provider_config,workspace_id,"
    "created_at,updated_at"
)
CONNECTION_SECRET_SELECT = CONNECTION_SELECT + ",access_token_ciphertext,refresh_token_ciphertext"


class ConnectorRepositoryError(Exception):
    pass


class ConnectorNotFound(ConnectorRepositoryError):
    pass


class ConnectorDenied(ConnectorRepositoryError):
    pass


class ConnectorUnavailable(ConnectorRepositoryError):
    pass


@dataclass(frozen=True)
class ConnectorConnection:
    connection_id: UUID
    user_id: UUID
    provider: str
    account_email: str | None
    scopes: tuple[str, ...]
    status: str
    last_sync_at: datetime | None
    last_error_category: str | None
    access_token_expires_at: datetime | None
    provider_config: dict[str, Any]
    workspace_id: UUID | None = None
    access_token_ciphertext: str | None = None
    refresh_token_ciphertext: str | None = None


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class ConnectorRepository:
    def __init__(
        self,
        settings: SupabaseSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    def _headers(self, access_token: str, *, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self._settings.publishable_key,
            "authorization": f"Bearer {access_token}",
            "content-type": "application/json",
            "accept": "application/json",
        }
        if prefer:
            headers["prefer"] = prefer
        return headers

    async def _request(
        self,
        method: str,
        access_token: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: object | None = None,
        prefer: str | None = None,
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.request(
                    method,
                    f"{self._settings.supabase_url}/rest/v1/{path}",
                    headers=self._headers(access_token, prefer=prefer),
                    params=params,
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable from exc
        if response.status_code in (401, 403):
            raise ConnectorDenied
        if response.status_code == 404:
            raise ConnectorNotFound
        if response.status_code >= 300:
            raise ConnectorUnavailable
        return response

    async def upsert_connection(
        self,
        access_token: str,
        *,
        user_id: UUID,
        provider: str,
        account_email: str | None,
        scopes: list[str],
        access_token_ciphertext: str | None,
        refresh_token_ciphertext: str | None,
        access_token_expires_at: datetime | None,
        provider_config: dict[str, Any] | None = None,
        workspace_id: UUID | None = None,
    ) -> ConnectorConnection:
        body = {
            "user_id": str(user_id),
            "provider": provider,
            "account_email": account_email,
            "scopes": scopes,
            "access_token_ciphertext": access_token_ciphertext,
            "refresh_token_ciphertext": refresh_token_ciphertext,
            "access_token_expires_at": (
                access_token_expires_at.isoformat() if access_token_expires_at else None
            ),
            "provider_config": provider_config or {},
            "workspace_id": str(workspace_id) if workspace_id else None,
            "status": "connected",
            "last_error_category": None,
            "updated_at": datetime.now().astimezone().isoformat(),
        }
        response = await self._request(
            "POST",
            access_token,
            "connector_connections",
            params={"on_conflict": "user_id,provider", "select": CONNECTION_SELECT},
            body=body,
            prefer="resolution=merge-duplicates,return=representation",
        )
        return self._connection(response.json()[0], include_secrets=False)

    async def list_connections(self, access_token: str) -> list[ConnectorConnection]:
        response = await self._request(
            "GET",
            access_token,
            "connector_connections",
            params={"select": CONNECTION_SELECT, "order": "created_at.desc"},
        )
        return [
            self._connection(row, include_secrets=False)
            for row in response.json()
            if isinstance(row, dict)
        ]

    async def get_connection(
        self, access_token: str, connection_id: UUID
    ) -> ConnectorConnection:
        response = await self._request(
            "GET",
            access_token,
            "connector_connections",
            params={
                "select": CONNECTION_SECRET_SELECT,
                "connection_id": f"eq.{connection_id}",
                "limit": "1",
            },
        )
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            raise ConnectorNotFound
        return self._connection(rows[0], include_secrets=True)

    async def get_connection_for_provider(
        self, access_token: str, provider: str
    ) -> ConnectorConnection | None:
        response = await self._request(
            "GET",
            access_token,
            "connector_connections",
            params={
                "select": CONNECTION_SECRET_SELECT,
                "provider": f"eq.{provider}",
                "limit": "1",
            },
        )
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            return None
        return self._connection(rows[0], include_secrets=True)

    async def update_tokens(
        self,
        access_token: str,
        *,
        connection_id: UUID,
        access_token_ciphertext: str,
        refresh_token_ciphertext: str | None,
        access_token_expires_at: datetime | None,
    ) -> None:
        await self._request(
            "PATCH",
            access_token,
            "connector_connections",
            params={"connection_id": f"eq.{connection_id}"},
            body={
                "access_token_ciphertext": access_token_ciphertext,
                "refresh_token_ciphertext": refresh_token_ciphertext,
                "access_token_expires_at": (
                    access_token_expires_at.isoformat() if access_token_expires_at else None
                ),
                "status": "connected",
                "last_error_category": None,
                "updated_at": datetime.now().astimezone().isoformat(),
            },
            prefer="return=minimal",
        )

    async def mark_sync(
        self,
        access_token: str,
        *,
        connection_id: UUID,
        success: bool,
        error_category: str | None = None,
    ) -> None:
        await self._request(
            "PATCH",
            access_token,
            "connector_connections",
            params={"connection_id": f"eq.{connection_id}"},
            body={
                "last_sync_at": datetime.now().astimezone().isoformat(),
                "status": "connected" if success else "error",
                "last_error_category": (
                    error_category
                    if error_category
                    else (None if success else "sync_failed")
                ),
                "updated_at": datetime.now().astimezone().isoformat(),
            },
            prefer="return=minimal",
        )

    async def delete_connection(self, access_token: str, connection_id: UUID) -> None:
        await self._request(
            "DELETE",
            access_token,
            "connector_connections",
            params={"connection_id": f"eq.{connection_id}"},
            prefer="return=minimal",
        )

    async def personal_workspace_id(
        self, access_token: str, user_id: UUID
    ) -> UUID | None:
        response = await self._request(
            "GET",
            access_token,
            "workspace_memberships",
            params={
                "select": "workspace_id,role,status,workspaces(id,name,created_at)",
                "user_id": f"eq.{user_id}",
                "role": "eq.owner",
                "status": "eq.active",
                "order": "created_at.asc",
                "limit": "1",
            },
        )
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            return None
        workspace = rows[0].get("workspaces")
        if not isinstance(workspace, dict) or not workspace.get("id"):
            return None
        return UUID(str(workspace["id"]))

    async def set_connection_workspace(
        self, access_token: str, connection_id: UUID, workspace_id: UUID
    ) -> None:
        await self._request(
            "PATCH",
            access_token,
            "connector_connections",
            params={"connection_id": f"eq.{connection_id}"},
            body={"workspace_id": str(workspace_id), "updated_at": datetime.now().astimezone().isoformat()},
            prefer="return=minimal",
        )

    async def upsert_evidence_documents(
        self,
        access_token: str,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        documents: list[dict[str, Any]],
    ) -> None:
        if not documents:
            return
        await self._request(
            "POST",
            access_token,
            "rpc/upsert_connector_evidence",
            body={
                "p_workspace_id": str(workspace_id),
                "p_connection_id": str(connection_id),
                "p_documents": documents,
            },
        )

    async def delete_stale_connector_evidence(
        self, access_token: str, connection_id: UUID, current_external_ids: set[str]
    ) -> None:
        response = await self._request(
            "GET",
            access_token,
            "evidence_records",
            params={
                "select": "id,connector_external_id",
                "connector_connection_id": f"eq.{connection_id}",
                "limit": "1000",
            },
        )
        rows = response.json()
        stale = [
            str(row["id"])
            for row in rows
            if isinstance(row, dict)
            and row.get("id")
            and str(row.get("connector_external_id") or "") not in current_external_ids
        ]
        if not stale:
            return
        await self._request(
            "DELETE",
            access_token,
            "evidence_records",
            params={"id": "in.(" + ",".join(stale) + ")"},
            prefer="return=minimal",
        )

    async def upsert_documents(
        self, access_token: str, documents: list[dict[str, Any]]
    ) -> None:
        if not documents:
            return
        await self._request(
            "POST",
            access_token,
            "connector_documents",
            params={
                "on_conflict": "connection_id,external_id",
                "select": "document_id",
            },
            body=documents,
            prefer="resolution=merge-duplicates,return=minimal",
        )

    def _connection(self, row: dict, *, include_secrets: bool) -> ConnectorConnection:
        try:
            connection_id = UUID(str(row["connection_id"]))
            user_id = UUID(str(row["user_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ConnectorUnavailable from exc
        return ConnectorConnection(
            connection_id=connection_id,
            user_id=user_id,
            provider=str(row.get("provider") or ""),
            account_email=row.get("account_email"),
            scopes=tuple(str(s) for s in (row.get("scopes") or [])),
            status=str(row.get("status") or "error"),
            last_sync_at=_parse_dt(row.get("last_sync_at")),
            last_error_category=row.get("last_error_category"),
            access_token_expires_at=_parse_dt(row.get("access_token_expires_at")),
            provider_config=(
                row.get("provider_config")
                if isinstance(row.get("provider_config"), dict)
                else {}
            ),
            workspace_id=(
                UUID(str(row["workspace_id"]))
                if row.get("workspace_id")
                else None
            ),
            access_token_ciphertext=(
                row.get("access_token_ciphertext") if include_secrets else None
            ),
            refresh_token_ciphertext=(
                row.get("refresh_token_ciphertext") if include_secrets else None
            ),
        )
