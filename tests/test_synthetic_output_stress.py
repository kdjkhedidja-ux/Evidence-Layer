"""Synthetic long-output stress tests for the Phase 6 answer policy.

All answers are deterministic synthetic text. No provider is called. The shared
`o200k_base` meter classifies visible length; the provider-native DeepSeek
tokenizer is checked only as additional telemetry.
"""

import asyncio
import hashlib
from pathlib import Path

import pytest

from evidence_layer import tokenization
from evidence_layer.output_policy import OUTPUT_POLICIES, visible_target_status
from evidence_layer.provider_execution import ProviderJob, ProviderReply, run_provider_job
from evidence_layer.synthetic_long_output import exact_visible_text

MODEL = "gpt-6.1-sol"
POLICY = OUTPUT_POLICIES[MODEL]

pytestmark = pytest.mark.skipif(
    not (tokenization.tokenizer_dir() / "deepseek_v4_tokenizer" / "tokenizer.json").is_file(),
    reason="run scripts/fetch_phase6_tokenizers.py to install pinned tokenizers",
)


@pytest.fixture(scope="module")
def shared_counter():
    counter, tokenizer_id, _sha = tokenization.shared_visible_counter()
    assert tokenizer_id == "o200k_base-tiktoken-0.14.0"
    return counter


@pytest.fixture(scope="module")
def exact(shared_counter):
    cache: dict[int, str] = {}

    def make(n: int) -> str:
        if n not in cache:
            cache[n] = exact_visible_text(n, shared_counter)
        return cache[n]

    return make


class FakeClient:
    def __init__(self, reply: ProviderReply) -> None:
        self._reply = reply

    async def generate(self, request):
        return self._reply


def openai_reply(visible: str, output_tokens: int, reasoning: int) -> ProviderReply:
    return ProviderReply({
        "model": MODEL,
        "output_text": visible,
        "usage": {
            "input_tokens": 100,
            "output_tokens": output_tokens,
            "input_tokens_details": {
                "cached_tokens": 0,
                "cache_write_tokens": 0,
                "cache_write_tokens_details": {"ttl_30m_tokens": 0, "ttl_12h_tokens": 0},
            },
            "output_tokens_details": {"reasoning_tokens": reasoning},
        },
    })


def make_job(reply: ProviderReply, counter, *, policy=POLICY, provider="openai", model=MODEL):
    return ProviderJob(
        job_ordinal=0, run_id="stress", case_id="case-1", arm_id="evidence_layer",
        provider_id=provider, requested_model_id=model, pricing=None,
        client=FakeClient(reply), usage_source="synthetic",
        visible_token_counter=counter,
        visible_output_tokenizer_id="o200k_base-tiktoken-0.14.0",
        output_policy=policy,
    )


def run_one(job):
    return asyncio.run(run_provider_job(job, max_attempts=1))[0]


def test_750_tokens_is_within_target(exact, shared_counter) -> None:
    text = exact(750)
    attempt = run_one(make_job(openai_reply(text, 750 + 100, 100), shared_counter))
    assert attempt.outcome == "success"
    assert attempt.usage.visible_output_tokens == 750
    assert attempt.visible_target_status == "within_target"
    assert attempt.cap_status == "within_cap"


def test_800_tokens_is_the_inclusive_boundary(exact, shared_counter) -> None:
    text = exact(800)
    attempt = run_one(make_job(openai_reply(text, 800 + 100, 100), shared_counter))
    assert attempt.usage.visible_output_tokens == 800
    assert attempt.visible_target_status == "within_target"
    # The rule is inclusive: the target is "at most 800", so 800 passes.
    assert visible_target_status(800, POLICY) == "within_target"


