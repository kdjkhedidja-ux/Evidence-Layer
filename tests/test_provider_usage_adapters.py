from decimal import Decimal

import pytest

from evidence_layer.provider_usage import (
    UsageIdentity,
    parse_anthropic_usage,
    parse_deepseek_chat_usage,
    parse_openai_responses_usage,
)
from evidence_layer.usage_measurement import PricingSnapshot, cost_for_event


IDENTITY = UsageIdentity(
    run_id="synthetic-run",
    case_id="synthetic-case",
    arm_id="evidence_layer",
    model_id="synthetic-model",
    stage="answer",
    attempt_id="attempt-1",
    started_at="2026-11-06T00:00:00Z",
    finished_at="2026-11-06T00:00:01Z",
    pricing_version="candidate-v1",
    usage_source="synthetic",
    visible_token_counter=lambda text: len(text.split()),
    visible_output_tokenizer_id="synthetic-whitespace-v1",
)


def anthropic_pricing() -> PricingSnapshot:
    return PricingSnapshot(
        "candidate-v1", Decimal("1"), Decimal("2"), Decimal("0.5"),
        cache_write_5m_per_million=Decimal("1.25"),
        cache_write_1h_per_million=Decimal("2"),
        cache_write_5m_billing="separately_metered",
        cache_write_1h_billing="separately_metered",
        cache_write_30m_billing="not_applicable",
        cache_write_12h_billing="not_applicable",
    )


def openai_pricing() -> PricingSnapshot:
    return PricingSnapshot(
        "candidate-v1", Decimal("1"), Decimal("2"), Decimal("0.5"),
        cache_write_30m_per_million=Decimal("1.25"),
        cache_write_12h_per_million=Decimal("2.5"),
        cache_write_5m_billing="not_applicable",
        cache_write_1h_billing="not_applicable",
        cache_write_30m_billing="separately_metered",
        cache_write_12h_billing="separately_metered",
    )


def test_anthropic_maps_total_input_and_ttl_writes() -> None:
    event = parse_anthropic_usage({
        "usage": {
            "input_tokens": 80,
            "output_tokens": 20,
            "cache_read_input_tokens": 5,
            "cache_creation_input_tokens": 15,
            "cache_creation": {"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 5},
        },
        "content": [
            {"type": "thinking", "thinking": "hidden reasoning not in visible answer"},
            {"type": "text", "text": "visible answer"},
        ],
    }, IDENTITY)

    assert event.provider_id == "anthropic"
    assert event.input_tokens == 100
    assert event.cached_input_tokens == 5
    assert event.cache_write_5m_tokens == 10
    assert event.cache_write_1h_tokens == 5
    assert event.cache_write_30m_tokens == event.cache_write_12h_tokens == 0
    assert event.token_status == "estimated"
    assert event.provider_output_tokens == 20
    assert event.reasoning_tokens is None
    assert event.visible_output_tokens == 2
    assert event.output_token_measurement_status == "synthetic"
    assert cost_for_event(event, anthropic_pricing()).amount_usd is not None


def test_anthropic_rejects_ttl_sum_mismatch() -> None:
    with pytest.raises(ValueError, match="does not equal its TTL breakdown"):
        parse_anthropic_usage({"usage": {
            "input_tokens": 80, "output_tokens": 20, "cache_read_input_tokens": 5,
            "cache_creation_input_tokens": 15,
            "cache_creation": {"ephemeral_5m_input_tokens": 9, "ephemeral_1h_input_tokens": 5},
        }}, IDENTITY)


def test_anthropic_missing_write_ttl_keeps_cost_unknown() -> None:
    event = parse_anthropic_usage({"usage": {
        "input_tokens": 80, "output_tokens": 20, "cache_read_input_tokens": 5,
        "cache_creation_input_tokens": 15,
    }}, IDENTITY)
    assert event.input_tokens == 100
    assert event.token_status == "unknown"
    result = cost_for_event(event, anthropic_pricing())
    assert result.amount_usd is None
    assert result.missing_reason == "cache_write_5m_usage_missing"


def test_openai_maps_ttl_counts_and_reasoning() -> None:
    event = parse_openai_responses_usage({
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "input_tokens_details": {
                "cached_tokens": 10,
                "cache_write_tokens": 30,
                "cache_write_tokens_details": {"ttl_30m_tokens": 20, "ttl_12h_tokens": 10},
            },
            "output_tokens_details": {"reasoning_tokens": 7},
        },
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "visible answer"}]}],
    }, IDENTITY)

    assert event.provider_id == "openai"
    assert event.input_tokens == 100
    assert event.cached_input_tokens == 10
    assert event.cache_write_30m_tokens == 20
    assert event.cache_write_12h_tokens == 10
    assert event.token_status == "estimated"
    assert event.provider_output_tokens == 20
    assert event.reasoning_tokens == 7
    assert event.visible_output_tokens == 2
    assert event.output_token_measurement_status == "synthetic"
    assert cost_for_event(event, openai_pricing()).amount_usd is not None


