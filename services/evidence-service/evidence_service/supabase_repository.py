"""User-scoped Supabase Data API access; never uses a service-role credential."""

from __future__ import annotations

from uuid import UUID

import httpx

from evidence_service.auth import AuthenticationRejected, Principal
from evidence_service.config import SupabaseSettings
from evidence_service.tenant_context import (
    AccountProfile,
    WorkspaceAccessDenied,
    WorkspaceDirectory,
    WorkspaceMembership,
)


class WorkspaceDataUnavailable(Exception):
    """The user-scoped workspace directory request could not be completed."""


class SupabaseWorkspaceRepository(WorkspaceDirectory):
    def __init__(
        self,
        settings: SupabaseSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    async def ensure_personal_workspace(
        self, principal: Principal, access_token: str
    ) -> WorkspaceMembership:
        rows = await self._request_personal_workspace(access_token)
        if len(rows) != 1:
            raise WorkspaceDataUnavailable

        row = rows[0]
        try:
            user_id = UUID(str(row["user_id"]))
            workspace_id = UUID(str(row["workspace_id"]))
            workspace_name = row["workspace_name"]
            role = row["role"]
            if (
                user_id != principal.subject
                or not isinstance(workspace_name, str)
                or not workspace_name.strip()
                or role != "owner"
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise WorkspaceDataUnavailable from exc

        return WorkspaceMembership(
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            role="owner",
        )

    async def list_memberships(
        self, principal: Principal, access_token: str
    ) -> list[WorkspaceMembership]:
        rows = await self._request(
            principal,
            access_token,
            {
                "user_id": f"eq.{principal.subject}",
                "status": "eq.active",
                "order": "workspace_id.asc",
            },
        )
        return [self._parse_row(row, principal) for row in rows]

    async def get_membership(
        self, principal: Principal, access_token: str, workspace_id: UUID
    ) -> WorkspaceMembership | None:
        rows = await self._request(
            principal,
            access_token,
            {
                "user_id": f"eq.{principal.subject}",
                "workspace_id": f"eq.{workspace_id}",
                "status": "eq.active",
                "limit": "1",
            },
        )
        if not rows:
            return None
        return self._parse_row(rows[0], principal)

    async def get_account(
        self, principal: Principal, access_token: str
    ) -> AccountProfile:
        headers = self._headers(access_token)
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    f"{self._settings.supabase_url.rstrip('/')}/rest/v1/accounts",
                    params={
                        "select": "account_type,organization_name",
                        "user_id": f"eq.{principal.subject}",
                        "limit": "1",
                    },
                    headers=headers,
                )
            if response.status_code != 200:
                raise WorkspaceDataUnavailable
            rows = response.json()
            if not isinstance(rows, list) or len(rows) > 1:
                raise WorkspaceDataUnavailable
            if not rows:
                return AccountProfile(account_type="individual", organization_name=None)
            return self._parse_account(rows[0])
        except WorkspaceDataUnavailable:
            raise
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            raise WorkspaceDataUnavailable from exc

    async def create_organization_account(
        self, principal: Principal, access_token: str, name: str
    ) -> AccountProfile:
        headers = self._headers(access_token)
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._settings.supabase_url.rstrip('/')}/rest/v1/rpc/create_organization_account",
                    json={"p_name": name},
                    headers=headers,
                )
            if response.status_code not in {200, 201}:
                raise WorkspaceDataUnavailable
            payload = response.json()
            if isinstance(payload, list):
                if not payload:
                    raise WorkspaceDataUnavailable
                payload = payload[0]
            if not isinstance(payload, dict):
                raise WorkspaceDataUnavailable
            return self._parse_account(payload)
        except WorkspaceDataUnavailable:
            raise
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            raise WorkspaceDataUnavailable from exc

    def _headers(self, access_token: str) -> dict[str, str]:
        return {
            "apikey": self._settings.publishable_key,
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

    def _parse_account(self, row: dict[str, object]) -> AccountProfile:
        account_type = row.get("account_type")
        organization_name = row.get("organization_name")
        if account_type not in {"individual", "organization"}:
            raise WorkspaceDataUnavailable
        if account_type == "individual":
            return AccountProfile(account_type="individual", organization_name=None)
        if (
            not isinstance(organization_name, str)
            or not organization_name.strip()
            or len(organization_name.strip()) > 120
        ):
            raise WorkspaceDataUnavailable
        return AccountProfile(
            account_type="organization", organization_name=organization_name.strip()
        )

    async def _request(
        self,
        principal: Principal,
        access_token: str,
        filters: dict[str, str],
    ) -> list[dict[str, object]]:
        params = {
            "select": "user_id,workspace_id,role,workspace:workspaces!inner(id,name,organization_id)",
            **filters,
        }
        headers = {
            "apikey": self._settings.publishable_key,
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    self._settings.rest_url,
                    params=params,
                    headers=headers,
                )
            if response.status_code != 200:
                raise WorkspaceDataUnavailable
            payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise WorkspaceDataUnavailable from exc
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise WorkspaceDataUnavailable
        return payload

    async def _request_personal_workspace(
        self, access_token: str
    ) -> list[dict[str, object]]:
        headers = {
            "apikey": self._settings.publishable_key,
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    self._settings.personal_workspace_rpc_url,
                    json={},
                    headers=headers,
                )
            if response.status_code != 200:
                error_code = self._postgrest_error_code(response)
                if response.status_code == 401 and error_code in {
                    "PGRST301",
                    "PGRST303",
                    "PT401",
                }:
                    raise AuthenticationRejected
                if response.status_code == 403 and error_code == "PT403":
                    raise WorkspaceAccessDenied
                raise WorkspaceDataUnavailable
            payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise WorkspaceDataUnavailable from exc
        if not isinstance(payload, list) or any(
            not isinstance(row, dict) for row in payload
        ):
            raise WorkspaceDataUnavailable
        return payload

    @staticmethod
    def _postgrest_error_code(response: httpx.Response) -> str | None:
        """Return only a structured error code; never expose provider details."""
        try:
            payload = response.json()
        except (ValueError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        code = payload.get("code")
        return code if isinstance(code, str) else None

    @staticmethod
    def _parse_row(
        row: dict[str, object], principal: Principal
    ) -> WorkspaceMembership:
        try:
            user_id = UUID(str(row["user_id"]))
            workspace_id = UUID(str(row["workspace_id"]))
            role = row["role"]
            workspace = row["workspace"]
            if user_id != principal.subject or role not in {"owner", "admin", "member"}:
                raise ValueError
            if not isinstance(workspace, dict):
                raise ValueError
            embedded_id = UUID(str(workspace["id"]))
            name = workspace["name"]
            organization_id = workspace.get("organization_id")
            if embedded_id != workspace_id or not isinstance(name, str):
                raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise WorkspaceDataUnavailable from exc
        return WorkspaceMembership(
            workspace_id=workspace_id,
            workspace_name=name,
            role=role,
            workspace_type="group" if organization_id is not None else "personal",
        )
