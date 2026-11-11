from __future__ import annotations

import base64
import hashlib
import io
import json
import unittest
import zipfile
from uuid import UUID

import httpx

from evidence_service.config import SupabaseSettings
from evidence_service.export_repository import (
    ExportDataUnavailable,
    ExportDownload,
    SupabaseExportRepository,
    build_export_archive,
)

SETTINGS = SupabaseSettings(
    supabase_url="https://project.supabase.co",
    publishable_key="sb_publishable_test",
    jwt_issuer="https://project.supabase.co/auth/v1",
    jwt_audience="authenticated",
    jwt_algorithms=("ES256",),
)
PRINCIPAL = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE = UUID("22222222-2222-4222-8222-222222222222")
EXPORT_ID = UUID("33333333-3333-4333-8333-333333333333")


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/rest/v1/accounts"):
        assert request.url.params["user_id"] == f"eq.{PRINCIPAL}"
        return httpx.Response(200, json=[{
            "user_id": str(PRINCIPAL),
            "account_type": "individual",
            "organization_name": None,
            "created_at": "2026-11-11T00:00:00Z",
        }])
    if path.endswith("/rest/v1/workspace_memberships"):
        assert request.url.params["user_id"] == f"eq.{PRINCIPAL}"
        assert request.url.params["role"] == "eq.owner"
        return httpx.Response(200, json=[{
            "workspace_id": str(WORKSPACE),
            "role": "owner",
            "status": "active",
            "workspaces": {
                "id": str(WORKSPACE),
                "name": "Personal",
                "created_at": "2026-11-11T00:00:00Z",
            },
        }])
    if path.endswith("/rest/v1/evidence_records"):
        return httpx.Response(200, json=[{
            "id": "44444444-4444-4444-8444-444444444444",
            "workspace_id": str(WORKSPACE),
            "content": "exported evidence",
            "source_label": None,
            "source_id": None,
            "source_version_id": None,
            "created_at": "2026-11-11T00:00:00Z",
            "updated_at": "2026-11-11T00:00:00Z",
        }])
    if path.endswith("/rest/v1/evidence_relationships"):
        return httpx.Response(200, json=[])
    if path.endswith("/rest/v1/source_snapshots"):
        return httpx.Response(200, json=[])
    if path.endswith("/rest/v1/rpc/finalize_account_export"):
        body = json.loads(request.content)
        assert body["p_artifact_size"] == len(base64.b64decode(body["p_artifact_base64"]))
        assert body["p_manifest"]["format_version"] == "evidence-layer-export-v1"
        assert body["p_manifest"]["counts"]["evidence"] == 1
        return httpx.Response(200, json={
            "export_id": body["p_export_id"],
            "status": "ready",
            "ready_at": "2026-11-11T00:00:00Z",
            "expires_at": "2026-11-12T00:00:00Z",
            "reused": False,
        })
    return httpx.Response(404)


class ExportRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_export_builds_manifest_and_grant(self):
        repository = SupabaseExportRepository(
            SETTINGS, transport=httpx.MockTransport(_handler)
        )
        job = await repository.create_export(
            "token", principal_id=PRINCIPAL, idempotency_key="export-1"
        )
        self.assertEqual(job.status, "ready")
        self.assertFalse(job.reused)
        self.assertIsNotNone(job.grant_token)
        self.assertEqual(job.expires_at.isoformat(), "2026-11-12T00:00:00+00:00")

    async def test_redeem_and_revoke_use_rpc_contract(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path.endswith("/rpc/redeem_account_export_grant"):
                return httpx.Response(200, json={
                    "artifact_base64": "aGVsbG8=",
                    "artifact_sha256": hashlib.sha256(b"hello").hexdigest(),
                    "artifact_size_bytes": 5,
                    "expires_at": "2026-11-12T00:00:00Z",
                })
            if request.url.path.endswith("/rpc/revoke_account_export"):
                return httpx.Response(200, json={"export_id": str(EXPORT_ID), "status": "revoked"})
            return httpx.Response(404)

        repository = SupabaseExportRepository(
            SETTINGS, transport=httpx.MockTransport(handler)
        )
        download = await repository.redeem_export(
            "token", export_id=EXPORT_ID, grant_token="grant"
        )
        self.assertIsInstance(download, ExportDownload)
        self.assertEqual(download.content, b"hello")
        self.assertTrue(await repository.revoke_export("token", export_id=EXPORT_ID))
        self.assertEqual(len(calls), 2)


class ExportArchiveTests(unittest.TestCase):
    def test_manifest_checksums_and_paths(self):
        payload, manifest, digest, manifest_digest = build_export_archive(
            account={"user_id": str(PRINCIPAL)},
            workspaces=[{"id": str(WORKSPACE), "name": "Personal"}],
            evidence={str(WORKSPACE): [{"id": "e1", "content": "x"}]},
            relationships={str(WORKSPACE): []},
            sources={str(WORKSPACE): []},
        )
        self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)
        self.assertEqual(len(manifest_digest), 64)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
            self.assertIn("manifest.json", names)
            self.assertIn("checksums.sha256", names)
            self.assertIn(f"workspaces/{WORKSPACE}/evidence.json", names)
            manifest_json = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest_json["counts"]["evidence"], 1)
            self.assertGreater(len(archive.read("checksums.sha256")), 0)
