"""Authenticated, uncached read of the external Recovery Fence Worker."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx


class RecoveryFenceConfigurationError(ValueError):
    """Recovery Fence settings are incomplete or unsafe."""


class RecoveryFenceUnavailable(RuntimeError):
    """The external Recovery Fence could not prove the current serving state."""


class RecoveryFenceTransientError(RecoveryFenceUnavailable):
    """No authoritative answer arrived (network error, timeout, 429 or 5xx).

    Only this failure is retried. A delivered answer, even an invalid or
    unverified one, is never retried, so retrying cannot turn a closed or
    suspicious fence into an open one.
    """


# One extra attempt absorbs a single dropped connection or cold start without
# hiding a real outage; each attempt still uses its own nonce and signature.
RECOVERY_FENCE_MAX_ATTEMPTS = 2
RECOVERY_FENCE_RETRY_DELAY_SECONDS = 0.2


class RecoveryFenceReader(Protocol):
    async def is_open(self) -> bool | None: ...


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


@dataclass(frozen=True)
class RecoveryFenceSettings:
    url: str
    key_id: str
    hmac_key: bytes
    timeout_seconds: float = 2.0

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> RecoveryFenceSettings:
        values = os.environ if environment is None else environment
        names = (
            "RECOVERY_FENCE_URL",
            "RECOVERY_FENCE_READ_KEY_ID",
            "RECOVERY_FENCE_READ_HMAC_KEY",
        )
        if not any(values.get(name, "").strip() for name in names):
            raise RecoveryFenceConfigurationError("Recovery Fence is not configured")
        if any(not values.get(name, "").strip() for name in names):
            raise RecoveryFenceConfigurationError("Recovery Fence settings are incomplete")

        url = values[names[0]].strip().rstrip("/")
        try:
            parsed = urlsplit(url)
            parsed.port
        except ValueError as exc:
            raise RecoveryFenceConfigurationError("Recovery Fence URL is invalid") from exc
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise RecoveryFenceConfigurationError("Recovery Fence URL is invalid")

        key_id = values[names[1]].strip()
        key = values[names[2]].encode("utf-8")
        if len(key) < 32 or len(key_id) > 128:
            raise RecoveryFenceConfigurationError("Recovery Fence authentication is invalid")
        return cls(url=url, key_id=key_id, hmac_key=key)


class CloudflareRecoveryFenceReader:
    """Make a signed, nonce-bound Worker read on every call; never cache open."""

    def __init__(
        self,
        settings: RecoveryFenceSettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> CloudflareRecoveryFenceReader:
        return cls(RecoveryFenceSettings.from_environment(environment), client=client)

    async def is_open(self) -> bool:
        for attempt in range(1, RECOVERY_FENCE_MAX_ATTEMPTS + 1):
            try:
                return await self._read_once()
            except RecoveryFenceTransientError:
                if attempt == RECOVERY_FENCE_MAX_ATTEMPTS:
                    raise
                await asyncio.sleep(RECOVERY_FENCE_RETRY_DELAY_SECONDS)
        raise RecoveryFenceUnavailable("Recovery Fence is unavailable")  # pragma: no cover

    async def _read_once(self) -> bool:
        timestamp = int(time.time())
        nonce = secrets.token_urlsafe(32)
        path = "/v1/recovery"
        canonical = (
            f"GET\n{self._settings.url}\n{path}\n{self._settings.key_id}\n{timestamp}\n{nonce}"
        ).encode("utf-8")
        signature = _base64url(
            hmac.new(self._settings.hmac_key, canonical, hashlib.sha256).digest()
        )
        headers = {
            "x-recovery-key-id": self._settings.key_id,
            "x-recovery-timestamp": str(timestamp),
            "x-recovery-nonce": nonce,
            "x-recovery-signature": signature,
            "accept": "application/json",
            "cache-control": "no-store",
        }

        try:
            if self._client is not None:
                response = await self._client.get(
                    f"{self._settings.url}{path}",
                    headers=headers,
                    timeout=self._settings.timeout_seconds,
                    follow_redirects=False,
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self._settings.timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    response = await client.get(
                        f"{self._settings.url}{path}", headers=headers
                    )
        except httpx.HTTPError as exc:
            raise RecoveryFenceTransientError("Recovery Fence is unavailable") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise RecoveryFenceTransientError("Recovery Fence is temporarily unavailable")
        if response.status_code != 200:
            raise RecoveryFenceUnavailable("Recovery Fence did not return an authoritative status")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RecoveryFenceUnavailable("Recovery Fence response is invalid") from exc

        if not isinstance(payload, dict):
            raise RecoveryFenceUnavailable("Recovery Fence response is invalid")
        if payload.get("request_nonce") != nonce or payload.get("verified") is not True:
            raise RecoveryFenceUnavailable("Recovery Fence freshness was not proven")
        if payload.get("state") not in {"closed", "reconciling", "open"}:
            raise RecoveryFenceUnavailable("Recovery Fence state is unknown")
        for field in ("generation", "sequence", "state_version"):
            value = payload.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise RecoveryFenceUnavailable("Recovery Fence version is invalid")
        return payload["state"] == "open"
