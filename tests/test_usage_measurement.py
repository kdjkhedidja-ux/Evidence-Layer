from decimal import Decimal

import pytest
from dataclasses import asdict

from evidence_layer.usage_measurement import PricingSnapshot, UsageEvent, cost_for_event, summarize_events


def event(
    *, token_status="actual", input_tokens=100, output_tokens=20,
    provider_output_tokens=None, reasoning_tokens=None, visible_output_tokens=None,
    visible_output_tokenizer_id=None, output_token_measurement_status="provider_reported_partial",
    cached_input_tokens=25, cache_write_5m_tokens=0, cache_write_1h_tokens=0,
    cache_write_30m_tokens=0, cache_write_12h_tokens=0,
):
    return UsageEvent(
        run_id="run-1", case_id="case-1", arm_id="lexical_rag", model_id="model-a",
        provider_id="provider-a", stage="answer", attempt_id="attempt-1",
        started_at="2026-11-05T00:00:00Z", finished_at="2026-11-05T00:00:01Z",
        input_tokens=input_tokens,
        provider_output_tokens=output_tokens if provider_output_tokens is None else provider_output_tokens,
        reasoning_tokens=reasoning_tokens,
        visible_output_tokens=visible_output_tokens,
        visible_output_tokenizer_id=visible_output_tokenizer_id,
        output_token_measurement_status=output_token_measurement_status,
        cached_input_tokens=cached_input_tokens,
        cache_write_5m_tokens=cache_write_5m_tokens,
        cache_write_1h_tokens=cache_write_1h_tokens,
        cache_write_30m_tokens=cache_write_30m_tokens,
        cache_write_12h_tokens=cache_write_12h_tokens,
        calls=1, retries=0,
        outcome="success", token_status=token_status, pricing_version="provider-a-v1",
    )


PRICING = PricingSnapshot(
    "provider-a-v1", Decimal("1"), Decimal("2"), Decimal("0.5"),
    Decimal("1.25"), Decimal("2"), None, None,
    cache_write_5m_billing="separately_metered",
    cache_write_1h_billing="separately_metered",
    cache_write_30m_billing="not_applicable",
    cache_write_12h_billing="not_applicable",
)


def test_actual_cost_is_recomputable_from_usage() -> None:
    result = cost_for_event(event(), PRICING)
    assert result.status == "actual"
    assert result.amount_usd == Decimal("0.0001275")


def test_reasoning_and_visible_subcounts_do_not_add_to_billed_provider_output() -> None:
    base = event()
    measured = UsageEvent(**{
        **base.__dict__,
        "reasoning_tokens": 12,
        "visible_output_tokens": 8,
        "visible_output_tokenizer_id": "fake-tokenizer-v1",
        "output_token_measurement_status": "provider_reported_complete",
    })
    assert measured.provider_output_tokens == 20
    assert measured.reasoning_tokens + measured.visible_output_tokens == measured.provider_output_tokens
    assert cost_for_event(measured, PRICING).amount_usd == cost_for_event(base, PRICING).amount_usd


def test_reasoning_tokens_are_a_subset_of_provider_output() -> None:
    with pytest.raises(ValueError, match="cannot exceed provider_output_tokens"):
        event(provider_output_tokens=10, reasoning_tokens=11)


def test_visible_output_count_requires_pinned_tokenizer_id() -> None:
    with pytest.raises(ValueError, match="tokenizer_id is required"):
        event(visible_output_tokens=8)


def test_cache_reads_and_write_lifetimes_are_separate_without_double_counting() -> None:
    usage = event(
        input_tokens=100,
        output_tokens=20,
        cached_input_tokens=20,
        cache_write_5m_tokens=10,
        cache_write_1h_tokens=5,
    )
    result = cost_for_event(usage, PRICING)
    # 65 uncached + 10 cached read + 12.5 short write + 10 long write + 40 output
    # Input token categories sum to the 100-token input total exactly once.
    assert result.status == "actual"
    assert result.amount_usd == Decimal("0.0001375")


def test_openai_cache_write_lifetimes_use_their_own_rates() -> None:
    usage = event(input_tokens=100, cached_input_tokens=0, cache_write_30m_tokens=40, cache_write_12h_tokens=10)
    pricing = PricingSnapshot(
        "openai-v1", Decimal("1"), Decimal("2"), Decimal("0.5"),
        cache_write_30m_per_million=Decimal("1.5"),
        cache_write_12h_per_million=Decimal("3"),
        cache_write_5m_billing="not_applicable",
        cache_write_1h_billing="not_applicable",
        cache_write_30m_billing="separately_metered",
        cache_write_12h_billing="separately_metered",
    )
    usage = UsageEvent(**{**usage.__dict__, "pricing_version": "openai-v1"})
    result = cost_for_event(usage, pricing)
    # 50 uncached + 40 * 1.5 (30m) + 10 * 3 (12h) + 40 output = 180 USD/MTok tokens.
    assert result.amount_usd == Decimal("0.00018")


def test_estimated_tokens_produce_estimated_cost_not_actual() -> None:
    result = cost_for_event(event(token_status="estimated"), PRICING)
    assert result.status == "estimated"
    assert result.amount_usd is not None


def test_cost_replays_from_serialized_usage_and_pinned_pricing_version() -> None:
    original = cost_for_event(event(), PRICING)
    replayed_event = UsageEvent(**asdict(event()))
    replayed = cost_for_event(replayed_event, PRICING)
    assert replayed.pricing_version == "provider-a-v1"
    assert replayed.amount_usd == original.amount_usd
    wrong_version = PricingSnapshot("provider-a-v2", Decimal("1"), Decimal("2"), Decimal("0.5"))
    mismatch = cost_for_event(replayed_event, wrong_version)
    assert mismatch.amount_usd is None
    assert mismatch.missing_reason == "pricing_version_mismatch"


