"""HTTP application for health, verified identity, and workspace boundaries."""

from __future__ import annotations

import base64
import hashlib
import secrets
import re
import json
import logging
import math
import os
from time import perf_counter
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal, TypeVar
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.concurrency import run_in_threadpool

from evidence_service.account_lifecycle import (
    AccountLifecycleReader,
    SupabaseAccountLifecycleReader,
)
from evidence_service.deletion_repository import (
    DeletionRepository,
    DeletionRequest,
    DeletionRequestUnavailable,
    SupabaseDeletionRepository,
)
from evidence_service.auth import (
    AuthenticatedRequest,
    AuthenticationRejected,
    AuthenticationUnavailable,
    MFAFactorVerifier,
    SupabaseMFAFactorVerifier,
    SupabaseTokenVerifier,
    TokenVerifier,
    VerifiedTokenVerifier,
)
from evidence_service.config import ConfigurationError, SupabaseSettings
from evidence_service.connector_crypto import TokenVault, TokenVaultUnavailable
from evidence_service.connector_repository import (
    ConnectorDenied,
    ConnectorNotFound,
    ConnectorRepository,
    ConnectorUnavailable,
)
from evidence_service.connector_sync_service import (
    ConnectorAuthUnavailable,
    ConnectorSyncError,
    GoogleDriveSyncService,
)
from evidence_service.notion_sync_service import (
    NotionSyncError,
    NotionSyncService,
)
from evidence_service.github_app_sync_service import (
    GitHubAppSyncError,
    GitHubAppSyncService,
    GitHubInstallationNotOwned,
)
from evidence_service.export_repository import (
    ExportAccessDenied,
    ExportDataUnavailable,
    ExportTooLarge,
    SupabaseExportRepository,
)
from evidence_service.evidence_repository import (
    EvidenceAccessDenied,
    EvidenceDataUnavailable,
    EvidenceRecord,
    EvidenceRepository,
    SupabaseEvidenceRepository,
)
from evidence_service.relationship_repository import (
    EvidenceRelationship,
    RelationshipAccessDenied,
    RelationshipConflict,
    RelationshipDataUnavailable,
    RelationshipRepository,
    SupabaseRelationshipRepository,
)
from evidence_service.retrieval_service import RetrievalSelection, select_evidence
from evidence_service.personal_api_key_repository import (
    PersonalApiKeyAccessDenied,
    PersonalApiKeyDataUnavailable,
    PersonalApiKeyLimitReached,
    PersonalApiKeyPage,
    PersonalApiKeyRateLimited,
    SupabasePersonalApiKeyRepository,
    decode_personal_api_key_cursor,
    parse_personal_api_key,
)
from evidence_service.remote_mcp import (
    REMOTE_MCP_PATH,
    RemoteMcpPrincipal,
    create_remote_mcp_runtime,
)
from evidence_service.task_run_ledger_repository import (
    SupabaseTaskRunLedgerRepository,
    TaskRunLedgerUnavailable,
)
from evidence_service.source_staging_repository import (
    SourceStageConflict,
    SourceStageForbidden,
    SourceStageRejected,
    SourceStageUnavailable,
    SupabaseSourceStagingRepository,
)
from evidence_service.source_grant_repository import (
    SourceGrantConflict,
    SourceGrantForbidden,
    SourceGrantRejected,
    SourceGrantUnavailable,
    SupabaseSourceGrantRepository,
)
from evidence_service.workspace_invitation_repository import (
    InvitationForbidden,
    InvitationRejected,
    InvitationUnavailable,
    SupabaseWorkspaceInvitationRepository,
)
from evidence_service.resend_email import send_invitation_email
from evidence_service.workspace_usage_repository import (
    SupabaseWorkspaceUsageRepository,
    WorkspaceUsageForbidden,
    WorkspaceUsageUnavailable,
)
from evidence_service.workspace_deletion_repository import (
    SupabaseWorkspaceDeletionRepository,
    WorkspaceDeletionForbidden,
    WorkspaceDeletionUnavailable,
)
from evidence_service.recovery_fence import (
    CloudflareRecoveryFenceReader,
    RecoveryFenceConfigurationError,
    RecoveryFenceReader,
)
from evidence_service.usage_caps import (
    SupabaseUsageCapsRepository,
    UsageCapsAccessDenied,
    UsageCapsSettings,
    UsageCapsUnavailable,
    UsageDecision,
)
from evidence_service.organization_repository import (
    OrganizationAccessDenied,
    OrganizationDataUnavailable,
    OrganizationMemberNotFound,
    OrganizationValidationError,
    SupabaseOrganizationRepository,
)
from evidence_service.supabase_repository import (
    SupabaseWorkspaceRepository,
    WorkspaceDataUnavailable,
)
from evidence_service.tenant_context import (
    AccountProfile,
    WorkspaceAccessDenied,
    WorkspaceContext,
    WorkspaceDirectory,
    resolve_workspace_context,
)

logger = logging.getLogger(__name__)
MAX_WORKSPACE_SOURCE_VERSIONS = 100
MAX_WORKSPACE_SOURCE_BYTES = 100 * 1024 * 1024
MAX_PENDING_WORKSPACE_INVITATIONS = 50

if TYPE_CHECKING:
    from evidence_layer.scoring.jev_request import JevRequestScorer


class WorkspaceSummary(BaseModel):
    id: UUID
    name: str
    role: Literal["owner", "admin", "member"]
    workspace_type: Literal["personal", "group"] = "personal"


class MeResponse(BaseModel):
    user_id: UUID
    issuer: str
    account_type: Literal["individual", "organization"] = "individual"
    organization_name: str | None = None
    can_manage_workspace: bool = False
    workspaces: list[WorkspaceSummary]


class OrganizationCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class OrganizationMemberSummary(BaseModel):
    user_id: UUID
    email: str
    role: Literal["admin", "member"]


class OrganizationWorkspaceSummary(BaseModel):
    id: UUID
    name: str


class OrganizationOverviewResponse(BaseModel):
    id: UUID
    name: str
    owner_user_id: UUID
    members: list[OrganizationMemberSummary]
    workspaces: list[OrganizationWorkspaceSummary]


class GroupWorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class GroupWorkspaceResponse(BaseModel):
    workspace_id: UUID
    name: str


class GroupWorkspaceDeleteResponse(BaseModel):
    workspace_id: UUID
    workspace_deleted: bool
    snapshots_deleted: int
    storage_cleanup_queued: int


class OrganizationMemberCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: Literal["admin", "member"]


class OrganizationMemberRoleRequest(BaseModel):
    role: Literal["admin", "member"]


class OrganizationMemberRoleResponse(BaseModel):
    user_id: UUID
    role: Literal["admin", "member"]


class OrganizationMemberRemovalResponse(BaseModel):
    user_id: UUID
    removed: bool



class AccountExportCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    idempotency_key: str = Field(min_length=1, max_length=128)


class AccountExportResponse(BaseModel):
    export_id: UUID
    status: str
    ready_at: datetime | None
    expires_at: datetime | None
    reused: bool
    grant_token: str | None
    download_path: str


class AccountExportRevokeResponse(BaseModel):
    export_id: UUID
    revoked: bool


class ConnectorConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str = Field(min_length=1)
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scopes: list[str] = Field(default_factory=list)
    account_email: str | None = Field(default=None, max_length=320)



class ConnectorGitHubConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installation_id: int = Field(gt=0)
    # One-time code GitHub issues when the App requests user authorization
    # during installation; it proves the caller can access the installation.
    oauth_code: str = Field(min_length=1, max_length=512)


class ConnectorConnectionResponse(BaseModel):
    connection_id: UUID
    workspace_id: UUID | None
    provider: str
    account_email: str | None
    scopes: list[str]
    status: str
    last_sync_at: datetime | None
    last_error_category: str | None


class ConnectorSyncResponse(BaseModel):
    connection_id: UUID
    listed: int
    ingested: int
    skipped: int
    next_cursor: str | None
    unchanged: int = 0
    not_indexed: int = 0


class ConnectorNotIndexedFile(BaseModel):
    """A synced file that has no evidence, with the reason. Owner-only (RLS)."""

    repository: str
    path: str
    reason: Literal["too_large", "binary", "fetch_error", "empty", "too_many_parts"]
    size_bytes: int


class WorkspaceResponse(BaseModel):
    id: UUID
    name: str
    role: Literal["owner", "admin", "member"]


class SourceVersionSummary(BaseModel):
    source_id: UUID
    version_id: UUID
    state: Literal["staged", "published"]
    sha256: str
    size_bytes: int
    created_at: datetime


class SourceStageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    content: str = Field(min_length=1, max_length=4_000_000)
    source_id: UUID | None = None


class SourceStageResponse(BaseModel):
    source_id: UUID
    version_id: UUID
    state: Literal["staged"]
    sha256: str
    size_bytes: int
    bucket: Literal["source-snapshots"]
    object_key: str
    created_at: datetime


class SourceGrantSummary(BaseModel):
    principal_id: UUID
    grant_type: Literal["read"]
    granted_by: UUID
    created_at: datetime


class SourceGrantCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal_id: UUID


class WorkspaceInvitationSummary(BaseModel):
    invitation_id: UUID
    email: str
    role: Literal["owner", "member"]
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None


class WorkspaceInvitationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: str = Field(min_length=3, max_length=320)


class WorkspaceInvitationCreateResponse(WorkspaceInvitationSummary):
    invite_token: str
    email_sent: bool


class WorkspaceInvitationAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    token: str = Field(min_length=20, max_length=256)


class WorkspaceInvitationAcceptResponse(BaseModel):
    workspace_id: UUID
    role: Literal["owner", "member"]


class SourceLinkFields(BaseModel):
    """Optional provenance link to an exact source snapshot version.

    The pair is all-or-nothing: a link pins both the source and the version.
    """

    source_id: UUID | None = None
    source_version_id: UUID | None = None

    def linked_pair(self) -> tuple[UUID, UUID] | None:
        if self.source_id is None and self.source_version_id is None:
            return None
        if self.source_id is None or self.source_version_id is None:
            raise ValueError("A source link requires both source_id and source_version_id")
        return self.source_id, self.source_version_id


class EvidenceCreateRequest(SourceLinkFields):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=65_536)
    source_label: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def require_complete_link(self) -> EvidenceCreateRequest:
        self.linked_pair()
        return self


class EvidenceUpdateRequest(SourceLinkFields):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: str | None = Field(default=None, min_length=1, max_length=65_536)
    source_label: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def require_update_fields(self) -> EvidenceUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("At least one evidence field must be provided")
        if "content" in self.model_fields_set and self.content is None:
            raise ValueError("Evidence content cannot be null")
        provided_link_fields = self.model_fields_set & {"source_id", "source_version_id"}
        if provided_link_fields and provided_link_fields != {"source_id", "source_version_id"}:
            raise ValueError("A source link requires both source_id and source_version_id")
        self.linked_pair()
        return self


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    content: str
    source_label: str | None
    source_id: UUID | None
    source_version_id: UUID | None
    created_at: datetime
    updated_at: datetime


class RelationshipCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evidence_id_a: UUID
    evidence_id_b: UUID
    relation_type: Literal["supports", "contradicts", "contextualizes", "derived_from"]
    rationale: str = Field(min_length=1, max_length=4000)
    provenance_note: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def normalize_pair(self) -> RelationshipCreateRequest:
        if self.evidence_id_a == self.evidence_id_b:
            raise ValueError("A relationship requires two different evidence records")
        if self.evidence_id_a > self.evidence_id_b:
            self.evidence_id_a, self.evidence_id_b = self.evidence_id_b, self.evidence_id_a
        return self


class RelationshipUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    relation_type: Literal["supports", "contradicts", "contextualizes", "derived_from"] | None = None
    rationale: str | None = Field(default=None, min_length=1, max_length=4000)
    provenance_note: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def require_update_fields(self) -> RelationshipUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("At least one relationship field must be provided")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Relationship fields cannot be null")
        return self


class RelationshipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    evidence_id_a: UUID
    evidence_id_b: UUID
    relation_type: Literal["supports", "contradicts", "contextualizes", "derived_from"]
    rationale: str
    provenance_note: str
    created_by: UUID
    created_at: datetime
    updated_by: UUID
    updated_at: datetime


class PersonalApiKeyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    workspace_id: UUID
    name: str = Field(min_length=1, max_length=80)
    expiration_days: int = Field(default=90, ge=1, le=90)


class PersonalApiKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    name: str
    key_prefix: str
    scopes: list[str]
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None


class PersonalApiKeyCreatedResponse(PersonalApiKeyResponse):
    token: str


class PersonalApiKeyRevocationResponse(BaseModel):
    id: UUID
    revoked: bool



# Retrieval ranks up to this many of the workspace's evidence records (newest
# first), matching what the signed-in web app reads. Before migration
# 20261126130000 the signed read RPC accepted at most 100 records.
RETRIEVAL_CANDIDATE_LIMIT = 1000
LEGACY_RETRIEVAL_CANDIDATE_LIMIT = 100


def _log_retrieval_timing(event: str, **fields: object) -> None:
    """One content-free JSON line per retrieval step, for latency tracking.

    Only counts, durations and flags are written: never queries, evidence,
    key material or workspace identifiers.
    """
    print(json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


async def _read_retrieval_candidates(
    repository: SupabasePersonalApiKeyRepository,
    *,
    key_id: UUID,
    key_hash: str,
    workspace_id: UUID,
    limit: int,
) -> PersonalApiKeyPage:
    started = perf_counter()
    fallback = False
    try:
        page = await repository.read_evidence(
            key_id=key_id,
            key_hash=key_hash,
            workspace_id=workspace_id,
            limit=limit,
            cursor_created_at=None,
            cursor_id=None,
        )
    except PersonalApiKeyAccessDenied:
        if limit <= LEGACY_RETRIEVAL_CANDIDATE_LIMIT:
            raise
        # A database that has not applied 20261126130000 yet rejects reads
        # above 100 records exactly like a bad key. Retry once inside the old
        # bound so the service can be deployed before the migration; a
        # genuinely invalid key is still refused by this second read.
        fallback = True
        page = await repository.read_evidence(
            key_id=key_id,
            key_hash=key_hash,
            workspace_id=workspace_id,
            limit=LEGACY_RETRIEVAL_CANDIDATE_LIMIT,
            cursor_created_at=None,
            cursor_id=None,
        )
    _log_retrieval_timing(
        "retrieval_candidates_read",
        limit=limit,
        records=len(page.items),
        legacy_fallback=fallback,
        read_ms=round((perf_counter() - started) * 1000, 1),
    )
    return page


class PersonalApiKeyRetrieveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    max_tokens: int = Field(default=1200, ge=1, le=8000)
    max_chars: int = Field(default=6000, ge=1, le=40000)
    candidate_limit: int = Field(
        default=RETRIEVAL_CANDIDATE_LIMIT, ge=1, le=RETRIEVAL_CANDIDATE_LIMIT
    )
    use_jev: bool = Field(default=True, strict=True)


class PersonalApiKeyRetrievedItem(BaseModel):
    evidence_id: UUID
    content: str
    source_label: str | None
    source_id: UUID | None
    source_version_id: UUID | None
    score: float
    token_count: int
    char_count: int
    chunk_id: str
    chunk_index: int
    chunk_start: int
    chunk_end: int
    path: str | None
    title: str | None


class PersonalApiKeyRetrieveResponse(BaseModel):
    status: str
    query: str
    workspace_id: UUID
    top_k: int
    max_tokens: int
    max_chars: int
    candidate_count: int
    chunk_count: int
    matched_chunk_count: int
    selected_count: int
    used_tokens: int
    used_chars: int
    token_estimator: str
    scoring_provider: str
    scoring_fallback_reason: str | None = None
    chunking: str
    evidence: list[PersonalApiKeyRetrievedItem]
    relationships: list[TaskContextRelationship] = Field(default_factory=list)


def _evidence_page_items(page: PersonalApiKeyPage) -> list[EvidenceRecord]:
    if not all(isinstance(item, EvidenceRecord) for item in page.items):
        raise PersonalApiKeyDataUnavailable
    return [item for item in page.items if isinstance(item, EvidenceRecord)]


def _relationship_page_items(page: PersonalApiKeyPage) -> list[EvidenceRelationship]:
    if not all(isinstance(item, EvidenceRelationship) for item in page.items):
        raise PersonalApiKeyDataUnavailable
    return [item for item in page.items if isinstance(item, EvidenceRelationship)]


class WorkspaceTaskContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    task: str = Field(min_length=1, max_length=2000)
    expected_outcome: str = Field(min_length=1, max_length=2000)
    context_requirements: str = Field(default="", max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    max_tokens: int = Field(default=1200, ge=1, le=8000)
    max_chars: int = Field(default=6000, ge=1, le=40000)
    use_jev: bool = Field(default=True, strict=True)

    def retrieval_query(self) -> str:
        return "\n".join(
            value
            for value in (self.task, self.expected_outcome, self.context_requirements)
            if value
        )


class TaskContextRelationship(BaseModel):
    id: UUID
    evidence_id_a: UUID
    evidence_id_b: UUID
    relation_type: Literal["supports", "contradicts", "contextualizes", "derived_from"]
    rationale: str
    provenance_note: str


class WorkspaceTaskContextResponse(BaseModel):
    task: str
    expected_outcome: str
    context_requirements: str
    retrieval: PersonalApiKeyRetrieveResponse
    relationships: list[TaskContextRelationship]


class TaskRunLedgerItem(BaseModel):
    run_id: UUID
    workspace_id: UUID
    run_type: str
    status: str
    created_at: datetime
    retrieval_latency_ms: int
    retrieval_tokens: int
    retrieval_chars: int
    selected_count: int
    chunk_count: int
    relationship_count: int
    scoring_provider: str
    fallback_reason: str | None
    use_jev: bool
    agent_provider: str | None
    agent_model: str | None
    agent_input_tokens: int | None
    agent_output_tokens: int | None
    agent_total_tokens: int | None
    agent_cost_usd: float | None
    agent_latency_ms: int | None
    quality_status: str | None
    quality_score: float | None


class PersonalApiKeyEvidencePage(BaseModel):
    items: list[EvidenceResponse]
    next_cursor: str | None


class PersonalApiKeyRelationshipPage(BaseModel):
    items: list[RelationshipResponse]
    next_cursor: str | None


class DeletionRequestCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    idempotency_key: str = Field(min_length=1, max_length=256)


class DeletionRequestResponse(BaseModel):
    request_id: UUID
    requester_user_id: UUID
    scope_type: Literal["account"]
    status: Literal["approval_pending", "journal_ack_pending", "accepted", "purge_pending", "purge_failed", "purged"]
    created_at: datetime
    accepted_at: datetime | None


class PersonalDeletionResponse(BaseModel):
    workspace_id: UUID
    evidence_deleted: int = 0
    relationships_deleted: int = 0
    source_grants_deleted: int = 0
    snapshots_deleted: int = 0
    storage_cleanup_queued: int = 0
    storage_cleanup_required: bool = False
    workspace_deleted: bool = False


bearer_scheme = HTTPBearer(auto_error=False)
_Result = TypeVar("_Result")


def _settings_for(request: Request) -> SupabaseSettings:
    settings = request.app.state.supabase_settings
    if settings is not None:
        return settings
    try:
        settings = SupabaseSettings.from_environment()
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=503, detail="Identity service is not configured"
        ) from exc
    request.app.state.supabase_settings = settings
    return settings


def _token_verifier(request: Request) -> TokenVerifier:
    verifier = request.app.state.token_verifier
    if verifier is None:
        verifier = SupabaseTokenVerifier(_settings_for(request))
        request.app.state.token_verifier = verifier
    return verifier


def _mfa_factor_verifier(request: Request) -> MFAFactorVerifier:
    verifier = request.app.state.mfa_factor_verifier
    if verifier is None:
        verifier = SupabaseMFAFactorVerifier(_settings_for(request))
        request.app.state.mfa_factor_verifier = verifier
    return verifier


async def _require_mfa_assurance(
    request: Request, authenticated_request: AuthenticatedRequest
) -> None:
    assurance_level = authenticated_request.principal.assurance_level
    if assurance_level == "aal2":
        return
    if assurance_level != "aal1":
        raise HTTPException(
            status_code=503, detail="Two-factor authentication status is unavailable"
        )
    try:
        has_verified_factor = await _mfa_factor_verifier(request).has_verified_factor(
            authenticated_request.access_token,
            authenticated_request.principal.subject,
        )
    except AuthenticationRejected as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid bearer access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except AuthenticationUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="Two-factor authentication status is unavailable"
        ) from exc
    if has_verified_factor:
        raise HTTPException(
            status_code=403, detail="Two-factor authentication is required"
        )


def _account_lifecycle_reader(request: Request) -> AccountLifecycleReader:
    reader = request.app.state.account_lifecycle_reader
    if reader is None:
        reader = SupabaseAccountLifecycleReader(_settings_for(request))
        request.app.state.account_lifecycle_reader = reader
    return reader


def _recovery_fence_reader(request: Request) -> RecoveryFenceReader | None:
    reader = request.app.state.recovery_fence_reader
    if reader is not None:
        return reader
    try:
        reader = CloudflareRecoveryFenceReader.from_environment()
    except RecoveryFenceConfigurationError:
        return None
    request.app.state.recovery_fence_reader = reader
    return reader


async def _require_recovery_fence_open(request: Request) -> None:
    """Fail closed unless an approved external recovery fence says open."""
    reader = _recovery_fence_reader(request)
    if reader is None:
        raise HTTPException(status_code=503, detail="Service is unavailable")
    try:
        is_open = await reader.is_open()
    except Exception as exc:
        # Content-free: the error class only, never the URL, key or payload.
        logger.warning("recovery_fence_unavailable error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Service is unavailable") from exc
    if is_open is not True:
        logger.warning("recovery_fence_not_open")
        raise HTTPException(status_code=503, detail="Service is unavailable")


async def _require_available_account(
    request: Request, authenticated_request: AuthenticatedRequest
) -> None:
    """Read the authoritative state afresh and fail closed on every error."""
    try:
        state = await _account_lifecycle_reader(request).read_state(
            authenticated_request.principal.subject,
            authenticated_request.access_token,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Account status is unavailable"
        ) from exc

    if state != "available":
        raise HTTPException(status_code=403, detail="Account unavailable")


async def _require_protected_access(
    request: Request, authenticated_request: AuthenticatedRequest
) -> None:
    """Apply the recovery fence first, then the account lifecycle gate."""
    await _require_recovery_fence_open(request)
    await _require_available_account(request, authenticated_request)


async def _with_account_gate(
    request: Request,
    authenticated_request: AuthenticatedRequest,
    operation: Callable[[], Awaitable[_Result]],
) -> _Result:
    """Recheck immediately before one protected repository/API operation."""
    await _require_protected_access(request, authenticated_request)
    return await operation()


class SourceContextDependencyUnavailable(Exception):
    """Server identity dependencies cannot safely issue a source context."""


def _trusted_source_context_issuer(request: Request):
    """Lazily compose the issuer from server-owned identity dependencies.

    This is an internal composition hook, not an HTTP endpoint. Legacy
    principal-only verifiers continue to authenticate existing routes, but are
    not sufficient to issue an expiry-bound source context.
    """
    issuer = request.app.state.trusted_source_context_issuer
    if issuer is not None:
        return issuer

    verifier, directory = _trusted_source_identity_dependencies(request)

    # source_context imports the core source contract. Keep it lazy so the
    # health-only app still starts without loading source integrations.
    from evidence_service.source_context import TrustedSourceContextIssuer

    issuer = TrustedSourceContextIssuer(verifier, directory)
    request.app.state.trusted_source_context_issuer = issuer
    return issuer


def _trusted_source_identity_dependencies(
    request: Request,
) -> tuple[VerifiedTokenVerifier, WorkspaceDirectory]:
    verifier = _token_verifier(request)
    if not isinstance(verifier, VerifiedTokenVerifier):
        raise SourceContextDependencyUnavailable(
            "A verified token with expiry claims is required"
        )
    return verifier, _workspace_directory(request)


def _trusted_source_read_boundary(
    request: Request,
    auth: AuthenticatedRequest,
    workspace_id: UUID,
):
    """Compose a request-scoped async boundary from trusted app dependencies."""
    verifier, directory = _trusted_source_identity_dependencies(request)
    from evidence_service.source_read_boundary import AsyncTrustedSourceReadBoundary

    return AsyncTrustedSourceReadBoundary(
        verifier,
        directory,
        authenticated_request=auth,
        workspace_id=workspace_id,
    )


def _snapshot_object_store(settings: SupabaseSettings):
    """Pick the Storage adapter that matches the configured project origin.

    ``SupabaseSettings`` already guarantees the project URL is HTTPS, or HTTP
    only on loopback, so the scheme alone selects the adapter. Any mismatch
    makes the chosen adapter's own validation fail closed with ``ValueError``.
    """
    from urllib.parse import urlsplit

    from evidence_service.hosted_snapshot_store import (
        HostedSupabaseStorageObjectStore,
    )
    from evidence_service.secure_snapshot_read import (
        LocalSupabaseStorageObjectStore,
    )

    store_class = (
        LocalSupabaseStorageObjectStore
        if urlsplit(settings.supabase_url).scheme == "http"
        else HostedSupabaseStorageObjectStore
    )
    return store_class(
        base_url=settings.supabase_url,
        bucket="source-snapshots",
        publishable_key=settings.publishable_key,
    )


def _workspace_deletion_repository(request: Request, access_token: str):
    factory = request.app.state.workspace_deletion_factory
    if factory is not None:
        return factory(access_token)
    return SupabaseWorkspaceDeletionRepository(_settings_for(request))


def _workspace_usage_repository(request: Request, access_token: str):
    factory = request.app.state.workspace_usage_factory
    if factory is not None:
        return factory(access_token)
    return SupabaseWorkspaceUsageRepository(_settings_for(request))


def _workspace_invitation_repository(request: Request, access_token: str):
    factory = request.app.state.workspace_invitation_factory
    if factory is not None:
        return factory(access_token)
    return SupabaseWorkspaceInvitationRepository(_settings_for(request))


def _source_grant_repository(request: Request, access_token: str):
    factory = request.app.state.source_grant_factory
    if factory is not None:
        return factory(access_token)
    return SupabaseSourceGrantRepository(_settings_for(request))


def _source_staging_repository(request: Request, access_token: str):
    factory = request.app.state.source_staging_factory
    if factory is not None:
        return factory(access_token)
    return SupabaseSourceStagingRepository(_settings_for(request))


def _source_catalog(request: Request, access_token: str):
    """Compose a request-scoped, user-token-bound snapshot catalog.

    Tests may install ``app.state.source_catalog_factory``; production always
    binds the verified bearer token to the PostgREST-backed RLS catalog.
    """
    factory = request.app.state.source_catalog_factory
    if factory is not None:
        return factory(access_token)
    from evidence_service.supabase_source_catalog import SupabaseSnapshotCatalog

    return SupabaseSnapshotCatalog(_settings_for(request), access_token)


def _secure_snapshot_read_path(request: Request, auth: AuthenticatedRequest):
    """Compose the trusted, fail-closed snapshot read path for one request.

    The verified bearer token is bound only here, inside the trusted composition
    root. The catalog and ACL adapters are request-scoped and never accept an
    identity, boundary, catalog, ACL, or store from the caller.
    """
    if request.app.state.source_read_path is not None:
        return request.app.state.source_read_path

    verifier, directory = _trusted_source_identity_dependencies(request)
    settings = _settings_for(request)

    from evidence_service.secure_snapshot_read import (
        SecureSnapshotReadPath,
    )
    from evidence_service.supabase_source_catalog import (
        SupabaseSnapshotCatalog,
        SupabaseSourceAccessPolicy,
    )

    catalog = SupabaseSnapshotCatalog(settings, auth.access_token)
    acl = SupabaseSourceAccessPolicy(settings, auth.access_token)
    store = _snapshot_object_store(settings)
    return SecureSnapshotReadPath(
        verifier=verifier,
        workspace_directory=directory,
        catalog=catalog,
        acl=acl,
        store=store,
        account_lifecycle_check=lambda authenticated_request: _require_protected_access(
            request, authenticated_request
        ),
    )


async def _require_visible_source_link(
    request: Request,
    auth: AuthenticatedRequest,
    workspace_id: UUID,
    link: tuple[UUID, UUID] | None,
) -> None:
    """Accept a provenance link only to a published, user-visible version.

    The catalog query runs under the caller's own token, so RLS visibility is
    part of the check; a missing or unpublished version is indistinguishable
    from one the caller may not see.
    """
    if link is None:
        return
    from evidence_service.supabase_source_catalog import SourceCatalogUnavailable

    source_id, version_id = link
    try:
        manifest = await _with_account_gate(
            request,
            auth,
            lambda: _source_catalog(request, auth.access_token).resolve_version(
                workspace_id, source_id, version_id
            ),
        )
    except SourceCatalogUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="Source catalog is unavailable"
        ) from exc
    if (
        manifest is None
        or manifest.workspace_id != workspace_id
        or manifest.source_id != source_id
        or manifest.version_id != version_id
    ):
        raise HTTPException(
            status_code=422, detail="Source version link is not available"
        )


async def _verify_bearer(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> AuthenticatedRequest:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="A valid bearer access token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    verifier = _token_verifier(request)

    try:
        principal = await verifier.verify(credentials.credentials)
    except AuthenticationRejected as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid bearer access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except AuthenticationUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="Identity verification is unavailable"
        ) from exc

    return AuthenticatedRequest(principal, credentials.credentials)


