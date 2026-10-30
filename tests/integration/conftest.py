from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import subprocess
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
import jwt
import pytest

from evidence_service.auth import (
    AuthenticationRejected,
    AuthenticationUnavailable,
    Principal,
    VerifiedAccessToken,
)
from evidence_service.secure_snapshot_read import (
    LocalSupabaseStorageObjectStore,
    SecureSnapshotReadPath,
    SnapshotState,
    SourceSnapshotManifest,
)
from evidence_service.tenant_context import WorkspaceMembership

BUCKET = "source-snapshots"
_PROJECT_PREFIX = "evidence-layer-integration-"
_REQUIRED_ENV = (
    "EVIDENCE_LAYER_LOCAL_STORAGE_URL",
    "EVIDENCE_LAYER_LOCAL_STORAGE_PUBLISHABLE_KEY",
    "EVIDENCE_LAYER_LOCAL_AUTH_MAILPIT_URL",
    "EVIDENCE_LAYER_LOCAL_DB_CONTAINER",
    "EVIDENCE_LAYER_LOCAL_PROJECT_ID",
    "EVIDENCE_LAYER_LOCAL_STACK_DISPOSABLE",
)


def pytest_collection_modifyitems(session, config, items):
    if config.option.collectonly:
        return
    integration_dir = Path(__file__).resolve().parent
    if not any(
        Path(item.path).resolve().is_relative_to(integration_dir)
        for item in items
    ):
        return
    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise pytest.UsageError(
            "Live integration tests require the disposable local stack. Missing "
            + ", ".join(missing)
            + ". Run tests/integration/run_local_supabase.py."
        )


def _is_loopback_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        return (
            parsed.scheme == "http"
            and parsed.username is None
            and parsed.password is None
            and parsed.query == ""
            and parsed.fragment == ""
            and (
                hostname == "localhost"
                or ipaddress.ip_address(hostname).is_loopback
            )
        )
    except ValueError:
        return False


def _response_json(response: httpx.Response, action: str) -> object:
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"{action} failed with HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"{action} returned invalid JSON") from exc


def is_rejected(
    response: httpx.Response, *, postgrest_code: str | None = None
) -> bool:
    """True when the response is a closed rejection, not an unexpected failure.

    A rejection is 400/401/403/404. PostgREST also maps PL/pgSQL exceptions to
    HTTP 500, so a 500 counts only when its body carries the exact expected
    PostgreSQL error code (for example P0002 "no data found"); any other 500 is
    an unexpected internal error and fails the assertion.
    """
    if response.status_code in {400, 401, 403, 404}:
        return True
    if response.status_code == 500 and postgrest_code is not None:
        try:
            body = response.json()
        except ValueError:
            return False
        return isinstance(body, dict) and body.get("code") == postgrest_code
    return False


def _user_id(payload: object) -> str:
    if not isinstance(payload, dict):
        raise RuntimeError("local Auth returned an invalid signup response")
    user = payload.get("user", payload)
    if not isinstance(user, dict) or not isinstance(user.get("id"), str):
        raise RuntimeError("local Auth signup response did not contain a user id")
    return str(UUID(user["id"]))


class _ConfirmationLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)


