"""User-scoped organization management through security-definer RPCs."""

from __future__ import annotations

from typing import Any

import httpx

from evidence_service.config import SupabaseSettings


class OrganizationDataUnavailable(Exception):
    """The organization RPC could not complete safely."""


class OrganizationAccessDenied(Exception):
    """The caller is not an active organization admin."""


class OrganizationMemberNotFound(Exception):
    """The requested organization member or email does not exist."""


class OrganizationValidationError(Exception):
    """The organization RPC rejected the supplied value."""


class SupabaseOrganizationRepository:
    def __init__(
        self,
        settings: SupabaseSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    async def overview(self, access_token: str) -> dict[str, Any]:
        return await self._rpc(access_token, "organization_overview", {})

    async def create_group_workspace(
        self, access_token: str, name: str
    ) -> dict[str, Any]:
        return await self._rpc(
            access_token, "create_group_workspace", {"p_name": name}
        )

    async def add_member(
        self, access_token: str, email: str, role: str
    ) -> dict[str, Any]:
        return await self._rpc(
            access_token,
            "add_organization_member",
            {"p_email": email, "p_role": role},
        )

    async def update_member_role(
        self, access_token: str, user_id: str, role: str
    ) -> dict[str, Any]:
        return await self._rpc(
            access_token,
            "update_organization_member_role",
            {"p_user_id": user_id, "p_role": role},
        )

    async def remove_member(
        self, access_token: str, user_id: str
    ) -> dict[str, Any]:
        return await self._rpc(
            access_token,
            "remove_organization_member",
            {"p_user_id": user_id},
        )

    async def _rpc(
        self, access_token: str, function_name: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        headers = {
            "apikey": self._settings.publishable_key,
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._settings.supabase_url.rstrip('/')}/rest/v1/rpc/{function_name}",
                    json=payload,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise OrganizationDataUnavailable from exc

        if response.status_code in {200, 201}:
            try:
                data = response.json()
            except ValueError as exc:
                raise OrganizationDataUnavailable from exc
            if isinstance(data, list):
                data = data[0] if data else None
            if not isinstance(data, dict):
                raise OrganizationDataUnavailable
            return data

        code: str | None = None
        try:
            error = response.json()
            if isinstance(error, dict) and isinstance(error.get("code"), str):
                code = error["code"]
        except ValueError:
            pass

        if response.status_code in {401, 403} or code == "42501":
            raise OrganizationAccessDenied
        if response.status_code == 404 or code == "P0002":
            raise OrganizationMemberNotFound
        if response.status_code in {400, 422} or code == "22023":
            raise OrganizationValidationError
        raise OrganizationDataUnavailable
