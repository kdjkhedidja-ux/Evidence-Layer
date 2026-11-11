"""Small bounded HTTP helper shared by read-only connectors."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .base import ConnectorError

RETRY_STATUSES = {429, 500, 502, 503, 504}


class ConnectorHttp:
    def __init__(
        self,
        provider: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 30.0,
        max_attempts: int = 3,
    ) -> None:
        self.provider = provider
        self._transport = transport
        self._timeout = timeout_seconds
        self._max_attempts = max(1, max_attempts)

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        json: object | None = None,
    ) -> httpx.Response:
        base_headers = {"User-Agent": "evidence-layer-connector/0.1"}
        if headers:
            base_headers.update(headers)
        attempt = 0
        while True:
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    transport=self._transport,
                    follow_redirects=False,
                ) as client:
                    response = await client.request(
                        method,
                        url,
                        headers=base_headers,
                        params=params,
                        json=json,
                    )
            except httpx.HTTPError as exc:
                if attempt + 1 >= self._max_attempts:
                    raise ConnectorError(
                        self.provider, "network request failed", retryable=True
                    ) from exc
                attempt += 1
                await asyncio.sleep(0.25 * (2**attempt))
                continue
            if response.status_code in RETRY_STATUSES and attempt + 1 < self._max_attempts:
                attempt += 1
                retry_after = response.headers.get("retry-after")
                try:
                    delay = min(5.0, max(0.0, float(retry_after))) if retry_after else 0.25 * (2**attempt)
                except ValueError:
                    delay = 0.25 * (2**attempt)
                await asyncio.sleep(delay)
                continue
            if response.status_code >= 300:
                raise ConnectorError(
                    self.provider,
                    f"request failed with HTTP {response.status_code}",
                    status=response.status_code,
                    retryable=response.status_code in RETRY_STATUSES,
                )
            return response
