"""Account/personal-workspace export producer and mediated grant repository.

The export is intentionally narrow: it covers the caller's account profile and
personal workspaces only. Group/shared workspaces are never queried. The ZIP is
kept as base64 in forced-RLS tables and is released only through the
security-definer grant RPCs; clients never receive a Storage URL.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import secrets
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import httpx

from evidence_service.config import SupabaseSettings

EXPORT_FORMAT_VERSION = "evidence-layer-export-v1"
MAX_EXPORT_BYTES = 8 * 1024 * 1024
MAX_EXPORT_ROWS = 5000
MAX_IDEMPOTENCY_KEY_LENGTH = 128


class ExportDataUnavailable(Exception):
    """The export Data API operation could not complete safely."""


class ExportAccessDenied(Exception):
    """The caller's account or export authorization is not available."""


class ExportTooLarge(Exception):
    """The generated export exceeds the narrowed size/row limit."""


@dataclass(frozen=True)
class ExportJob:
    export_id: UUID
    status: str
    ready_at: datetime | None
    expires_at: datetime | None
    reused: bool
    grant_token: str | None


@dataclass(frozen=True)
class ExportDownload:
    content: bytes
    sha256: str
    size_bytes: int
    expires_at: datetime


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _parse_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ExportDataUnavailable
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExportDataUnavailable from exc
    if parsed.tzinfo is None:
        raise ExportDataUnavailable
    return parsed


