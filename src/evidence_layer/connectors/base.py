"""Provider-agnostic read-only connector contract.

Connectors in this first slice accept an already-authorized access token and
return normalized source items/documents. OAuth acquisition, token storage,
sync workers, and database ingestion are intentionally outside this module.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

ConnectorKind = Literal["file", "document", "page", "repo"]


class ConnectorError(Exception):
    """A provider operation failed safely; messages never contain tokens."""

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.status = status
        self.retryable = retryable


@dataclass(frozen=True)
class ConnectorItem:
    provider: str
    external_id: str
    name: str
    kind: ConnectorKind
    mime_type: str | None = None
    revision: str | None = None
    updated_at: datetime | None = None
    url: str | None = None
    parent_id: str | None = None
    size_bytes: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConnectorPage:
    items: tuple[ConnectorItem, ...]
    next_cursor: str | None = None
    # False when the provider listing was cut short (size cap or provider
    # truncation), so callers must not treat missing items as deleted.
    complete: bool = True


@dataclass(frozen=True)
class ConnectorDocument:
    item: ConnectorItem
    content: bytes
    content_type: str
    revision: str | None
    sha256: str

    @classmethod
    def build(
        cls,
        item: ConnectorItem,
        content: bytes,
        content_type: str,
        *,
        revision: str | None = None,
    ) -> "ConnectorDocument":
        return cls(
            item=item,
            content=content,
            content_type=content_type,
            revision=revision or item.revision,
            sha256=hashlib.sha256(content).hexdigest(),
        )


class EvidenceConnector(Protocol):
    provider: str

    async def list_items(
        self, *, cursor: str | None = None, limit: int = 100
    ) -> ConnectorPage: ...

    async def fetch_document(self, item: ConnectorItem) -> ConnectorDocument: ...


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed
