import asyncio
from decimal import Decimal

import pytest

from evidence_layer.provider_execution import (
    ProviderJob,
    ProviderReply,
    ProviderRequestError,
    run_provider_batch,
    run_provider_job,
)
from evidence_layer.output_policy import OutputPolicy
from evidence_layer.usage_measurement import PricingSnapshot


class FakeClient:
    def __init__(self, *script):
        self.script = list(script)
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        if not self.script:
            raise AssertionError("unexpected fake client call")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if asyncio.iscoroutine(item):
            return await item
        return item


def openai_pricing() -> PricingSnapshot:
    return PricingSnapshot(
        "openai-candidate-v1", Decimal("1"), Decimal("2"), Decimal("0.1"),
        cache_write_30m_per_million=Decimal("1.25"),
        cache_write_12h_per_million=Decimal("2.5"),
        cache_write_5m_billing="not_applicable",
        cache_write_1h_billing="not_applicable",
        cache_write_30m_billing="separately_metered",
        cache_write_12h_billing="separately_metered",
    )


def reply(*, outcome="success", model="openai-model-a", usage=None, content="synthetic answer"):
    return ProviderReply({
        "model": model,
        "usage": usage if usage is not None else {
            "input_tokens": 100,
            "output_tokens": 20,
            "input_tokens_details": {
                "cached_tokens": 10,
                "cache_write_tokens": 30,
                "cache_write_tokens_details": {"ttl_30m_tokens": 20, "ttl_12h_tokens": 10},
            },
        },
        "output_text": content,
    }, outcome=outcome)


def job(client, *, ordinal=0, provider="openai", model="openai-model-a"):
    return ProviderJob(
        job_ordinal=ordinal,
        run_id="fake-run",
        case_id=f"case-{ordinal}",
        arm_id="evidence_layer",
        provider_id=provider,
        requested_model_id=model,
        pricing=openai_pricing() if provider == "openai" else None,
        client=client,
        usage_source="synthetic",
        visible_token_counter=lambda text: len(text.split()),
        visible_output_tokenizer_id="synthetic-whitespace-v1",
    )


def clock():
    ticks = iter(("start", "end", "retry-start", "retry-end", "third-start", "third-end"))
    return lambda: next(ticks)


def test_fake_success_parses_usage_cost_and_keeps_content_out_of_record() -> None:
    client = FakeClient(reply(content="private synthetic answer body"))
    attempts = asyncio.run(run_provider_job(job(client), clock=clock()))
    attempt = attempts[0]

    assert client.calls == 1
    assert attempt.outcome == "success"
    assert attempt.requested_model_id == "openai-model-a"
    assert attempt.returned_model_id == "openai-model-a"
    assert attempt.usage.input_tokens == 100
    assert attempt.usage.cache_write_30m_tokens == 20
    assert attempt.usage.provider_output_tokens == 20
    assert attempt.usage.visible_output_tokens == 4
    assert attempt.usage.output_token_measurement_status == "synthetic"
    assert attempt.cost.status == "estimated"
    assert attempt.answer_sha256 is not None
    assert "private synthetic answer body" not in repr(attempt)


def test_rejection_produces_failure_attempt_without_zero_cost() -> None:
    client = FakeClient(ProviderRequestError("request_rejected"))
    attempts = asyncio.run(run_provider_job(job(client), clock=clock()))
    attempt = attempts[0]

    assert client.calls == 1
    assert attempt.outcome == "failure"
    assert attempt.safe_error_category == "request_rejected"
    assert attempt.returned_model_id is None
    assert attempt.usage.input_tokens is None
    assert attempt.cost.amount_usd is None
    assert attempt.cost.status == "unknown"


def test_timeout_retry_creates_distinct_ordered_attempts() -> None:
    client = FakeClient(ProviderRequestError("timeout"), reply())
    attempts = asyncio.run(run_provider_job(job(client, ordinal=2), max_attempts=3, clock=clock()))

    assert client.calls == 2
    assert [item.attempt_ordinal for item in attempts] == [7, 8]
    assert [item.attempt_index for item in attempts] == [0, 1]
    assert [item.usage.retries for item in attempts] == [0, 1]
    assert attempts[0].attempt_id != attempts[1].attempt_id
    assert attempts[0].cost.amount_usd is None
    assert attempts[1].cost.status == "estimated"