def _zip_info(path: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    return info


def build_export_archive(
    *,
    account: dict[str, Any],
    workspaces: list[dict[str, Any]],
    evidence: dict[str, list[dict[str, Any]]],
    relationships: dict[str, list[dict[str, Any]]],
    sources: dict[str, list[dict[str, Any]]],
    generated_at: datetime | None = None,
) -> tuple[bytes, dict[str, Any], str, str]:
    """Return (zip bytes, manifest, artifact sha256, manifest sha256)."""
    generated = generated_at or datetime.now(timezone.utc)
    files: dict[str, bytes] = {
        "account.json": _json_bytes(account),
    }
    counts = {"workspaces": len(workspaces)}
    for workspace in workspaces:
        workspace_id = str(workspace["id"])
        prefix = f"workspaces/{workspace_id}"
        files[f"{prefix}/workspace.json"] = _json_bytes(workspace)
        for name, rows in (
            ("evidence.json", evidence.get(workspace_id, [])),
            ("relationships.json", relationships.get(workspace_id, [])),
            ("sources.json", sources.get(workspace_id, [])),
        ):
            files[f"{prefix}/{name}"] = _json_bytes(rows)
            counts[name.split(".")[0]] = (
                counts.get(name.split(".")[0], 0) + len(rows)
            )

    manifest_files = []
    checksum_lines = []
    for path in sorted(files):
        digest = hashlib.sha256(files[path]).hexdigest()
        manifest_files.append(
            {"path": path, "sha256": digest, "size_bytes": len(files[path])}
        )
        checksum_lines.append(f"{digest}  {path}\n")

    manifest = {
        "format_version": EXPORT_FORMAT_VERSION,
        "generated_at": generated.isoformat(),
        "scope": {"type": "account"},
        "counts": counts,
        "files": manifest_files,
        "limits": {
            "max_artifact_bytes": MAX_EXPORT_BYTES,
            "max_rows_per_resource": MAX_EXPORT_ROWS,
        },
    }
    manifest_bytes = _json_bytes(manifest)
    checksums = "".join(checksum_lines).encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(files):
            archive.writestr(_zip_info(path), files[path])
        archive.writestr(_zip_info("manifest.json"), manifest_bytes)
        archive.writestr(_zip_info("checksums.sha256"), checksums)
    payload = buffer.getvalue()
    if len(payload) > MAX_EXPORT_BYTES:
        raise ExportTooLarge
    return (
        payload,
        manifest,
        hashlib.sha256(payload).hexdigest(),
        hashlib.sha256(manifest_bytes).hexdigest(),
    )


class SupabaseExportRepository:
    """Build and mediate exports with the caller's token; no service_role."""

    def __init__(
        self,
        settings: SupabaseSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    def _headers(self, access_token: str) -> dict[str, str]:
        return {
            "apikey": self._settings.publishable_key,
            "authorization": f"Bearer {access_token}",
            "content-type": "application/json",
            "accept": "application/json",
        }

    async def _request(
        self,
        method: str,
        access_token: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: object | None = None,
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
            ) as client:
                return await client.request(
                    method,
                    f"{self._settings.supabase_url}/rest/v1/{path}",
                    headers=self._headers(access_token),
                    params=params,
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise ExportDataUnavailable from exc

    async def _rows(
        self, access_token: str, table: str, params: dict[str, str]
    ) -> list[dict[str, Any]]:
        response = await self._request("GET", access_token, table, params=params)
        if response.status_code in (401, 403):
            raise ExportAccessDenied
        if response.status_code != 200:
            raise ExportDataUnavailable
        try:
            payload = response.json()
        except ValueError as exc:
            raise ExportDataUnavailable from exc
        if not isinstance(payload, list) or any(
            not isinstance(row, dict) for row in payload
        ):
            raise ExportDataUnavailable
        if len(payload) > MAX_EXPORT_ROWS:
            raise ExportTooLarge
        return payload

    async def _collect(
        self, access_token: str, principal_id: UUID
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
        account_rows = await self._rows(
            access_token,
            "accounts",
            {
                "select": "user_id,account_type,organization_name,created_at",
                "user_id": f"eq.{principal_id}",
                "limit": "1",
            },
        )
        if len(account_rows) != 1:
            raise ExportAccessDenied
        membership_rows = await self._rows(
            access_token,
            "workspace_memberships",
            {
                "select": "workspace_id,role,status,workspaces(id,name,created_at)",
                "user_id": f"eq.{principal_id}",
                "status": "eq.active",
                "role": "eq.owner",
                "order": "workspace_id.asc",
            },
        )
        workspaces: list[dict[str, Any]] = []
        for membership in membership_rows:
            workspace = membership.get("workspaces")
            if not isinstance(workspace, dict) or not workspace.get("id"):
                raise ExportDataUnavailable
            workspaces.append(workspace)
        evidence: dict[str, list[dict[str, Any]]] = {}
        relationships: dict[str, list[dict[str, Any]]] = {}
        sources: dict[str, list[dict[str, Any]]] = {}
        for workspace in workspaces:
            workspace_id = UUID(str(workspace["id"]))
            key = str(workspace_id)
            evidence[key] = await self._rows(
                access_token,
                "evidence_records",
                {
                    "select": "id,workspace_id,content,source_label,source_id,source_version_id,created_at,updated_at",
                    "workspace_id": f"eq.{workspace_id}",
                    "order": "created_at.asc,id.asc",
                    "limit": str(MAX_EXPORT_ROWS + 1),
                },
            )
            relationships[key] = await self._rows(
                access_token,
                "evidence_relationships",
                {
                    "select": "id,workspace_id,evidence_id_a,evidence_id_b,relation_type,rationale,provenance_note,created_by,created_at,updated_by,updated_at",
                    "workspace_id": f"eq.{workspace_id}",
                    "order": "created_at.asc,id.asc",
                    "limit": str(MAX_EXPORT_ROWS + 1),
                },
            )
            sources[key] = await self._rows(
                access_token,
                "source_snapshots",
                {
                    "select": "workspace_id,source_id,version_id,sha256,size_bytes,state,created_at",
                    "workspace_id": f"eq.{workspace_id}",
                    "order": "source_id.asc,version_id.asc",
                    "limit": str(MAX_EXPORT_ROWS + 1),
                },
            )
        return account_rows[0], workspaces, evidence, relationships, sources

    async def create_export(
        self, access_token: str, *, principal_id: UUID, idempotency_key: str
    ) -> ExportJob:
        key = idempotency_key.strip()
        if not key or len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
            raise ExportAccessDenied
        account, workspaces, evidence, relationships, sources = await self._collect(
            access_token, principal_id
        )
        payload, manifest, artifact_sha256, manifest_sha256 = build_export_archive(
            account=account,
            workspaces=workspaces,
            evidence=evidence,
            relationships=relationships,
            sources=sources,
        )
        grant_token = secrets.token_urlsafe(32)
        grant_hash = hashlib.sha256(grant_token.encode("utf-8")).hexdigest()
        export_id = uuid4()
        body = {
            "p_export_id": str(export_id),
            "p_idempotency_key": key,
            "p_artifact_base64": base64.b64encode(payload).decode("ascii"),
            "p_artifact_sha256": artifact_sha256,
            "p_artifact_size": len(payload),
            "p_manifest": manifest,
            "p_manifest_sha256": manifest_sha256,
            "p_grant_token_hash": grant_hash,
        }
        response = await self._request(
            "POST",
            access_token,
            "rpc/finalize_account_export",
            body=body,
        )
        if response.status_code in (401, 403):
            raise ExportAccessDenied
        if response.status_code != 200:
            raise ExportDataUnavailable
        try:
            result = response.json()
        except ValueError as exc:
            raise ExportDataUnavailable from exc
        if not isinstance(result, dict):
            raise ExportDataUnavailable
        try:
            export_uuid = UUID(str(result["export_id"]))
            status = str(result["status"])
            reused = bool(result.get("reused"))
            ready_at = _parse_timestamp(result.get("ready_at"))
            expires_at = _parse_timestamp(result.get("expires_at"))
        except (KeyError, TypeError, ValueError) as exc:
            raise ExportDataUnavailable from exc
        if export_uuid != export_id or status != "ready":
            raise ExportDataUnavailable
        return ExportJob(
            export_id=export_uuid,
            status=status,
            ready_at=ready_at,
            expires_at=expires_at,
            reused=reused,
            grant_token=None if reused else grant_token,
        )

    async def redeem_export(
        self, access_token: str, *, export_id: UUID, grant_token: str
    ) -> ExportDownload | None:
        if not grant_token or len(grant_token) > 128:
            return None
        token_hash = hashlib.sha256(grant_token.encode("utf-8")).hexdigest()
        response = await self._request(
            "POST",
            access_token,
            "rpc/redeem_account_export_grant",
            body={
                "p_export_id": str(export_id),
                "p_token_hash": token_hash,
            },
        )
        if response.status_code in (401, 403):
            raise ExportAccessDenied
        if response.status_code != 200:
            raise ExportDataUnavailable
        try:
            result = response.json()
        except ValueError as exc:
            raise ExportDataUnavailable from exc
        if result is None:
            return None
        if not isinstance(result, dict):
            raise ExportDataUnavailable
        try:
            content = base64.b64decode(str(result["artifact_base64"]), validate=True)
            expected_sha = str(result["artifact_sha256"])
            expected_size = int(result["artifact_size_bytes"])
            expires_at = _parse_timestamp(result.get("expires_at"))
        except (KeyError, TypeError, ValueError) as exc:
            raise ExportDataUnavailable from exc
        if expires_at is None or len(content) != expected_size:
            raise ExportDataUnavailable
        if hashlib.sha256(content).hexdigest() != expected_sha:
            raise ExportDataUnavailable
        return ExportDownload(
            content=content,
            sha256=expected_sha,
            size_bytes=expected_size,
            expires_at=expires_at,
        )

    async def revoke_export(self, access_token: str, *, export_id: UUID) -> bool:
        response = await self._request(
            "POST",
            access_token,
            "rpc/revoke_account_export",
            body={"p_export_id": str(export_id)},
        )
        if response.status_code in (401, 403):
            raise ExportAccessDenied
        if response.status_code != 200:
            raise ExportDataUnavailable
        try:
            result = response.json()
        except ValueError as exc:
            raise ExportDataUnavailable from exc
        return isinstance(result, dict) and result.get("status") == "revoked"
