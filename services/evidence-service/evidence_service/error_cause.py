"""Content-free cause of a 503 (or Remote MCP "unavailable") for the logs.

A 503 used to leave no trace of why it happened: the repositories turn every
upstream failure (timeout, refused connection, non-2xx answer) into one
"unavailable" exception. This module walks the exception chain and reports
only exception class names and, where a repository recorded it, the upstream
HTTP status. It never reads exception messages, so URLs, identifiers, queries
and evidence content cannot reach the log line.
"""

from __future__ import annotations

import json
from typing import Any

MAX_CHAIN = 6


def mark_upstream_status(exc: BaseException, status_code: int) -> BaseException:
    """Attach the upstream HTTP status to an "unavailable" exception."""
    exc.upstream_status = int(status_code)  # type: ignore[attr-defined]
    return exc


def describe_cause(exc: BaseException | None) -> dict[str, Any]:
    chain: list[str] = []
    upstream_status: int | None = None
    seen: set[int] = set()
    current = exc
    while current is not None and id(current) not in seen and len(chain) < MAX_CHAIN:
        seen.add(id(current))
        cls = type(current)
        module = cls.__module__.split(".")[0]
        chain.append(cls.__name__ if module in {"builtins", "evidence_service", "fastapi", "starlette"} else f"{module}.{cls.__name__}")
        status = getattr(current, "upstream_status", None)
        if upstream_status is None and isinstance(status, int):
            upstream_status = status
        current = current.__cause__ or current.__context__
    return {"cause": chain, "upstream_status": upstream_status}


def log_unavailable(*, surface: str, route: str, detail: str, exc: BaseException | None) -> None:
    """One JSON line; ``detail`` must be one of the service's fixed messages."""
    print(json.dumps({
        "event": "service_unavailable",
        "surface": surface,
        "route": route,
        "detail": detail,
        **describe_cause(exc),
    }, sort_keys=True), flush=True)