async def require_authenticated_request(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedRequest:
    authenticated_request = await _verify_bearer(request, credentials)
    await _require_mfa_assurance(request, authenticated_request)
    await _require_protected_access(request, authenticated_request)
    return authenticated_request


async def require_control_request(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedRequest:
    """Skip lifecycle gates while retaining verified identity and MFA checks.

    These two control operations must remain callable while a revocation is
    pending, otherwise the owner cannot observe status or replay an accepted
    request. The repository query is still requester-scoped and RLS-bound.
    """
    authenticated_request = await _verify_bearer(request, credentials)
    await _require_mfa_assurance(request, authenticated_request)
    return authenticated_request


async def require_personal_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> tuple[UUID, str]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Personal API key is required")
    parsed = parse_personal_api_key(credentials.credentials)
    if parsed is None:
        raise HTTPException(status_code=401, detail="Personal API key is invalid")
    return parsed


def _workspace_directory(request: Request) -> WorkspaceDirectory:
    directory = request.app.state.workspace_directory
    if directory is None:
        directory = SupabaseWorkspaceRepository(_settings_for(request))
        request.app.state.workspace_directory = directory
    return directory


def _organization_repository(request: Request) -> SupabaseOrganizationRepository:
    repository = request.app.state.organization_repository
    if repository is None:
        repository = SupabaseOrganizationRepository(_settings_for(request))
        request.app.state.organization_repository = repository
    return repository


def _raise_organization_error(exc: Exception) -> None:
    if isinstance(exc, OrganizationAccessDenied):
        raise HTTPException(status_code=403, detail="Organization admin required")
    if isinstance(exc, OrganizationMemberNotFound):
        raise HTTPException(status_code=404, detail="Organization member not found")
    if isinstance(exc, OrganizationValidationError):
        raise HTTPException(status_code=422, detail="Invalid organization value")
    raise HTTPException(status_code=503, detail="Organization service is unavailable")


def _evidence_repository(request: Request) -> EvidenceRepository:
    repository = request.app.state.evidence_repository
    if repository is None:
        repository = SupabaseEvidenceRepository(_settings_for(request))
        request.app.state.evidence_repository = repository
    return repository


def _relationship_repository(request: Request) -> RelationshipRepository:
    repository = request.app.state.relationship_repository
    if repository is None:
        repository = SupabaseRelationshipRepository(_settings_for(request))
        request.app.state.relationship_repository = repository
    return repository


def _task_run_ledger_repository(request: Request) -> SupabaseTaskRunLedgerRepository:
    repository = request.app.state.task_run_ledger_repository
    if repository is None:
        repository = SupabaseTaskRunLedgerRepository(_settings_for(request))
        request.app.state.task_run_ledger_repository = repository
    return repository





def _connector_connection_response(connection) -> ConnectorConnectionResponse:
    return ConnectorConnectionResponse(
        connection_id=connection.connection_id,
        workspace_id=connection.workspace_id,
        provider=connection.provider,
        account_email=connection.account_email,
        scopes=list(connection.scopes),
        status=connection.status,
        last_sync_at=connection.last_sync_at,
        last_error_category=connection.last_error_category,
    )

def _connector_repository(request: Request) -> ConnectorRepository:
    repository = request.app.state.connector_repository
    if repository is None:
        repository = ConnectorRepository(_settings_for(request))
        request.app.state.connector_repository = repository
    return repository


def _connector_vault(request: Request) -> TokenVault:
    vault = request.app.state.connector_vault
    if vault is None:
        vault = TokenVault.from_environment()
        request.app.state.connector_vault = vault
    return vault




def _github_app_sync_service(request: Request) -> GitHubAppSyncService:
    service = request.app.state.github_sync_service
    if service is None:
        service = GitHubAppSyncService(
            _connector_repository(request),
            app_id=os.environ.get("GITHUB_APP_ID") or os.environ.get("GIT_APP_ID", ""),
            private_key=(
                os.environ.get("GITHUB_APP_PRIVATE_KEY")
                or os.environ.get("GIT_APP_PRIVATE_KEY", "")
            ),
            client_id=(
                os.environ.get("GITHUB_APP_CLIENT_ID")
                or os.environ.get("GIT_APP_CLIENT_ID", "")
            ),
            client_secret=(
                os.environ.get("GITHUB_APP_CLIENT_SECRET")
                or os.environ.get("GIT_APP_CLIENT_SECRET", "")
            ),
        )
        request.app.state.github_sync_service = service
    return service

def _notion_sync_service(request: Request) -> NotionSyncService:
    service = request.app.state.notion_sync_service
    if service is None:
        service = NotionSyncService(
            _connector_repository(request),
            _connector_vault(request),
        )
        request.app.state.notion_sync_service = service
    return service

def _google_drive_sync_service(request: Request) -> GoogleDriveSyncService:
    service = request.app.state.connector_sync_service
    if service is None:
        service = GoogleDriveSyncService(
            _connector_repository(request),
            _connector_vault(request),
            client_id=os.environ.get("GOOGLE_OAUTH_CLIENT_ID", ""),
            client_secret=os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
        )
        request.app.state.connector_sync_service = service
    return service

def _export_repository(request: Request) -> SupabaseExportRepository:
    repository = request.app.state.export_repository
    if repository is None:
        repository = SupabaseExportRepository(_settings_for(request))
        request.app.state.export_repository = repository
    return repository

def _personal_api_key_repository(request: Request) -> SupabasePersonalApiKeyRepository:
    repository = request.app.state.personal_api_key_repository
    if repository is None:
        repository = SupabasePersonalApiKeyRepository(_settings_for(request))
        request.app.state.personal_api_key_repository = repository
    return repository


def _usage_caps_repository(request: Request) -> SupabaseUsageCapsRepository:
    repository = request.app.state.usage_caps_repository
    if repository is None:
        repository = SupabaseUsageCapsRepository(_settings_for(request))
        request.app.state.usage_caps_repository = repository
    return repository


USER_RATE_LIMITED_CODE = "user_rate_limited"


def _user_rate_limited_detail(decision: UsageDecision) -> dict:
    """Documented 429 body for the per-user retrieval caps."""
    return {
        "code": USER_RATE_LIMITED_CODE,
        "window": decision.window,
        "retry_after_seconds": decision.retry_after_seconds,
        "message": f"Per-user retrieval limit reached for this {decision.window}.",
    }


def _raise_if_user_rate_limited(decision: UsageDecision) -> None:
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail=_user_rate_limited_detail(decision),
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )


async def _consume_session_usage(
    request: Request, access_token: str, kind: str
) -> UsageDecision | None:
    """None when caps are disabled; raises UsageCapsUnavailable on failure."""
    settings: UsageCapsSettings = request.app.state.usage_caps_settings
    if not settings.enabled:
        return None
    per_minute, per_day = settings.limits(kind)  # type: ignore[arg-type]
    return await _usage_caps_repository(request).consume_for_user(
        access_token, kind, per_minute=per_minute, per_day=per_day  # type: ignore[arg-type]
    )


async def _consume_api_key_usage(
    request: Request, *, key_id: UUID, key_hash: str, workspace_id: UUID, kind: str
) -> UsageDecision | None:
    """None when caps are disabled; raises UsageCapsUnavailable/AccessDenied."""
    settings: UsageCapsSettings = request.app.state.usage_caps_settings
    if not settings.enabled:
        return None
    per_minute, per_day = settings.limits(kind)  # type: ignore[arg-type]
    return await _usage_caps_repository(request).consume_for_api_key(
        key_id=key_id,
        key_hash=key_hash,
        workspace_id=workspace_id,
        kind=kind,  # type: ignore[arg-type]
        per_minute=per_minute,
        per_day=per_day,
    )


def _jev_budget_for(consume: Callable[[], Awaitable[UsageDecision | None]]):
    """Adapt a usage consumer to the ranking hook: True = JEV may run."""

    async def check() -> bool:
        decision = await consume()
        return decision is None or decision.allowed

    return check


def _deletion_repository(request: Request) -> DeletionRepository:
    repository = request.app.state.deletion_repository
    if repository is None:
        repository = SupabaseDeletionRepository(_settings_for(request))
        request.app.state.deletion_repository = repository
    return repository


def _deletion_response(request_row: DeletionRequest) -> DeletionRequestResponse:
    return DeletionRequestResponse(
        request_id=request_row.request_id,
        requester_user_id=request_row.requester_user_id,
        scope_type="account",
        status=request_row.status,
        created_at=request_row.created_at,
        accepted_at=request_row.accepted_at,
    )


async def _resolve_workspace_or_404(
    request: Request,
    auth: AuthenticatedRequest,
    workspace_id: UUID,
) -> WorkspaceContext:
    try:
        return await _with_account_gate(
            request,
            auth,
            lambda: resolve_workspace_context(
                _workspace_directory(request),
                auth.principal,
                auth.access_token,
                workspace_id,
            ),
        )
    except WorkspaceAccessDenied as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    except WorkspaceDataUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="Workspace directory is unavailable"
        ) from exc


def _evidence_access_error(exc: EvidenceAccessDenied) -> HTTPException:
    return HTTPException(status_code=404, detail="Evidence record not found")


def _evidence_data_error(exc: EvidenceDataUnavailable) -> HTTPException:
    return HTTPException(
        status_code=503, detail="Evidence storage is unavailable"
    )


def _relationship_data_error(exc: RelationshipDataUnavailable) -> HTTPException:
    return HTTPException(
        status_code=503, detail="Relationship storage is unavailable"
    )


def _source_read_error(exc) -> HTTPException:
    from evidence_layer.source_access import SourceReadDenialReason

    reason = exc.denial.reason
    if reason in {
        SourceReadDenialReason.UNKNOWN_SOURCE,
        SourceReadDenialReason.VERSION_MISMATCH,
    }:
        return HTTPException(status_code=404, detail="Source version not found")
    if reason in {
        SourceReadDenialReason.STORAGE_FAILURE,
        SourceReadDenialReason.INVALID_SOURCE_DATA,
    }:
        return HTTPException(
            status_code=503, detail="Source storage is unavailable"
        )
    return HTTPException(status_code=403, detail="Source read is not authorized")


async def _jev_budget_refusal(
    jev_budget: Callable[[], Awaitable[bool]] | None,
) -> str | None:
    """Fallback reason when the per-user JEV budget refuses, else None.

    A refused or unverifiable budget keeps the lexical ranking whole: JEV and
    lexical scores are never mixed in one result.
    """
    if jev_budget is None:
        return None
    try:
        allowed = await jev_budget()
    except Exception:
        logger.warning("jev_budget_unavailable")
        return "jev_budget_unavailable"
    return None if allowed else "jev_budget_exceeded"


async def _retrieval_response(
    page: PersonalApiKeyPage,
    workspace_id: UUID,
    payload: PersonalApiKeyRetrieveRequest | WorkspaceTaskContextRequest,
    *,
    seed_record_ids: set[UUID] | None = None,
    related_record_scores: dict[UUID, float] | None = None,
    relationships: list[EvidenceRelationship] | None = None,
    jev_enabled: bool = False,
    jev_scorer: JevRequestScorer | None = None,
    jev_principal_id: str | None = None,
    jev_budget: Callable[[], Awaitable[bool]] | None = None,
) -> PersonalApiKeyRetrieveResponse:
    ranking_started = perf_counter()
    records = [item for item in page.items if hasattr(item, "content")]
    query = (
        payload.query
        if isinstance(payload, PersonalApiKeyRetrieveRequest)
        else payload.retrieval_query()
    )
    result: RetrievalSelection = select_evidence(
        records,
        query=query,
        top_k=payload.top_k,
        max_tokens=payload.max_tokens,
        max_chars=payload.max_chars,
        seed_record_ids=seed_record_ids,
        related_record_scores=related_record_scores,
    )
    scoring_provider = "lexical_stopword_filtered"
    scoring_fallback_reason: str | None = None
    if payload.use_jev:
        if not jev_enabled:
            scoring_fallback_reason = "jev_disabled"
        elif jev_principal_id is None:
            scoring_fallback_reason = "jev_unavailable"
        else:
            candidate_selection = select_evidence(
                records,
                query=query,
                top_k=50,
                max_tokens=payload.max_tokens,
                max_chars=payload.max_chars,
                seed_record_ids=seed_record_ids,
                related_record_scores=related_record_scores,
            )
            budget_refusal = (
                await _jev_budget_refusal(jev_budget)
                if candidate_selection.selected
                else None
            )
            if budget_refusal is not None:
                scoring_fallback_reason = budget_refusal
            elif candidate_selection.selected:
                try:
                    from evidence_layer.scoring.jev_request import (
                        JevRequestCandidate,
                        JevRequestScorer,
                        JevResponseError,
                        JevScorerError,
                        _issue_jev_request_transfer_permit,
                    )
                except ImportError:
                    scoring_fallback_reason = "dependency_missing"
                else:
                    candidates = [
                        JevRequestCandidate(
                            candidate_id=item.chunk_id,
                            source_id=str(item.record.source_id or workspace_id),
                            text=item.content,
                        )
                        for item in candidate_selection.selected
                    ]
                    try:
                        permit = _issue_jev_request_transfer_permit(
                            request_id=str(uuid4()),
                            principal_id=jev_principal_id,
                            tenant_id=str(workspace_id),
                            query=query,
                            candidates=candidates,
                            server_enabled=jev_enabled,
                            permitted_by_request=payload.use_jev,
                        )
                        scorer = jev_scorer or JevRequestScorer()
                        scores = await run_in_threadpool(
                            scorer.score_candidates,
                            query,
                            candidates,
                            transfer_permit=permit,
                        )
                        if len(scores) != len(candidates):
                            raise JevScorerError(
                                "JEV returned an incomplete score batch."
                            )
                        if any(
                            isinstance(score, bool)
                            or not isinstance(score, (int, float))
                            or not math.isfinite(score)
                            or not 0 <= score <= 1
                            for score in scores
                        ):
                            raise JevResponseError(
                                "JEV returned an invalid score batch."
                            )
                    except JevScorerError as exc:
                        scoring_fallback_reason = exc.category
                    except Exception:
                        scoring_fallback_reason = "provider_failure"
                    else:
                        score_overrides = {
                            item.candidate_id: score
                            for item, score in zip(candidates, scores, strict=True)
                        }
                        result = select_evidence(
                            records,
                            query=query,
                            top_k=payload.top_k,
                            max_tokens=payload.max_tokens,
                            max_chars=payload.max_chars,
                            seed_record_ids=(
                                seed_record_ids if related_record_scores else None
                            ),
                            related_record_scores=related_record_scores,
                            score_overrides=score_overrides,
                        )
                        scoring_provider = "jev"
    selected_ids = {item.record.id for item in result.selected}
    selected_relationships = [
        TaskContextRelationship(
            id=relationship.id,
            evidence_id_a=relationship.evidence_id_a,
            evidence_id_b=relationship.evidence_id_b,
            relation_type=relationship.relation_type,
            rationale=relationship.rationale,
            provenance_note=relationship.provenance_note,
        )
        for relationship in relationships or []
        if relationship.workspace_id == workspace_id
        and relationship.evidence_id_a in selected_ids
        and relationship.evidence_id_b in selected_ids
    ]
    _log_retrieval_timing(
        "retrieval_ranked",
        records=len(records),
        candidate_chunks=result.chunk_count,
        selected=len(result.selected),
        scoring_provider=scoring_provider,
        rank_ms=round((perf_counter() - ranking_started) * 1000, 1),
    )
    return PersonalApiKeyRetrieveResponse(
        status=result.status,
        query=query,
        workspace_id=workspace_id,
        top_k=payload.top_k,
        max_tokens=payload.max_tokens,
        max_chars=payload.max_chars,
        candidate_count=result.candidate_count,
        chunk_count=result.chunk_count,
        matched_chunk_count=result.matched_chunk_count,
        selected_count=len(result.selected),
        used_tokens=result.used_tokens,
        used_chars=result.used_chars,
        token_estimator=result.token_estimator,
        scoring_provider=scoring_provider,
        scoring_fallback_reason=scoring_fallback_reason,
        chunking=result.chunking,
        evidence=[
            PersonalApiKeyRetrievedItem(
                evidence_id=item.record.id,
                content=item.content,
                source_label=item.record.source_label,
                source_id=item.record.source_id,
                source_version_id=item.record.source_version_id,
                score=item.score,
                token_count=item.token_count,
                char_count=item.char_count,
                chunk_id=item.chunk_id,
                chunk_index=item.chunk_index,
                chunk_start=item.chunk_start,
                chunk_end=item.chunk_end,
                path=item.path,
                title=item.title,
            )
            for item in result.selected
        ],
        relationships=selected_relationships,
    )


