"""Hosted Streamable HTTP MCP endpoint for the personal API-key retrieve path."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from evidence_service.personal_api_key_repository import parse_personal_api_key

REMOTE_MCP_PATH = "/mcp/workspaces/{workspace_id}"


@dataclass(frozen=True)
class RemoteMcpPrincipal:
    workspace_id: UUID
    key_id: UUID
    key_hash: str


class RemoteMcpBackend(Protocol):
    async def retrieve(
        self,
        principal: RemoteMcpPrincipal,
        *,
        query: str,
        top_k: int,
        max_tokens: int,
        max_chars: int,
        use_jev: bool,
    ) -> dict: ...

    async def get_document(self, principal: RemoteMcpPrincipal, evidence_id: str) -> dict: ...


@dataclass(frozen=True)
class RemoteMcpRuntime:
    app: ASGIApp
    server: MCPServer


_principal: ContextVar[RemoteMcpPrincipal | None] = ContextVar("remote_mcp_principal", default=None)


async def _error(
    scope: Scope,
    receive: Receive,
    send: Send,
    status_code: int,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> None:
    response = JSONResponse(
        {"error": {"code": "unauthorized" if status_code == 401 else "unavailable", "message": message}},
        status_code=status_code,
        headers=headers,
    )
    await response(scope, receive, send)


class _RemoteMcpAuth:
    def __init__(self, app: ASGIApp, *, enabled: bool) -> None:
        self._app = app
        self._enabled = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        if not self._enabled:
            await _error(scope, receive, send, 404, "Remote MCP is not enabled")
            return
        workspace = scope.get("path_params", {}).get("workspace_id")
        try:
            workspace_id = UUID(str(workspace))
        except (TypeError, ValueError):
            await _error(scope, receive, send, 404, "Workspace not found")
            return
        authorization = Headers(scope=scope).get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            await _error(
                scope,
                receive,
                send,
                401,
                "Personal API key is required",
                headers={"WWW-Authenticate": "Bearer"},
            )
            return
        parsed = parse_personal_api_key(token)
        if parsed is None:
            await _error(
                scope,
                receive,
                send,
                401,
                "Personal API key is invalid",
                headers={"WWW-Authenticate": "Bearer"},
            )
            return
        key_id, key_hash = parsed
        reset = _principal.set(RemoteMcpPrincipal(workspace_id, key_id, key_hash))
        try:
            await self._app(scope, receive, send)
        finally:
            _principal.reset(reset)


def _current_principal() -> RemoteMcpPrincipal:
    principal = _principal.get()
    if principal is None:
        raise RuntimeError("remote MCP principal is not set")
    return principal


def create_remote_mcp_runtime(backend: RemoteMcpBackend, *, enabled: bool) -> RemoteMcpRuntime:
    server = MCPServer(
        "Evidence Layer Remote MCP",
        instructions=(
            "Read-only Evidence Layer tools. Resolve the workspace from the hosted URL and the "
            "personal API key from the Authorization header. Use retrieve_evidence to get a "
            "bounded, provenance-rich context, then get_document only when the full parent record "
            "is needed. JEV is primary when the server flag is enabled; set use_jev=false to "
            "force lexical scoring. The server may still disable JEV and fall back lexically."
        ),
    )

    @server.tool(
        name="retrieve_evidence",
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
    )
    async def retrieve_evidence(
        query: str,
        top_k: int = 5,
        max_tokens: int = 1200,
        max_chars: int = 6000,
        use_jev: bool = True,
    ) -> dict:
        """Retrieve bounded evidence; JEV is primary when enabled, with lexical fallback."""
        return await backend.retrieve(
            _current_principal(),
            query=query,
            top_k=top_k,
            max_tokens=max_tokens,
            max_chars=max_chars,
            use_jev=use_jev,
        )

    @server.tool(
        name="get_document",
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
    )
    async def get_document(evidence_id: str) -> dict:
        """Read one parent evidence document by ID within the hosted workspace."""
        return await backend.get_document(_current_principal(), evidence_id)

    inner = server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        max_request_body_size=1_000_000,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return RemoteMcpRuntime(app=_RemoteMcpAuth(inner, enabled=enabled), server=server)
