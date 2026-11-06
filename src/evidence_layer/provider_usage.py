"""Offline parsers for documented provider usage payloads.

These functions parse already-received JSON only. They make no network calls.
Missing fields remain null/unknown; inconsistent token totals raise ValueError.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping

from evidence_layer.usage_measurement import UsageEvent


@dataclass(frozen=True)
class UsageIdentity:
    run_id: str
    case_id: str
    arm_id: str
    model_id: str
    stage: str
    attempt_id: str
    started_at: str
    finished_at: str
    pricing_version: str | None
    retries: int = 0
    outcome: str = "success"
    usage_source: Literal["provider_reported", "synthetic"] = "provider_reported"
    visible_token_counter: Callable[[str], int] | None = None
    visible_output_tokenizer_id: str | None = None


def _count(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer or null")
    return value


def _usage(response: Mapping[str, Any], provider: str) -> Mapping[str, Any]:
    usage = response.get("usage")
    if not isinstance(usage, Mapping):
        raise ValueError(f"{provider} response is missing a usage object")
    return usage


def _make_event(
    identity: UsageIdentity,
    *,
    provider_id: str,
    input_tokens: int | None,
    provider_output_tokens: int | None,
    reasoning_tokens: int | None,
    visible_output_tokens: int | None,
    visible_output_tokenizer_id: str | None,
    cached_input_tokens: int | None,
    cache_write_5m_tokens: int | None,
    cache_write_1h_tokens: int | None,
    cache_write_30m_tokens: int | None,
    cache_write_12h_tokens: int | None,
    complete: bool,
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
        input_tokens=input_tokens,
        provider_output_tokens=provider_output_tokens,
        reasoning_tokens=reasoning_tokens,
        visible_output_tokens=visible_output_tokens,
        visible_output_tokenizer_id=visible_output_tokenizer_id,
        output_token_measurement_status=(
            "unknown" if provider_output_tokens is None
            else "synthetic" if identity.usage_source == "synthetic"
            else "provider_reported_complete"
            if reasoning_tokens is not None and visible_output_tokens is not None
            else "provider_reported_partial"
        ),
        cached_input_tokens=cached_input_tokens,
        cache_write_5m_tokens=cache_write_5m_tokens,
        cache_write_1h_tokens=cache_write_1h_tokens,
        cache_write_30m_tokens=cache_write_30m_tokens,
        cache_write_12h_tokens=cache_write_12h_tokens,
        calls=1,
        retries=identity.retries,
        outcome=identity.outcome,  # type: ignore[arg-type]
        token_status=(
            "unknown" if not complete
            else "estimated" if identity.usage_source == "synthetic"
            else "actual"
        ),
        pricing_version=identity.pricing_version,
    )


def _visible_output(identity: UsageIdentity, text: str | None) -> tuple[int | None, str | None]:
    if text is None or identity.visible_token_counter is None or not identity.visible_output_tokenizer_id:
        return None, None
    count = identity.visible_token_counter(text)
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("visible output tokenizer must return a non-negative integer")
    return count, identity.visible_output_tokenizer_id


def _anthropic_visible_text(response: Mapping[str, Any]) -> str | None:
    content = response.get("content")
    if not isinstance(content, list):
        return None
    text_blocks = [block.get("text") for block in content
                   if isinstance(block, Mapping) and block.get("type") == "text"
                   and isinstance(block.get("text"), str)]
    return "\n".join(text_blocks) if text_blocks else None


def _openai_visible_text(response: Mapping[str, Any]) -> str | None:
    output_text = response.get("output_text")
    if isinstance(output_text, str):
        return output_text
    output = response.get("output")
    if not isinstance(output, list):
        return None
    blocks: list[str] = []
    for item in output:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        blocks.extend(part.get("text") for part in content
                      if isinstance(part, Mapping)
                      and part.get("type") in {"output_text", "text"}
                      and isinstance(part.get("text"), str))
    return "\n".join(blocks) if blocks else None


def _deepseek_visible_text(response: Mapping[str, Any]) -> str | None:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        return None
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        blocks = [part.get("text") for part in content
                  if isinstance(part, Mapping) and isinstance(part.get("text"), str)]
        return "\n".join(blocks) if blocks else None
    return None


def parse_anthropic_usage(response: Mapping[str, Any], identity: UsageIdentity) -> UsageEvent:
    """Normalize Anthropic Messages usage and its 5m/1h cache-write breakdown."""
    usage = _usage(response, "Anthropic")
    base_input = _count(usage.get("input_tokens"), "usage.input_tokens")
    output = _count(usage.get("output_tokens"), "usage.output_tokens")
    reasoning = None  # Messages usage reports total output, not a reasoning sub-count.
    visible, tokenizer_id = _visible_output(identity, _anthropic_visible_text(response))
    read = _count(usage.get("cache_read_input_tokens"), "usage.cache_read_input_tokens")
    creation = _count(usage.get("cache_creation_input_tokens"), "usage.cache_creation_input_tokens")
    cache_creation = usage.get("cache_creation")
    five = one = None
    if isinstance(cache_creation, Mapping):
        five = _count(cache_creation.get("ephemeral_5m_input_tokens"), "usage.cache_creation.ephemeral_5m_input_tokens")
        one = _count(cache_creation.get("ephemeral_1h_input_tokens"), "usage.cache_creation.ephemeral_1h_input_tokens")
    if creation == 0 and (five is None or one is None):
        five = one = 0
    if creation is not None and five is not None and one is not None and creation != five + one:
        raise ValueError("Anthropic cache_creation_input_tokens does not equal its TTL breakdown")
    if creation is None and five is not None and one is not None:
        creation = five + one
    total_input = (
        base_input + read + creation
        if base_input is not None and read is not None and creation is not None
        else None
    )
    complete = all(value is not None for value in (total_input, output, read, five, one))
    return _make_event(
        identity,
        provider_id="anthropic",
        input_tokens=total_input,
        provider_output_tokens=output,
        reasoning_tokens=reasoning,
        visible_output_tokens=visible,
        visible_output_tokenizer_id=tokenizer_id,
        cached_input_tokens=read,
        cache_write_5m_tokens=five,
        cache_write_1h_tokens=one,
        cache_write_30m_tokens=0,
        cache_write_12h_tokens=0,
        complete=complete,
    )


def parse_openai_responses_usage(response: Mapping[str, Any], identity: UsageIdentity) -> UsageEvent:
    """Normalize OpenAI Responses usage and its 30m/12h cache-write breakdown."""
    usage = _usage(response, "OpenAI Responses")
    total_input = _count(usage.get("input_tokens"), "usage.input_tokens")
    output = _count(usage.get("output_tokens"), "usage.output_tokens")
    output_details = usage.get("output_tokens_details")
    if not isinstance(output_details, Mapping):
        output_details = {}
    reasoning = _count(output_details.get("reasoning_tokens"), "usage.output_tokens_details.reasoning_tokens")
    visible, tokenizer_id = _visible_output(identity, _openai_visible_text(response))
    details = usage.get("input_tokens_details")
    if not isinstance(details, Mapping):
        details = {}
    read = _count(details.get("cached_tokens"), "usage.input_tokens_details.cached_tokens")
    aggregate = _count(details.get("cache_write_tokens"), "usage.input_tokens_details.cache_write_tokens")
    ttl_details = details.get("cache_write_tokens_details")
    thirty = twelve = None
    if isinstance(ttl_details, Mapping):
        thirty = _count(ttl_details.get("ttl_30m_tokens"), "usage.input_tokens_details.cache_write_tokens_details.ttl_30m_tokens")
        twelve = _count(ttl_details.get("ttl_12h_tokens"), "usage.input_tokens_details.cache_write_tokens_details.ttl_12h_tokens")
    if aggregate == 0 and (thirty is None or twelve is None):
        thirty = twelve = 0
    if aggregate is not None and thirty is not None and twelve is not None and aggregate != thirty + twelve:
        raise ValueError("OpenAI cache_write_tokens does not equal its TTL breakdown")
    if aggregate is None and thirty is not None and twelve is not None:
        aggregate = thirty + twelve
    complete = all(value is not None for value in (total_input, output, read, aggregate, thirty, twelve))
    return _make_event(
        identity,
        provider_id="openai",
        input_tokens=total_input,
        provider_output_tokens=output,
        reasoning_tokens=reasoning,
        visible_output_tokens=visible,
        visible_output_tokenizer_id=tokenizer_id,
        cached_input_tokens=read,
        cache_write_5m_tokens=0,
        cache_write_1h_tokens=0,
        cache_write_30m_tokens=thirty,
        cache_write_12h_tokens=twelve,
        complete=complete,
    )


def parse_deepseek_chat_usage(response: Mapping[str, Any], identity: UsageIdentity) -> UsageEvent:
    """Normalize DeepSeek hit/miss usage without inferring cache writes from misses."""
    usage = _usage(response, "DeepSeek Chat Completions")
    total_input = _count(usage.get("prompt_tokens"), "usage.prompt_tokens")
    output = _count(usage.get("completion_tokens"), "usage.completion_tokens")
    output_details = usage.get("completion_tokens_details")
    if not isinstance(output_details, Mapping):
        output_details = {}
    reasoning = _count(output_details.get("reasoning_tokens"), "usage.completion_tokens_details.reasoning_tokens")
    visible, tokenizer_id = _visible_output(identity, _deepseek_visible_text(response))
    hit = _count(usage.get("prompt_cache_hit_tokens"), "usage.prompt_cache_hit_tokens")
    miss = _count(usage.get("prompt_cache_miss_tokens"), "usage.prompt_cache_miss_tokens")
    if total_input is not None and hit is not None and miss is not None and total_input != hit + miss:
        raise ValueError("DeepSeek prompt_tokens does not equal cache hit plus miss tokens")
    complete = all(value is not None for value in (total_input, output, hit, miss))
    return _make_event(
        identity,
        provider_id="deepseek",
        input_tokens=total_input,
        provider_output_tokens=output,
        reasoning_tokens=reasoning,
        visible_output_tokens=visible,
        visible_output_tokenizer_id=tokenizer_id,
        cached_input_tokens=hit,
        cache_write_5m_tokens=None,
        cache_write_1h_tokens=None,
        cache_write_30m_tokens=None,
        cache_write_12h_tokens=None,
        complete=complete,
    )


def visible_text_for(provider_id: str, payload: Mapping[str, Any]) -> str | None:
    """Return the visible answer text of a provider payload (no reasoning)."""
    if provider_id == "anthropic":
        return _anthropic_visible_text(payload)
    if provider_id == "openai":
        return _openai_visible_text(payload)
    if provider_id == "deepseek":
        return _deepseek_visible_text(payload)
    raise ValueError(f"unknown provider {provider_id}")
