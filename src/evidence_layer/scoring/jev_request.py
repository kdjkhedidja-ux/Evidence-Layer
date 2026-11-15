"""Request-scoped JEV scoring behind a trusted-host transfer permit."""

from __future__ import annotations

import math
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import NoReturn

from evidence_layer.scoring.jev import (
    DEFAULT_JEV_API_URL,
    DEFAULT_JEV_MODEL,
    JevConfigurationError,
    JevProviderError,
    JevResponseError,
    JevScorerError,
    JevTransferDeniedError,
    _QUESTION_TEXT,
    _SystemOneClient,
    _extract_score,
    _load_sdk,
    _sdk_retry_argument,
    _text_sha256,
    _valid_identifier,
)


@dataclass(frozen=True)
class JevRequestCandidate:
    candidate_id: str
    source_id: str
    text: str


_REQUEST_PERMIT_ISSUER = object()


class _JevRequestTransferPermit:
    """One-use permit for a trusted host's exact, explicitly opted-in request."""

    def __init__(
        self,
        *,
        request_id: str,
        principal_id: str,
        tenant_id: str,
        query_sha256: str,
        candidates: dict[str, tuple[str, str]],
        expires_at: datetime,
        _issuer: object,
    ) -> None:
        if _issuer is not _REQUEST_PERMIT_ISSUER:
            raise ValueError("JEV request permits are issued by the trusted host")
        self.request_id = request_id
        self.principal_id = principal_id
        self.tenant_id = tenant_id
        self._query_sha256 = query_sha256
        self._candidates = dict(candidates)
        self._remaining = set(candidates)
        self.expires_at = expires_at
        self._lock = threading.Lock()

    def allows_batch(
        self, query: str, candidates: list[JevRequestCandidate]
    ) -> bool:
        if datetime.now(timezone.utc) >= self.expires_at:
            return False
        expected: dict[str, tuple[str, str]] = {}
        for candidate in candidates:
            if candidate.candidate_id in expected:
                return False
            expected[candidate.candidate_id] = (
                candidate.source_id,
                _text_sha256(candidate.text),
            )
        return (
            _text_sha256(query) == self._query_sha256
            and len(expected) == len(candidates)
            and expected == self._candidates
            and set(expected) == self._remaining
        )

    def consume(self, query: str, candidate: JevRequestCandidate) -> bool:
        with self._lock:
            if datetime.now(timezone.utc) >= self.expires_at:
                return False
            expected = self._candidates.get(candidate.candidate_id)
            if (
                candidate.candidate_id not in self._remaining
                or _text_sha256(query) != self._query_sha256
                or expected
                != (candidate.source_id, _text_sha256(candidate.text))
            ):
                return False
            self._remaining.remove(candidate.candidate_id)
            return True


def _issue_jev_request_transfer_permit(
    *,
    request_id: str,
    principal_id: str,
    tenant_id: str,
    query: str,
    candidates: list[JevRequestCandidate],
    server_enabled: bool,
    permitted_by_request: bool,
) -> _JevRequestTransferPermit:
    """Issue a one-use permit only after host config and per-request consent."""
    if server_enabled is not True or permitted_by_request is not True:
        raise JevTransferDeniedError("JEV transfer is not authorized.")
    if (
        not _valid_identifier(request_id)
        or not _valid_identifier(principal_id)
        or not _valid_identifier(tenant_id)
        or not query
        or len(query) > 8_000
        or not 1 <= len(candidates) <= 50
    ):
        raise JevTransferDeniedError("JEV transfer is not authorized.")
    candidate_scope: dict[str, tuple[str, str]] = {}
    for candidate in candidates:
        if (
            not _valid_identifier(candidate.candidate_id)
            or not _valid_identifier(candidate.source_id)
            or not candidate.text
            or len(candidate.text) > 2_400
            or candidate.candidate_id in candidate_scope
        ):
            raise JevTransferDeniedError("JEV transfer is not authorized.")
        candidate_scope[candidate.candidate_id] = (
            candidate.source_id,
            _text_sha256(candidate.text),
        )
    return _JevRequestTransferPermit(
        request_id=request_id,
        principal_id=principal_id,
        tenant_id=tenant_id,
        query_sha256=_text_sha256(query),
        candidates=candidate_scope,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=2),
        _issuer=_REQUEST_PERMIT_ISSUER,
    )


