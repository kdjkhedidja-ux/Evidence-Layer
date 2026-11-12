from __future__ import annotations

import json
import unittest

import httpx

from evidence_layer.mcp.http_client import EvidenceApiClient, EvidenceApiError

WORKSPACE = "22222222-2222-4222-8222-222222222222"


class McpHttpClientTests(unittest.TestCase):
    def test_retrieve_and_document_paths(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["authorization"] == "Bearer evl_pk_test"
            if request.method == "POST":
                body = json.loads(request.content)
                assert body["query"] == "lexical"
                assert request.url.path == f"/v1/personal-api-keys/workspaces/{WORKSPACE}/retrieve"
                return httpx.Response(200, json={"status": "ok", "evidence": []})
            assert request.url.path == f"/v1/personal-api-keys/workspaces/{WORKSPACE}/evidence/e1"
            return httpx.Response(200, json={"id": "e1", "content": "text"})

        client = EvidenceApiClient("https://api.example", "evl_pk_test", WORKSPACE, transport=httpx.MockTransport(handler))
        self.assertEqual(client.retrieve("lexical")["status"], "ok")
        self.assertEqual(client.get_document("e1")["id"], "e1")

    def test_non_success_is_safe(self):
        client = EvidenceApiClient(
            "https://api.example",
            "evl_pk_test",
            WORKSPACE,
            transport=httpx.MockTransport(lambda request: httpx.Response(503)),
        )
        with self.assertRaises(EvidenceApiError):
            client.retrieve("lexical")