def test_cost_requires_pricing_version_on_event() -> None:
    unpinned = UsageEvent(**{**event().__dict__, "pricing_version": None})
    result = cost_for_event(unpinned, PRICING)
    assert result.amount_usd is None
    assert result.missing_reason == "pricing_version_missing"


def test_unknown_usage_never_becomes_zero() -> None:
    result = cost_for_event(event(input_tokens=None), PRICING)
    assert result.amount_usd is None
    assert result.missing_reason == "token_usage_missing"
    summary = summarize_events([event(input_tokens=None)], {"model-a": PRICING})
    assert summary["total_cost_usd"] is None
    assert summary["cost_status"] == "unknown"


def test_missing_cache_write_usage_makes_cost_unknown() -> None:
    usage = UsageEvent(**{**event().__dict__, "cache_write_5m_tokens": None})
    result = cost_for_event(usage, PRICING)
    assert result.amount_usd is None
    assert result.missing_reason == "cache_write_5m_usage_missing"


def test_missing_cache_write_price_makes_nonzero_write_cost_unknown() -> None:
    pricing = PricingSnapshot(
        "without-write-price", Decimal("1"), Decimal("2"), Decimal("0.5"),
        cache_write_5m_billing="separately_metered",
        cache_write_1h_billing="not_applicable",
        cache_write_30m_billing="not_applicable",
        cache_write_12h_billing="not_applicable",
    )
    usage = UsageEvent(**{**event(cache_write_5m_tokens=10).__dict__, "pricing_version": "without-write-price"})
    result = cost_for_event(usage, pricing)
    assert result.amount_usd is None
    assert result.missing_reason == "cache_write_5m_price_missing"


def test_empty_event_summary_is_unknown_not_zero() -> None:
    summary = summarize_events([], {})
    assert summary["total_cost_usd"] is None
    assert summary["cost_status"] == "unknown"
    assert summary["missing_cost_reasons"] == ["no_usage_events"]


def test_summary_includes_retries_and_cached_tokens() -> None:
    second = event()
    second = UsageEvent(**{**second.__dict__, "attempt_id": "attempt-2", "retries": 1})
    summary = summarize_events([event(), second], {"model-a": PRICING})
    assert summary["events"] == 2
    assert summary["input_tokens"] == 200
    assert summary["provider_output_tokens"] == 40
    assert summary["reasoning_tokens"] is None
    assert summary["visible_output_tokens"] is None
    assert summary["cached_input_tokens"] == 50
    assert summary["cache_write_5m_tokens"] == 0
    assert summary["cache_write_1h_tokens"] == 0
    assert summary["cache_write_30m_tokens"] == 0
    assert summary["cache_write_12h_tokens"] == 0
    assert summary["calls"] == 2
    assert summary["retries"] == 1


def test_cached_tokens_cannot_exceed_input() -> None:
    with pytest.raises(ValueError, match="cannot exceed input_tokens"):
        event(input_tokens=10, cached_input_tokens=11)


def test_cache_categories_cannot_double_count_input() -> None:
    with pytest.raises(ValueError, match="cannot exceed input_tokens"):
        event(input_tokens=10, cached_input_tokens=4, cache_write_5m_tokens=4, cache_write_1h_tokens=3)


def test_included_in_input_does_not_double_count_or_require_write_price() -> None:
    pricing = PricingSnapshot(
        "included-v1", Decimal("1"), Decimal("2"), Decimal("0.5"),
        cache_write_5m_billing="included_in_input",
        cache_write_1h_billing="not_applicable",
        cache_write_30m_billing="not_applicable",
        cache_write_12h_billing="not_applicable",
    )
    usage = UsageEvent(**{
        **event(input_tokens=100, cached_input_tokens=20, cache_write_5m_tokens=30).__dict__,
        "pricing_version": "included-v1",
    })
    result = cost_for_event(usage, pricing)
    assert result.amount_usd == Decimal("0.00013")


def test_not_applicable_is_distinct_from_unknown_policy() -> None:
    not_applicable = PricingSnapshot(
        "no-write-v1", Decimal("1"), Decimal("2"), Decimal("0.5"),
        cache_write_5m_billing="not_applicable",
        cache_write_1h_billing="not_applicable",
        cache_write_30m_billing="not_applicable",
        cache_write_12h_billing="not_applicable",
    )
    usage = UsageEvent(**{
        **event(cached_input_tokens=20).__dict__,
        "cache_write_5m_tokens": None,
        "cache_write_1h_tokens": None,
        "cache_write_30m_tokens": None,
        "cache_write_12h_tokens": None,
        "pricing_version": "no-write-v1",
    })
    result = cost_for_event(usage, not_applicable)
    assert result.status == "actual"
    assert result.amount_usd == Decimal("0.00013")

    unknown = PricingSnapshot("unknown-v1", Decimal("1"), Decimal("2"), Decimal("0.5"))
    unknown_event = UsageEvent(**{**usage.__dict__, "pricing_version": "unknown-v1"})
    unknown_result = cost_for_event(unknown_event, unknown)
    assert unknown_result.amount_usd is None
    assert unknown_result.missing_reason == "cache_write_5m_billing_unknown"


def test_nonseparate_write_policy_rejects_separate_price() -> None:
    with pytest.raises(ValueError, match="must be null for included_in_input"):
        PricingSnapshot(
            "contradictory-v1", Decimal("1"), Decimal("2"), Decimal("0.5"),
            cache_write_5m_per_million=Decimal("1.25"),
            cache_write_5m_billing="included_in_input",
            cache_write_1h_billing="not_applicable",
            cache_write_30m_billing="not_applicable",
            cache_write_12h_billing="not_applicable",
        )