def _deny_request_transfer() -> NoReturn:
    raise JevTransferDeniedError("JEV request transfer is not authorized.")


class JevRequestScorer:
    """Score an exact request candidate batch after trusted-host authorization.

    The application must issue the permit from verified identity/workspace
    context after explicit per-request consent. SDK retries are disabled.
    """

    def __init__(
        self,
        *,
        client: _SystemOneClient | None = None,
        noul_factory: Callable[[str], object] | None = None,
        model: str = DEFAULT_JEV_MODEL,
        timeout: float = 8.0,
        sdk_retry_policy: object | None = None,
    ) -> None:
        if not _valid_identifier(model):
            raise ValueError("model must be a safe model identifier")
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a finite positive number")
        if client is not None and (
            noul_factory is None
            or getattr(sdk_retry_policy, "max_retries", None) != 0
        ):
            raise ValueError("an injected SDK client requires disabled retries")
        self.model = model
        self.timeout = timeout
        self._client = client
        self._noul_factory = noul_factory
        self._sdk_retry_policy = sdk_retry_policy

    def _resolve_client(
        self,
    ) -> tuple[_SystemOneClient, Callable[[str], object]]:
        if self._client is None or self._noul_factory is None:
            api_key = os.environ.get("TYPESAFE_API_KEY")
            if not api_key:
                raise JevConfigurationError(
                    "TYPESAFE_API_KEY is required for JEV scoring."
                )
            client_type, noul_type, retry_policy_type = _load_sdk()
            self._sdk_retry_policy = _sdk_retry_argument(retry_policy_type)
            if self._client is None:
                try:
                    self._client = client_type(
                        api_key=api_key,
                        base_url=DEFAULT_JEV_API_URL,
                        timeout=self.timeout,
                        retry=self._sdk_retry_policy,
                    )
                except Exception:
                    raise JevProviderError("The JEV provider request failed.") from None
            if self._noul_factory is None:
                self._noul_factory = lambda instructions: noul_type(
                    instructions=instructions
                )
        return self._client, self._noul_factory

    def score_candidates(
        self,
        query: str,
        candidates: list[JevRequestCandidate],
        *,
        transfer_permit: _JevRequestTransferPermit,
    ) -> tuple[float, ...]:
        if not isinstance(transfer_permit, _JevRequestTransferPermit) or not (
            transfer_permit.allows_batch(query, candidates)
        ):
            _deny_request_transfer()
        client, noul_factory = self._resolve_client()
        for candidate in candidates:
            if not transfer_permit.consume(query, candidate):
                _deny_request_transfer()
        state: dict[str, object] = {
            "query": query,
            "candidates": {
                candidate.candidate_id: candidate.text for candidate in candidates
            },
        }
        questions = {
            candidate.candidate_id: noul_factory(
                f"{_QUESTION_TEXT} Candidate identifier: {candidate.candidate_id}."
            )
            for candidate in candidates
        }
        try:
            response = client.system_one(
                state,
                questions,
                model=self.model,
                retry=self._sdk_retry_policy,
                timeout=self.timeout,
            )
        except Exception:
            raise JevProviderError("The JEV provider request failed.") from None
        returned_nouls = getattr(response, "nouls", None)
        if not isinstance(returned_nouls, Mapping) or set(returned_nouls) != {
            candidate.candidate_id for candidate in candidates
        }:
            raise JevResponseError(
                "The Jev provider returned an invalid relevance response."
            )
        scores = tuple(
            _extract_score(response, candidate.candidate_id)
            for candidate in candidates
        )
        if transfer_permit._remaining:
            _deny_request_transfer()
        return scores