def _relationship_expansion(
    records: list[EvidenceRecord],
    relationships: list[EvidenceRelationship],
    workspace_id: UUID,
    query: str,
    *,
    top_k: int,
    max_tokens: int,
    max_chars: int,
) -> tuple[set[UUID], dict[UUID, float]]:
    seed = select_evidence(
        records,
        query=query,
        top_k=max(1, top_k - 1),
        max_tokens=max_tokens,
        max_chars=max_chars,
    )
    seed_scores: dict[UUID, float] = {}
    for item in seed.selected:
        seed_scores[item.record.id] = max(seed_scores.get(item.record.id, 0), item.score)

    record_ids = {record.id for record in records}
    related_candidates: dict[UUID, float] = {}
    if seed_scores and top_k > 1:
        for relationship in relationships:
            if relationship.workspace_id != workspace_id:
                continue
            if relationship.evidence_id_a in seed_scores:
                related_id = relationship.evidence_id_b
                anchor_id = relationship.evidence_id_a
            elif relationship.evidence_id_b in seed_scores:
                related_id = relationship.evidence_id_a
                anchor_id = relationship.evidence_id_b
            else:
                continue
            if related_id not in record_ids or related_id in seed_scores:
                continue
            related_candidates[related_id] = max(
                related_candidates.get(related_id, 0),
                seed_scores[anchor_id] * 0.8,
            )
    related_ids = sorted(
        related_candidates,
        key=lambda record_id: (-related_candidates[record_id], str(record_id)),
    )[: min(3, top_k - 1)]
    return set(seed_scores), {
        record_id: related_candidates[record_id] for record_id in related_ids
    }


async def _workspace_task_context_response(
    records: list[EvidenceRecord],
    relationships: list[EvidenceRelationship],
    workspace_id: UUID,
    payload: WorkspaceTaskContextRequest,
    *,
    jev_enabled: bool = False,
    jev_scorer: JevRequestScorer | None = None,
    jev_principal_id: str | None = None,
    jev_budget: Callable[[], Awaitable[bool]] | None = None,
) -> WorkspaceTaskContextResponse:
    query = payload.retrieval_query()
    seed_ids, related_record_scores = _relationship_expansion(
        records,
        relationships,
        workspace_id,
        query=query,
        top_k=payload.top_k,
        max_tokens=payload.max_tokens,
        max_chars=payload.max_chars,
    )

    retrieval = await _retrieval_response(
        PersonalApiKeyPage(items=records, next_cursor=None),
        workspace_id,
        payload,
        seed_record_ids=seed_ids,
        related_record_scores=related_record_scores,
        relationships=relationships,
        jev_enabled=jev_enabled,
        jev_scorer=jev_scorer,
        jev_principal_id=jev_principal_id,
        jev_budget=jev_budget,
    )
    return WorkspaceTaskContextResponse(
        task=payload.task,
        expected_outcome=payload.expected_outcome,
        context_requirements=payload.context_requirements,
        retrieval=retrieval,
        relationships=retrieval.relationships,
    )


class _AppStateRequest:
    def __init__(self, application: FastAPI) -> None:
        self.app = application


