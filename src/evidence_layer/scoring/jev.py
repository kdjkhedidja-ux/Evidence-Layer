"""Optional Jev relevance scorer with a lazy TypeSafe SDK boundary."""

from __future__ import annotations

import importlib
from importlib.metadata import PackageNotFoundError, version
import hashlib
import math
import os
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn, Protocol

from evidence_layer.scoring.base import Scorer

DEFAULT_JEV_MODEL = "jev-latest"
DEFAULT_JEV_API_URL = "https://api.typesafe.ai"
_QUESTION_ID = "candidate_0"
_QUESTION_TEXT = (
    "Determine whether the referenced candidate contains useful evidence "
    "needed to answer the task query. Return only its relevance score from "
    "0 to 1."
)
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SCORE_PATTERN = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")
_COST_LABEL = "estimate from vendor-published pricing; not an invoice"


class JevScorerError(Exception):
    """Base class for safe Jev integration errors."""

    category = "provider_failure"


class JevConfigurationError(JevScorerError):
    category = "configuration_error"


class JevDependencyError(JevScorerError):
    category = "dependency_missing"


class JevProviderError(JevScorerError):
    category = "provider_failure"


class JevResponseError(JevScorerError):
    category = "invalid_response"


class JevTransferDeniedError(JevScorerError):
    """Safe denial when no exact benchmark transfer permit is available."""

    category = "transfer_denied"


