"""MCP stdio server backed by the Evidence Layer API-key retrieval endpoint.

Environment:
- EVIDENCE_API_URL
- EVIDENCE_API_KEY
- EVIDENCE_WORKSPACE_ID

The API key stays in the server environment and is never returned to the MCP
client. The service must have ``EVIDENCE_RETRIEVE_API_ENABLED=true``.
"""

from __future__ import annotations

import logging
import os
import sys

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from evidence_layer.mcp.http_client import EvidenceApiClient, EvidenceApiError

logger = logging.getLogger(__name__)
mcp = MCPServer("Evidence Layer API")


def _client() -> EvidenceApiClient | None:
    base_url = os.environ.get("EVIDENCE_API_URL", "")
    api_key = os.environ.get("EVIDENCE_API_KEY", "")
    workspace_id = os.environ.get("EVIDENCE_WORKSPACE_ID", "")
    if not base_url or not api_key or not workspace_id:
        return None
    return EvidenceApiClient(base_url, api_key, workspace_id)


@mcp.tool(
    name="retrieve_evidence",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
)
def retrieve_evidence_tool(
    query: str,
    top_k: int = 5,
    max_tokens: int = 1200,
    max_chars: int = 6000,
) -> dict:
    """Retrieve a bounded, lexical-selected evidence context for a query."""
    client = _client()
    if client is None:
        return {"error": {"code": "not_configured", "message": "Evidence API is not configured"}}
    try:
        return client.retrieve(query, top_k=top_k, max_tokens=max_tokens, max_chars=max_chars)
    except EvidenceApiError as exc:
        return {"error": {"code": "api_unavailable", "message": str(exc)}}
    except Exception:
        return {"error": {"code": "internal_error", "message": "retrieve_evidence failed"}}


@mcp.tool(
    name="get_document",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
)
def get_document_tool(evidence_id: str) -> dict:
    """Read one evidence document by ID within the configured workspace."""
    client = _client()
    if client is None:
        return {"error": {"code": "not_configured", "message": "Evidence API is not configured"}}
    try:
        return client.get_document(evidence_id)
    except EvidenceApiError as exc:
        return {"error": {"code": "api_unavailable", "message": str(exc)}}
    except Exception:
        return {"error": {"code": "internal_error", "message": "get_document failed"}}


def _configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)


def main() -> None:
    _configure_logging()
    logger.info("starting Evidence Layer API MCP stdio server")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