@pytest.mark.parametrize("n", [801, 850])
def test_above_800_is_over_target_and_not_truncated(exact, shared_counter, n) -> None:
    text = exact(n)
    attempt = run_one(make_job(openai_reply(text, n + 100, 100), shared_counter))
    assert attempt.outcome == "success"  # over_target is a flag, not a failure
    assert attempt.visible_target_status == "over_target"
    # Full text was measured: nothing was cut to force compliance.
    assert attempt.usage.visible_output_tokens == n
    assert attempt.answer_sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_reaching_8192_is_output_cap_reached_not_a_complete_answer(shared_counter) -> None:
    assert POLICY.hard_output_cap == 8192
    attempt = run_one(make_job(openai_reply("short visible answer", 8192, 7000), shared_counter))
    assert attempt.outcome == "failure"
    assert attempt.safe_error_category == "output_cap_reached"
    assert attempt.cap_status == "cap_reached"
    # Usage stays recorded so the spend of a capped attempt is not lost.
    assert attempt.usage.provider_output_tokens == 8192


def test_just_below_the_cap_is_still_a_success(shared_counter) -> None:
    attempt = run_one(make_job(openai_reply("short visible answer", 8191, 7000), shared_counter))
    assert attempt.outcome == "success"
    assert attempt.cap_status == "within_cap"


def test_missing_tokenizer_fails_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EVIDENCE_TOKENIZER_DIR", str(tmp_path / "absent"))
    with pytest.raises(FileNotFoundError, match="fetch_phase6_tokenizers"):
        tokenization.load_counter("o200k_base-tiktoken-0.14.0")
    # Through the orchestration, the response must not become a measured success.
    missing = lambda text: tokenization.load_counter("o200k_base-tiktoken-0.14.0")(text)
    attempt = run_one(make_job(openai_reply("some answer", 200, 50), missing))
    assert attempt.outcome == "failure"
    assert attempt.safe_error_category == "measurement_unavailable"
    assert attempt.usage.visible_output_tokens is None
    assert attempt.visible_target_status == "unmeasured"


def test_tokenizer_hash_mismatch_is_refused(monkeypatch, tmp_path) -> None:
    import hashlib as _h

    url = tokenization.TIKTOKEN_O200K_URL
    cache = tmp_path / "tiktoken"
    cache.mkdir()
    (cache / _h.sha1(url.encode()).hexdigest()).write_bytes(b"tampered vocabulary")
    monkeypatch.setenv("EVIDENCE_TOKENIZER_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="failed SHA-256 verification"):
        tokenization.load_counter("o200k_base-tiktoken-0.14.0")
    tampered = lambda text: tokenization.load_counter("o200k_base-tiktoken-0.14.0")(text)
    attempt = run_one(make_job(openai_reply("some answer", 200, 50), tampered))
    assert attempt.outcome == "failure"
    assert attempt.usage.visible_output_tokens is None


def test_native_deepseek_telemetry_is_additional_not_the_classifier(exact, shared_counter) -> None:
    native = tokenization.native_counter("deepseek-flash")
    assert native is not None
    native_counter, native_id, _sha = native
    text = exact(800)
    reply = ProviderReply({
        "model": "deepseek-flash",
        "usage": {
            "prompt_tokens": 100, "completion_tokens": 900,
            "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 100,
            "completion_tokens_details": {"reasoning_tokens": 100},
        },
        "choices": [{"message": {"content": text, "reasoning_content": "hidden"}}],
    })
    job = ProviderJob(
        job_ordinal=0, run_id="stress", case_id="case-1", arm_id="evidence_layer",
        provider_id="deepseek", requested_model_id="deepseek-flash", pricing=None,
        client=FakeClient(reply), usage_source="synthetic",
        visible_token_counter=shared_counter,
        visible_output_tokenizer_id="o200k_base-tiktoken-0.14.0",
        output_policy=OUTPUT_POLICIES["deepseek-flash"],
        native_token_counter=native_counter, native_tokenizer_id=native_id,
    )
    attempt = run_one(job)
    assert attempt.usage.visible_output_tokens == 800          # classifier: shared meter
    assert attempt.usage.native_visible_output_tokenizer_id == native_id
    assert attempt.usage.native_visible_output_tokens == native_counter(text)
    assert attempt.visible_target_status == "within_target"
