"""Provider-neutral offline usage and cost accounting.

This module records metadata only. It performs no provider calls and is not a
billing ledger. Unknown usage or pricing remains unknown rather than zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

TokenStatus = Literal["actual", "estimated", "unknown"]
CostStatus = Literal["actual", "estimated", "unknown"]
CacheWriteBilling = Literal[
    "separately_metered", "included_in_input", "not_applicable", "unknown"
]
OutputTokenMeasurement = Literal[
    "provider_reported_complete",
    "provider_reported_partial",
    "synthetic",
    "not_applicable",
    "unknown",
]


@dataclass(frozen=True)
class PricingSnapshot:
    pricing_version: str
    input_per_million: Decimal | None
    output_per_million: Decimal | None
    cached_input_per_million: Decimal | None
    cache_write_5m_per_million: Decimal | None = None
    cache_write_1h_per_million: Decimal | None = None
    cache_write_30m_per_million: Decimal | None = None
    cache_write_12h_per_million: Decimal | None = None
    cache_write_5m_billing: CacheWriteBilling = "unknown"
    cache_write_1h_billing: CacheWriteBilling = "unknown"
    cache_write_30m_billing: CacheWriteBilling = "unknown"
    cache_write_12h_billing: CacheWriteBilling = "unknown"

    def __post_init__(self) -> None:
        for name in (
            "input_per_million", "output_per_million", "cached_input_per_million",
            "cache_write_5m_per_million", "cache_write_1h_per_million",
            "cache_write_30m_per_million", "cache_write_12h_per_million",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative or null")
        write_pairs = (
            ("cache_write_5m_billing", "cache_write_5m_per_million"),
            ("cache_write_1h_billing", "cache_write_1h_per_million"),
            ("cache_write_30m_billing", "cache_write_30m_per_million"),
            ("cache_write_12h_billing", "cache_write_12h_per_million"),
        )
        for billing_field, price_field in write_pairs:
            billing = getattr(self, billing_field)
            price = getattr(self, price_field)
            if billing not in {
                "separately_metered", "included_in_input", "not_applicable", "unknown"
            }:
                raise ValueError(f"{billing_field} has an unsupported billing category")
            if billing in {"included_in_input", "not_applicable"} and price is not None:
                raise ValueError(f"{price_field} must be null for {billing}")


@dataclass(frozen=True)
class UsageEvent:
    run_id: str
    case_id: str
    arm_id: str
    model_id: str
    provider_id: str
    stage: str
    attempt_id: str
    started_at: str
    finished_at: str
    input_tokens: int | None
    provider_output_tokens: int | None
    reasoning_tokens: int | None
    visible_output_tokens: int | None
    visible_output_tokenizer_id: str | None
    output_token_measurement_status: OutputTokenMeasurement
    cached_input_tokens: int | None
    cache_write_5m_tokens: int | None
    cache_write_1h_tokens: int | None
    cache_write_30m_tokens: int | None
    cache_write_12h_tokens: int | None
    calls: int
    retries: int
    outcome: Literal["success", "failure", "refusal", "unknown"]
    token_status: TokenStatus
    pricing_version: str | None = None
    native_visible_output_tokens: int | None = None
    native_visible_output_tokenizer_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "input_tokens", "provider_output_tokens", "reasoning_tokens",
            "visible_output_tokens", "cached_input_tokens",
            "cache_write_5m_tokens", "cache_write_1h_tokens",
            "cache_write_30m_tokens", "cache_write_12h_tokens",
        ):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or null")
        categorized_input = (
            self.cached_input_tokens,
            self.cache_write_5m_tokens,
            self.cache_write_1h_tokens,
            self.cache_write_30m_tokens,
            self.cache_write_12h_tokens,
        )
        if self.input_tokens is not None and all(value is not None for value in categorized_input):
            if sum(categorized_input) > self.input_tokens:
                raise ValueError("cached/read/write input tokens cannot exceed input_tokens")
        if self.calls < 0 or self.retries < 0:
            raise ValueError("calls and retries must be non-negative")
        if self.native_visible_output_tokens is not None and self.native_visible_output_tokenizer_id is None:
            raise ValueError("native_visible_output_tokenizer_id is required with native_visible_output_tokens")
        if self.reasoning_tokens is not None and self.provider_output_tokens is not None:
            if self.reasoning_tokens > self.provider_output_tokens:
                raise ValueError("reasoning_tokens cannot exceed provider_output_tokens")
        if self.visible_output_tokens is not None and not self.visible_output_tokenizer_id:
            raise ValueError("visible_output_tokenizer_id is required with visible_output_tokens")
        if self.provider_output_tokens is None and self.output_token_measurement_status in {
            "provider_reported_complete", "provider_reported_partial"
        }:
            raise ValueError("provider output measurement status requires provider_output_tokens")


@dataclass(frozen=True)
class EventCost:
    amount_usd: Decimal | None
    status: CostStatus
    pricing_version: str | None
    missing_reason: str | None


def cost_for_event(event: UsageEvent, pricing: PricingSnapshot | None) -> EventCost:
    if pricing is None:
        return EventCost(None, "unknown", None, "pricing_missing")
    if event.pricing_version is None:
        return EventCost(None, "unknown", pricing.pricing_version, "pricing_version_missing")
    if event.pricing_version != pricing.pricing_version:
        return EventCost(None, "unknown", pricing.pricing_version, "pricing_version_mismatch")
    if event.input_tokens is None or event.provider_output_tokens is None:
        return EventCost(None, "unknown", pricing.pricing_version, "token_usage_missing")
    if event.cached_input_tokens is None:
        return EventCost(None, "unknown", pricing.pricing_version, "cached_usage_missing")
    write_categories = (
        ("5m", event.cache_write_5m_tokens, pricing.cache_write_5m_billing, pricing.cache_write_5m_per_million),
        ("1h", event.cache_write_1h_tokens, pricing.cache_write_1h_billing, pricing.cache_write_1h_per_million),
        ("30m", event.cache_write_30m_tokens, pricing.cache_write_30m_billing, pricing.cache_write_30m_per_million),
        ("12h", event.cache_write_12h_tokens, pricing.cache_write_12h_billing, pricing.cache_write_12h_per_million),
    )
    separately_metered_writes = 0
    for lifetime, tokens, billing, _ in write_categories:
        if billing == "unknown":
            return EventCost(None, "unknown", pricing.pricing_version, f"cache_write_{lifetime}_billing_unknown")
        if billing == "separately_metered":
            if tokens is None:
                return EventCost(None, "unknown", pricing.pricing_version, f"cache_write_{lifetime}_usage_missing")
            separately_metered_writes += tokens
        elif billing == "not_applicable" and tokens not in (None, 0):
            return EventCost(None, "unknown", pricing.pricing_version, f"cache_write_{lifetime}_unexpected_usage")

    if event.token_status == "unknown":
        return EventCost(None, "unknown", pricing.pricing_version, "token_usage_unknown")

    # included_in_input remains part of ordinary input; only independently
    # billed writes are removed from that bucket and costed at their own rate.
    uncached = event.input_tokens - event.cached_input_tokens - separately_metered_writes
    if uncached < 0:
        return EventCost(None, "unknown", pricing.pricing_version, "input_token_categories_exceed_total")
    if uncached and pricing.input_per_million is None:
        return EventCost(None, "unknown", pricing.pricing_version, "input_price_missing")
    if event.provider_output_tokens and pricing.output_per_million is None:
        return EventCost(None, "unknown", pricing.pricing_version, "output_price_missing")
    if event.cached_input_tokens and pricing.cached_input_per_million is None:
        return EventCost(None, "unknown", pricing.pricing_version, "cached_input_price_missing")
    if pricing.cache_write_5m_billing == "separately_metered" and event.cache_write_5m_tokens and pricing.cache_write_5m_per_million is None:
        return EventCost(None, "unknown", pricing.pricing_version, "cache_write_5m_price_missing")
    if pricing.cache_write_1h_billing == "separately_metered" and event.cache_write_1h_tokens and pricing.cache_write_1h_per_million is None:
        return EventCost(None, "unknown", pricing.pricing_version, "cache_write_1h_price_missing")
    if pricing.cache_write_30m_billing == "separately_metered" and event.cache_write_30m_tokens and pricing.cache_write_30m_per_million is None:
        return EventCost(None, "unknown", pricing.pricing_version, "cache_write_30m_price_missing")
    if pricing.cache_write_12h_billing == "separately_metered" and event.cache_write_12h_tokens and pricing.cache_write_12h_per_million is None:
        return EventCost(None, "unknown", pricing.pricing_version, "cache_write_12h_price_missing")

    amount = (
        Decimal(uncached) * (pricing.input_per_million or Decimal(0))
        + Decimal(event.cached_input_tokens) * (pricing.cached_input_per_million or Decimal(0))
        + (Decimal(event.cache_write_5m_tokens or 0) * (pricing.cache_write_5m_per_million or Decimal(0))
           if pricing.cache_write_5m_billing == "separately_metered" else Decimal(0))
        + (Decimal(event.cache_write_1h_tokens or 0) * (pricing.cache_write_1h_per_million or Decimal(0))
           if pricing.cache_write_1h_billing == "separately_metered" else Decimal(0))
        + (Decimal(event.cache_write_30m_tokens or 0) * (pricing.cache_write_30m_per_million or Decimal(0))
           if pricing.cache_write_30m_billing == "separately_metered" else Decimal(0))
        + (Decimal(event.cache_write_12h_tokens or 0) * (pricing.cache_write_12h_per_million or Decimal(0))
           if pricing.cache_write_12h_billing == "separately_metered" else Decimal(0))
        + Decimal(event.provider_output_tokens) * (pricing.output_per_million or Decimal(0))
    ) / Decimal(1_000_000)
    status: CostStatus = "actual" if event.token_status == "actual" else "estimated"
    return EventCost(amount, status, pricing.pricing_version, None)


def summarize_events(
    events: list[UsageEvent], pricing: dict[str, PricingSnapshot]
) -> dict[str, object]:
    costs = [cost_for_event(event, pricing.get(event.model_id)) for event in events]
    input_tokens = (
        sum(event.input_tokens for event in events if event.input_tokens is not None)
        if all(event.input_tokens is not None for event in events)
        else None
    )
    provider_output_tokens = (
        sum(event.provider_output_tokens for event in events if event.provider_output_tokens is not None)
        if all(event.provider_output_tokens is not None for event in events)
        else None
    )
    reasoning_tokens = (
        sum(event.reasoning_tokens for event in events if event.reasoning_tokens is not None)
        if all(event.reasoning_tokens is not None for event in events)
        else None
    )
    visible_output_tokens = (
        sum(event.visible_output_tokens for event in events if event.visible_output_tokens is not None)
        if all(event.visible_output_tokens is not None for event in events)
        else None
    )
    cached_tokens = (
        sum(event.cached_input_tokens for event in events if event.cached_input_tokens is not None)
        if all(event.cached_input_tokens is not None for event in events)
        else None
    )
    cache_write_5m_tokens = (
        sum(event.cache_write_5m_tokens for event in events if event.cache_write_5m_tokens is not None)
        if all(event.cache_write_5m_tokens is not None for event in events)
        else None
    )
    cache_write_1h_tokens = (
        sum(event.cache_write_1h_tokens for event in events if event.cache_write_1h_tokens is not None)
        if all(event.cache_write_1h_tokens is not None for event in events)
        else None
    )
    cache_write_30m_tokens = (
        sum(event.cache_write_30m_tokens for event in events if event.cache_write_30m_tokens is not None)
        if all(event.cache_write_30m_tokens is not None for event in events)
        else None
    )
    cache_write_12h_tokens = (
        sum(event.cache_write_12h_tokens for event in events if event.cache_write_12h_tokens is not None)
        if all(event.cache_write_12h_tokens is not None for event in events)
        else None
    )
    known_costs = [cost.amount_usd for cost in costs if cost.amount_usd is not None]
    if not events or len(known_costs) != len(costs):
        total_cost = None
        cost_status: CostStatus = "unknown"
        missing_reasons = sorted({cost.missing_reason for cost in costs if cost.missing_reason})
        if not events:
            missing_reasons = ["no_usage_events"]
    else:
        total_cost = sum(known_costs, Decimal(0))
        cost_status = "actual" if all(cost.status == "actual" for cost in costs) else "estimated"
        missing_reasons = []
    return {
        "events": len(events),
        "input_tokens": input_tokens,
        "provider_output_tokens": provider_output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "visible_output_tokens": visible_output_tokens,
        "cached_input_tokens": cached_tokens,
        "cache_write_5m_tokens": cache_write_5m_tokens,
        "cache_write_1h_tokens": cache_write_1h_tokens,
        "cache_write_30m_tokens": cache_write_30m_tokens,
        "cache_write_12h_tokens": cache_write_12h_tokens,
        "calls": sum(event.calls for event in events),
        "retries": sum(event.retries for event in events),
        "total_cost_usd": total_cost,
        "cost_status": cost_status,
        "missing_cost_reasons": missing_reasons,
    }
