"""HTTP application for health, verified identity, and workspace boundaries."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal, TypeVar
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    SupabaseTokenVerifier,
    TokenVerifier,
    VerifiedTokenVerifier,
)
from evidence_service.config import ConfigurationError, SupabaseSettings
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
from evidence_service.recovery_fence import (
    CloudflareRecoveryFenceReader,
    RecoveryFenceConfigurationError,
    RecoveryFenceReader,
)
from evidence_service.supabase_repository import (
    SupabaseWorkspaceRepository,
    WorkspaceDataUnavailable,
)
from evidence_service.tenant_context import (
    WorkspaceAccessDenied,
    WorkspaceContext,
    WorkspaceDirectory,
    resolve_workspace_context,
)


class WorkspaceSummary(BaseModel):
    id: UUID
    name: str
    role: Literal["owner", "member"]


class MeResponse(BaseModel):
    user_id: UUID
    issuer: str
    workspaces: list[WorkspaceSummary]


class WorkspaceResponse(BaseModel):
    id: UUID
    name: str
    role: Literal["owner", "member"]


class SourceVersionSummary(BaseModel):
    source_id: UUID
    version_id: UUID
    state: Literal["staged", "published"]
    sha256: str
    size_bytes: int
    created_at: datetime


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
        raise HTTPException(status_code=503, detail="Service is unavailable") from exc
    if is_open is not True:
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
    await _require_protected_access(request, authenticated_request)
    return authenticated_request


async def require_control_request(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedRequest:
    """Authenticate only: deletion status and acceptance replay stay reachable.

    These two control operations must remain callable while a revocation is
    pending, otherwise the owner cannot observe status or replay an accepted
    request. The repository query is still requester-scoped and RLS-bound.
    """
    return await _verify_bearer(request, credentials)


def _workspace_directory(request: Request) -> WorkspaceDirectory:
    directory = request.app.state.workspace_directory
    if directory is None:
        directory = SupabaseWorkspaceRepository(_settings_for(request))
        request.app.state.workspace_directory = directory
    return directory


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


def create_app(
    settings: SupabaseSettings | None = None,
    *,
    token_verifier: TokenVerifier | None = None,
    account_lifecycle_reader: AccountLifecycleReader | None = None,
    recovery_fence_reader: RecoveryFenceReader | None = None,
    workspace_directory: WorkspaceDirectory | None = None,
    evidence_repository: EvidenceRepository | None = None,
    relationship_repository: RelationshipRepository | None = None,
    deletion_repository: DeletionRepository | None = None,
    source_read_path=None,
    source_catalog_factory=None,
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
    application.state.account_lifecycle_reader = account_lifecycle_reader
    application.state.recovery_fence_reader = recovery_fence_reader
    application.state.workspace_directory = workspace_directory
    application.state.evidence_repository = evidence_repository
    application.state.relationship_repository = relationship_repository
    application.state.deletion_repository = deletion_repository
    application.state.trusted_source_context_issuer = None
    application.state.source_read_path = source_read_path
    application.state.source_catalog_factory = source_catalog_factory

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
        try:
            memberships = await _with_account_gate(
                request,
                auth,
                lambda: _workspace_directory(request).list_memberships(
                    auth.principal, auth.access_token
                ),
            )
        except WorkspaceDataUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Workspace directory is unavailable"
            ) from exc

        return MeResponse(
            user_id=auth.principal.subject,
            issuer=auth.principal.issuer,
            workspaces=[
                WorkspaceSummary(
                    id=membership.workspace_id,
                    name=membership.workspace_name,
                    role=membership.role,
                )
                for membership in memberships
            ],
        )

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
        if context.role != "owner":
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
        if context.role != "owner":
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
        if context.role != "owner":
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
        if context.role != "owner":
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
        if context.role != "owner":
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
        if context.role != "owner":
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

    return application


app = create_app()
