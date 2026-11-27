"""Every 503 (and Remote MCP "unavailable") logs a content-free cause."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from evidence_service.error_cause import describe_cause, mark_upstream_status
from evidence_service.personal_api_key_repository import (
    PersonalApiKeyDataUnavailable,
    SupabasePersonalApiKeyRepository,
)
from test_evidence_search_prefilter import QUERY, settings
from test_evidence_service_retrieval import TOKEN, WORKSPACE, FakeRepository, make_client

SECRET_QUERY = "confidential merger plan zebrafish"


class TimingOutRepository(FakeRepository):
    async def read_evidence(self, **kwargs):
        try:
            raise httpx.ReadTimeout("read timed out for https://project.supabase.co/rest/v1/rpc/x?workspace=abc")
        except httpx.ReadTimeout as exc:
            raise PersonalApiKeyDataUnavailable from exc


def unavailable_lines(output: str) -> list[dict]:
    lines = []
    for raw in output.splitlines():
        try:
            payload = json.loads(raw)
        except ValueError:
            continue
        if payload.get("event") == "service_unavailable":
            lines.append(payload)
    return lines


def test_a_503_logs_its_cause_without_content(capsys: pytest.CaptureFixture[str]) -> None:
    response = make_client(repository=TimingOutRepository()).post(
        f"/v1/personal-api-keys/workspaces/{WORKSPACE}/retrieve",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"query": SECRET_QUERY, "top_k": 1},
    )

    assert response.status_code == 503
    output = capsys.readouterr().out
    assert unavailable_lines(output) == [{
        "event": "service_unavailable",
        "surface": "http",
        "route": "POST /v1/personal-api-keys/workspaces/{workspace_id}/retrieve",
        "detail": "Personal API key service is unavailable",
        "cause": ["PersonalApiKeyDataUnavailable", "httpx.ReadTimeout"],
        "upstream_status": None,
    }]
    line = next(raw for raw in output.splitlines() if "service_unavailable" in raw)
    for forbidden in (SECRET_QUERY, str(WORKSPACE), "supabase.co", "timed out", TOKEN):
        assert forbidden not in line


def test_other_http_errors_are_not_logged_as_unavailable(capsys: pytest.CaptureFixture[str]) -> None:
    response = make_client().post(
        f"/v1/personal-api-keys/workspaces/{WORKSPACE}/retrieve",
        headers={"Authorization": "Bearer not-a-key"},
        json={"query": QUERY},
    )

    assert response.status_code == 401
    assert unavailable_lines(capsys.readouterr().out) == []


def test_remote_mcp_unavailable_logs_its_cause(capsys: pytest.CaptureFixture[str]) -> None:
    with make_client(remote_mcp_enabled=True, repository=TimingOutRepository()) as client:
        headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json, text/event-stream"}
        client.post(f"/mcp/workspaces/{WORKSPACE}/", headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}},
        })
        called = client.post(f"/mcp/workspaces/{WORKSPACE}/", headers=headers, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "retrieve_evidence", "arguments": {"query": SECRET_QUERY}},
        })

    assert called.status_code == 200
    output = capsys.readouterr().out
    assert unavailable_lines(output) == [{
        "event": "service_unavailable",
        "surface": "remote_mcp",
        "route": "unavailable",
        "detail": "Personal API key service is unavailable",
        "cause": ["PersonalApiKeyDataUnavailable", "httpx.ReadTimeout"],
        "upstream_status": None,
    }]
    assert SECRET_QUERY not in output


def test_repositories_record_the_upstream_status() -> None:
    repository = SupabasePersonalApiKeyRepository(
        settings(personal_api_key_gateway_secret="ab" * 32),
        transport=httpx.MockTransport(lambda request: httpx.Response(502, json={"message": "bad gateway"})),
    )
    with pytest.raises(PersonalApiKeyDataUnavailable) as raised:
        asyncio.run(repository.read_evidence(
            key_id=WORKSPACE, key_hash="c" * 64, workspace_id=WORKSPACE,
            limit=10, cursor_created_at=None, cursor_id=None,
        ))
    assert describe_cause(raised.value) == {
        "cause": ["PersonalApiKeyDataUnavailable"], "upstream_status": 502,
    }


def test_cause_chain_is_bounded_and_never_reads_messages() -> None:
    error: BaseException = ValueError("secret value 1")
    for _ in range(10):
        try:
            raise RuntimeError("secret value 2") from error
        except RuntimeError as exc:
            error = exc
    described = describe_cause(mark_upstream_status(error, 504))
    assert len(described["cause"]) == 6
    assert described["upstream_status"] == 504
    assert "secret" not in json.dumps(described)