class LocalSupabaseAuthVerifier:
    def __init__(self, api_url: str, publishable_key: str) -> None:
        self._api_url = api_url.rstrip("/")
        self._publishable_key = publishable_key

    async def _jwks_key(self, kid: object):
        if not isinstance(kid, str) or not kid:
            raise AuthenticationRejected
        try:
            async with httpx.AsyncClient(
                timeout=5.0, follow_redirects=False, trust_env=False
            ) as client:
                response = await client.get(
                    f"{self._api_url}/auth/v1/.well-known/jwks.json",
                    headers={"apikey": self._publishable_key},
                )
        except httpx.HTTPError as exc:
            raise AuthenticationUnavailable from exc
        if response.status_code != 200:
            raise AuthenticationUnavailable
        try:
            keys = response.json().get("keys", [])
            return jwt.PyJWK(
                next(
                    candidate
                    for candidate in keys
                    if candidate.get("kid") == kid
                    and candidate.get("alg") in {"ES256", "RS256"}
                )
            ).key
        except (StopIteration, AttributeError, TypeError, ValueError, jwt.PyJWTError) as exc:
            raise AuthenticationRejected from exc

    async def verified_claims(self, token: str) -> dict:
        try:
            header = jwt.get_unverified_header(token)
        except (jwt.PyJWTError, TypeError, ValueError) as exc:
            raise AuthenticationRejected from exc
        algorithm = header.get("alg")
        if header.get("typ") != "JWT" or algorithm not in {"ES256", "RS256"}:
            raise AuthenticationRejected
        key = await self._jwks_key(header.get("kid"))
        try:
            return jwt.decode(
                token,
                key=key,
                algorithms=[algorithm],
                audience="authenticated",
                issuer=f"{self._api_url}/auth/v1",
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except (jwt.PyJWTError, TypeError, ValueError) as exc:
            raise AuthenticationRejected from exc

    async def verify_access_token(self, token: str) -> VerifiedAccessToken:
        claims = await self.verified_claims(token)
        try:
            async with httpx.AsyncClient(
                timeout=5.0, follow_redirects=False, trust_env=False
            ) as client:
                response = await client.get(
                    f"{self._api_url}/auth/v1/user",
                    headers={
                        "apikey": self._publishable_key,
                        "authorization": f"Bearer {token}",
                    },
                )
        except httpx.HTTPError as exc:
            raise AuthenticationUnavailable from exc
        if response.status_code in {400, 401, 403}:
            raise AuthenticationRejected
        if response.status_code != 200:
            raise AuthenticationUnavailable

        try:
            auth_user = response.json()
            subject = UUID(auth_user["id"])
            expiry = datetime.fromtimestamp(float(claims["exp"]), timezone.utc)
            if (
                UUID(claims["sub"]) != subject
                or claims.get("role") != "authenticated"
                or auth_user.get("is_anonymous") is True
            ):
                raise AuthenticationRejected
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise AuthenticationRejected from exc
        if expiry <= datetime.now(timezone.utc):
            raise AuthenticationRejected
        return VerifiedAccessToken(
            principal=Principal(issuer=claims["iss"], subject=subject),
            expires_at=expiry,
        )


class LocalSupabaseStack:
    def __init__(self) -> None:
        missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
        if missing:
            raise RuntimeError(
                "Live integration environment is incomplete: "
                + ", ".join(missing)
                + ". Run tests/integration/run_local_supabase.py."
            )
        self.api = os.environ["EVIDENCE_LAYER_LOCAL_STORAGE_URL"].rstrip("/")
        self.mailpit = os.environ["EVIDENCE_LAYER_LOCAL_AUTH_MAILPIT_URL"].rstrip("/")
        self.project_id = os.environ["EVIDENCE_LAYER_LOCAL_PROJECT_ID"]
        self.container = os.environ["EVIDENCE_LAYER_LOCAL_DB_CONTAINER"]
        self.key = os.environ["EVIDENCE_LAYER_LOCAL_STORAGE_PUBLISHABLE_KEY"]
        if (
            not _is_loopback_url(self.api)
            or not _is_loopback_url(self.mailpit)
            or not self.project_id.startswith(_PROJECT_PREFIX)
            or self.container != f"supabase_db_{self.project_id}"
            or os.environ["EVIDENCE_LAYER_LOCAL_STACK_DISPOSABLE"] != "1"
            or not self._is_publishable_key(self.key)
        ):
            raise RuntimeError(
                "Integration tests require the disposable loopback Supabase project "
                f"created by tests/integration/run_local_supabase.py; refusing "
                f"project {self.project_id!r}."
            )
        self.client = httpx.Client(
            timeout=10.0, follow_redirects=False, trust_env=False
        )
        self.verifier = LocalSupabaseAuthVerifier(self.api, self.key)
        try:
            self._assert_services_ready()
        except Exception:
            self.client.close()
            raise

    @staticmethod
    def _is_publishable_key(value: str) -> bool:
        if value.startswith("sb_publishable_"):
            return len(value) > len("sb_publishable_")
        parts = value.split(".")
        if len(parts) != 3:
            return False
        try:
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        except (ValueError, UnicodeError, json.JSONDecodeError):
            return False
        return isinstance(claims, dict) and claims.get("role") == "anon"

    def _assert_services_ready(self) -> None:
        try:
            auth = self.client.get(
                f"{self.api}/auth/v1/.well-known/jwks.json",
                headers={"apikey": self.key},
            )
            storage = self.client.get(
                f"{self.api}/storage/v1/bucket/{BUCKET}",
                headers={"apikey": self.key},
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(
                "Local Supabase Auth or Storage is unreachable; start the isolated "
                "stack with tests/integration/run_local_supabase.py."
            ) from exc
        if auth.status_code != 200:
            raise RuntimeError(f"Local Supabase JWKS endpoint returned HTTP {auth.status_code}")
        if storage.status_code >= 500 or storage.status_code == 404:
            raise RuntimeError(
                "Local Supabase Storage or the source-snapshots bucket is unavailable "
                f"(HTTP {storage.status_code})."
            )

    def headers(self, token: str, **extra: str) -> dict[str, str]:
        return {
            "apikey": self.key,
            "authorization": f"Bearer {token}",
            **extra,
        }

    def _confirmation_token(self, email: str) -> tuple[str, str]:
        deadline = time.monotonic() + 30
        message_id: str | None = None
        while time.monotonic() < deadline and message_id is None:
            try:
                response = self.client.get(
                    f"{self.mailpit}/api/v1/search",
                    params={"query": f"to:{email}"},
                )
            except httpx.HTTPError as exc:
                raise RuntimeError("Local Mailpit is unreachable") from exc
            messages = _response_json(response, "Mailpit message search")
            entries = messages.get("messages", []) if isinstance(messages, dict) else messages
            if isinstance(entries, list):
                for message in entries:
                    if not isinstance(message, dict):
                        continue
                    recipients = message.get("To", [])
                    if any(
                        isinstance(recipient, dict)
                        and recipient.get("Address", "").casefold() == email.casefold()
                        for recipient in recipients
                    ):
                        message_id = message.get("ID") or message.get("id")
                        break
            if message_id is None:
                time.sleep(0.25)
        if not isinstance(message_id, str) or not message_id:
            raise RuntimeError(
                "Local Auth did not deliver the confirmation email to Mailpit."
            )

        response = self.client.get(f"{self.mailpit}/api/v1/message/{message_id}")
        message = _response_json(response, "Mailpit confirmation message")
        if not isinstance(message, dict):
            raise RuntimeError("Mailpit returned an invalid confirmation message")
        parser = _ConfirmationLinks()
        html = message.get("HTML", "")
        text = message.get("Text", "")
        if isinstance(html, str):
            parser.feed(html)
        candidates = [*parser.links]
        if isinstance(text, str):
            candidates.extend(re.findall(r"https?://[^\s<>\"']+", text))
        for candidate in candidates:
            parsed = urlsplit(candidate.replace("&amp;", "&"))
            query = parse_qs(parsed.query)
            token_type = (query.get("type") or ["signup"])[0]
            code = (query.get("token") or query.get("token_hash") or [""])[0]
            if code and token_type in {"signup", "email"}:
                return code, token_type
        raise RuntimeError("Local Auth confirmation email did not contain a signup token.")

    def signup(self, tag: str) -> tuple[str, str]:
        email = f"{tag}-{secrets.token_hex(8)}@example.invalid"
        password = secrets.token_urlsafe(32)
        try:
            response = self.client.post(
                f"{self.api}/auth/v1/signup",
                headers={"apikey": self.key},
                json={"email": email, "password": password},
            )
        except httpx.HTTPError as exc:
            raise RuntimeError("Local Supabase Auth signup request failed") from exc
        payload = _response_json(response, "Local Auth signup")
        user_id = UUID(_user_id(payload))
        confirmation, token_type = self._confirmation_token(email)
        try:
            response = self.client.get(
                f"{self.api}/auth/v1/verify",
                params={"token": confirmation, "type": token_type},
                headers={"apikey": self.key},
            )
        except httpx.HTTPError as exc:
            raise RuntimeError("Local Auth email confirmation request failed") from exc
        if response.status_code not in {301, 302, 303, 307, 308}:
            raise RuntimeError(
                f"Local Auth email confirmation failed with HTTP {response.status_code}"
            )
        destination = urlsplit(response.headers.get("location", ""))
        session = parse_qs(destination.fragment or destination.query)
        token = (session.get("access_token") or [""])[0]
        if not isinstance(token, str) or not token:
            raise RuntimeError("Local Auth did not issue an access JWT after signup.")

        try:
            response = self.client.get(
                f"{self.api}/auth/v1/user",
                headers=self.headers(token),
            )
        except httpx.HTTPError as exc:
            raise RuntimeError("Local Auth could not validate the issued JWT") from exc
        auth_user = _response_json(response, "Local Auth JWT validation")
        if not isinstance(auth_user, dict) or UUID(auth_user.get("id", "")) != user_id:
            raise RuntimeError("Local Auth JWT identity did not match the created user.")
        return token, str(user_id)

    def personal_workspace(self, token: str) -> str:
        try:
            response = self.client.post(
                f"{self.api}/rest/v1/rpc/ensure_personal_workspace",
                headers=self.headers(token, **{"content-type": "application/json"}),
                json={},
            )
        except httpx.HTTPError as exc:
            raise RuntimeError("Could not provision the test user's local workspace") from exc
        result = _response_json(response, "Personal workspace provisioning")
        row = result[0] if isinstance(result, list) and result else result
        if not isinstance(row, dict) or not isinstance(row.get("workspace_id"), str):
            raise RuntimeError("Workspace provisioning returned no workspace id")
        return str(UUID(row["workspace_id"]))

    def sql(self, statement: str) -> None:
        # Operator seeding runs as supabase_admin: the local image's postgres role
        # is not a superuser and private security tables (e.g. the snapshot
        # verifier allowlist) revoke all privileges from it by design.
        try:
            subprocess.run(
                [
                    "docker",
                    "exec",
                    self.container,
                    "psql",
                    "-U",
                    "supabase_admin",
                    "-d",
                    "postgres",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-qc",
                    statement,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("Isolated Supabase database operator action failed") from exc

    @staticmethod
    def _uuid(value: str) -> str:
        return str(UUID(value))

    def add_member(self, workspace_id: str, user_id: str) -> None:
        workspace, user = self._uuid(workspace_id), self._uuid(user_id)
        self.sql(
            "insert into public.workspace_memberships "
            "(workspace_id, user_id, role, status) "
            f"values ('{workspace}', '{user}', 'member', 'active')"
        )

    def set_verifier_listed(self, listed: bool, user_id: str, workspace_id: str) -> None:
        user, workspace = self._uuid(user_id), self._uuid(workspace_id)
        statement = (
            "insert into private.snapshot_verifier_allowlist (user_id, workspace_id) "
            f"values ('{user}', '{workspace}') on conflict do nothing"
            if listed
            else "delete from private.snapshot_verifier_allowlist "
            f"where user_id = '{user}' and workspace_id = '{workspace}'"
        )
        self.sql(statement)

    def stage(
        self,
        token: str,
        workspace_id: str,
        source_id: str,
        version_id: str,
        data: bytes,
        **override,
    ) -> httpx.Response:
        row = {
            "workspace_id": self._uuid(workspace_id),
            "source_id": self._uuid(source_id),
            "version_id": self._uuid(version_id),
            "object_key": object_key(workspace_id, source_id, version_id),
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "state": "staged",
        }
        row.update(override)
        return self.client.post(
            f"{self.api}/rest/v1/source_snapshots",
            headers=self.headers(
                token,
                **{"content-type": "application/json", "prefer": "return=minimal"},
            ),
            json=row,
        )

    def begin(
        self, token: str, workspace_id: str, source_id: str, version_id: str
    ) -> httpx.Response:
        return self.client.post(
            f"{self.api}/rest/v1/rpc/begin_snapshot_verification",
            headers=self.headers(token, **{"content-type": "application/json"}),
            json={
                "p_workspace_id": self._uuid(workspace_id),
                "p_source_id": self._uuid(source_id),
                "p_version_id": self._uuid(version_id),
            },
        )

    def publish(
        self,
        token: str,
        workspace_id: str,
        source_id: str,
        version_id: str,
        *,
        digest: str,
        size: int,
    ) -> httpx.Response:
        opened = self.begin(token, workspace_id, source_id, version_id)
        if opened.status_code != 200:
            return opened
        try:
            rows = opened.json()
        except ValueError:
            return opened
        if not isinstance(rows, list) or len(rows) != 1:
            return opened
        claim = rows[0]
        return self.client.post(
            f"{self.api}/rest/v1/rpc/publish_verified_snapshot",
            headers=self.headers(token, **{"content-type": "application/json"}),
            json={
                "p_workspace_id": self._uuid(workspace_id),
                "p_source_id": self._uuid(source_id),
                "p_version_id": self._uuid(version_id),
                "p_sha256": digest,
                "p_size_bytes": size,
                "p_storage_object_id": claim["out_storage_object_id"],
                "p_storage_object_version": claim["out_storage_object_version"],
            },
        )

    def catalog_state(self, token: str, version_id: str) -> list[dict]:
        response = self.client.get(
            f"{self.api}/rest/v1/source_snapshots",
            params={"select": "state,verified_at", "version_id": f"eq.{version_id}"},
            headers=self.headers(token),
        )
        value = _response_json(response, "Snapshot catalog query")
        if not isinstance(value, list):
            raise RuntimeError("Snapshot catalog query returned invalid data")
        return value

    def grant(
        self, owner_token: str, workspace_id: str, source_id: str, principal_id: str
    ) -> httpx.Response:
        return self.client.post(
            f"{self.api}/rest/v1/source_grants",
            headers=self.headers(
                owner_token,
                **{"content-type": "application/json", "prefer": "return=minimal"},
            ),
            json={
                "workspace_id": self._uuid(workspace_id),
                "source_id": self._uuid(source_id),
                "principal_id": self._uuid(principal_id),
                "grant_type": "read",
            },
        )

    def revoke(
        self, owner_token: str, workspace_id: str, source_id: str, principal_id: str
    ) -> httpx.Response:
        return self.client.delete(
            f"{self.api}/rest/v1/source_grants",
            params={
                "workspace_id": f"eq.{self._uuid(workspace_id)}",
                "source_id": f"eq.{self._uuid(source_id)}",
                "principal_id": f"eq.{self._uuid(principal_id)}",
            },
            headers=self.headers(owner_token),
        )

    def upload(
        self, token: str, key: str, data: bytes, *, upsert: bool = False
    ) -> httpx.Response:
        return self.client.post(
            f"{self.api}/storage/v1/object/{BUCKET}/{key}",
            headers=self.headers(
                token,
                **{
                    "content-type": "application/octet-stream",
                    "x-upsert": "true" if upsert else "false",
                },
            ),
            content=data,
        )

    def replace(self, token: str, key: str, data: bytes) -> httpx.Response:
        return self.client.put(
            f"{self.api}/storage/v1/object/{BUCKET}/{key}",
            headers=self.headers(token, **{"content-type": "application/octet-stream"}),
            content=data,
        )

    def read(self, token: str, key: str) -> httpx.Response:
        return self.client.get(
            f"{self.api}/storage/v1/object/{BUCKET}/{key}",
            headers=self.headers(token),
        )

    def delete(self, token: str, key: str) -> httpx.Response:
        return self.client.delete(
            f"{self.api}/storage/v1/object/{BUCKET}/{key}",
            headers=self.headers(token),
        )

    def secure_read_path(self, token: str) -> SecureSnapshotReadPath:
        # The production default read deadline (10s) is sized for a healthy
        # deployment; the disposable two-CPU Codespaces host needs headroom for
        # the full identity/membership/ACL/Storage chain. The deadline still
        # fails closed, so widening it for the live test changes timing only.
        return SecureSnapshotReadPath(
            verifier=self.verifier,
            workspace_directory=_RestWorkspaceDirectory(self.api, self.key),
            catalog=_RestSnapshotCatalog(self.api, self.key, token),
            acl=_RestSourceAcl(self.api, self.key, token),
            store=LocalSupabaseStorageObjectStore(
                base_url=self.api,
                bucket=BUCKET,
                publishable_key=self.key,
            ),
            max_read_seconds=45.0,
        )

    def close(self) -> None:
        self.client.close()


class _RestWorkspaceDirectory:
    def __init__(self, api: str, key: str) -> None:
        self._api = api
        self._key = key

    async def ensure_personal_workspace(self, principal, access_token):
        raise AssertionError("not used by the live read test")

    async def list_memberships(self, principal, access_token):
        raise AssertionError("not used by the live read test")

    async def get_membership(self, principal, access_token, workspace_id):
        async with httpx.AsyncClient(
            timeout=5.0, follow_redirects=False, trust_env=False
        ) as client:
            response = await client.get(
                f"{self._api}/rest/v1/workspace_memberships",
                params={
                    "select": "workspace_id,role",
                    "workspace_id": f"eq.{workspace_id}",
                    "user_id": f"eq.{principal.subject}",
                    "status": "eq.active",
                },
                headers={
                    "apikey": self._key,
                    "authorization": f"Bearer {access_token}",
                },
            )
        rows = _response_json(response, "Workspace membership query")
        if not isinstance(rows, list) or not rows:
            return None
        try:
            return WorkspaceMembership(
                workspace_id=UUID(rows[0]["workspace_id"]),
                workspace_name="Local integration workspace",
                role=rows[0]["role"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Workspace membership response was invalid") from exc


class _RestSnapshotCatalog:
    def __init__(self, api: str, key: str, token: str) -> None:
        self._api = api
        self._key = key
        self._token = token

    async def resolve_version(self, workspace_id, source_id, version_id):
        async with httpx.AsyncClient(
            timeout=5.0, follow_redirects=False, trust_env=False
        ) as client:
            response = await client.get(
                f"{self._api}/rest/v1/source_snapshots",
                params={
                    "select": (
                        "workspace_id,source_id,version_id,object_key,sha256,"
                        "size_bytes,state,created_by,created_at"
                    ),
                    "workspace_id": f"eq.{workspace_id}",
                    "source_id": f"eq.{source_id}",
                    "version_id": f"eq.{version_id}",
                },
                headers={
                    "apikey": self._key,
                    "authorization": f"Bearer {self._token}",
                },
            )
        rows = _response_json(response, "Snapshot catalog query")
        if not isinstance(rows, list) or not rows:
            return None
        row = rows[0]
        try:
            created_at = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            return SourceSnapshotManifest(
                workspace_id=UUID(row["workspace_id"]),
                source_id=UUID(row["source_id"]),
                owner_id=UUID(row["created_by"]),
                version_id=UUID(row["version_id"]),
                object_key=row["object_key"],
                sha256=row["sha256"],
                size_bytes=int(row["size_bytes"]),
                state=SnapshotState(row["state"]),
                created_at=created_at,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Snapshot catalog response was invalid") from exc

    async def publish_if_absent(self, manifest: SourceSnapshotManifest) -> None:
        raise AssertionError("The live read test does not write through its catalog adapter")


class _RestSourceAcl:
    def __init__(self, api: str, key: str, token: str) -> None:
        self._api = api
        self._key = key
        self._token = token

    async def may_read(self, principal_id, workspace_id, source_id, version_id):
        async with httpx.AsyncClient(
            timeout=5.0, follow_redirects=False, trust_env=False
        ) as client:
            response = await client.get(
                f"{self._api}/rest/v1/source_grants",
                params={
                    "select": "workspace_id",
                    "workspace_id": f"eq.{workspace_id}",
                    "source_id": f"eq.{source_id}",
                    "principal_id": f"eq.{principal_id}",
                    "grant_type": "eq.read",
                },
                headers={
                    "apikey": self._key,
                    "authorization": f"Bearer {self._token}",
                },
            )
        rows = _response_json(response, "Source grant query")
        return isinstance(rows, list) and bool(rows)


def object_key(workspace_id: str, source_id: str, version_id: str) -> str:
    return f"workspace/{workspace_id}/source/{source_id}/version/{version_id}"


@pytest.fixture(scope="module")
def stack():
    instance = LocalSupabaseStack()
    yield instance
    instance.close()
