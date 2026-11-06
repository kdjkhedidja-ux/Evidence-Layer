"""Per-model output limits for the Phase 6 answer protocol.

The visible-answer target is a uniform scientific constraint (<=800 visible
tokens, counted with the pinned tokenizer). The hard output cap is an operational
per-model ceiling sent with each request. It includes hidden reasoning where the
provider counts it, so it must exceed the visible target by a reserve. Answers
over the visible target are flagged, never truncated. Attempts that reach the
hard cap are recorded as failures with a safe category.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

VISIBLE_ANSWER_TARGET = 800
MINIMUM_RESERVE = 2000
PRACTICAL_HARD_CAP = 8192

VisibleStatus = Literal["within_target", "over_target", "unmeasured"]
CapStatus = Literal["within_cap", "cap_reached", "unmeasured"]


@dataclass(frozen=True)
class OutputPolicy:
    model_id: str
    provider_id: str
    api_max_output_tokens: int
    hard_output_cap: int
    visible_target_tokens: int = VISIBLE_ANSWER_TARGET

    def __post_init__(self) -> None:
        if self.hard_output_cap > self.api_max_output_tokens:
            raise ValueError(f"{self.model_id}: hard cap exceeds documented API maximum")
        if self.hard_output_cap < self.visible_target_tokens + MINIMUM_RESERVE:
            raise ValueError(f"{self.model_id}: hard cap leaves no reasoning reserve")


OUTPUT_POLICIES: dict[str, OutputPolicy] = {
    "claude-opus-5-5": OutputPolicy("claude-opus-5-5", "anthropic", 128_000, PRACTICAL_HARD_CAP),
    "claude-sonnet-5-5": OutputPolicy("claude-sonnet-5-5", "anthropic", 128_000, PRACTICAL_HARD_CAP),
    "claude-haiku-5-5": OutputPolicy("claude-haiku-5-5", "anthropic", 128_000, PRACTICAL_HARD_CAP),
    "gpt-6.1-sol": OutputPolicy("gpt-6.1-sol", "openai", 128_000, PRACTICAL_HARD_CAP),
    "gpt-6.1-terra": OutputPolicy("gpt-6.1-terra", "openai", 128_000, PRACTICAL_HARD_CAP),
    "gpt-6-luna": OutputPolicy("gpt-6-luna", "openai", 128_000, PRACTICAL_HARD_CAP),
    "deepseek-flash": OutputPolicy("deepseek-flash", "deepseek", 384_000, PRACTICAL_HARD_CAP),
    "deepseek-v4-pro": OutputPolicy("deepseek-v4-pro", "deepseek", 384_000, PRACTICAL_HARD_CAP),
}


def visible_target_status(visible_tokens: int | None, policy: OutputPolicy) -> VisibleStatus:
    if visible_tokens is None:
        return "unmeasured"
    return "within_target" if visible_tokens <= policy.visible_target_tokens else "over_target"


def cap_status(provider_output_tokens: int | None, policy: OutputPolicy) -> CapStatus:
    if provider_output_tokens is None:
        return "unmeasured"
    return "cap_reached" if provider_output_tokens >= policy.hard_output_cap else "within_cap"