def test_openai_rejects_ttl_sum_mismatch() -> None:
    with pytest.raises(ValueError, match="does not equal its TTL breakdown"):
        parse_openai_responses_usage({"usage": {
            "input_tokens": 100, "output_tokens": 20,
            "input_tokens_details": {
                "cached_tokens": 10, "cache_write_tokens": 30,
                "cache_write_tokens_details": {"ttl_30m_tokens": 19, "ttl_12h_tokens": 10},
            },
        }}, IDENTITY)


def test_openai_missing_ttl_detail_keeps_cost_unknown() -> None:
    event = parse_openai_responses_usage({"usage": {
        "input_tokens": 100, "output_tokens": 20,
        "input_tokens_details": {"cached_tokens": 10, "cache_write_tokens": 30},
    }}, IDENTITY)
    assert event.token_status == "unknown"
    assert cost_for_event(event, openai_pricing()).amount_usd is None


def test_deepseek_hit_miss_and_reasoning_are_not_cache_writes() -> None:
    event = parse_deepseek_chat_usage({
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_cache_hit_tokens": 30,
            "prompt_cache_miss_tokens": 70,
            "completion_tokens_details": {"reasoning_tokens": 6},
        },
        "choices": [{"message": {"content": "visible answer", "reasoning_content": "hidden reasoning"}}],
    }, IDENTITY)

    assert event.provider_id == "deepseek"
    assert event.input_tokens == 100
    assert event.cached_input_tokens == 30
    assert event.cache_write_5m_tokens is None
    assert event.cache_write_1h_tokens is None
    assert event.cache_write_30m_tokens is None
    assert event.cache_write_12h_tokens is None
    assert event.token_status == "estimated"
    assert event.provider_output_tokens == 20
    assert event.reasoning_tokens == 6
    assert event.visible_output_tokens == 2
    assert event.output_token_measurement_status == "synthetic"
    pricing = PricingSnapshot(
        "candidate-v1", Decimal("1"), Decimal("2"), Decimal("0.5"),
        cache_write_5m_billing="unknown", cache_write_1h_billing="unknown",
        cache_write_30m_billing="unknown", cache_write_12h_billing="unknown",
    )
    result = cost_for_event(event, pricing)
    assert result.amount_usd is None
    assert result.missing_reason == "cache_write_5m_billing_unknown"


def test_deepseek_rejects_hit_miss_sum_mismatch() -> None:
    with pytest.raises(ValueError, match="does not equal cache hit plus miss"):
        parse_deepseek_chat_usage({"usage": {
            "prompt_tokens": 100, "completion_tokens": 20,
            "prompt_cache_hit_tokens": 30, "prompt_cache_miss_tokens": 60,
        }}, IDENTITY)
