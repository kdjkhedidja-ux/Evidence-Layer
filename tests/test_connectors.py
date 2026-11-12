from __future__ import annotations

import unittest

import httpx

from evidence_layer.connectors import (
    ConfluenceConfig,
    ConfluenceConnector,
    DropboxConfig,
    DropboxConnector,
    GitHubConfig,
    GitHubConnector,
    GoogleDriveConfig,
    GoogleDriveConnector,
    NotionConfig,
    NotionConnector,
)


class GoogleDriveTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_export_and_binary_fetch(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path, dict(request.url.params)))
            if request.url.path == "/drive/v3/files":
                assert request.headers["authorization"] == "Bearer drive-token"
                return httpx.Response(200, json={
                    "nextPageToken": "next",
                    "files": [
                        {
                            "id": "doc1",
                            "name": "Doc",
                            "mimeType": "application/vnd.google-apps.document",
                            "modifiedTime": "2026-11-11T00:00:00Z",
                            "version": "7",
                            "webViewLink": "https://drive.example/doc1",
                        },
                        {
                            "id": "pdf1",
                            "name": "Paper.pdf",
                            "mimeType": "application/pdf",
                            "modifiedTime": "2026-11-11T00:00:00Z",
                            "md5Checksum": "a" * 32,
                            "size": "12",
                        },
                    ],
                })
            if request.url.path == "/drive/v3/files/doc1/export":
                return httpx.Response(200, content=b"<html>doc</html>", headers={"content-type": "text/html"})
            if request.url.path == "/drive/v3/files/pdf1":
                return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
            return httpx.Response(404)

        connector = GoogleDriveConnector(
            GoogleDriveConfig(access_token="drive-token"),
            transport=httpx.MockTransport(handler),
        )
        page = await connector.list_items(limit=10)
        self.assertEqual(len(page.items), 2)
        self.assertEqual(page.next_cursor, "next")
        self.assertEqual(page.items[0].kind, "document")
        self.assertEqual(page.items[1].revision, "a" * 32)

        exported = await connector.fetch_document(page.items[0])
        self.assertEqual(exported.content, b"<html>doc</html>")
        self.assertEqual(exported.content_type, "text/html")
        binary = await connector.fetch_document(page.items[1])
        self.assertEqual(binary.content, b"%PDF")
        self.assertIn(("GET", "/drive/v3/files/doc1/export", {"mimeType": "text/html"}), seen)


class GitHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_tree_and_raw_fetch(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/repos/acme/docs/git/trees/main":
                assert request.headers["x-github-api-version"] == "2022-11-28"
                return httpx.Response(200, json={"tree": [
                    {"path": "README.md", "type": "blob", "sha": "sha-readme", "size": 5},
                    {"path": "big.bin", "type": "blob", "sha": "sha-big", "size": 999999},
                ]})
            if request.url.path == "/repos/acme/docs/contents/README.md":
                return httpx.Response(200, content=b"hello", headers={"content-type": "application/vnd.github.raw"})
            return httpx.Response(404)

        connector = GitHubConnector(
            GitHubConfig(owner="acme", repo="docs", ref="main", max_file_bytes=100),
            transport=httpx.MockTransport(handler),
        )
        page = await connector.list_items(limit=10)
        self.assertEqual(len(page.items), 1)
        item = page.items[0]
        self.assertEqual(item.external_id, "acme/docs/README.md")
        self.assertEqual(item.mime_type, "text/markdown")
        document = await connector.fetch_document(item)
        self.assertEqual(document.content, b"hello")
        self.assertEqual(document.content_type, "text/markdown")


class DropboxTests(unittest.IsolatedAsyncioTestCase):
    async def test_folder_pagination_and_download(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/2/files/list_folder":
                return httpx.Response(200, json={
                    "entries": [
                        {".tag": "file", "id": "id:1", "name": "a.txt", "path_lower": "/a.txt", "path_display": "/a.txt", "size": 4, "rev": "r1", "server_modified": "2026-11-11T00:00:00Z"},
                        {".tag": "folder", "id": "id:folder", "name": "folder", "path_lower": "/folder"},
                    ],
                    "cursor": "cursor-1",
                    "has_more": True,
                })
            if request.url.path == "/2/files/list_folder/continue":
                return httpx.Response(200, json={
                    "entries": [{".tag": "file", "id": "id:2", "name": "b.txt", "path_lower": "/b.txt", "path_display": "/b.txt", "size": 5, "rev": "r2"}],
                    "cursor": "cursor-2",
                    "has_more": False,
                })
            if request.url.path == "/2/files/download":
                assert "a.txt" in request.headers["dropbox-api-arg"]
                return httpx.Response(200, content=b"data", headers={"content-type": "text/plain"})
            return httpx.Response(404)

        connector = DropboxConnector(
            DropboxConfig(access_token="dropbox-token"), transport=httpx.MockTransport(handler)
        )
        first = await connector.list_items(limit=1)
        self.assertEqual(first.next_cursor, "cursor-1")
        self.assertEqual(len(first.items), 1)
        second = await connector.list_items(cursor=first.next_cursor, limit=1)
        self.assertEqual(second.next_cursor, None)
        self.assertEqual(second.items[0].name, "b.txt")
        document = await connector.fetch_document(first.items[0])
        self.assertEqual(document.content, b"data")
        self.assertEqual(document.revision, "r1")


class ConfluenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_list_and_storage_body(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/wiki/rest/api/content":
                assert request.url.params["type"] == "page"
                return httpx.Response(200, json={
                    "results": [{
                        "id": "123",
                        "title": "Page",
                        "version": {"number": 3, "when": "2026-11-11T00:00:00Z"},
                        "space": {"key": "DOC"},
                        "_links": {"webui": "/spaces/DOC/pages/123"},
                    }],
                    "size": 1,
                    "_links": {"base": "https://example.atlassian.net/wiki"},
                })
            if request.url.path == "/wiki/rest/api/content/123":
                return httpx.Response(200, json={
                    "id": "123",
                    "title": "Page",
                    "version": {"number": 3},
                    "body": {"storage": {"value": "<p>Hello</p>"}},
                    "_links": {"base": "https://example.atlassian.net/wiki", "webui": "/spaces/DOC/pages/123"},
                })
            return httpx.Response(404)

        connector = ConfluenceConnector(
            ConfluenceConfig(base_url="https://example.atlassian.net/wiki", access_token="conf-token"),
            transport=httpx.MockTransport(handler),
        )
        page = await connector.list_items()
        self.assertEqual(page.next_cursor, None)
        self.assertEqual(page.items[0].url, "https://example.atlassian.net/wiki/spaces/DOC/pages/123")
        document = await connector.fetch_document(page.items[0])
        self.assertEqual(document.content, b"<p>Hello</p>")
        self.assertEqual(document.revision, "3")


class NotionTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_and_recursive_blocks(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/search":
                return httpx.Response(200, json={
                    "object": "list",
                    "has_more": False,
                    "results": [{
                        "object": "page",
                        "id": "page1",
                        "last_edited_time": "2026-11-11T00:00:00Z",
                        "url": "https://notion.example/page1",
                        "properties": {
                            "Name": {"type": "title", "title": [{"plain_text": "My Page"}]}
                        },
                    }],
                })
            if request.url.path == "/v1/blocks/page1/children":
                return httpx.Response(200, json={
                    "results": [
                        {"id": "p1", "type": "paragraph", "has_children": False, "paragraph": {"rich_text": [{"plain_text": "Hello"}]}},
                        {"id": "child1", "type": "child_page", "has_children": True, "child_page": {"title": "Child"}},
                    ],
                    "has_more": False,
                })
            if request.url.path == "/v1/blocks/child1/children":
                return httpx.Response(200, json={
                    "results": [
                        {"id": "p2", "type": "paragraph", "has_children": False, "paragraph": {"rich_text": [{"plain_text": "Child body"}]}}
                    ],
                    "has_more": False,
                })
            return httpx.Response(404)

        connector = NotionConnector(
            NotionConfig(access_token="notion-token"), transport=httpx.MockTransport(handler)
        )
        page = await connector.list_items()
        self.assertEqual(page.items[0].name, "My Page")
        document = await connector.fetch_document(page.items[0])
        text = document.content.decode("utf-8")
        self.assertIn("Hello", text)
        self.assertIn("Child", text)
        self.assertIn("Child body", text)
