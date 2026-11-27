"""Workspace-scoped personal API keys with a narrow, RLS-bound read RPC."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import httpx

from evidence_service.config import SupabaseSettings
from evidence_service.error_cause import mark_upstream_status
from evidence_service.evidence_repository import (
    SEARCH_CANDIDATE_LIMIT,
    EvidenceRecord,
    SupabaseEvidenceRepository,
)
from evidence_service.relationship_repository import (
    EvidenceRelationship,
    SupabaseRelationshipRepository,
)

PERSONAL_API_KEY_PATTERN = re.compile(
    r"^evl_pk_([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.([A-Za-z0-9_-]{43})$"
)


class PersonalApiKeyDataUnavailable(Exception):
    """A key management or key-backed data request could not be completed safely."""


class PersonalApiKeyAccessDenied(Exception):
    """The personal key, workspace, or current membership is not authorized."""


class PersonalApiKeyRateLimited(Exception):
    """The per-key database rate limit was reached."""


class PersonalApiKeySearchUnavailable(PersonalApiKeyDataUnavailable):
    """The database has no whole-workspace candidate read yet (before 20261127120000).

    PostgREST answers PGRST202 without running anything, so the read is not
    counted against the key's rate limit and the caller can fall back.
    """


class PersonalApiKeyLimitReached(Exception):
    """The user already has the maximum number of active personal keys."""


@dataclass(frozen=True)
class PersonalApiKey:
    id: UUID
    workspace_id: UUID
    name: str
    key_prefix: str
    scopes: tuple[str, ...]
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None


@dataclass(frozen=True)
class PersonalApiKeyPage:
    items: list[EvidenceRecord] | list[EvidenceRelationship]
    next_cursor: str | None


def parse_personal_api_key(token: str) -> tuple[UUID, str] | None:
    if len(token) > 96:
        return None
    match = PERSONAL_API_KEY_PATTERN.fullmatch(token)
    if not match:
        return None
    try:
        key_id = UUID(match.group(1))
    except ValueError:
        return None
    return key_id, hashlib.sha256(token.encode("utf-8")).hexdigest()


def decode_personal_api_key_cursor(value: str | None) -> tuple[str | None, UUID | None]:
    if value is None:
        return None, None
    if len(value) > 512:
        raise ValueError("Invalid cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if not isinstance(payload, dict) or set(payload) != {"created_at", "id"}:
            raise ValueError
        created_at = payload["created_at"]
        if not isinstance(created_at, str):
            raise ValueError
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return created_at, UUID(str(payload["id"]))
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid cursor") from exc


def gateway_signature(
    secret: str,
    *,
    resource: str,
    key_id: UUID,
    workspace_id: UUID,
    key_hash: str,
    timestamp: int,
    nonce: UUID,
    limit: int,
    cursor_created_at: str | None,
    cursor_id: UUID | None,
) -> str:
    message = "\n".join(
        (
            "v1",
            resource,
            str(key_id),
            str(workspace_id),
            key_hash,
            str(timestamp),
            str(nonce),
            str(limit),
            cursor_created_at or "",
            str(cursor_id) if cursor_id else "",
        )
    )
    return hmac.new(bytes.fromhex(secret), message.encode("utf-8"), hashlib.sha256).hexdigest()


class SupabasePersonalApiKeyRepository:
    """Key metadata uses the owner JWT; machine reads use guarded SQL RPCs."""

    def __init__(
        self,
        settings: SupabaseSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    async def list_keys(self, access_token: str, user_id: UUID) -> list[PersonalApiKey]:
        response = await self._request(
            "GET",
            "personal_api_keys",
            access_token=access_token,
            params={
                "select": "id,workspace_id,name,key_prefix,scopes,created_at,expires_at,revoked_at,last_used_at",
                "user_id": f"eq.{user_id}",
                "order": "created_at.desc,id.desc",
            },
        )
        payload = self._json_list(response)
        result: list[PersonalApiKey] = []
        for item in payload:
            if not isinstance(item, dict):
                raise PersonalApiKeyDataUnavailable
            try:
                key = PersonalApiKey(
                    id=UUID(str(item["id"])),
                    workspace_id=UUID(str(item["workspace_id"])),
                    name=self._text(item["name"], 80),
                    key_prefix=self._text(item["key_prefix"], 32),
                    scopes=tuple(self._scopes(item["scopes"])),
                    created_at=self._datetime(item["created_at"]),
                    expires_at=self._datetime(item["expires_at"]),
                    revoked_at=self._optional_datetime(item["revoked_at"]),
                    last_used_at=self._optional_datetime(item["last_used_at"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise PersonalApiKeyDataUnavailable from exc
            result.append(key)
        return result

    async def create_key(
        self,
        access_token: str,
        *,
        workspace_id: UUID,
        name: str,
        expiration_days: int,
    ) -> tuple[PersonalApiKey, str]:
        key_id = uuid4()
        secret = secrets.token_urlsafe(32)
        token = f"evl_pk_{key_id}.{secret}"
        key_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        response = await self._request(
            "POST",
            "rpc/create_personal_api_key",
            access_token=access_token,
            headers={"x-evidence-api-key-hash": key_hash},
            json={
                "p_id": str(key_id),
                "p_workspace_id": str(workspace_id),
                "p_name": name,
                "p_expiration_days": expiration_days,
            },
        )
        payload = self._json_list(response)
        if len(payload) != 1 or not isinstance(payload[0], dict):
            raise PersonalApiKeyDataUnavailable
        item = payload[0]
        try:
            key = PersonalApiKey(
                id=UUID(str(item["id"])),
                workspace_id=UUID(str(item["workspace_id"])),
                name=self._text(item["name"], 80),
                key_prefix=self._text(item["key_prefix"], 32),
                scopes=tuple(self._scopes(item["scopes"])),
                created_at=self._datetime(item["created_at"]),
                expires_at=self._datetime(item["expires_at"]),
                revoked_at=None,
                last_used_at=None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PersonalApiKeyDataUnavailable from exc
        if key.id != key_id or key.workspace_id != workspace_id:
            raise PersonalApiKeyDataUnavailable
        return key, token

    async def revoke_key(self, access_token: str, key_id: UUID) -> bool:
        response = await self._request(
            "POST",
            "rpc/revoke_personal_api_key",
            access_token=access_token,
            json={"p_id": str(key_id)},
        )
        try:
            result = response.json()
        except (ValueError, TypeError) as exc:
            raise PersonalApiKeyDataUnavailable from exc
        if not isinstance(result, bool):
            raise PersonalApiKeyDataUnavailable
        return result

    async def read_evidence(
        self,
        *,
        key_id: UUID,
        key_hash: str,
        workspace_id: UUID,
        limit: int,
        cursor_created_at: str | None,
        cursor_id: UUID | None,
    ) -> PersonalApiKeyPage:
        payload = await self._read_page(
            resource="evidence",
            key_id=key_id,
            key_hash=key_hash,
            workspace_id=workspace_id,
            limit=limit,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
        )
        response = httpx.Response(200, json=payload)
        records = SupabaseEvidenceRepository._parse_rows(response, workspace_id)
        items, next_cursor = self._page(records, limit)
        return PersonalApiKeyPage(items=items, next_cursor=next_cursor)

    async def read_evidence_candidates(
        self,
        *,
        key_id: UUID,
        key_hash: str,
        workspace_id: UUID,
        limit: int,
        query: str,
        search_limit: int = SEARCH_CANDIDATE_LIMIT,
    ) -> list[EvidenceRecord]:
        """Newest ``limit`` records plus up to ``search_limit`` full-text matches
        from the whole workspace, in ONE signed call (one rate-limit count).

        The proof is the unchanged evidence read proof (no cursor); the query
        only narrows rows the key may already read.
        """
        secret = self._settings.personal_api_key_gateway_secret
        if secret is None:
            raise PersonalApiKeyDataUnavailable
        timestamp = int(time.time())
        nonce = uuid4()
        proof = gateway_signature(
            secret,
            resource="evidence",
            key_id=key_id,
            workspace_id=workspace_id,
            key_hash=key_hash,
            timestamp=timestamp,
            nonce=nonce,
            limit=limit,
            cursor_created_at=None,
            cursor_id=None,
        )
        response = await self._request(
            "POST",
            "rpc/read_personal_api_key_evidence_candidates",
            json={
                "p_key_id": str(key_id),
                "p_workspace_id": str(workspace_id),
                "p_limit": limit,
                "p_query": query[:2000],
                "p_search_limit": search_limit,
            },
            headers={
                "x-evidence-api-key-hash": key_hash,
                "x-evidence-api-key-gateway-timestamp": str(timestamp),
                "x-evidence-api-key-gateway-nonce": str(nonce),
                "x-evidence-api-key-gateway-proof": proof,
            },
        )
        payload = self._json_list(response)
        return SupabaseEvidenceRepository._parse_rows(
            httpx.Response(200, json=payload), workspace_id
        )

    async def read_evidence_document(
        self,
        *,
        key_id: UUID,
        key_hash: str,
        workspace_id: UUID,
        evidence_id: UUID,
    ) -> EvidenceRecord | None:
        secret = self._settings.personal_api_key_gateway_secret
        if secret is None:
            raise PersonalApiKeyDataUnavailable
        timestamp = int(time.time())
        nonce = uuid4()
        proof = gateway_signature(
            secret,
            resource="evidence",
            key_id=key_id,
            workspace_id=workspace_id,
            key_hash=key_hash,
            timestamp=timestamp,
            nonce=nonce,
            limit=1,
            cursor_created_at=None,
            cursor_id=None,
        )
        response = await self._request(
            "POST",
            "rpc/read_personal_api_key_evidence_document",
            json={
                "p_key_id": str(key_id),
                "p_workspace_id": str(workspace_id),
                "p_evidence_id": str(evidence_id),
            },
            headers={
                "x-evidence-api-key-hash": key_hash,
                "x-evidence-api-key-gateway-timestamp": str(timestamp),
                "x-evidence-api-key-gateway-nonce": str(nonce),
                "x-evidence-api-key-gateway-proof": proof,
            },
        )
        payload = self._json_list(response)
        records = SupabaseEvidenceRepository._parse_rows(
            httpx.Response(200, json=payload), workspace_id
        )
        return records[0] if records else None

    async def read_relationships(
        self,
        *,
        key_id: UUID,
        key_hash: str,
        workspace_id: UUID,
        limit: int,
        cursor_created_at: str | None,
        cursor_id: UUID | None,
    ) -> PersonalApiKeyPage:
        payload = await self._read_page(
            resource="relationships",
            key_id=key_id,
            key_hash=key_hash,
            workspace_id=workspace_id,
            limit=limit,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
        )
        response = httpx.Response(200, json=payload)
        records = SupabaseRelationshipRepository._parse_rows(response, workspace_id)
        items, next_cursor = self._page(records, limit)
        return PersonalApiKeyPage(items=items, next_cursor=next_cursor)

    async def _read_page(
        self,
        *,
        resource: str,
        key_id: UUID,
        key_hash: str,
        workspace_id: UUID,
        limit: int,
        cursor_created_at: str | None,
        cursor_id: UUID | None,
    ) -> list[object]:
        secret = self._settings.personal_api_key_gateway_secret
        if secret is None:
            raise PersonalApiKeyDataUnavailable
        timestamp = int(time.time())
        nonce = uuid4()
        proof = gateway_signature(
            secret,
            resource=resource,
            key_id=key_id,
            workspace_id=workspace_id,
            key_hash=key_hash,
            timestamp=timestamp,
            nonce=nonce,
            limit=limit + 1,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
        )
        rpc_name = (
            "read_personal_api_key_evidence"
            if resource == "evidence"
            else "read_personal_api_key_relationships"
        )
        response = await self._request(
            "POST",
            f"rpc/{rpc_name}",
            json={
                "p_key_id": str(key_id),
                "p_workspace_id": str(workspace_id),
                "p_limit": limit + 1,
                "p_cursor_created_at": cursor_created_at,
                "p_cursor_id": str(cursor_id) if cursor_id else None,
            },
            headers={
                "x-evidence-api-key-hash": key_hash,
                "x-evidence-api-key-gateway-timestamp": str(timestamp),
                "x-evidence-api-key-gateway-nonce": str(nonce),
                "x-evidence-api-key-gateway-proof": proof,
            },
        )
        return self._json_list(response)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        access_token: str | None = None,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        request_headers = {
            "apikey": self._settings.publishable_key,
            "Accept": "application/json",
        }
        if access_token is not None:
            request_headers["Authorization"] = f"Bearer {access_token}"
        if json is not None:
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)
        url = f"{self._settings.supabase_url.rstrip('/')}/rest/v1/{path}"
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    headers=request_headers,
                    json=json,
                )
        except httpx.HTTPError as exc:
            raise PersonalApiKeyDataUnavailable from exc
        if response.status_code in {401, 403}:
            raise PersonalApiKeyAccessDenied
        if response.status_code == 429:
            raise PersonalApiKeyRateLimited
        if response.status_code not in {200, 201, 204}:
            try:
                error = response.json()
                code = error.get("code") if isinstance(error, dict) else None
            except (ValueError, TypeError):
                code = None
            if code == "PGRST202":
                # Function not in the schema cache: nothing ran.
                raise PersonalApiKeySearchUnavailable
            if code == "P4290":
                raise PersonalApiKeyRateLimited
            if code == "P4291":
                raise PersonalApiKeyLimitReached
            if code in {"28000", "42501"}:
                raise PersonalApiKeyAccessDenied
            raise mark_upstream_status(PersonalApiKeyDataUnavailable(), response.status_code)
        return response

    @staticmethod
    def _page(items: list[Any], limit: int) -> tuple[list[Any], str | None]:
        if len(items) <= limit:
            return items, None
        page = items[:limit]
        last = page[-1]
        cursor = json.dumps(
            {"created_at": last.created_at.isoformat(), "id": str(last.id)},
            separators=(",", ":"),
        ).encode("utf-8")
        return page, base64.urlsafe_b64encode(cursor).decode("ascii").rstrip("=")

    @staticmethod
    def _json_list(response: httpx.Response) -> list[object]:
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise PersonalApiKeyDataUnavailable from exc
        if not isinstance(payload, list):
            raise PersonalApiKeyDataUnavailable
        return payload

    @staticmethod
    def _text(value: object, max_length: int) -> str:
        if not isinstance(value, str) or not value or len(value) > max_length:
            raise ValueError
        return value

    @staticmethod
    def _datetime(value: object) -> datetime:
        if not isinstance(value, str):
            raise ValueError
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed

    @classmethod
    def _optional_datetime(cls, value: object) -> datetime | None:
        return None if value is None else cls._datetime(value)

    @staticmethod
    def _scopes(value: object) -> list[str]:
        expected = {"evidence:read", "relationships:read"}
        if not isinstance(value, list) or set(value) != expected:
            raise ValueError
        return list(value)
