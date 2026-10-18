"""HTTP application for health, verified identity, and workspace boundaries."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, model_validator

from evidence_service.auth import (
    AuthenticatedRequest,
    AuthenticationRejected,
    AuthenticationUnavailable,
    SupabaseTokenVerifier,
    TokenVerifier,
)
from evidence_service.config import ConfigurationError, SupabaseSettings
from evidence_service.evidence_repository import (
    EvidenceAccessDenied,
    EvidenceDataUnavailable,
    EvidenceRecord,
    EvidenceRepository,
    SupabaseEvidenceRepository,
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


class EvidenceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=65_536)
    source_label: str | None = Field(default=None, max_length=512)


class EvidenceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: str | None = Field(default=None, min_length=1, max_length=65_536)
    source_label: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def require_update_fields(self) -> EvidenceUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("At least one evidence field must be provided")
        if "content" in self.model_fields_set and self.content is None:
            raise ValueError("Evidence content cannot be null")
        return self


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    content: str
    source_label: str | None
    created_at: datetime
    updated_at: datetime


bearer_scheme = HTTPBearer(auto_error=False)


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


async def require_authenticated_request(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedRequest:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="A valid bearer access token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    settings = _settings_for(request)
    verifier = request.app.state.token_verifier
    if verifier is None:
        verifier = SupabaseTokenVerifier(settings)
        request.app.state.token_verifier = verifier

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


async def _resolve_workspace_or_404(
    request: Request,
    auth: AuthenticatedRequest,
    workspace_id: UUID,
) -> WorkspaceContext:
    try:
        return await resolve_workspace_context(
            _workspace_directory(request),
            auth.principal,
            auth.access_token,
            workspace_id,
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


def create_app(
    settings: SupabaseSettings | None = None,
    *,
    token_verifier: TokenVerifier | None = None,
    workspace_directory: WorkspaceDirectory | None = None,
    evidence_repository: EvidenceRepository | None = None,
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
    application.state.workspace_directory = workspace_directory
    application.state.evidence_repository = evidence_repository

    @application.api_route(
        "/healthz", methods=["GET"], status_code=200, include_in_schema=False
    )
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/v1/me", response_model=MeResponse)
    async def me(
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> MeResponse:
        try:
            memberships = await _workspace_directory(request).list_memberships(
                auth.principal, auth.access_token
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

    @application.get("/v1/workspaces/{workspace_id}", response_model=WorkspaceResponse)
    async def get_workspace(
        workspace_id: UUID,
        request: Request,
        auth: AuthenticatedRequest = Depends(require_authenticated_request),
    ) -> WorkspaceResponse:
        try:
            context = await resolve_workspace_context(
                _workspace_directory(request),
                auth.principal,
                auth.access_token,
                workspace_id,
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
            return await _evidence_repository(request).list_records(
                auth.access_token, context.workspace_id
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
        try:
            return await _evidence_repository(request).create_record(
                auth.access_token,
                context.workspace_id,
                content=body.content,
                source_label=body.source_label,
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
            record = await _evidence_repository(request).get_record(
                auth.access_token, context.workspace_id, evidence_id
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
        try:
            record = await _evidence_repository(request).update_record(
                auth.access_token,
                context.workspace_id,
                evidence_id,
                body.model_dump(exclude_unset=True),
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
        try:
            deleted = await _evidence_repository(request).delete_record(
                auth.access_token, context.workspace_id, evidence_id
            )
        except EvidenceAccessDenied as exc:
            raise _evidence_access_error(exc) from exc
        except EvidenceDataUnavailable as exc:
            raise _evidence_data_error(exc) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="Evidence record not found")
        return Response(status_code=204)

    return application


app = create_app()
