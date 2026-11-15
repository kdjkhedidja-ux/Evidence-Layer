"""PostgREST RPC bridge for one-time workspace invitations."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import httpx

from evidence_service.config import SupabaseSettings


class InvitationForbidden(Exception):
    pass


class InvitationRejected(Exception):
    pass


class InvitationUnavailable(Exception):
    pass


class Invitation:
    def __init__(self, *, invitation_id: UUID, email: str, role: str,
                 created_at: datetime, expires_at: datetime,
                 accepted_at: datetime | None, revoked_at: datetime | None) -> None:
        self.invitation_id = invitation_id
        self.email = email
        self.role = role
        self.created_at = created_at
        self.expires_at = expires_at
        self.accepted_at = accepted_at
        self.revoked_at = revoked_at


def _dt(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise InvitationUnavailable
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise InvitationUnavailable from exc
    if parsed.tzinfo is None:
        raise InvitationUnavailable
    return parsed.astimezone(timezone.utc)


def _invitation(row: object) -> Invitation:
    if not isinstance(row, dict):
        raise InvitationUnavailable
    try:
        invitation_id = UUID(str(row["out_invitation_id"]))
        email = row["out_email"]
        role = row["out_role"]
        created_at = _dt(row["out_created_at"])
        expires_at = _dt(row["out_expires_at"])
        accepted_at = _dt(row.get("out_accepted_at"))
        revoked_at = _dt(row.get("out_revoked_at"))
    except (KeyError, TypeError, ValueError) as exc:
        raise InvitationUnavailable from exc
    if role not in {"owner", "member"} or created_at is None or expires_at is None:
        raise InvitationUnavailable
    return Invitation(invitation_id=invitation_id, email=email, role=role,
                      created_at=created_at, expires_at=expires_at,
                      accepted_at=accepted_at, revoked_at=revoked_at)


class SupabaseWorkspaceInvitationRepository:
    def __init__(self, settings: SupabaseSettings, *,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    async def _rpc(self, name: str, access_token: str, body: dict) -> tuple[int, list]:
        if not isinstance(access_token, str) or not access_token.strip():
            raise InvitationForbidden
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
                trust_env=False,
            ) as client:
                response = await client.post(
                    f"{self._settings.supabase_url.rstrip('/')}/rest/v1/rpc/{name}",
                    json=body,
                    headers={
                        "apikey": self._settings.publishable_key,
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            raise InvitationUnavailable from exc
        if response.status_code in {401, 403}:
            raise InvitationForbidden
        if response.status_code in {400, 409, 422}:
            raise InvitationRejected
        if response.status_code not in {200, 201, 204}:
            raise InvitationUnavailable
        if response.status_code == 204 or not response.content:
            return response.status_code, []
        try:
            payload = response.json()
        except ValueError as exc:
            raise InvitationUnavailable from exc
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            raise InvitationUnavailable
        return response.status_code, payload

    async def create(self, access_token: str, *, workspace_id: UUID, email: str,
                     token_hash: str, expires_at: datetime) -> Invitation:
        _, rows = await self._rpc(
            "create_workspace_invitation", access_token,
            {"p_workspace_id": str(workspace_id), "p_email": email,
             "p_token_hash": token_hash, "p_expires_at": expires_at.isoformat()},
        )
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise InvitationUnavailable
        row = rows[0]
        return Invitation(
            invitation_id=UUID(str(row["invitation_id"])),
            email=row["email"], role=row["role"],
            created_at=_dt(row["created_at"]), expires_at=_dt(row["expires_at"]),
            accepted_at=None, revoked_at=None,
        )

    async def list(self, access_token: str, *, workspace_id: UUID) -> list[Invitation]:
        _, rows = await self._rpc(
            "list_workspace_invitations", access_token,
            {"p_workspace_id": str(workspace_id)},
        )
        return [_invitation(row) for row in rows]

    async def revoke(self, access_token: str, *, invitation_id: UUID) -> None:
        await self._rpc(
            "revoke_workspace_invitation", access_token,
            {"p_invitation_id": str(invitation_id)},
        )

    async def accept(self, access_token: str, *, token_hash: str) -> tuple[UUID, str]:
        _, rows = await self._rpc(
            "accept_workspace_invitation", access_token,
            {"p_token_hash": token_hash},
        )
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise InvitationUnavailable
        row = rows[0]
        try:
            return UUID(str(row["workspace_id"])), row["role"]
        except (KeyError, TypeError, ValueError) as exc:
            raise InvitationUnavailable from exc
