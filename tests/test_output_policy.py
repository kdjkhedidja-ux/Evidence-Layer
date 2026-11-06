import hashlib
from decimal import Decimal

import pytest

from evidence_layer import tokenization
from evidence_layer.output_policy import (
    OUTPUT_POLICIES,
    VISIBLE_ANSWER_TARGET,
    OutputPolicy,
    cap_status,
    visible_target_status,
)


def test_every_candidate_has_a_policy_within_api_limits() -> None:
    assert len(OUTPUT_POLICIES) == 8
    for policy in OUTPUT_POLICIES.values():
        assert policy.hard_output_cap <= policy.api_max_output_tokens
        assert policy.hard_output_cap >= VISIBLE_ANSWER_TARGET + 2000


def test_policy_rejects_cap_above_api_maximum() -> None:
    with pytest.raises(ValueError, match="exceeds documented API maximum"):
        OutputPolicy("m", "openai", 1000, 2000)


def test_policy_rejects_cap_without_reasoning_reserve() -> None:
    with pytest.raises(ValueError, match="no reasoning reserve"):
        OutputPolicy("m", "openai", 128_000, 1000)


def test_visible_status_flags_but_does_not_truncate() -> None:
    policy = OUTPUT_POLICIES["gpt-6.1-sol"]
    assert visible_target_status(800, policy) == "within_target"
    assert visible_target_status(801, policy) == "over_target"
    assert visible_target_status(None, policy) == "unmeasured"


def test_cap_status_detects_reached_limit() -> None:
    policy = OUTPUT_POLICIES["deepseek-flash"]
    assert cap_status(policy.hard_output_cap - 1, policy) == "within_cap"
    assert cap_status(policy.hard_output_cap, policy) == "cap_reached"
    assert cap_status(None, policy) == "unmeasured"


def test_tokenizer_artifact_failing_sha256_is_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EVIDENCE_TOKENIZER_DIR", str(tmp_path))
    target = tmp_path / "deepseek_v4_tokenizer"
    target.mkdir()
    (target / "tokenizer.json").write_bytes(b"not the pinned artifact")
    with pytest.raises(ValueError, match="failed SHA-256 verification"):
        tokenization.load_counter("deepseek-v4-hf-tokenizers-0.23.3")


def test_missing_tokenizer_artifact_fails_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EVIDENCE_TOKENIZER_DIR", str(tmp_path / "absent"))
    with pytest.raises(FileNotFoundError, match="fetch_phase6_tokenizers"):
        tokenization.load_counter("o200k_base-tiktoken-0.14.0")


def test_every_candidate_is_measured_with_the_shared_meter() -> None:
    assert tokenization.SHARED_VISIBLE_TOKENIZER_ID == "o200k_base-tiktoken-0.14.0"
    assert set(OUTPUT_POLICIES) == {
        "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5",
        "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna",
        "deepseek-flash", "deepseek-v4-pro",
    }
    assert tokenization.native_counter("claude-opus-5-5") is None
    assert tokenization.native_counter("gpt-6.1-sol") is None
    for model in ("deepseek-flash", "deepseek-v4-pro"):
        assert tokenization.native_counter(model) is not None  # additional telemetry only
