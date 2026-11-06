"""Authoritative, request-time reads of account lifecycle state."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

import httpx

from evidence_service.config import SupabaseSettings


class AccountLifecycleReadError(Exception):
    """The authoritative lifecycle row could not be read safely."""


class AccountLifecycleReader(Protocol):
    async def read_state(self, subject_id: UUID, access_token: str) -> str | None: ...


class SupabaseAccountLifecycleReader:
    """Read the caller's own lifecycle state through the primary Data API.

    The request uses the verified user's JWT and the publishable key. RLS limits
    the row to ``auth.uid()``; no service credential or status cache is used.
    The configured Supabase API URL must point at the project's primary API.
    """

    def __init__(
        self,
        settings: SupabaseSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._url = f"{settings.supabase_url}/rest/v1/account_lifecycle"
        self._publishable_key = settings.publishable_key
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    async def read_state(self, subject_id: UUID, access_token: str) -> str | None:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    self._url,
                    params={
                        "select": "state",
                        "subject_id": f"eq.{subject_id}",
                    },
                    headers={
                        "apikey": self._publishable_key,
                        "authorization": f"Bearer {access_token}",
                    },
                )
            if response.status_code != 200:
                raise AccountLifecycleReadError
            rows = response.json()
        except AccountLifecycleReadError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise AccountLifecycleReadError from exc

        if not isinstance(rows, list):
            raise AccountLifecycleReadError
        if not rows:
            return None
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise AccountLifecycleReadError
        state = rows[0].get("state")
        if not isinstance(state, str):
            raise AccountLifecycleReadError
        return state
