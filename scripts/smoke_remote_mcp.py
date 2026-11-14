#!/usr/bin/env python3
"""Smoke-test the hosted Evidence Layer Remote MCP endpoint.

Reads EL_API_KEY and EL_WORKSPACE_ID from the environment. Prints only
metadata: tool names, retrieval status, tokens, paths, and document size.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

DEFAULT_BASE = "https://evidence-layer-production-c4b5.up.railway.app/mcp"


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


async def run(url: str, api_key: str, query: str, top_k: int) -> int:
    async with create_mcp_http_client(
        headers={"Authorization": f"Bearer {api_key}"}
    ) as http:
        async with streamable_http_client(url, http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                names = [tool.name for tool in tools.tools]
                print("server:", initialized.server_info.name)
                print("tools:", ", ".join(names))
                if not {"retrieve_evidence", "get_document"}.issubset(names):
                    print("ERROR: expected retrieve_evidence and get_document")
                    return 1
                result = await session.call_tool(
                    "retrieve_evidence",
                    {"query": query, "top_k": top_k},
                )
                if result.is_error:
                    print("ERROR: retrieve_evidence returned isError")
                    return 1
                payload = json.loads(result.content[0].text)
                evidence = payload.get("evidence", [])
                print(
                    "retrieve:",
                    payload.get("status"),
                    "selected=", payload.get("selected_count"),
                    "tokens=", payload.get("used_tokens"),
                    "chunks=", payload.get("chunk_count"),
                )
                print("paths:", ", ".join(str(item.get("path")) for item in evidence))
                if not evidence:
                    print("WARNING: no selected evidence")
                    return 0
                document = await session.call_tool(
                    "get_document",
                    {"evidence_id": evidence[0].get("evidence_id")},
                )
                if document.is_error:
                    print("ERROR: get_document returned isError")
                    return 1
                document_payload = json.loads(document.content[0].text)
                print(
                    "get_document:",
                    "id_match=",
                    document_payload.get("id") == evidence[0].get("evidence_id"),
                    "content_chars=",
                    len(document_payload.get("content", "")),
                )
                return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path("/workspaces/.codespaces/shared/.env"))
    parser.add_argument("--base-url", default=os.environ.get("EL_MCP_BASE_URL", DEFAULT_BASE))
    parser.add_argument("--workspace-id", default=os.environ.get("EL_WORKSPACE_ID"))
    parser.add_argument("--query", default="What is Evidence Layer and what problem does it solve?")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()
    load_env_file(args.env_file)
    api_key = os.environ.get("EL_API_KEY", "")
    workspace_id = args.workspace_id or os.environ.get("EL_WORKSPACE_ID", "")
    if not api_key or not workspace_id:
        print("ERROR: EL_API_KEY and EL_WORKSPACE_ID are required")
        return 2
    url = f"{args.base_url.rstrip('/')}/workspaces/{workspace_id}/"
    print("endpoint:", url)
    return anyio.run(run, url, api_key, args.query, args.top_k)


if __name__ == "__main__":
    raise SystemExit(main())