def _remote_mcp_error(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


class _HostedRemoteMcpBackend:
    def __init__(self, application: FastAPI) -> None:
        self._application = application
        self._state_request = _AppStateRequest(application)

    async def _guard(self) -> dict | None:
        if not self._application.state.retrieval_enabled:
            return _remote_mcp_error("unavailable", "Retrieval API is not enabled")
        try:
            await _require_recovery_fence_open(self._state_request)
        except HTTPException:
            return _remote_mcp_error("unavailable", "Service is unavailable")
        return None

    async def retrieve(
        self,
        principal: RemoteMcpPrincipal,
        *,
        query: str,
        top_k: int,
        max_tokens: int,
        max_chars: int,
        use_jev: bool,
    ) -> dict:
        blocked = await self._guard()
        if blocked is not None:
            return blocked
        try:
            payload = PersonalApiKeyRetrieveRequest(
                query=query,
                top_k=top_k,
                max_tokens=max_tokens,
                max_chars=max_chars,
                use_jev=use_jev,
            )
        except ValueError:
            return _remote_mcp_error("invalid_argument", "Invalid retrieve arguments")
        try:
            decision = await _consume_api_key_usage(
                self._state_request,  # type: ignore[arg-type]
                key_id=principal.key_id,
                key_hash=principal.key_hash,
                workspace_id=principal.workspace_id,
                kind="retrieval",
            )
        except UsageCapsAccessDenied:
            return _remote_mcp_error("unauthorized", "Personal API key is invalid or revoked")
        except UsageCapsUnavailable:
            return _remote_mcp_error("unavailable", "Usage limits are unavailable")
        if decision is not None and not decision.allowed:
            detail = _user_rate_limited_detail(decision)
            return {"error": detail}
        try:
            page = await _read_retrieval_candidates(
                _personal_api_key_repository(self._state_request),
                key_id=principal.key_id,
                key_hash=principal.key_hash,
                workspace_id=principal.workspace_id,
                limit=payload.candidate_limit,
            )
        except PersonalApiKeyAccessDenied:
            return _remote_mcp_error("unauthorized", "Personal API key is invalid or revoked")
        except PersonalApiKeyRateLimited:
            return _remote_mcp_error("rate_limited", "Personal API key rate limit exceeded")
        except PersonalApiKeyDataUnavailable:
            return _remote_mcp_error("unavailable", "Personal API key service is unavailable")
        except Exception:
            return _remote_mcp_error("internal_error", "retrieve_evidence failed")
        relationship_page = PersonalApiKeyPage(items=[], next_cursor=None)
        if payload.top_k > 1:
            try:
                relationship_page = await _personal_api_key_repository(
                    self._state_request
                ).read_relationships(
                    key_id=principal.key_id,
                    key_hash=principal.key_hash,
                    workspace_id=principal.workspace_id,
                    limit=100,
                    cursor_created_at=None,
                    cursor_id=None,
                )
            except PersonalApiKeyAccessDenied:
                return _remote_mcp_error("unauthorized", "Personal API key is invalid or revoked")
            except PersonalApiKeyRateLimited:
                return _remote_mcp_error("rate_limited", "Personal API key rate limit exceeded")
            except PersonalApiKeyDataUnavailable:
                return _remote_mcp_error("unavailable", "Personal API key service is unavailable")
            except Exception:
                return _remote_mcp_error("internal_error", "retrieve_evidence failed")
        try:
            records = _evidence_page_items(page)
            relationships = _relationship_page_items(relationship_page)
            seed_ids, related_scores = _relationship_expansion(
                records,
                relationships,
                principal.workspace_id,
                payload.query,
                top_k=payload.top_k,
                max_tokens=payload.max_tokens,
                max_chars=payload.max_chars,
            )
            return (await _retrieval_response(
                page,
                principal.workspace_id,
                payload,
                seed_record_ids=seed_ids,
                related_record_scores=related_scores,
                relationships=relationships,
                jev_enabled=self._application.state.jev_enabled,
                jev_scorer=self._application.state.jev_scorer,
                jev_principal_id=str(principal.key_id),
                jev_budget=_jev_budget_for(
                    lambda: _consume_api_key_usage(
                        self._state_request,  # type: ignore[arg-type]
                        key_id=principal.key_id,
                        key_hash=principal.key_hash,
                        workspace_id=principal.workspace_id,
                        kind="jev",
                    )
                ),
            )).model_dump(mode="json")
        except PersonalApiKeyDataUnavailable:
            return _remote_mcp_error("unavailable", "Personal API key service is unavailable")
        except Exception:
            return _remote_mcp_error("internal_error", "retrieve_evidence failed")

    async def get_document(self, principal: RemoteMcpPrincipal, evidence_id: str) -> dict:
        blocked = await self._guard()
        if blocked is not None:
            return blocked
        try:
            document_id = UUID(evidence_id)
        except (TypeError, ValueError):
            return _remote_mcp_error("invalid_argument", "evidence_id must be a UUID")
        try:
            record = await _personal_api_key_repository(
                self._state_request
            ).read_evidence_document(
                key_id=principal.key_id,
                key_hash=principal.key_hash,
                workspace_id=principal.workspace_id,
                evidence_id=document_id,
            )
        except PersonalApiKeyAccessDenied:
            return _remote_mcp_error("unauthorized", "Personal API key is invalid or revoked")
        except PersonalApiKeyRateLimited:
            return _remote_mcp_error("rate_limited", "Personal API key rate limit exceeded")
        except PersonalApiKeyDataUnavailable:
            return _remote_mcp_error("unavailable", "Personal API key service is unavailable")
        except Exception:
            return _remote_mcp_error("internal_error", "get_document failed")
        if record is None:
            return _remote_mcp_error("not_found", "Evidence document not found")
        return EvidenceResponse.model_validate(record).model_dump(mode="json")


def create_app(
    settings: SupabaseSettings | None = None,
    *,
    token_verifier: TokenVerifier | None = None,
    mfa_factor_verifier: MFAFactorVerifier | None = None,
    account_lifecycle_reader: AccountLifecycleReader | None = None,
    recovery_fence_reader: RecoveryFenceReader | None = None,
    workspace_directory: WorkspaceDirectory | None = None,
    evidence_repository: EvidenceRepository | None = None,
    relationship_repository: RelationshipRepository | None = None,
    personal_api_key_repository: SupabasePersonalApiKeyRepository | None = None,
    usage_caps_settings: UsageCapsSettings | None = None,
    usage_caps_repository: SupabaseUsageCapsRepository | None = None,
    retrieval_enabled: bool | None = None,
    jev_enabled: bool | None = None,
    jev_scorer: JevRequestScorer | None = None,
    deletion_repository: DeletionRepository | None = None,
    export_repository: SupabaseExportRepository | None = None,
    connector_repository: ConnectorRepository | None = None,
    connector_sync_service: GoogleDriveSyncService | None = None,
    notion_sync_service: NotionSyncService | None = None,
    github_sync_service: GitHubAppSyncService | None = None,
    connector_vault: TokenVault | None = None,
    organization_repository=None,
    source_read_path=None,
    source_catalog_factory=None,
    source_staging_factory=None,
    source_grant_factory=None,
    workspace_invitation_factory=None,
    workspace_usage_factory=None,
    workspace_deletion_factory=None,
    remote_mcp_enabled: bool | None = None,
    task_run_ledger_repository=None,
    task_ledger_enabled: bool | None = None,
) -> FastAPI:
    """Create the service app; optional integrations are initialized on demand."""
    application = FastAPI(
        title="Evidence Layer identity service",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.supabase_settings = settings
    application.state.token_verifier = token_verifier
    application.state.mfa_factor_verifier = mfa_factor_verifier
    application.state.account_lifecycle_reader = account_lifecycle_reader
    application.state.recovery_fence_reader = recovery_fence_reader
    application.state.workspace_directory = workspace_directory
    application.state.evidence_repository = evidence_repository
    application.state.relationship_repository = relationship_repository
    application.state.personal_api_key_repository = personal_api_key_repository
    application.state.usage_caps_settings = (
        usage_caps_settings
        if usage_caps_settings is not None
        else UsageCapsSettings.from_environment()
    )
    application.state.usage_caps_repository = usage_caps_repository
    application.state.retrieval_enabled = (
        retrieval_enabled
        if retrieval_enabled is not None
        else os.environ.get("EVIDENCE_RETRIEVE_API_ENABLED") == "true"
    )
    application.state.jev_enabled = (
        jev_enabled
        if jev_enabled is not None
        else os.environ.get("EVIDENCE_JEV_ENABLED") == "true"
    )
    application.state.jev_scorer = jev_scorer
    application.state.deletion_repository = deletion_repository
    application.state.export_repository = export_repository
    application.state.connector_repository = connector_repository
    application.state.connector_sync_service = connector_sync_service
    application.state.notion_sync_service = notion_sync_service
    application.state.github_sync_service = github_sync_service
    application.state.connector_vault = connector_vault
    application.state.organization_repository = organization_repository
    application.state.trusted_source_context_issuer = None
    application.state.source_read_path = source_read_path
    application.state.source_catalog_factory = source_catalog_factory
    application.state.source_staging_factory = source_staging_factory
    application.state.source_grant_factory = source_grant_factory
    application.state.workspace_invitation_factory = workspace_invitation_factory
    application.state.workspace_usage_factory = workspace_usage_factory
    application.state.workspace_deletion_factory = workspace_deletion_factory
    application.state.remote_mcp_enabled = (
        remote_mcp_enabled
        if remote_mcp_enabled is not None
        else os.environ.get("EVIDENCE_REMOTE_MCP_ENABLED") == "true"
    )
    application.state.task_run_ledger_repository = task_run_ledger_repository
    application.state.task_ledger_enabled = (
        task_ledger_enabled
        if task_ledger_enabled is not None
        else os.environ.get("EVIDENCE_TASK_LEDGER_ENABLED") == "true"
    )

    @application.api_route(
        "/healthz", methods=["GET"], status_code=200, include_in_schema=False
    )
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @application.post(
        "/v1/deletion-requests",
        response_model=DeletionRequestResponse,
        status_code=201,
    )
    async def create_deletion_request(
        payload: DeletionRequestCreateRequest,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> DeletionRequestResponse:
        try:
            request_row = await _with_account_gate(
                request,
                auth,
                lambda: _deletion_repository(request).create_request(
                    auth.access_token,
                    principal_id=auth.principal.subject,
                    idempotency_key=payload.idempotency_key,
                ),
            )
        except DeletionRequestUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Deletion request service is unavailable"
            ) from exc
        return _deletion_response(request_row)

    @application.post(
        "/v1/deletion-requests/{request_id}/accept",
        response_model=DeletionRequestResponse,
    )
    async def accept_deletion_request(
        request_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_control_request),
    ) -> DeletionRequestResponse:
        try:
            # Acceptance is the control operation that creates the denial
            # marker; replay remains available to return the same outcome.
            request_row = await _deletion_repository(request).accept_request(
                auth.access_token, request_id=request_id
            )
        except DeletionRequestUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Deletion request service is unavailable"
            ) from exc
        return _deletion_response(request_row)

    @application.get(
        "/v1/deletion-requests/{request_id}",
        response_model=DeletionRequestResponse,
    )
    async def get_deletion_request(
        request_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_control_request),
    ) -> DeletionRequestResponse:
        # Status is the explicitly permitted control-only view for a blocked
        # account; the repository query remains requester- and RLS-scoped.
        try:
            request_row = await _deletion_repository(request).get_request(
                auth.access_token,
                principal_id=auth.principal.subject,
                request_id=request_id,
            )
        except DeletionRequestUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Deletion request service is unavailable"
            ) from exc
        if request_row is None:
            raise HTTPException(status_code=404, detail="Deletion request not found")
        return _deletion_response(request_row)

    @application.get(
        "/v1/me/deletion-request",
        response_model=DeletionRequestResponse,
    )
    async def get_current_deletion_request(
        request: Request,
        auth: AuthenticatedRequest = Depends(require_control_request),
    ) -> DeletionRequestResponse:
        try:
            request_row = await _deletion_repository(request).get_current_request(
                auth.access_token,
                principal_id=auth.principal.subject,
            )
        except DeletionRequestUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Deletion request service is unavailable"
            ) from exc
        if request_row is None:
            raise HTTPException(status_code=404, detail="Deletion request not found")
        return _deletion_response(request_row)

    @application.post(
        "/v1/me/data-deletions",
        response_model=PersonalDeletionResponse,
        status_code=202,
    )
    async def delete_personal_data(
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> PersonalDeletionResponse:
        try:
            result = await _with_account_gate(
                request,
                auth,
                lambda: _deletion_repository(request).delete_personal_workspace_data(
                    auth.access_token
                ),
            )
        except DeletionRequestUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Deletion service is unavailable"
            ) from exc
        return PersonalDeletionResponse(**result)

    @application.delete(
        "/v1/me/personal-workspace",
        response_model=PersonalDeletionResponse,
        status_code=202,
    )
    async def delete_personal_workspace_route(
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> PersonalDeletionResponse:
        try:
            result = await _with_account_gate(
                request,
                auth,
                lambda: _deletion_repository(request).delete_personal_workspace(
                    auth.access_token
                ),
            )
        except DeletionRequestUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Deletion service is unavailable"
            ) from exc
        return PersonalDeletionResponse(**result)

    @application.get("/v1/me", response_model=MeResponse)
    async def me(
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> MeResponse:
        async def load_me():
            directory = _workspace_directory(request)
            memberships = await directory.list_memberships(
                auth.principal, auth.access_token
            )
            account_getter = getattr(directory, "get_account", None)
            account = (
                await account_getter(auth.principal, auth.access_token)
                if account_getter is not None
                else AccountProfile(account_type="individual", organization_name=None)
            )
            return memberships, account

        try:
            memberships, account = await _with_account_gate(request, auth, load_me)
        except WorkspaceDataUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Workspace directory is unavailable"
            ) from exc

        return MeResponse(
            user_id=auth.principal.subject,
            issuer=auth.principal.issuer,
            account_type=account.account_type,
            organization_name=account.organization_name,
            can_manage_workspace=account.account_type == "organization",
            workspaces=[
                WorkspaceSummary(
                    id=membership.workspace_id,
                    name=membership.workspace_name,
                    role=membership.role,
                    workspace_type=membership.workspace_type,
                )
                for membership in memberships
            ],
        )


    @application.post(
        "/v1/me/exports",
        response_model=AccountExportResponse,
        status_code=201,
    )
    async def create_account_export(
        payload: AccountExportCreateRequest,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> AccountExportResponse:
        try:
            job = await _with_account_gate(
                request,
                auth,
                lambda: _export_repository(request).create_export(
                    auth.access_token,
                    principal_id=auth.principal.subject,
                    idempotency_key=payload.idempotency_key,
                ),
            )
        except ExportTooLarge as exc:
            raise HTTPException(
                status_code=413, detail="Export exceeds the narrowed size limit"
            ) from exc
        except ExportAccessDenied as exc:
            raise HTTPException(status_code=403, detail="Export is not authorized") from exc
        except ExportDataUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Export service is unavailable"
            ) from exc
        return AccountExportResponse(
            export_id=job.export_id,
            status=job.status,
            ready_at=job.ready_at,
            expires_at=job.expires_at,
            reused=job.reused,
            grant_token=job.grant_token,
            download_path=f"/v1/me/exports/{job.export_id}/download",
        )

    @application.get("/v1/me/exports/{export_id}/download")
    async def download_account_export(
        export_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
        grant: str | None = Header(default=None, alias="X-Export-Grant"),
    ) -> Response:
        if not grant:
            raise HTTPException(status_code=404, detail="Export not found")
        try:
            download = await _with_account_gate(
                request,
                auth,
                lambda: _export_repository(request).redeem_export(
                    auth.access_token,
                    export_id=export_id,
                    grant_token=grant,
                ),
            )
        except ExportAccessDenied as exc:
            raise HTTPException(status_code=403, detail="Export is not authorized") from exc
        except ExportDataUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Export download is unavailable"
            ) from exc
        if download is None:
            raise HTTPException(status_code=404, detail="Export not found")
        # Recheck immediately before releasing the buffered artifact.
        await _require_protected_access(request, auth)
        digest = base64.b64encode(bytes.fromhex(download.sha256)).decode("ascii")
        return Response(
            content=download.content,
            media_type="application/zip",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": (
                    f'attachment; filename="evidence-layer-export-{export_id}.zip"'
                ),
                "Content-Digest": f"sha-256=:{digest}:",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @application.post(
        "/v1/me/exports/{export_id}/revoke",
        response_model=AccountExportRevokeResponse,
    )
    async def revoke_account_export(
        export_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> AccountExportRevokeResponse:
        try:
            revoked = await _with_account_gate(
                request,
                auth,
                lambda: _export_repository(request).revoke_export(
                    auth.access_token, export_id=export_id
                ),
            )
        except ExportAccessDenied as exc:
            raise HTTPException(status_code=403, detail="Export is not authorized") from exc
        except ExportDataUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Export service is unavailable"
            ) from exc
        return AccountExportRevokeResponse(export_id=export_id, revoked=revoked)


    @application.post(
        "/v1/connectors/google_drive/connection",
        response_model=ConnectorConnectionResponse,
        status_code=201,
    )
    async def connect_google_drive(
        payload: ConnectorConnectionRequest,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> ConnectorConnectionResponse:
        try:
            vault = _connector_vault(request)
            encrypted_access = vault.encrypt(payload.access_token)
            if not encrypted_access:
                raise TokenVaultUnavailable
            repository = _connector_repository(request)
            existing = await _with_account_gate(
                request,
                auth,
                lambda: repository.get_connection_for_provider(
                    auth.access_token, "google_drive"
                ),
            )
            encrypted_refresh = (
                vault.encrypt(payload.refresh_token)
                if payload.refresh_token
                else (existing.refresh_token_ciphertext if existing else None)
            )
            connection = await _with_account_gate(
                request,
                auth,
                lambda: repository.upsert_connection(
                    auth.access_token,
                    user_id=auth.principal.subject,
                    provider="google_drive",
                    account_email=payload.account_email,
                    scopes=list(payload.scopes),
                    access_token_ciphertext=encrypted_access,
                    refresh_token_ciphertext=encrypted_refresh,
                    access_token_expires_at=payload.expires_at,
                ),
            )
        except TokenVaultUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Connector token vault is unavailable"
            ) from exc
        except ConnectorDenied as exc:
            raise HTTPException(status_code=403, detail="Connector is not authorized") from exc
        except ConnectorUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Connector service is unavailable"
            ) from exc
        return _connector_connection_response(connection)



    @application.post(
        "/v1/connectors/github/connection",
        response_model=ConnectorConnectionResponse,
        status_code=201,
    )
    async def connect_github(
        payload: ConnectorGitHubConnectionRequest,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> ConnectorConnectionResponse:
        try:
            connection = await _with_account_gate(
                request,
                auth,
                lambda: _github_app_sync_service(request).connect(
                    auth.access_token,
                    user_id=auth.principal.subject,
                    installation_id=payload.installation_id,
                    oauth_code=payload.oauth_code,
                ),
            )
        except GitHubInstallationNotOwned as exc:
            raise HTTPException(
                status_code=403, detail="GitHub installation is not authorized"
            ) from exc
        except GitHubAppSyncError as exc:
            raise HTTPException(
                status_code=503, detail="GitHub connector is unavailable"
            ) from exc
        except ConnectorDenied as exc:
            raise HTTPException(status_code=403, detail="Connector is not authorized") from exc
        except ConnectorUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Connector service is unavailable"
            ) from exc
        return _connector_connection_response(connection)

    @application.post(
        "/v1/connectors/notion/connection",
        response_model=ConnectorConnectionResponse,
        status_code=201,
    )
    async def connect_notion(
        payload: ConnectorConnectionRequest,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> ConnectorConnectionResponse:
        try:
            vault = _connector_vault(request)
            encrypted_access = vault.encrypt(payload.access_token)
            if not encrypted_access:
                raise TokenVaultUnavailable
            connection = await _with_account_gate(
                request,
                auth,
                lambda: _connector_repository(request).upsert_connection(
                    auth.access_token,
                    user_id=auth.principal.subject,
                    provider="notion",
                    account_email=payload.account_email,
                    scopes=list(payload.scopes),
                    access_token_ciphertext=encrypted_access,
                    refresh_token_ciphertext=None,
                    access_token_expires_at=None,
                ),
            )
        except TokenVaultUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Connector token vault is unavailable"
            ) from exc
        except ConnectorDenied as exc:
            raise HTTPException(status_code=403, detail="Connector is not authorized") from exc
        except ConnectorUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Connector service is unavailable"
            ) from exc
        return _connector_connection_response(connection)

    @application.get(
        "/v1/connectors",
        response_model=list[ConnectorConnectionResponse],
    )
    async def list_connectors(
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> list[ConnectorConnectionResponse]:
        try:
            connections = await _with_account_gate(
                request,
                auth,
                lambda: _connector_repository(request).list_connections(
                    auth.access_token
                ),
            )
        except ConnectorDenied as exc:
            raise HTTPException(status_code=403, detail="Connector is not authorized") from exc
        except ConnectorUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Connector service is unavailable"
            ) from exc
        return [_connector_connection_response(connection) for connection in connections]

    @application.delete("/v1/connectors/{connection_id}", status_code=204)
    async def delete_connector(
        connection_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> Response:
        try:
            connection = await _with_account_gate(
                request,
                auth,
                lambda: _connector_repository(request).get_connection(
                    auth.access_token, connection_id
                ),
            )
            if connection.provider == "notion":
                await _with_account_gate(
                    request,
                    auth,
                    lambda: _notion_sync_service(request).disconnect(
                        auth.access_token, connection_id
                    ),
                )
            elif connection.provider == "github":
                await _with_account_gate(
                    request,
                    auth,
                    lambda: _github_app_sync_service(request).disconnect(
                        auth.access_token, connection_id
                    ),
                )
            else:
                await _with_account_gate(
                    request,
                    auth,
                    lambda: _google_drive_sync_service(request).disconnect(
                        auth.access_token, connection_id
                    ),
                )
        except ConnectorNotFound as exc:
            raise HTTPException(status_code=404, detail="Connector not found") from exc
        except ConnectorDenied as exc:
            raise HTTPException(status_code=403, detail="Connector is not authorized") from exc
        except ConnectorUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Connector service is unavailable"
            ) from exc
        return Response(status_code=204)

    @application.post(
        "/v1/connectors/{connection_id}/sync",
        response_model=ConnectorSyncResponse,
    )
    async def sync_connector(
        connection_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
        limit: int = Query(default=25, ge=1, le=25),
    ) -> ConnectorSyncResponse:
        try:
            connection = await _with_account_gate(
                request,
                auth,
                lambda: _connector_repository(request).get_connection(
                    auth.access_token, connection_id
                ),
            )
            if connection.provider == "notion":
                result = await _with_account_gate(
                    request,
                    auth,
                    lambda: _notion_sync_service(request).sync(
                        auth.access_token, connection_id, limit=limit
                    ),
                )
            elif connection.provider == "google_drive":
                result = await _with_account_gate(
                    request,
                    auth,
                    lambda: _google_drive_sync_service(request).sync(
                        auth.access_token, connection_id, limit=limit
                    ),
                )
            elif connection.provider == "github":
                result = await _with_account_gate(
                    request,
                    auth,
                    lambda: _github_app_sync_service(request).sync(
                        auth.access_token, connection_id, limit=limit
                    ),
                )
            else:
                raise HTTPException(status_code=400, detail="Unsupported connector provider")
        except TokenVaultUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Connector token vault is unavailable"
            ) from exc
        except ConnectorNotFound as exc:
            raise HTTPException(status_code=404, detail="Connector not found") from exc
        except ConnectorDenied as exc:
            raise HTTPException(status_code=403, detail="Connector is not authorized") from exc
        except ConnectorAuthUnavailable as exc:
            raise HTTPException(
                status_code=409, detail="Connector authorization expired"
            ) from exc
        except (ConnectorSyncError, NotionSyncError, GitHubAppSyncError, ConnectorUnavailable) as exc:
            raise HTTPException(
                status_code=503, detail="Connector sync is unavailable"
            ) from exc
        return ConnectorSyncResponse(
            connection_id=UUID(result.connection_id),
            listed=result.listed,
            ingested=result.ingested,
            skipped=result.skipped,
            next_cursor=result.next_cursor,
            unchanged=getattr(result, "unchanged", 0),
            not_indexed=getattr(result, "not_indexed", 0),
        )

    @application.get(
        "/v1/connectors/{connection_id}/not-indexed",
        response_model=list[ConnectorNotIndexedFile],
    )
    async def list_not_indexed_connector_files(
        connection_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> list[ConnectorNotIndexedFile]:
        try:
            rows = await _with_account_gate(
                request,
                auth,
                lambda: _connector_repository(request).list_not_indexed(
                    auth.access_token, connection_id
                ),
            )
        except ConnectorNotFound as exc:
            raise HTTPException(status_code=404, detail="Connector not found") from exc
        except ConnectorDenied as exc:
            raise HTTPException(status_code=403, detail="Connector is not authorized") from exc
        except ConnectorUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Connector service is unavailable"
            ) from exc
        return [ConnectorNotIndexedFile(**row) for row in rows]

    @application.get("/v1/me/api-keys", response_model=list[PersonalApiKeyResponse])
    async def list_personal_api_keys(
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> list[PersonalApiKeyResponse]:
        try:
            keys = await _with_account_gate(
                request,
                auth,
                lambda: _personal_api_key_repository(request).list_keys(
                    auth.access_token, auth.principal.subject
                ),
            )
        except PersonalApiKeyDataUnavailable as exc:
            raise HTTPException(status_code=503, detail="API key service is unavailable") from exc
        return [PersonalApiKeyResponse.model_validate(key) for key in keys]

    @application.post(
        "/v1/me/api-keys",
        response_model=PersonalApiKeyCreatedResponse,
        status_code=201,
    )
    async def create_personal_api_key(
        payload: PersonalApiKeyCreateRequest,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> PersonalApiKeyCreatedResponse:
        try:
            key, token = await _with_account_gate(
                request,
                auth,
                lambda: _personal_api_key_repository(request).create_key(
                    auth.access_token,
                    workspace_id=payload.workspace_id,
                    name=payload.name,
                    expiration_days=payload.expiration_days,
                ),
            )
        except PersonalApiKeyAccessDenied as exc:
            raise HTTPException(status_code=403, detail="Active workspace membership is required") from exc
        except PersonalApiKeyLimitReached as exc:
            raise HTTPException(status_code=409, detail="The active API key limit was reached") from exc
        except PersonalApiKeyDataUnavailable as exc:
            raise HTTPException(status_code=503, detail="API key service is unavailable") from exc
        return PersonalApiKeyCreatedResponse(**key.__dict__, token=token)

    @application.delete(
        "/v1/me/api-keys/{key_id}",
        response_model=PersonalApiKeyRevocationResponse,
    )
    async def revoke_personal_api_key(
        key_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> PersonalApiKeyRevocationResponse:
        try:
            revoked = await _with_account_gate(
                request,
                auth,
                lambda: _personal_api_key_repository(request).revoke_key(
                    auth.access_token, key_id
                ),
            )
        except PersonalApiKeyAccessDenied as exc:
            raise HTTPException(status_code=404, detail="API key not found") from exc
        except PersonalApiKeyDataUnavailable as exc:
            raise HTTPException(status_code=503, detail="API key service is unavailable") from exc
        if not revoked:
            raise HTTPException(status_code=404, detail="API key not found")
        return PersonalApiKeyRevocationResponse(id=key_id, revoked=True)

    @application.get(
        "/v1/personal-api-keys/workspaces/{workspace_id}/evidence",
        response_model=PersonalApiKeyEvidencePage,
    )
    async def read_evidence_with_personal_api_key(
        workspace_id: UUID,
        request: Request,
        parsed_key: tuple[UUID, str] = Depends(require_personal_api_key),
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
    ) -> PersonalApiKeyEvidencePage:
        try:
            cursor_created_at, cursor_id = decode_personal_api_key_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid page cursor") from exc

        await _require_recovery_fence_open(request)
        key_id, key_hash = parsed_key
        try:
            page = await _personal_api_key_repository(request).read_evidence(
                key_id=key_id,
                key_hash=key_hash,
                workspace_id=workspace_id,
                limit=limit,
                cursor_created_at=cursor_created_at,
                cursor_id=cursor_id,
            )
        except PersonalApiKeyAccessDenied as exc:
            raise HTTPException(status_code=401, detail="Personal API key is invalid or revoked") from exc
        except PersonalApiKeyRateLimited as exc:
            raise HTTPException(
                status_code=429,
                detail="Personal API key rate limit exceeded",
                headers={"Retry-After": "60"},
            ) from exc
        except PersonalApiKeyDataUnavailable as exc:
            raise HTTPException(status_code=503, detail="Personal API key service is unavailable") from exc
        return PersonalApiKeyEvidencePage(
            items=[EvidenceResponse.model_validate(item) for item in page.items],
            next_cursor=page.next_cursor,
        )

    @application.get(
        "/v1/personal-api-keys/workspaces/{workspace_id}/relationships",
        response_model=PersonalApiKeyRelationshipPage,
    )
    async def read_relationships_with_personal_api_key(
        workspace_id: UUID,
        request: Request,
        parsed_key: tuple[UUID, str] = Depends(require_personal_api_key),
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
    ) -> PersonalApiKeyRelationshipPage:
        try:
            cursor_created_at, cursor_id = decode_personal_api_key_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid page cursor") from exc

        await _require_recovery_fence_open(request)
        key_id, key_hash = parsed_key
        try:
            page = await _personal_api_key_repository(request).read_relationships(
                key_id=key_id,
                key_hash=key_hash,
                workspace_id=workspace_id,
                limit=limit,
                cursor_created_at=cursor_created_at,
                cursor_id=cursor_id,
            )
        except PersonalApiKeyAccessDenied as exc:
            raise HTTPException(status_code=401, detail="Personal API key is invalid or revoked") from exc
        except PersonalApiKeyRateLimited as exc:
            raise HTTPException(
                status_code=429,
                detail="Personal API key rate limit exceeded",
                headers={"Retry-After": "60"},
            ) from exc
        except PersonalApiKeyDataUnavailable as exc:
            raise HTTPException(status_code=503, detail="Personal API key service is unavailable") from exc
        return PersonalApiKeyRelationshipPage(
            items=[RelationshipResponse.model_validate(item) for item in page.items],
            next_cursor=page.next_cursor,
        )


    @application.post(
        "/v1/personal-api-keys/workspaces/{workspace_id}/retrieve",
        response_model=PersonalApiKeyRetrieveResponse,
    )
    async def retrieve_with_personal_api_key(
        workspace_id: UUID,
        payload: PersonalApiKeyRetrieveRequest,
        request: Request,
        parsed_key: tuple[UUID, str] = Depends(require_personal_api_key),
    ) -> PersonalApiKeyRetrieveResponse:
        if not request.app.state.retrieval_enabled:
            raise HTTPException(status_code=404, detail="Retrieval API is not enabled")
        await _require_recovery_fence_open(request)
        key_id, key_hash = parsed_key
        try:
            decision = await _consume_api_key_usage(
                request,
                key_id=key_id,
                key_hash=key_hash,
                workspace_id=workspace_id,
                kind="retrieval",
            )
        except UsageCapsAccessDenied as exc:
            raise HTTPException(status_code=401, detail="Personal API key is invalid or revoked") from exc
        except UsageCapsUnavailable as exc:
            raise HTTPException(status_code=503, detail="Usage limits are unavailable") from exc
        if decision is not None:
            _raise_if_user_rate_limited(decision)
        try:
            page = await _read_retrieval_candidates(
                _personal_api_key_repository(request),
                key_id=key_id,
                key_hash=key_hash,
                workspace_id=workspace_id,
                limit=payload.candidate_limit,
            )
        except PersonalApiKeyAccessDenied as exc:
            raise HTTPException(status_code=401, detail="Personal API key is invalid or revoked") from exc
        except PersonalApiKeyRateLimited as exc:
            raise HTTPException(
                status_code=429,
                detail="Personal API key rate limit exceeded",
                headers={"Retry-After": "60"},
            ) from exc
        except PersonalApiKeyDataUnavailable as exc:
            raise HTTPException(status_code=503, detail="Personal API key service is unavailable") from exc

        relationship_page = PersonalApiKeyPage(items=[], next_cursor=None)
        if payload.top_k > 1:
            try:
                relationship_page = await _personal_api_key_repository(request).read_relationships(
                    key_id=key_id,
                    key_hash=key_hash,
                    workspace_id=workspace_id,
                    limit=100,
                    cursor_created_at=None,
                    cursor_id=None,
                )
            except PersonalApiKeyAccessDenied as exc:
                raise HTTPException(status_code=401, detail="Personal API key is invalid or revoked") from exc
            except PersonalApiKeyRateLimited as exc:
                raise HTTPException(
                    status_code=429,
                    detail="Personal API key rate limit exceeded",
                    headers={"Retry-After": "60"},
                ) from exc
            except PersonalApiKeyDataUnavailable as exc:
                raise HTTPException(status_code=503, detail="Personal API key service is unavailable") from exc

        try:
            records = _evidence_page_items(page)
            relationships = _relationship_page_items(relationship_page)
        except PersonalApiKeyDataUnavailable as exc:
            raise HTTPException(status_code=503, detail="Personal API key service is unavailable") from exc
        seed_ids, related_scores = _relationship_expansion(
            records,
            relationships,
            workspace_id,
            payload.query,
            top_k=payload.top_k,
            max_tokens=payload.max_tokens,
            max_chars=payload.max_chars,
        )
        return await _retrieval_response(
            page,
            workspace_id,
            payload,
            seed_record_ids=seed_ids,
            related_record_scores=related_scores,
            relationships=relationships,
            jev_enabled=request.app.state.jev_enabled,
            jev_scorer=request.app.state.jev_scorer,
            jev_principal_id=str(key_id),
            jev_budget=_jev_budget_for(
                lambda: _consume_api_key_usage(
                    request,
                    key_id=key_id,
                    key_hash=key_hash,
                    workspace_id=workspace_id,
                    kind="jev",
                )
            ),
        )

    @application.get(
        "/v1/personal-api-keys/workspaces/{workspace_id}/evidence/{evidence_id}",
        response_model=EvidenceResponse,
    )
    async def read_evidence_document_with_personal_api_key(
        workspace_id: UUID,
        evidence_id: UUID,
        request: Request,
        parsed_key: tuple[UUID, str] = Depends(require_personal_api_key),
    ) -> EvidenceResponse:
        if not request.app.state.retrieval_enabled:
            raise HTTPException(status_code=404, detail="Retrieval API is not enabled")
        await _require_recovery_fence_open(request)
        key_id, key_hash = parsed_key
        try:
            record = await _personal_api_key_repository(request).read_evidence_document(
                key_id=key_id,
                key_hash=key_hash,
                workspace_id=workspace_id,
                evidence_id=evidence_id,
            )
        except PersonalApiKeyAccessDenied as exc:
            raise HTTPException(status_code=401, detail="Personal API key is invalid or revoked") from exc
        except PersonalApiKeyRateLimited as exc:
            raise HTTPException(
                status_code=429,
                detail="Personal API key rate limit exceeded",
                headers={"Retry-After": "60"},
            ) from exc
        except PersonalApiKeyDataUnavailable as exc:
            raise HTTPException(status_code=503, detail="Personal API key service is unavailable") from exc
        if record is None:
            raise HTTPException(status_code=404, detail="Evidence document not found")
        return EvidenceResponse.model_validate(record)

    @application.post(
        "/v1/me/organization",
        response_model=MeResponse,
    )
    async def create_organization_account_route(
        payload: OrganizationCreateRequest,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> MeResponse:
        async def load_account():
            directory = _workspace_directory(request)
            creator = getattr(directory, "create_organization_account", None)
            if creator is None:
                raise WorkspaceDataUnavailable
            account = await creator(
                auth.principal, auth.access_token, payload.name
            )
            memberships = await directory.list_memberships(
                auth.principal, auth.access_token
            )
            return memberships, account

        try:
            memberships, account = await _with_account_gate(
                request, auth, load_account
            )
        except WorkspaceDataUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Organization account service is unavailable"
            ) from exc

        return MeResponse(
            user_id=auth.principal.subject,
            issuer=auth.principal.issuer,
            account_type=account.account_type,
            organization_name=account.organization_name,
            can_manage_workspace=account.account_type == "organization",
            workspaces=[
                WorkspaceSummary(
                    id=membership.workspace_id,
                    name=membership.workspace_name,
                    role=membership.role,
                    workspace_type=membership.workspace_type,
                )
                for membership in memberships
            ],
        )

    @application.get(
        "/v1/organization",
        response_model=OrganizationOverviewResponse,
    )
    async def get_organization(
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> OrganizationOverviewResponse:
        try:
            data = await _with_account_gate(
                request,
                auth,
                lambda: _organization_repository(request).overview(auth.access_token),
            )
        except (
            OrganizationDataUnavailable,
            OrganizationAccessDenied,
            OrganizationMemberNotFound,
            OrganizationValidationError,
        ) as exc:
            _raise_organization_error(exc)
        return OrganizationOverviewResponse(**data)

    @application.post(
        "/v1/organization/workspaces",
        response_model=GroupWorkspaceResponse,
        status_code=201,
    )
    async def create_group_workspace_route(
        payload: GroupWorkspaceCreateRequest,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> GroupWorkspaceResponse:
        try:
            data = await _with_account_gate(
                request,
                auth,
                lambda: _organization_repository(request).create_group_workspace(
                    auth.access_token, payload.name
                ),
            )
        except (
            OrganizationDataUnavailable,
            OrganizationAccessDenied,
            OrganizationMemberNotFound,
            OrganizationValidationError,
        ) as exc:
            _raise_organization_error(exc)
        return GroupWorkspaceResponse(**data)

    @application.delete(
        "/v1/organization/workspaces/{workspace_id}",
        response_model=GroupWorkspaceDeleteResponse,
    )
    async def delete_group_workspace_route(
        workspace_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> GroupWorkspaceDeleteResponse:
        try:
            data = await _with_account_gate(
                request,
                auth,
                lambda: _organization_repository(request).delete_group_workspace(
                    auth.access_token, str(workspace_id)
                ),
            )
        except (
            OrganizationDataUnavailable,
            OrganizationAccessDenied,
            OrganizationMemberNotFound,
            OrganizationValidationError,
        ) as exc:
            _raise_organization_error(exc)
        return GroupWorkspaceDeleteResponse(**data)

    @application.post(
        "/v1/organization/members",
        response_model=OrganizationMemberSummary,
        status_code=201,
    )
    async def add_organization_member_route(
        payload: OrganizationMemberCreateRequest,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> OrganizationMemberSummary:
        try:
            data = await _with_account_gate(
                request,
                auth,
                lambda: _organization_repository(request).add_member(
                    auth.access_token, payload.email, payload.role
                ),
            )
        except (
            OrganizationDataUnavailable,
            OrganizationAccessDenied,
            OrganizationMemberNotFound,
            OrganizationValidationError,
        ) as exc:
            _raise_organization_error(exc)
        return OrganizationMemberSummary(**data)

    @application.patch(
        "/v1/organization/members/{user_id}",
        response_model=OrganizationMemberRoleResponse,
    )
    async def update_organization_member_role_route(
        user_id: UUID,
        payload: OrganizationMemberRoleRequest,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> OrganizationMemberRoleResponse:
        try:
            data = await _with_account_gate(
                request,
                auth,
                lambda: _organization_repository(request).update_member_role(
                    auth.access_token, str(user_id), payload.role
                ),
            )
        except (
            OrganizationDataUnavailable,
            OrganizationAccessDenied,
            OrganizationMemberNotFound,
            OrganizationValidationError,
        ) as exc:
            _raise_organization_error(exc)
        return OrganizationMemberRoleResponse(**data)

    @application.delete(
        "/v1/organization/members/{user_id}",
        response_model=OrganizationMemberRemovalResponse,
    )
    async def remove_organization_member_route(
        user_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> OrganizationMemberRemovalResponse:
        try:
            data = await _with_account_gate(
                request,
                auth,
                lambda: _organization_repository(request).remove_member(
                    auth.access_token, str(user_id)
                ),
            )
        except (
            OrganizationDataUnavailable,
            OrganizationAccessDenied,
            OrganizationMemberNotFound,
            OrganizationValidationError,
        ) as exc:
            _raise_organization_error(exc)
        return OrganizationMemberRemovalResponse(**data)

    @application.put(
        "/v1/me/personal-workspace", response_model=WorkspaceResponse
    )
    async def ensure_personal_workspace(
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> WorkspaceResponse:
        try:
            membership = await _with_account_gate(
                request,
                auth,
                lambda: _workspace_directory(request).ensure_personal_workspace(
                    auth.principal, auth.access_token
                ),
            )
        except AuthenticationRejected as exc:
            raise HTTPException(
                status_code=401,
                detail="Invalid bearer access token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        except WorkspaceAccessDenied as exc:
            raise HTTPException(
                status_code=403,
                detail="Personal workspace is unavailable for this account",
            ) from exc
        except WorkspaceDataUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Workspace directory is unavailable"
            ) from exc

        return WorkspaceResponse(
            id=membership.workspace_id,
            name=membership.workspace_name,
            role=membership.role,
        )

    @application.get("/v1/workspaces/{workspace_id}", response_model=WorkspaceResponse)
    async def get_workspace(
        workspace_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> WorkspaceResponse:
        try:
            context = await _with_account_gate(
                request,
                auth,
                lambda: resolve_workspace_context(
                    _workspace_directory(request),
                    auth.principal,
                    auth.access_token,
                    workspace_id,
                ),
            )
        except WorkspaceAccessDenied as exc:
            raise HTTPException(status_code=404, detail="Workspace not found") from exc
        except WorkspaceDataUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Workspace directory is unavailable"
            ) from exc

        return WorkspaceResponse(
            id=context.workspace_id,
            name=context.workspace_name,
            role=context.role,
        )

    @application.get(
        "/v1/workspaces/{workspace_id}/sources",
        response_model=list[SourceVersionSummary],
    )
    async def list_workspace_sources(
        workspace_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> list[SourceVersionSummary]:
        # A non-member sees 404; the catalog query itself is additionally
        # RLS-scoped to versions the requesting user may see.
        context = await _resolve_workspace_or_404(request, auth, workspace_id)

        from evidence_service.supabase_source_catalog import SourceCatalogUnavailable

        try:
            manifests = await _with_account_gate(
                request,
                auth,
                lambda: _source_catalog(
                    request, auth.access_token
                ).list_visible_versions(context.workspace_id),
            )
        except SourceCatalogUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Source catalog is unavailable"
            ) from exc

        return [
            SourceVersionSummary(
                source_id=manifest.source_id,
                version_id=manifest.version_id,
                state=manifest.state.value,
                sha256=manifest.sha256,
                size_bytes=manifest.size_bytes,
                created_at=manifest.created_at,
            )
            for manifest in manifests
        ]

    @application.post(
        "/v1/workspaces/{workspace_id}/sources",
        response_model=SourceStageResponse,
        status_code=201,
    )
    async def stage_workspace_source(
        workspace_id: UUID,
        payload: SourceStageRequest,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> SourceStageResponse:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        if context.role != "owner":
            raise HTTPException(status_code=403, detail="Workspace owner required")
        content = payload.content.encode("utf-8")
        from evidence_service.secure_snapshot_read import snapshot_object_key

        if len(content) > 4_000_000:
            raise HTTPException(status_code=413, detail="Source version is too large")
        try:
            usage = await _with_account_gate(
                request,
                auth,
                lambda: _workspace_usage_repository(
                    request, auth.access_token
                ).usage(auth.access_token, workspace_id=context.workspace_id),
            )
        except WorkspaceUsageForbidden as exc:
            raise HTTPException(status_code=403, detail="Workspace owner required") from exc
        except WorkspaceUsageUnavailable as exc:
            raise HTTPException(status_code=503, detail="Workspace usage is unavailable") from exc
        if (
            usage["version_count"] >= MAX_WORKSPACE_SOURCE_VERSIONS
            or usage["total_bytes"] + len(content) > MAX_WORKSPACE_SOURCE_BYTES
        ):
            raise HTTPException(status_code=429, detail="Workspace source quota exceeded")
        source_id = payload.source_id or uuid4()
        version_id = uuid4()
        object_key = snapshot_object_key(context.workspace_id, source_id, version_id)
        digest = hashlib.sha256(content).hexdigest()
        try:
            staged = await _with_account_gate(
                request,
                auth,
                lambda: _source_staging_repository(
                    request, auth.access_token
                ).stage(
                    access_token=auth.access_token,
                    workspace_id=context.workspace_id,
                    source_id=source_id,
                    version_id=version_id,
                    principal_id=auth.principal.subject,
                    object_key=object_key,
                    sha256=digest,
                    size_bytes=len(content),
                ),
            )
        except SourceStageForbidden as exc:
            raise HTTPException(
                status_code=403, detail="Workspace owner required"
            ) from exc
        except SourceStageConflict as exc:
            raise HTTPException(
                status_code=409, detail="Source version already exists"
            ) from exc
        except SourceStageRejected as exc:
            raise HTTPException(
                status_code=422, detail="Source version was rejected"
            ) from exc
        except SourceStageUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Source catalog is unavailable"
            ) from exc
        return SourceStageResponse(
            source_id=staged.source_id,
            version_id=staged.version_id,
            state="staged",
            sha256=staged.sha256,
            size_bytes=staged.size_bytes,
            bucket="source-snapshots",
            object_key=staged.object_key,
            created_at=staged.created_at,
        )

    @application.post(
        "/v1/workspaces/{workspace_id}/deletion-request",
    )
    async def request_workspace_deletion(
        workspace_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> dict:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        if context.role != "owner":
            raise HTTPException(status_code=403, detail="Workspace owner required")
        try:
            return await _with_account_gate(
                request, auth,
                lambda: _workspace_deletion_repository(
                    request, auth.access_token
                ).request(auth.access_token, workspace_id=context.workspace_id),
            )
        except WorkspaceDeletionForbidden as exc:
            raise HTTPException(status_code=403, detail="Workspace owner required") from exc
        except WorkspaceDeletionUnavailable as exc:
            raise HTTPException(status_code=503, detail="Workspace deletion is unavailable") from exc

    @application.get(
        "/v1/workspaces/{workspace_id}/deletion-request",
    )
    async def get_workspace_deletion_status(
        workspace_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> dict | None:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        if context.role != "owner":
            raise HTTPException(status_code=403, detail="Workspace owner required")
        try:
            return await _with_account_gate(
                request, auth,
                lambda: _workspace_deletion_repository(
                    request, auth.access_token
                ).status(auth.access_token, workspace_id=context.workspace_id),
            )
        except WorkspaceDeletionForbidden as exc:
            raise HTTPException(status_code=403, detail="Workspace owner required") from exc
        except WorkspaceDeletionUnavailable as exc:
            raise HTTPException(status_code=503, detail="Workspace deletion is unavailable") from exc

    @application.post(
        "/v1/workspaces/{workspace_id}/deletion-request/{request_id}/approve",
    )
    async def approve_workspace_deletion(
        workspace_id: UUID,
        request_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> dict:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        if context.role != "owner":
            raise HTTPException(status_code=403, detail="Workspace owner required")
        try:
            return await _with_account_gate(
                request, auth,
                lambda: _workspace_deletion_repository(
                    request, auth.access_token
                ).approve(auth.access_token, request_id=request_id),
            )
        except WorkspaceDeletionForbidden as exc:
            raise HTTPException(status_code=403, detail="Workspace owner required") from exc
        except WorkspaceDeletionUnavailable as exc:
            raise HTTPException(status_code=503, detail="Workspace deletion is unavailable") from exc

    @application.post(
        "/v1/workspaces/{workspace_id}/deletion-request/{request_id}/withdraw",
    )
    async def withdraw_workspace_deletion(
        workspace_id: UUID,
        request_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> dict:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        if context.role != "owner":
            raise HTTPException(status_code=403, detail="Workspace owner required")
        try:
            return await _with_account_gate(
                request, auth,
                lambda: _workspace_deletion_repository(
                    request, auth.access_token
                ).withdraw(auth.access_token, request_id=request_id),
            )
        except WorkspaceDeletionForbidden as exc:
            raise HTTPException(status_code=403, detail="Workspace owner required") from exc
        except WorkspaceDeletionUnavailable as exc:
            raise HTTPException(status_code=503, detail="Workspace deletion is unavailable") from exc

    @application.post(
        "/v1/workspaces/{workspace_id}/deletion-request/{request_id}/cancel",
    )
    async def cancel_workspace_deletion(
        workspace_id: UUID,
        request_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> dict:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        if context.role != "owner":
            raise HTTPException(status_code=403, detail="Workspace owner required")
        try:
            return await _with_account_gate(
                request, auth,
                lambda: _workspace_deletion_repository(
                    request, auth.access_token
                ).cancel(auth.access_token, request_id=request_id),
            )
        except WorkspaceDeletionForbidden as exc:
            raise HTTPException(status_code=403, detail="Workspace owner required") from exc
        except WorkspaceDeletionUnavailable as exc:
            raise HTTPException(status_code=503, detail="Workspace deletion is unavailable") from exc

    @application.post(
        "/v1/workspaces/{workspace_id}/invitations",
        response_model=WorkspaceInvitationCreateResponse,
        status_code=201,
    )
    async def create_workspace_invitation(
        workspace_id: UUID,
        payload: WorkspaceInvitationCreateRequest,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> WorkspaceInvitationCreateResponse:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        if context.role != "owner":
            raise HTTPException(status_code=403, detail="Workspace owner required")
        email = payload.email.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise HTTPException(status_code=422, detail="A valid email is required")
        try:
            usage = await _with_account_gate(
                request,
                auth,
                lambda: _workspace_usage_repository(
                    request, auth.access_token
                ).usage(auth.access_token, workspace_id=context.workspace_id),
            )
        except WorkspaceUsageForbidden as exc:
            raise HTTPException(status_code=403, detail="Workspace owner required") from exc
        except WorkspaceUsageUnavailable as exc:
            raise HTTPException(status_code=503, detail="Workspace usage is unavailable") from exc
        if usage["pending_invitations"] >= MAX_PENDING_WORKSPACE_INVITATIONS:
            raise HTTPException(status_code=429, detail="Pending invitation quota exceeded")
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        try:
            invitation = await _with_account_gate(
                request,
                auth,
                lambda: _workspace_invitation_repository(
                    request, auth.access_token
                ).create(
                    auth.access_token,
                    workspace_id=context.workspace_id,
                    email=email,
                    token_hash=token_hash,
                    expires_at=expires_at,
                ),
            )
        except InvitationForbidden as exc:
            raise HTTPException(status_code=403, detail="Workspace owner required") from exc
        except InvitationRejected as exc:
            raise HTTPException(status_code=422, detail="Invitation was rejected") from exc
        except InvitationUnavailable as exc:
            raise HTTPException(status_code=503, detail="Invitations are unavailable") from exc
        email_sent = await send_invitation_email(
            api_key=os.environ.get("RESEND_API_KEY", ""),
            from_email=os.environ.get("INVITATION_FROM_EMAIL", ""),
            base_url=os.environ.get("INVITATION_BASE_URL", ""),
            to_email=invitation.email,
            token=token,
        )
        return WorkspaceInvitationCreateResponse(
            invitation_id=invitation.invitation_id,
            email=invitation.email,
            role=invitation.role,
            created_at=invitation.created_at,
            expires_at=invitation.expires_at,
            accepted_at=None,
            revoked_at=None,
            invite_token=token,
            email_sent=email_sent,
        )

    @application.get(
        "/v1/workspaces/{workspace_id}/invitations",
        response_model=list[WorkspaceInvitationSummary],
    )
    async def list_workspace_invitations(
        workspace_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> list[WorkspaceInvitationSummary]:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        if context.role != "owner":
            raise HTTPException(status_code=403, detail="Workspace owner required")
        try:
            invitations = await _with_account_gate(
                request,
                auth,
                lambda: _workspace_invitation_repository(
                    request, auth.access_token
                ).list(auth.access_token, workspace_id=context.workspace_id),
            )
        except InvitationForbidden as exc:
            raise HTTPException(status_code=403, detail="Workspace owner required") from exc
        except InvitationUnavailable as exc:
            raise HTTPException(status_code=503, detail="Invitations are unavailable") from exc
        return [
            WorkspaceInvitationSummary(
                invitation_id=invitation.invitation_id,
                email=invitation.email,
                role=invitation.role,
                created_at=invitation.created_at,
                expires_at=invitation.expires_at,
                accepted_at=invitation.accepted_at,
                revoked_at=invitation.revoked_at,
            )
            for invitation in invitations
        ]

    @application.delete(
        "/v1/workspaces/{workspace_id}/invitations/{invitation_id}",
        status_code=204,
    )
    async def revoke_workspace_invitation(
        workspace_id: UUID,
        invitation_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> Response:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        if context.role != "owner":
            raise HTTPException(status_code=403, detail="Workspace owner required")
        try:
            await _with_account_gate(
                request,
                auth,
                lambda: _workspace_invitation_repository(
                    request, auth.access_token
                ).revoke(auth.access_token, invitation_id=invitation_id),
            )
        except InvitationForbidden as exc:
            raise HTTPException(status_code=403, detail="Workspace owner required") from exc
        except InvitationRejected as exc:
            raise HTTPException(status_code=404, detail="Invitation not found") from exc
        except InvitationUnavailable as exc:
            raise HTTPException(status_code=503, detail="Invitations are unavailable") from exc
        return Response(status_code=204)

    @application.post(
        "/v1/invitations/accept",
        response_model=WorkspaceInvitationAcceptResponse,
    )
    async def accept_workspace_invitation(
        payload: WorkspaceInvitationAcceptRequest,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> WorkspaceInvitationAcceptResponse:
        token_hash = hashlib.sha256(payload.token.encode()).hexdigest()
        try:
            workspace_id, role = await _with_account_gate(
                request,
                auth,
                lambda: _workspace_invitation_repository(
                    request, auth.access_token
                ).accept(auth.access_token, token_hash=token_hash),
            )
        except InvitationForbidden as exc:
            raise HTTPException(status_code=403, detail="Invitation is not available") from exc
        except InvitationRejected as exc:
            raise HTTPException(status_code=422, detail="Invitation is not available") from exc
        except InvitationUnavailable as exc:
            raise HTTPException(status_code=503, detail="Invitations are unavailable") from exc
        return WorkspaceInvitationAcceptResponse(workspace_id=workspace_id, role=role)

    @application.get(
        "/v1/workspaces/{workspace_id}/sources/{source_id}/grants",
        response_model=list[SourceGrantSummary],
    )
    async def list_source_grants(
        workspace_id: UUID,
        source_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> list[SourceGrantSummary]:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        if context.role != "owner":
            raise HTTPException(status_code=403, detail="Workspace owner required")
        try:
            grants = await _with_account_gate(
                request,
                auth,
                lambda: _source_grant_repository(
                    request, auth.access_token
                ).list(
                    auth.access_token,
                    workspace_id=context.workspace_id,
                    source_id=source_id,
                ),
            )
        except SourceGrantForbidden as exc:
            raise HTTPException(
                status_code=403, detail="Workspace owner required"
            ) from exc
        except SourceGrantUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Source grants are unavailable"
            ) from exc
        return [
            SourceGrantSummary(
                principal_id=grant.principal_id,
                grant_type=grant.grant_type,
                granted_by=grant.granted_by,
                created_at=grant.created_at,
            )
            for grant in grants
        ]

    @application.post(
        "/v1/workspaces/{workspace_id}/sources/{source_id}/grants",
        response_model=SourceGrantSummary,
        status_code=201,
    )
    async def create_source_grant(
        workspace_id: UUID,
        source_id: UUID,
        payload: SourceGrantCreateRequest,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> SourceGrantSummary:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        if context.role != "owner":
            raise HTTPException(status_code=403, detail="Workspace owner required")
        try:
            grant = await _with_account_gate(
                request,
                auth,
                lambda: _source_grant_repository(
                    request, auth.access_token
                ).grant(
                    auth.access_token,
                    workspace_id=context.workspace_id,
                    source_id=source_id,
                    principal_id=payload.principal_id,
                ),
            )
        except SourceGrantForbidden as exc:
            raise HTTPException(
                status_code=403, detail="Workspace owner required"
            ) from exc
        except SourceGrantConflict as exc:
            raise HTTPException(
                status_code=409, detail="Read grant already exists"
            ) from exc
        except SourceGrantRejected as exc:
            raise HTTPException(
                status_code=422, detail="Principal must be a workspace member"
            ) from exc
        except SourceGrantUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Source grants are unavailable"
            ) from exc
        return SourceGrantSummary(
            principal_id=grant.principal_id,
            grant_type=grant.grant_type,
            granted_by=grant.granted_by,
            created_at=grant.created_at,
        )

    @application.delete(
        "/v1/workspaces/{workspace_id}/sources/{source_id}/grants/{principal_id}",
        status_code=204,
    )
    async def delete_source_grant(
        workspace_id: UUID,
        source_id: UUID,
        principal_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> Response:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        if context.role != "owner":
            raise HTTPException(status_code=403, detail="Workspace owner required")
        try:
            await _with_account_gate(
                request,
                auth,
                lambda: _source_grant_repository(
                    request, auth.access_token
                ).revoke(
                    auth.access_token,
                    workspace_id=context.workspace_id,
                    source_id=source_id,
                    principal_id=principal_id,
                ),
            )
        except SourceGrantForbidden as exc:
            raise HTTPException(
                status_code=403, detail="Workspace owner required"
            ) from exc
        except SourceGrantRejected as exc:
            raise HTTPException(status_code=404, detail="Read grant not found") from exc
        except SourceGrantUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Source grants are unavailable"
            ) from exc
        return Response(status_code=204)

    @application.get(
        "/v1/workspaces/{workspace_id}/evidence",
        response_model=list[EvidenceResponse],
    )
    async def list_evidence(
        workspace_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> list[EvidenceRecord]:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        try:
            return await _with_account_gate(
                request,
                auth,
                lambda: _evidence_repository(request).list_records(
                    auth.access_token, context.workspace_id
                ),
            )
        except EvidenceAccessDenied as exc:
            raise _evidence_access_error(exc) from exc
        except EvidenceDataUnavailable as exc:
            raise _evidence_data_error(exc) from exc

    @application.post(
        "/v1/workspaces/{workspace_id}/task-context",
        response_model=WorkspaceTaskContextResponse,
    )
    async def prepare_workspace_task_context(
        workspace_id: UUID,
        payload: WorkspaceTaskContextRequest,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> WorkspaceTaskContextResponse:
        if not request.app.state.retrieval_enabled:
            raise HTTPException(status_code=404, detail="Retrieval API is not enabled")
        started_at = perf_counter()
        await _require_recovery_fence_open(request)
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        try:
            decision = await _consume_session_usage(request, auth.access_token, "retrieval")
        except UsageCapsAccessDenied as exc:
            raise HTTPException(status_code=401, detail="Authentication is required") from exc
        except UsageCapsUnavailable as exc:
            raise HTTPException(status_code=503, detail="Usage limits are unavailable") from exc
        if decision is not None:
            _raise_if_user_rate_limited(decision)
        try:
            records = await _with_account_gate(
                request,
                auth,
                lambda: _evidence_repository(request).list_records(
                    auth.access_token, context.workspace_id
                ),
            )
            relationships = (
                await _with_account_gate(
                    request,
                    auth,
                    lambda: _relationship_repository(request).list_relationships(
                        auth.access_token, context.workspace_id
                    ),
                )
                if payload.top_k > 1
                else []
            )
        except EvidenceAccessDenied as exc:
            raise _evidence_access_error(exc) from exc
        except EvidenceDataUnavailable as exc:
            raise _evidence_data_error(exc) from exc
        except RelationshipAccessDenied as exc:
            raise HTTPException(status_code=404, detail="Workspace not found") from exc
        except RelationshipDataUnavailable as exc:
            raise _relationship_data_error(exc) from exc
        response = await _workspace_task_context_response(
            records,
            relationships,
            context.workspace_id,
            payload,
            jev_enabled=request.app.state.jev_enabled,
            jev_scorer=request.app.state.jev_scorer,
            jev_principal_id=str(auth.principal.subject),
            jev_budget=_jev_budget_for(
                lambda: _consume_session_usage(request, auth.access_token, "jev")
            ),
        )
        if request.app.state.task_ledger_enabled:
            try:
                await _task_run_ledger_repository(request).record(
                    auth.access_token,
                    workspace_id=context.workspace_id,
                    actor_user_id=auth.principal.subject,
                    metrics={
                        "run_type": "task_context",
                        "status": response.retrieval.status,
                        "retrieval_latency_ms": max(0, round((perf_counter() - started_at) * 1000)),
                        "retrieval_tokens": response.retrieval.used_tokens,
                        "retrieval_chars": response.retrieval.used_chars,
                        "selected_count": response.retrieval.selected_count,
                        "chunk_count": response.retrieval.chunk_count,
                        "relationship_count": len(response.relationships),
                        "scoring_provider": response.retrieval.scoring_provider,
                        "fallback_reason": response.retrieval.scoring_fallback_reason,
                        "use_jev": payload.use_jev,
                    },
                )
            except TaskRunLedgerUnavailable:
                logger.warning("task-context ledger write failed")
        return response

    @application.get(
        "/v1/workspaces/{workspace_id}/task-runs",
        response_model=list[TaskRunLedgerItem],
    )
    async def list_workspace_task_runs(
        workspace_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> list[TaskRunLedgerItem]:
        if not request.app.state.task_ledger_enabled:
            raise HTTPException(status_code=404, detail="Task ledger is not enabled")
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        try:
            rows = await _with_account_gate(
                request,
                auth,
                lambda: _task_run_ledger_repository(request).list_runs(
                    auth.access_token, workspace_id=context.workspace_id, limit=100
                ),
            )
        except TaskRunLedgerUnavailable as exc:
            raise HTTPException(status_code=503, detail="Task ledger is unavailable") from exc
        return [TaskRunLedgerItem.model_validate(row) for row in rows]

    @application.post(
        "/v1/workspaces/{workspace_id}/evidence",
        response_model=EvidenceResponse,
        status_code=201,
    )
    async def create_evidence(
        workspace_id: UUID,
        body: EvidenceCreateRequest,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> EvidenceRecord:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        if context.role not in {"owner", "admin"}:
            raise HTTPException(status_code=403, detail="Workspace owner access is required")
        await _require_visible_source_link(
            request, auth, context.workspace_id, body.linked_pair()
        )
        try:
            return await _with_account_gate(
                request,
                auth,
                lambda: _evidence_repository(request).create_record(
                    auth.access_token,
                    context.workspace_id,
                    content=body.content,
                    source_label=body.source_label,
                    source_id=body.source_id,
                    source_version_id=body.source_version_id,
                ),
            )
        except EvidenceAccessDenied as exc:
            raise _evidence_access_error(exc) from exc
        except EvidenceDataUnavailable as exc:
            raise _evidence_data_error(exc) from exc

    @application.get(
        "/v1/workspaces/{workspace_id}/evidence/{evidence_id}",
        response_model=EvidenceResponse,
    )
    async def get_evidence(
        workspace_id: UUID,
        evidence_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> EvidenceRecord:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        try:
            record = await _with_account_gate(
                request,
                auth,
                lambda: _evidence_repository(request).get_record(
                    auth.access_token, context.workspace_id, evidence_id
                ),
            )
        except EvidenceAccessDenied as exc:
            raise _evidence_access_error(exc) from exc
        except EvidenceDataUnavailable as exc:
            raise _evidence_data_error(exc) from exc
        if record is None:
            raise HTTPException(status_code=404, detail="Evidence record not found")
        return record

    @application.patch(
        "/v1/workspaces/{workspace_id}/evidence/{evidence_id}",
        response_model=EvidenceResponse,
    )
    async def update_evidence(
        workspace_id: UUID,
        evidence_id: UUID,
        body: EvidenceUpdateRequest,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> EvidenceRecord:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        if context.role not in {"owner", "admin"}:
            raise HTTPException(status_code=403, detail="Workspace owner access is required")
        if body.model_fields_set & {"source_id", "source_version_id"}:
            await _require_visible_source_link(
                request, auth, context.workspace_id, body.linked_pair()
            )
        try:
            record = await _with_account_gate(
                request,
                auth,
                lambda: _evidence_repository(request).update_record(
                    auth.access_token,
                    context.workspace_id,
                    evidence_id,
                    body.model_dump(exclude_unset=True),
                ),
            )
        except EvidenceAccessDenied as exc:
            raise _evidence_access_error(exc) from exc
        except EvidenceDataUnavailable as exc:
            raise _evidence_data_error(exc) from exc
        if record is None:
            raise HTTPException(status_code=404, detail="Evidence record not found")
        return record

    @application.delete(
        "/v1/workspaces/{workspace_id}/evidence/{evidence_id}",
        status_code=204,
    )
    async def delete_evidence(
        workspace_id: UUID,
        evidence_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> Response:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        if context.role not in {"owner", "admin"}:
            raise HTTPException(status_code=403, detail="Workspace owner access is required")
        try:
            deleted = await _with_account_gate(
                request,
                auth,
                lambda: _evidence_repository(request).delete_record(
                    auth.access_token, context.workspace_id, evidence_id
                ),
            )
        except EvidenceAccessDenied as exc:
            raise _evidence_access_error(exc) from exc
        except EvidenceDataUnavailable as exc:
            raise _evidence_data_error(exc) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="Evidence record not found")
        return Response(status_code=204)

    @application.get(
        "/v1/workspaces/{workspace_id}/relationships",
        response_model=list[RelationshipResponse],
    )
    async def list_relationships(
        workspace_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> list[EvidenceRelationship]:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        try:
            return await _with_account_gate(
                request,
                auth,
                lambda: _relationship_repository(request).list_relationships(
                    auth.access_token, context.workspace_id
                ),
            )
        except RelationshipAccessDenied as exc:
            raise HTTPException(status_code=404, detail="Workspace not found") from exc
        except RelationshipDataUnavailable as exc:
            raise _relationship_data_error(exc) from exc

    @application.post(
        "/v1/workspaces/{workspace_id}/relationships",
        response_model=RelationshipResponse,
        status_code=201,
    )
    async def create_relationship(
        workspace_id: UUID,
        body: RelationshipCreateRequest,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> EvidenceRelationship:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        if context.role not in {"owner", "admin"}:
            raise HTTPException(status_code=403, detail="Workspace owner access is required")
        try:
            return await _with_account_gate(
                request,
                auth,
                lambda: _relationship_repository(request).create_relationship(
                    auth.access_token,
                    context.workspace_id,
                    evidence_id_a=body.evidence_id_a,
                    evidence_id_b=body.evidence_id_b,
                    relation_type=body.relation_type,
                    rationale=body.rationale,
                    provenance_note=body.provenance_note,
                ),
            )
        except RelationshipAccessDenied as exc:
            raise HTTPException(status_code=403, detail="Relationship write is not authorized") from exc
        except RelationshipConflict as exc:
            raise HTTPException(status_code=409, detail="The relationship conflicts with existing evidence") from exc
        except RelationshipDataUnavailable as exc:
            raise _relationship_data_error(exc) from exc

    @application.patch(
        "/v1/workspaces/{workspace_id}/relationships/{relationship_id}",
        response_model=RelationshipResponse,
    )
    async def update_relationship(
        workspace_id: UUID,
        relationship_id: UUID,
        body: RelationshipUpdateRequest,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> EvidenceRelationship:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        if context.role not in {"owner", "admin"}:
            raise HTTPException(status_code=403, detail="Workspace owner access is required")
        try:
            relationship = await _with_account_gate(
                request,
                auth,
                lambda: _relationship_repository(request).update_relationship(
                    auth.access_token,
                    context.workspace_id,
                    relationship_id,
                    body.model_dump(exclude_unset=True),
                ),
            )
        except RelationshipAccessDenied as exc:
            raise HTTPException(status_code=403, detail="Relationship write is not authorized") from exc
        except RelationshipConflict as exc:
            raise HTTPException(status_code=409, detail="The relationship conflicts with existing evidence") from exc
        except RelationshipDataUnavailable as exc:
            raise _relationship_data_error(exc) from exc
        if relationship is None:
            raise HTTPException(status_code=404, detail="Relationship not found")
        return relationship

    @application.delete(
        "/v1/workspaces/{workspace_id}/relationships/{relationship_id}",
        status_code=204,
    )
    async def delete_relationship(
        workspace_id: UUID,
        relationship_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> Response:
        context = await _resolve_workspace_or_404(request, auth, workspace_id)
        if context.role not in {"owner", "admin"}:
            raise HTTPException(status_code=403, detail="Workspace owner access is required")
        try:
            deleted = await _with_account_gate(
                request,
                auth,
                lambda: _relationship_repository(request).delete_relationship(
                    auth.access_token, context.workspace_id, relationship_id
                ),
            )
        except RelationshipAccessDenied as exc:
            raise HTTPException(status_code=403, detail="Relationship write is not authorized") from exc
        except RelationshipDataUnavailable as exc:
            raise _relationship_data_error(exc) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="Relationship not found")
        return Response(status_code=204)

    @application.get(
        "/v1/workspaces/{workspace_id}/sources/{source_id}/versions/{version_id}/content",
        response_class=Response,
    )
    async def get_source_content(
        workspace_id: UUID,
        source_id: UUID,
        version_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> Response:
        from evidence_layer.source_access import SourceReadError

        # Membership is checked once here (a non-member sees 404) and re-checked
        # inside the read path before the object is opened and before delivery.
        await _resolve_workspace_or_404(request, auth, workspace_id)

        try:
            async def read_snapshot():
                path = _secure_snapshot_read_path(request, auth)
                return await path.read(
                    auth,
                    workspace_id=workspace_id,
                    source_id=source_id,
                    version_id=version_id,
                )

            snapshot = await _with_account_gate(request, auth, read_snapshot)
        except SourceReadError as exc:
            raise _source_read_error(exc) from exc
        except ValueError as exc:
            # A Storage configuration that matches neither the loopback nor
            # the hosted adapter contract fails closed as unavailable.
            raise HTTPException(
                status_code=503, detail="Source storage is unavailable"
            ) from exc

        # The complete buffered body remains server-side until this final
        # authoritative read succeeds; no digest or bytes are emitted earlier.
        await _require_protected_access(request, auth)

        return Response(
            content=snapshot.content,
            media_type="application/octet-stream",
            headers={"Content-Digest": f"sha-256=:{snapshot.manifest.sha256}:"},
        )

    if application.state.remote_mcp_enabled:
        runtime = create_remote_mcp_runtime(_HostedRemoteMcpBackend(application), enabled=True)
        application.state.remote_mcp = runtime
        application.mount(REMOTE_MCP_PATH, runtime.app)
        previous_lifespan = application.router.lifespan_context

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            async with runtime.server.session_manager.run():
                async with previous_lifespan(app) as state:
                    yield state

        application.router.lifespan_context = lifespan

    return application


app = create_app()
