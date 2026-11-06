"""Offline orchestration contract for fake or explicitly authorized clients.

No concrete provider SDK client is implemented here. Tests use fake clients and
synthetic response mappings only.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable, Literal, Mapping, Protocol

from evidence_layer.provider_usage import (
    UsageIdentity,
    parse_anthropic_usage,
    parse_deepseek_chat_usage,
    parse_openai_responses_usage,
    visible_text_for,
)
from evidence_layer.output_policy import OutputPolicy, cap_status, visible_target_status
from evidence_layer.usage_measurement import EventCost, PricingSnapshot, UsageEvent, cost_for_event

ProviderName = Literal["anthropic", "openai", "deepseek"]
ProviderOutcome = Literal["success", "refusal", "partial"]


@dataclass(frozen=True)
class ProviderReply:
    """Metadata and a synthetic/provider payload; payload is never persisted."""

    payload: Mapping[str, object]
    outcome: ProviderOutcome = "success"


class ProviderClient(Protocol):
    async def generate(self, request: Mapping[str, object]) -> ProviderReply: ...


class ProviderRequestError(Exception):
    """Sanitized transport/provider error category, never an error message."""

    def __init__(
        self,
        category: str,
        http_status: int | None = None,
        provider_error_code: str | None = None,
    ) -> None:
        if category not in {"timeout", "request_rejected", "transport_error", "unknown_error", "rate_limited"}:
            raise ValueError("unsupported provider error category")
        self.category = category
        self.http_status = http_status
        self.provider_error_code = provider_error_code


@dataclass(frozen=True)
class ProviderJob:
    job_ordinal: int
    run_id: str
    case_id: str
    arm_id: str
    provider_id: ProviderName
    requested_model_id: str
    pricing: PricingSnapshot | None
    client: ProviderClient
    usage_source: Literal["provider_reported", "synthetic"] = "provider_reported"
    visible_token_counter: Callable[[str], int] | None = None
    visible_output_tokenizer_id: str | None = None
    output_policy: OutputPolicy | None = None
    native_token_counter: Callable[[str], int] | None = None
    native_tokenizer_id: str | None = None
    prompt: str | None = None


@dataclass(frozen=True)
class ProviderAttempt:
    run_id: str
    case_id: str
    arm_id: str
    attempt_id: str
    attempt_ordinal: int
    attempt_index: int
    requested_model_id: str
    returned_model_id: str | None
    provider_id: ProviderName
    outcome: Literal["success", "failure", "refusal"]
    safe_error_category: str | None
    usage_parse_status: Literal["parsed", "missing", "invariant_failure", "not_available"]
    usage: UsageEvent
    cost: EventCost
    answer_sha256: str | None = None
    http_status: int | None = None
    provider_error_code: str | None = None
    visible_target_status: str = "unmeasured"
    cap_status: str = "unmeasured"


Parser = Callable[[Mapping[str, object], UsageIdentity], UsageEvent]
PARSERS: dict[ProviderName, Parser] = {
    "anthropic": parse_anthropic_usage,
    "openai": parse_openai_responses_usage,
    "deepseek": parse_deepseek_chat_usage,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_failure_usage(
    identity: UsageIdentity,
    *,
    provider_id: str,
) -> UsageEvent:
    return UsageEvent(
        run_id=identity.run_id,
        case_id=identity.case_id,
        arm_id=identity.arm_id,
        model_id=identity.model_id,
        provider_id=provider_id,
        stage=identity.stage,
        attempt_id=identity.attempt_id,
        started_at=identity.started_at,
        finished_at=identity.finished_at,
        input_tokens=None,
        provider_output_tokens=None,
        reasoning_tokens=None,
        visible_output_tokens=None,
        visible_output_tokenizer_id=None,
        output_token_measurement_status="unknown",
        cached_input_tokens=None,
        cache_write_5m_tokens=None,
        cache_write_1h_tokens=None,
        cache_write_30m_tokens=None,
        cache_write_12h_tokens=None,
        calls=1,
        retries=identity.retries,
        outcome="failure",
        token_status="unknown",
        pricing_version=identity.pricing_version,
    )


async def run_provider_job(
    job: ProviderJob,
    *,
    max_attempts: int = 2,
    clock: Callable[[], str] = _now,
    timestamp_factory: Callable[[ProviderJob, int, str], str] | None = None,
) -> list[ProviderAttempt]:
    if max_attempts < 1 or job.job_ordinal < 0:
        raise ValueError("max_attempts must be positive and job_ordinal non-negative")
    parser = PARSERS[job.provider_id]
    attempts: list[ProviderAttempt] = []

    for attempt_index in range(max_attempts):
        attempt_ordinal = job.job_ordinal * max_attempts + attempt_index + 1
        attempt_id = (
            f"{job.run_id}:{job.case_id}:{job.arm_id}:"
            f"{job.provider_id}:{job.requested_model_id}:{attempt_index + 1}"
        )
        started_at = (
            timestamp_factory(job, attempt_index, "start")
            if timestamp_factory else clock()
        )
        identity = UsageIdentity(
            run_id=job.run_id,
            case_id=job.case_id,
            arm_id=job.arm_id,
            model_id=job.requested_model_id,
            stage="answer",
            attempt_id=attempt_id,
            started_at=started_at,
            finished_at=started_at,
            pricing_version=job.pricing.pricing_version if job.pricing else None,
            retries=1 if attempt_index else 0,
            usage_source=job.usage_source,
            visible_token_counter=job.visible_token_counter,
            visible_output_tokenizer_id=job.visible_output_tokenizer_id,
        )

        try:
            reply = await job.client.generate({
                "run_id": job.run_id,
                "case_id": job.case_id,
                "arm_id": job.arm_id,
                "model": job.requested_model_id,
                "hard_output_cap": job.output_policy.hard_output_cap if job.output_policy else None,
                "prompt": job.prompt,
            })
        except ProviderRequestError as exc:
            finished_at = (
                timestamp_factory(job, attempt_index, "finish")
                if timestamp_factory else clock()
            )
            identity = UsageIdentity(**{**identity.__dict__, "finished_at": finished_at})
            usage = _empty_failure_usage(identity, provider_id=job.provider_id)
            cost = cost_for_event(usage, job.pricing)
            attempts.append(ProviderAttempt(
                run_id=job.run_id,
                case_id=job.case_id,
                arm_id=job.arm_id,
                attempt_id=attempt_id,
                attempt_ordinal=attempt_ordinal,
                attempt_index=attempt_index,
                requested_model_id=job.requested_model_id,
                returned_model_id=None,
                provider_id=job.provider_id,
                outcome="failure",
                safe_error_category=exc.category,
                usage_parse_status="not_available",
                usage=usage,
                cost=cost,
                http_status=exc.http_status,
                provider_error_code=exc.provider_error_code,
            ))
            if exc.category == "timeout" and attempt_index + 1 < max_attempts:
                continue
            break
        except Exception:
            finished_at = (
                timestamp_factory(job, attempt_index, "finish")
                if timestamp_factory else clock()
            )
            identity = UsageIdentity(**{**identity.__dict__, "finished_at": finished_at})
            usage = _empty_failure_usage(identity, provider_id=job.provider_id)
            cost = cost_for_event(usage, job.pricing)
            attempts.append(ProviderAttempt(
                run_id=job.run_id,
                case_id=job.case_id,
                arm_id=job.arm_id,
                attempt_id=attempt_id,
                attempt_ordinal=attempt_ordinal,
                attempt_index=attempt_index,
                requested_model_id=job.requested_model_id,
                returned_model_id=None,
                provider_id=job.provider_id,
                outcome="failure",
                safe_error_category="unknown_error",
                usage_parse_status="not_available",
                usage=usage,
                cost=cost,
            ))
            break

        finished_at = (
            timestamp_factory(job, attempt_index, "finish")
            if timestamp_factory else clock()
        )
        returned_model = reply.payload.get("model")
        returned_model_id = returned_model if isinstance(returned_model, str) else None
        identity = UsageIdentity(**{**identity.__dict__, "finished_at": finished_at})

        if reply.outcome == "partial":
            usage = _empty_failure_usage(identity, provider_id=job.provider_id)
            cost = cost_for_event(usage, job.pricing)
            attempts.append(ProviderAttempt(
                job.run_id, job.case_id, job.arm_id, attempt_id, attempt_ordinal,
                attempt_index, job.requested_model_id, returned_model_id,
                job.provider_id, "failure", "partial_response", "not_available", usage, cost,
            ))
            break

        try:
            usage = parser(reply.payload, identity)
        except (ValueError, TypeError, KeyError):
            usage = _empty_failure_usage(identity, provider_id=job.provider_id)
            cost = cost_for_event(usage, job.pricing)
            attempts.append(ProviderAttempt(
                job.run_id, job.case_id, job.arm_id, attempt_id, attempt_ordinal,
                attempt_index, job.requested_model_id, returned_model_id,
                job.provider_id, "failure", "usage_invariant_failure", "invariant_failure", usage, cost,
            ))
            break
        except OSError:
            # Measurement is unavailable (e.g. tokenizer missing or hash rejected).
            # Fail closed: never record this response as a successful measured answer.
            usage = _empty_failure_usage(identity, provider_id=job.provider_id)
            cost = cost_for_event(usage, job.pricing)
            attempts.append(ProviderAttempt(
                job.run_id, job.case_id, job.arm_id, attempt_id, attempt_ordinal,
                attempt_index, job.requested_model_id, returned_model_id,
                job.provider_id, "failure", "measurement_unavailable", "not_available", usage, cost,
            ))
            break

        if job.native_token_counter is not None:
            native_text = visible_text_for(job.provider_id, reply.payload)
            if native_text is not None:
                usage = replace(
                    usage,
                    native_visible_output_tokens=job.native_token_counter(native_text),
                    native_visible_output_tokenizer_id=job.native_tokenizer_id,
                )

        safe_error = None
        outcome: Literal["success", "failure", "refusal"] = (
            "refusal" if reply.outcome == "refusal" else "success"
        )
        if returned_model_id is None:
            outcome, safe_error = "failure", "returned_model_missing"
        elif returned_model_id != job.requested_model_id:
            outcome, safe_error = "failure", "returned_model_mismatch"
        policy_visible = "unmeasured"
        policy_cap = "unmeasured"
        if job.output_policy is not None:
            policy_visible = visible_target_status(usage.visible_output_tokens, job.output_policy)
            policy_cap = cap_status(usage.provider_output_tokens, job.output_policy)
            if outcome == "success" and policy_cap == "cap_reached":
                outcome, safe_error = "failure", "output_cap_reached"
        cost = cost_for_event(usage, job.pricing)
        answer = reply.payload.get("output_text")
        answer_sha256 = (
            hashlib.sha256(answer.encode("utf-8")).hexdigest()
            if isinstance(answer, str) and reply.outcome != "partial"
            else None
        )
        attempts.append(ProviderAttempt(
            job.run_id,
            job.case_id,
            job.arm_id,
            attempt_id,
            attempt_ordinal,
            attempt_index,
            job.requested_model_id,
            returned_model_id,
            job.provider_id,
            outcome,
            safe_error,
            "parsed" if usage.token_status == "actual" else "missing",
            usage,
            cost,
            answer_sha256=answer_sha256,
            visible_target_status=policy_visible,
            cap_status=policy_cap,
        ))
        break

    return attempts


async def run_provider_batch(
    jobs: list[ProviderJob],
    *,
    max_attempts: int = 2,
    clock: Callable[[], str] = _now,
    timestamp_factory: Callable[[ProviderJob, int, str], str] | None = None,
) -> list[ProviderAttempt]:
    """Run jobs concurrently while deriving ordinals from fixed job slots."""
    ordinals = [job.job_ordinal for job in jobs]
    if len(set(ordinals)) != len(ordinals):
        raise ValueError("job_ordinal must be unique within a run")
    results = await asyncio.gather(*(
        run_provider_job(
            job,
            max_attempts=max_attempts,
            clock=clock,
            timestamp_factory=timestamp_factory,
        )
        for job in sorted(jobs, key=lambda item: item.job_ordinal)
    ))
    return [attempt for job_attempts in results for attempt in job_attempts]
