"""Per-user usage caps for retrieval and the JEV budget.

Counting is atomic and durable in Postgres (``private.consume_usage``,
migration ``20261127100000``); this module only chooses the caps and calls the
two entry points:

* ``public.consume_user_usage`` with the signed-in user's JWT (web task-context);
* ``public.consume_personal_api_key_usage`` with a gateway proof that binds the
  caps, exactly like the signed API-key reads (API retrieve and Remote MCP).

Environment (all optional; an invalid value falls back to the default, which is
the conservative choice):

=====================================  =======  ==========================================
``EVIDENCE_USAGE_CAPS_ENABLED``        false    ``true`` turns enforcement on (after the migration)
``EVIDENCE_USER_RETRIEVAL_PER_MINUTE``  30      task-context + API + MCP retrievals per user per minute
``EVIDENCE_USER_RETRIEVAL_PER_DAY``     500     same, per user per UTC day
``EVIDENCE_USER_JEV_PER_DAY``           200     JEV-scored retrievals per user per UTC day
=====================================  =======  ==========================================

A value of ``0`` disables that one cap.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx

from evidence_service.config import SupabaseSettings

logger = logging.getLogger(__name__)

UsageKind = Literal["retrieval", "jev"]

DEFAULT_RETRIEVAL_PER_MINUTE = 30
DEFAULT_RETRIEVAL_PER_DAY = 500
DEFAULT_JEV_PER_DAY = 200
_MAX_PER_MINUTE = 1_000_000
_MAX_PER_DAY = 100_000_000


class UsageCapsUnavailable(RuntimeError):
    """The usage counter could not give an authoritative decision."""


class UsageCapsAccessDenied(RuntimeError):
    """The signed usage proof or the key was refused (maps to 401)."""


@dataclass(frozen=True)
class UsageDecision:
    allowed: bool
    window: Literal["minute", "day"] | None = None
    retry_after_seconds: int = 0


def _cap(values: Mapping[str, str], name: str, default: int, maximum: int) -> int:
    raw = values.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("usage_cap_invalid name=%s using_default=%s", name, default)
        return default
    if value < 0 or value > maximum:
        logger.warning("usage_cap_out_of_range name=%s using_default=%s", name, default)
        return default
    return value


@dataclass(frozen=True)
class UsageCapsSettings:
    enabled: bool = False
    retrieval_per_minute: int = DEFAULT_RETRIEVAL_PER_MINUTE
    retrieval_per_day: int = DEFAULT_RETRIEVAL_PER_DAY
    jev_per_day: int = DEFAULT_JEV_PER_DAY

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> UsageCapsSettings:
        values = os.environ if environment is None else environment
        return cls(
            enabled=values.get("EVIDENCE_USAGE_CAPS_ENABLED", "").strip() == "true",
            retrieval_per_minute=_cap(
                values, "EVIDENCE_USER_RETRIEVAL_PER_MINUTE",
                DEFAULT_RETRIEVAL_PER_MINUTE, _MAX_PER_MINUTE,
            ),
            retrieval_per_day=_cap(
                values, "EVIDENCE_USER_RETRIEVAL_PER_DAY",
                DEFAULT_RETRIEVAL_PER_DAY, _MAX_PER_DAY,
            ),
            jev_per_day=_cap(values, "EVIDENCE_USER_JEV_PER_DAY", DEFAULT_JEV_PER_DAY, _MAX_PER_DAY),
        )

    def limits(self, kind: UsageKind) -> tuple[int, int]:
        """(per_minute, per_day) for one kind; 0 means uncapped."""
        if kind == "retrieval":
            return self.retrieval_per_minute, self.retrieval_per_day
        return 0, self.jev_per_day


def usage_gateway_signature(
    secret: str,
    *,
    kind: UsageKind,
    key_id: UUID,
    workspace_id: UUID,
    key_hash: str,
    timestamp: int,
    nonce: UUID,
    per_minute: int,
    per_day: int,
) -> str:
    """HMAC proof checked by ``public.consume_personal_api_key_usage``.

    The ``v1-usage`` prefix keeps a usage proof from being replayed as a read
    proof (``v1``) and the reverse; the caps are part of the signed message.
    """
    message = "\n".join(
        (
            "v1-usage",
            kind,
            str(key_id),
            str(workspace_id),
            key_hash,
            str(timestamp),
            str(nonce),
            str(per_minute),
            str(per_day),
        )
    )
    return hmac.new(bytes.fromhex(secret), message.encode("utf-8"), hashlib.sha256).hexdigest()


def _decision(payload: Any) -> UsageDecision:
    if not isinstance(payload, dict) or not isinstance(payload.get("allowed"), bool):
        raise UsageCapsUnavailable("Usage decision is invalid")
    if payload["allowed"]:
        return UsageDecision(allowed=True)
    window = payload.get("window")
    retry_after = payload.get("retry_after_seconds")
    if window not in {"minute", "day"} or isinstance(retry_after, bool) or not isinstance(retry_after, int):
        raise UsageCapsUnavailable("Usage decision is invalid")
    return UsageDecision(allowed=False, window=window, retry_after_seconds=max(1, retry_after))


class SupabaseUsageCapsRepository:
    def __init__(
        self,
        settings: SupabaseSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    async def consume_for_user(
        self, access_token: str, kind: UsageKind, *, per_minute: int, per_day: int
    ) -> UsageDecision:
        response = await self._post(
            "rpc/consume_user_usage",
            {"p_kind": kind, "p_per_minute": per_minute, "p_per_day": per_day},
            access_token=access_token,
        )
        return _decision(self._json(response))

    async def consume_for_api_key(
        self,
        *,
        key_id: UUID,
        key_hash: str,
        workspace_id: UUID,
        kind: UsageKind,
        per_minute: int,
        per_day: int,
    ) -> UsageDecision:
        secret = self._settings.personal_api_key_gateway_secret
        if secret is None:
            raise UsageCapsUnavailable("Personal API key gateway is not configured")
        timestamp = int(time.time())
        nonce = uuid4()
        proof = usage_gateway_signature(
            secret,
            kind=kind,
            key_id=key_id,
            workspace_id=workspace_id,
            key_hash=key_hash,
            timestamp=timestamp,
            nonce=nonce,
            per_minute=per_minute,
            per_day=per_day,
        )
        response = await self._post(
            "rpc/consume_personal_api_key_usage",
            {
                "p_key_id": str(key_id),
                "p_workspace_id": str(workspace_id),
                "p_kind": kind,
                "p_per_minute": per_minute,
                "p_per_day": per_day,
            },
            headers={
                "x-evidence-api-key-hash": key_hash,
                "x-evidence-api-key-gateway-timestamp": str(timestamp),
                "x-evidence-api-key-gateway-nonce": str(nonce),
                "x-evidence-api-key-gateway-proof": proof,
            },
        )
        return _decision(self._json(response))

    async def _post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        access_token: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        request_headers = {
            "apikey": self._settings.publishable_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if access_token is not None:
            request_headers["Authorization"] = f"Bearer {access_token}"
        if headers:
            request_headers.update(headers)
        url = f"{self._settings.supabase_url.rstrip('/')}/rest/v1/{path}"
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds, transport=self._transport
            ) as client:
                response = await client.post(url, headers=request_headers, json=body)
        except httpx.HTTPError as exc:
            raise UsageCapsUnavailable("Usage counter is unreachable") from exc
        if response.status_code in {401, 403}:
            raise UsageCapsAccessDenied("Usage request was refused")
        if response.status_code >= 300:
            raise UsageCapsUnavailable("Usage counter is unavailable")
        return response

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise UsageCapsUnavailable("Usage decision is invalid") from exc
