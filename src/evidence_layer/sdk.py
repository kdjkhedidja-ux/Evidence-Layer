"""Small Python SDK for the Evidence Layer personal API-key retrieve API.

The client is read-only and workspace-scoped. It exposes the same two operations
as the remote MCP endpoint: ``retrieve`` and ``get_document``.

Example:
    from evidence_layer.sdk import EvidenceLayerClient

    client = EvidenceLayerClient(
        "https://evidence-layer.example",
        "evl_pk_...",
        "workspace-uuid",
    )
    context = client.retrieve("What is the product?", top_k=5)
    document = client.get_document(context["evidence"][0]["evidence_id"])
"""

from evidence_layer.mcp.http_client import EvidenceApiClient as EvidenceLayerClient
from evidence_layer.mcp.http_client import EvidenceApiError

__all__ = ["EvidenceLayerClient", "EvidenceApiError"]