def test_partial_response_is_not_parsed_as_complete_usage() -> None:
    client = FakeClient(reply(outcome="partial"))
    attempt = asyncio.run(run_provider_job(job(client), clock=clock()))[0]

    assert attempt.outcome == "failure"
    assert attempt.safe_error_category == "partial_response"
    assert attempt.usage.input_tokens is None
    assert attempt.usage.token_status == "unknown"
    assert attempt.cost.amount_usd is None


def test_returned_model_is_not_replaced_when_missing_or_mismatched() -> None:
    missing = asyncio.run(run_provider_job(job(FakeClient(reply(model=None)), model="requested-id"), clock=clock()))[0]
    mismatch = asyncio.run(run_provider_job(job(FakeClient(reply(model="different-id")), model="requested-id"), clock=clock()))[0]

    assert missing.returned_model_id is None
    assert missing.requested_model_id == "requested-id"
    assert missing.safe_error_category == "returned_model_missing"
    assert mismatch.returned_model_id == "different-id"
    assert mismatch.safe_error_category == "returned_model_mismatch"


def test_concurrent_batch_ordinals_are_stable_from_job_slots() -> None:
    async def delayed(seconds):
        await asyncio.sleep(seconds)
        return reply()

    slower_first = job(FakeClient(delayed(0.02)), ordinal=0)
    faster_second = job(FakeClient(delayed(0)), ordinal=2)
    attempts = asyncio.run(run_provider_batch([faster_second, slower_first], max_attempts=3, clock=clock()))

    assert [item.attempt_ordinal for item in attempts] == [1, 7]
    assert [item.case_id for item in attempts] == ["case-0", "case-2"]
    assert len({item.attempt_ordinal for item in attempts}) == len(attempts)


def test_usage_parser_failure_is_safe_and_never_claims_zero() -> None:
    malformed = reply(usage={
        "input_tokens": 100,
        "output_tokens": 20,
        "input_tokens_details": {
            "cached_tokens": 10,
            "cache_write_tokens": 30,
            "cache_write_tokens_details": {"ttl_30m_tokens": 11, "ttl_12h_tokens": 10},
        },
    })
    attempt = asyncio.run(run_provider_job(job(FakeClient(malformed)), clock=clock()))[0]

    assert attempt.outcome == "failure"
    assert attempt.safe_error_category == "usage_invariant_failure"
    assert attempt.usage_parse_status == "invariant_failure"
    assert attempt.cost.amount_usd is None
    assert attempt.cost.status == "unknown"


def test_reaching_hard_cap_is_a_failure_not_a_success() -> None:
    capped = OutputPolicy("openai-model-a", 128_000, 128_000, 2800)
    base = job(FakeClient(reply(usage={
        "input_tokens": 100, "output_tokens": 2800,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0,
                                 "cache_write_tokens_details": {"ttl_30m_tokens": 0, "ttl_12h_tokens": 0}},
        "output_tokens_details": {"reasoning_tokens": 2000},
    })))
    attempt = asyncio.run(run_provider_job(
        ProviderJob(**{**base.__dict__, "output_policy": capped}), clock=clock()))[0]
    assert attempt.outcome == "failure"
    assert attempt.safe_error_category == "output_cap_reached"
    assert attempt.cap_status == "cap_reached"
    assert attempt.cost.status == "estimated"


def test_over_visible_target_is_flagged_without_failing_the_attempt() -> None:
    policy = OutputPolicy("openai-model-a", 128_000, 128_000, 8192)
    long_text = " ".join(["word"] * 900)
    base = job(FakeClient(reply(content=long_text)))
    attempt = asyncio.run(run_provider_job(
        ProviderJob(**{**base.__dict__, "output_policy": policy,
                       "visible_token_counter": lambda text: len(text.split())}),
        clock=clock()))[0]
    assert attempt.outcome == "success"
    assert attempt.visible_target_status == "over_target"
    assert attempt.cap_status == "within_cap"