def _deny_live_transfer() -> NoReturn:
    """Reject any request outside a one-use benchmark authorization."""
    raise JevTransferDeniedError(
        "JEV transfer requires an exact Real-World v1 benchmark authorization."
    )


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry settings used by the future development benchmark."""

    max_retries: int = 2
    backoff_seconds: float = 0.1

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_retries, bool)
            or not isinstance(self.max_retries, int)
            or not 0 <= self.max_retries <= 3
        ):
            raise ValueError("max_retries must be between 0 and 3")
        if (
            isinstance(self.backoff_seconds, bool)
            or not math.isfinite(self.backoff_seconds)
            or not 0 <= self.backoff_seconds <= 2
        ):
            raise ValueError("backoff_seconds must be between 0 and 2")


@dataclass(frozen=True)
class JevBenchmarkRecord:
    """Non-sensitive metadata only; query and evidence bodies are not fields."""

    run_id: str
    split: str
    case_id: str | None
    candidate_id: str | None
    candidate_count: int
    model_requested: str
    model_returned: str | None
    sdk_version: str | None
    timestamp_utc: str
    git_commit_sha: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    retry_count: int | None
    failure_category: str | None
    estimated_input_cost_usd: float | None
    input_cost_label: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class _SystemOneClient(Protocol):
    def system_one(
        self,
        state: dict[str, object],
        questions: dict[str, object],
        *,
        model: str,
        retry: object | None,
        timeout: float,
    ) -> object: ...


def _load_sdk() -> tuple[type, type, type]:
    try:
        sdk = importlib.import_module("typesafe_sdk")
        client_type = getattr(sdk, "TypeSafeClient")
        noul_type = getattr(sdk, "Noul")
        retry_policy_type = getattr(sdk, "RetryPolicy")
    except Exception:
        raise JevDependencyError(
            "Install the optional 'jev' extra to use live Jev scoring."
        ) from None
    return client_type, noul_type, retry_policy_type


def _sdk_retry_argument(retry_policy_type: type) -> object:
    """Disable hidden SDK retries so one scorer call is one API attempt."""
    return retry_policy_type(max_retries=0)


def _local_sdk_version() -> str | None:
    try:
        return version("typesafe-sdk")
    except PackageNotFoundError:
        return None


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_BENCHMARK_PERMIT_ISSUER = object()


class _RealWorldBenchmarkTransferPermit:
    """One-use authorization scoped to all fixed Real-World v1 score calls.

    The benchmark runner issues this only after the deterministic benchmark
    verifier succeeds. Each use is bound to a split, case, candidate, query,
    and candidate-text digest. It is deliberately unusable by the service's
    provider-selection policy, which remains lexical-only.
    """

    def __init__(
        self,
        *,
        run_id: str,
        input_sha256: str,
        calls: dict[tuple[str, str, str], tuple[str, str]],
        expires_at: datetime,
        _issuer: object,
    ) -> None:
        if _issuer is not _BENCHMARK_PERMIT_ISSUER:
            raise ValueError("benchmark transfer permits are issued by the verifier")
        self.run_id = run_id
        self.input_sha256 = input_sha256
        self.expires_at = expires_at
        self._calls = dict(calls)
        self._remaining = set(calls)
        self._lock = threading.Lock()

    @staticmethod
    def _key(split: str, case_id: str | None, candidate_id: str | None):
        if case_id is None or candidate_id is None:
            return None
        return split, case_id, candidate_id

    def allows(
        self,
        split: str,
        case_id: str | None,
        candidate_id: str | None,
        query: str,
        text: str,
    ) -> bool:
        key = self._key(split, case_id, candidate_id)
        if key is None or datetime.now(timezone.utc) >= self.expires_at:
            return False
        expected = self._calls.get(key)
        return expected == (_text_sha256(query), _text_sha256(text)) and key in self._remaining

    def includes(self, split: str, case_id: str | None, candidate_id: str | None) -> bool:
        key = self._key(split, case_id, candidate_id)
        return (
            key is not None
            and datetime.now(timezone.utc) < self.expires_at
            and key in self._remaining
        )

    def consume(
        self,
        split: str,
        case_id: str | None,
        candidate_id: str | None,
        query: str,
        text: str,
    ) -> bool:
        key = self._key(split, case_id, candidate_id)
        if key is None:
            return False
        with self._lock:
            if not self.allows(split, case_id, candidate_id, query, text):
                return False
            self._remaining.remove(key)
            return True

    @property
    def remaining_calls(self) -> int:
        return len(self._remaining)


def _issue_real_world_benchmark_permit(
    cases_by_split: Mapping[str, list[dict]],
    *,
    run_id: str,
    input_sha256: str,
) -> _RealWorldBenchmarkTransferPermit:
    """Issue a single-run permit only for the complete, fixed Real-World v1 set."""
    expected_split_sizes = {"dev": 22, "validation": 10, "exposed_test": 8}
    if set(cases_by_split) != set(expected_split_sizes):
        raise ValueError("the complete Real-World v1 split set is required")
    if not re.fullmatch(r"[0-9a-f]{64}", input_sha256):
        raise ValueError("input_sha256 must be a lowercase SHA-256 digest")
    calls: dict[tuple[str, str, str], tuple[str, str]] = {}
    for split, expected_size in expected_split_sizes.items():
        cases = cases_by_split[split]
        if len(cases) != expected_size:
            raise ValueError("the complete Real-World v1 split set is required")
        for case in cases:
            case_id = case.get("case_id")
            query = case.get("task", {}).get("input")
            candidates = case.get("candidates")
            if (
                not _valid_identifier(case_id)
                or not isinstance(query, str)
                or not isinstance(candidates, list)
                or len(candidates) != 5
            ):
                raise ValueError("the complete Real-World v1 candidate set is required")
            for candidate in candidates:
                candidate_id = candidate.get("candidate_id")
                text = candidate.get("text")
                key = (split, case_id, candidate_id)
                if (
                    not _valid_identifier(candidate_id)
                    or not isinstance(text, str)
                    or key in calls
                ):
                    raise ValueError("the complete Real-World v1 candidate set is required")
                calls[key] = (_text_sha256(query), _text_sha256(text))
    if len(calls) != 200:
        raise ValueError("the complete Real-World v1 candidate set is required")
    return _RealWorldBenchmarkTransferPermit(
        run_id=run_id,
        input_sha256=input_sha256,
        calls=calls,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=8),
        _issuer=_BENCHMARK_PERMIT_ISSUER,
    )


def _git_commit_sha() -> str | None:
    try:
        result = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=Path(__file__).resolve().parents[3],
            check=True,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit_sha = result.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40,64}", commit_sha):
        return commit_sha
    return None


def _valid_identifier(value: object) -> str | None:
    if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value):
        return value
    return None


def _safe_returned_model(
    value: object,
    requested_model: str,
    query: str,
    text: str,
    api_key: str | None,
) -> str | None:
    model = _valid_identifier(value)
    if model is None:
        return None
    model_folded = model.casefold()
    if model.casefold() != requested_model.casefold() and not model_folded.startswith(
        "jev-"
    ):
        return None
    if any(
        forbidden and forbidden.casefold() in model_folded
        for forbidden in (query, text, api_key)
    ):
        return None
    return model


def _optional_token_count(usage: object, name: str) -> int | None:
    if usage is None:
        return None
    try:
        count = getattr(usage, name)
    except Exception:
        return None
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return None
    return count


def _extract_score(response: object, question_id: str) -> float:
    try:
        nouls = getattr(response, "nouls")
        if not isinstance(nouls, Mapping) or question_id not in nouls:
            raise JevResponseError(
                "The Jev provider returned an invalid relevance response."
            )
        result = getattr(nouls[question_id], "noul")
    except JevScorerError:
        raise
    except Exception:
        raise JevResponseError(
            "The Jev provider returned an invalid relevance response."
        ) from None

    if isinstance(result, bool):
        raise JevResponseError(
            "The Jev provider returned an invalid relevance response."
        )
    if isinstance(result, str):
        if not _SCORE_PATTERN.fullmatch(result):
            raise JevResponseError(
                "The Jev provider returned an invalid relevance response."
            )
        try:
            score = float(result)
        except (OverflowError, ValueError):
            raise JevResponseError(
                "The Jev provider returned an invalid relevance response."
            ) from None
    elif isinstance(result, (int, float)):
        score = float(result)
    else:
        raise JevResponseError(
            "The Jev provider returned an invalid relevance response."
        )

    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise JevResponseError(
            "The Jev provider returned an invalid relevance response."
        )
    return score


class JevEvidenceScorer(Scorer):
    """Score one candidate, requiring a one-use Real-World v1 transfer permit.

    The normal application provider policy remains lexical-only. Live requests
    are available here only to the fixed benchmark runner, which issues a
    one-run permit after verifying the immutable Real-World v1 inputs.
    """

    name = "jev"

    def __init__(
        self,
        client: _SystemOneClient | None = None,
        noul_factory: Callable[[str], object] | None = None,
        model: str = DEFAULT_JEV_MODEL,
        timeout: float = 10.0,
        retry_policy: RetryPolicy = RetryPolicy(),
        split: str = "dev",
        case_id: str | None = None,
        candidate_id: str | None = None,
        transfer_permit: _RealWorldBenchmarkTransferPermit | None = None,
        sdk_retry_policy: object | None = None,
    ) -> None:
        if not _valid_identifier(model):
            raise ValueError("model must be a safe model identifier")
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a finite positive number")
        if split not in {"dev", "validation", "exposed_test"}:
            raise ValueError("split must be a Real-World v1 split")
        if case_id is not None and not _valid_identifier(case_id):
            raise ValueError("case_id must be a safe identifier")
        if candidate_id is not None and not _valid_identifier(candidate_id):
            raise ValueError("candidate_id must be a safe identifier")
        if transfer_permit is not None and not isinstance(
            transfer_permit, _RealWorldBenchmarkTransferPermit
        ):
            raise TypeError("transfer_permit must come from the benchmark verifier")
        if sdk_retry_policy is not None and getattr(
            sdk_retry_policy, "max_retries", None
        ) != 0:
            raise ValueError("JEV benchmark SDK retries must be disabled")
        if client is not None and sdk_retry_policy is None:
            raise ValueError("an injected SDK client requires an explicit retry policy")

        self.model = model
        self.timeout = timeout
        self.retry_policy = retry_policy
        self.split = split
        self.case_id = case_id
        self.candidate_id = candidate_id
        self._client = client
        self._noul_factory = noul_factory
        self._transfer_permit = transfer_permit
        self._sdk_retry_policy = sdk_retry_policy
        self._run_id = transfer_permit.run_id if transfer_permit else str(uuid.uuid4())
        self.last_record: JevBenchmarkRecord | None = None

    def _resolve_client(
        self, query: str, text: str
    ) -> tuple[_SystemOneClient, Callable[[str], object], str | None]:
        """Resolve the existing SDK only after exact benchmark scope validation."""
        permit = self._transfer_permit
        if permit is None or not permit.allows(
            self.split, self.case_id, self.candidate_id, query, text
        ):
            _deny_live_transfer()
        api_key: str | None = None
        if self._client is None or self._noul_factory is None:
            api_key = os.environ.get("TYPESAFE_API_KEY")
            if not api_key:
                raise JevConfigurationError(
                    "TYPESAFE_API_KEY is required for live Jev scoring."
                )
            client_type, noul_type, retry_policy_type = _load_sdk()
            if self._sdk_retry_policy is None:
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
                    raise JevProviderError("The Jev provider request failed.") from None
            if self._noul_factory is None:
                self._noul_factory = lambda instructions: noul_type(
                    instructions=instructions
                )
        return self._client, self._noul_factory, api_key

    def _make_record(
        self,
        *,
        model_returned: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        latency_ms: int = 0,
        retry_count: int | None = None,
        failure_category: str | None = None,
        sdk_version: str | None = None,
    ) -> JevBenchmarkRecord:
        return JevBenchmarkRecord(
            run_id=self._run_id,
            split=self.split,
            case_id=self.case_id,
            candidate_id=self.candidate_id,
            candidate_count=1,
            model_requested=self.model,
            model_returned=model_returned,
            sdk_version=sdk_version if sdk_version is not None else _local_sdk_version(),
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            git_commit_sha=_git_commit_sha(),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=max(0, latency_ms),
            retry_count=retry_count,
            failure_category=failure_category,
            estimated_input_cost_usd=None,
            input_cost_label=None,
        )

    def score(self, query: str, text: str) -> float:
        started = time.monotonic()
        self.last_record = None
        permit = self._transfer_permit
        if permit is None or not permit.allows(
            self.split, self.case_id, self.candidate_id, query, text
        ):
            self.last_record = JevBenchmarkRecord(
                run_id=self._run_id,
                split=self.split,
                case_id=self.case_id,
                candidate_id=self.candidate_id,
                candidate_count=1,
                model_requested=self.model,
                model_returned=None,
                sdk_version=None,
                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                git_commit_sha=None,
                input_tokens=None,
                output_tokens=None,
                latency_ms=0,
                retry_count=None,
                failure_category=JevTransferDeniedError.category,
                estimated_input_cost_usd=None,
                input_cost_label=None,
            )
            _deny_live_transfer()
        try:
            client, noul_factory, api_key = self._resolve_client(query, text)
            state: dict[str, object] = {
                "query": query,
                "candidates": {_QUESTION_ID: text},
            }
            instructions = f"{_QUESTION_TEXT} Candidate identifier: {_QUESTION_ID}."
            questions = {_QUESTION_ID: noul_factory(instructions)}
            response = self._system_one(
                client,
                state,
                questions,
                model=self.model,
                retry=None,
                timeout=self.timeout,
                transfer_permit=permit,
                split=self.split,
                case_id=self.case_id,
                candidate_id=self.candidate_id,
                query=query,
                text=text,
                sdk_retry=self._sdk_retry_policy,
            )
            score = _extract_score(response, _QUESTION_ID)
            returned_model = getattr(response, "model", None)
            usage = getattr(response, "usage", None)
            self.last_record = self._make_record(
                model_returned=_safe_returned_model(
                    returned_model, self.model, query, text, api_key
                ),
                input_tokens=_optional_token_count(usage, "input_tokens"),
                output_tokens=_optional_token_count(usage, "output_tokens"),
                latency_ms=int((time.monotonic() - started) * 1000),
                retry_count=0,
            )
            return score
        except JevScorerError as error:
            self.last_record = self._make_record(
                latency_ms=int((time.monotonic() - started) * 1000),
                retry_count=0,
                failure_category=error.category,
            )
            raise
        except Exception:
            error = JevProviderError("The Jev provider request failed.")
            self.last_record = self._make_record(
                latency_ms=int((time.monotonic() - started) * 1000),
                retry_count=0,
                failure_category=error.category,
            )
            raise error from None

    @staticmethod
    def _system_one(
        client: _SystemOneClient,
        state: dict[str, object],
        questions: dict[str, object],
        *,
        model: str,
        retry: object | None,
        timeout: float,
        transfer_permit: _RealWorldBenchmarkTransferPermit,
        split: str,
        case_id: str | None,
        candidate_id: str | None,
        query: str,
        text: str,
        sdk_retry: object | None = None,
    ) -> object:
        """Consume the exact one-use permit immediately before the SDK call."""
        if not transfer_permit.consume(split, case_id, candidate_id, query, text):
            _deny_live_transfer()
        return client.system_one(
            state,
            questions,
            model=model,
            retry=sdk_retry if sdk_retry is not None else retry,
            timeout=timeout,
        )


def render_benchmark_csv(records: list[JevBenchmarkRecord]) -> str:
    """Render only the fixed, non-sensitive record schema as CSV."""
    import csv
    import io

    output = io.StringIO(newline="")
    fieldnames = list(JevBenchmarkRecord.__dataclass_fields__)
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for record in records:
        writer.writerow(record.to_dict())
    return output.getvalue()


def write_benchmark_csv(
    records: list[JevBenchmarkRecord], filename: str
) -> Path:
    """Write reports only below the repository's ignored reports directory."""
    reports_dir = Path(__file__).resolve().parents[3] / "reports"
    target = (reports_dir / filename).resolve()
    if target.parent != reports_dir.resolve() or not filename.endswith(".csv"):
        raise ValueError("benchmark reports must be CSV files directly under reports/")
    reports_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(render_benchmark_csv(records), encoding="utf-8")
    return target
