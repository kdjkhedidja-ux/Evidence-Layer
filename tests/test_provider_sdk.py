"""Offline tests for the live-capable provider adapters.

Responses are synthetic, shaped after each provider's documented usage schema.
httpx.MockTransport replaces the network entirely; no test contacts a provider.
"""

import asyncio
import json

import httpx
import pytest

from evidence_layer import tokenization
from evidence_layer.output_policy import OUTPUT_POLICIES
from evidence_layer.provider_execution import ProviderJob, ProviderRequestError
from evidence_layer.provider_sdk import (
    ENDPOINT,
    LIVE_FLAG,
    ROSTER_PROVIDER,
    LiveProviderClient,
    build_http_request,
    live_enabled,
)

KEY = "sk-test-SECRET-VALUE-123"


def openai_ok(model="gpt-6.1-sol", output=120, text="visible answer"):
    body = {
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {
            "input_tokens": 100, "output_tokens": output,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0,
                                     "cache_write_tokens_details": {"ttl_30m_tokens": 0, "ttl_12h_tokens": 0}},
            "output_tokens_details": {"reasoning_tokens": 40},
        },
    }
    if model is not None:
        body["model"] = model
    return body


def anthropic_ok(model="claude-opus-5-5", output=120, text="visible answer"):
    body = {
        "stop_reason": "end_turn",
        "content": [{"type": "thinking", "thinking": "hidden"}, {"type": "text", "text": text}],
        "usage": {"input_tokens": 80, "output_tokens": output, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0,
                  "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0}},
    }
    if model is not None:
        body["model"] = model
    return body


def deepseek_ok(model="deepseek-flash", output=120, text="visible answer"):
    body = {
        "choices": [{"finish_reason": "stop",
                     "message": {"content": text, "reasoning_content": "hidden"}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": output,
                  "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 100,
                  "completion_tokens_details": {"reasoning_tokens": 40}},
    }
    if model is not None:
        body["model"] = model
    return body


def transport_for(response_body=None, status=200, *, raises=None, record=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if record is not None:
            record.append(request)
        if raises is not None:
            raise raises
        return httpx.Response(status, content=json.dumps(response_body).encode()
                              if not isinstance(response_body, bytes) else response_body)
    return httpx.MockTransport(handler)


def run(model, transport, *, cap=8192, policy=None, counter=None, prompt="Answer briefly."):
    if counter is None:
        counter = tokenization.shared_visible_counter()[0]  # shared meter, as in production
    client = LiveProviderClient(model, api_key=KEY, transport=transport)
    job = ProviderJob(
        job_ordinal=0, run_id="sdk-test", case_id="case-1", arm_id="evidence_layer",
        provider_id=ROSTER_PROVIDER[model], requested_model_id=model, pricing=None,
        client=client, usage_source="provider_reported",
        visible_token_counter=counter, visible_output_tokenizer_id="o200k_base-tiktoken-0.14.0",
        output_policy=policy if policy is not None else OUTPUT_POLICIES[model], prompt=prompt,
    )
    from evidence_layer.provider_execution import run_provider_job
    return asyncio.run(run_provider_job(job, max_attempts=1))[0]


# ---- request shapes -------------------------------------------------------

@pytest.mark.parametrize("model", sorted(ROSTER_PROVIDER))
def test_request_pins_model_cap_and_keeps_key_out_of_body(model) -> None:
    req = build_http_request(model, "hello", 8192, KEY)
    assert req.url == ENDPOINT[ROSTER_PROVIDER[model]]
    assert req.body["model"] == model
    assert KEY not in json.dumps(req.body)
    assert any(KEY in value for value in req.headers.values())  # key only in auth header


def test_request_shapes_follow_the_frozen_profiles() -> None:
    openai = build_http_request("gpt-6.1-sol", "p", 8192, KEY).body
    assert openai["max_output_tokens"] == 8192 and openai["store"] is False
    assert openai["reasoning"] == {"effort": "low"} and "temperature" not in openai

    anth = build_http_request("claude-opus-5-5", "p", 8192, KEY)
    assert anth.headers["anthropic-version"] == "2026-10-21"
    assert anth.body["max_tokens"] == 8192 and anth.body["output_config"] == {"effort": "medium"}

    ds = build_http_request("deepseek-flash", "p", 8192, KEY).body
    assert ds["max_tokens"] == 8192 and ds["temperature"] == 0 and ds["top_p"] == 1
    assert ds["thinking"] == {"type": "disabled"}


def test_unknown_model_is_rejected() -> None:
    with pytest.raises(ValueError, match="not in roster v1"):
        build_http_request("gpt-6-astra", "p", 8192, KEY)


# ---- response mapping ------------------------------------------------------

def test_openai_success_maps_returned_model_usage_and_visible_text() -> None:
    record: list = []
    attempt = run("gpt-6.1-sol", transport_for(openai_ok(), record=record))
    assert len(record) == 1  # exactly one HTTP request
    assert attempt.outcome == "success"
    assert attempt.returned_model_id == "gpt-6.1-sol"
    assert attempt.usage.provider_output_tokens == 120
    assert attempt.usage.reasoning_tokens == 40
    assert attempt.usage.visible_output_tokens is not None
    assert attempt.answer_sha256 is not None


def test_anthropic_success_excludes_thinking_from_visible_text() -> None:
    attempt = run("claude-opus-5-5", transport_for(anthropic_ok()))
    assert attempt.outcome == "success"
    assert attempt.usage.reasoning_tokens is None  # no reasoning sub-count: never invented
    assert attempt.usage.provider_output_tokens == 120


def test_deepseek_success_maps_reasoning_and_excludes_reasoning_content() -> None:
    attempt = run("deepseek-flash", transport_for(deepseek_ok()))
    assert attempt.outcome == "success"
    assert attempt.usage.reasoning_tokens == 40
    assert attempt.returned_model_id == "deepseek-flash"


def test_missing_returned_model_is_not_replaced_by_requested() -> None:
    attempt = run("gpt-6.1-sol", transport_for(openai_ok(model=None)))
    assert attempt.returned_model_id is None
    assert attempt.outcome == "failure"
    assert attempt.safe_error_category == "returned_model_missing"


def test_returned_model_mismatch_is_recorded_not_relabelled() -> None:
    attempt = run("gpt-6.1-sol", transport_for(openai_ok(model="gpt-6.1-terra")))
    assert attempt.returned_model_id == "gpt-6.1-terra"
    assert attempt.safe_error_category == "returned_model_mismatch"


def test_reaching_the_cap_is_output_cap_reached() -> None:
    cap = OUTPUT_POLICIES["gpt-6.1-sol"].hard_output_cap  # 8192
    body = openai_ok(output=cap)
    body["status"] = "incomplete"
    body["incomplete_details"] = {"reason": "max_output_tokens"}
    attempt = run("gpt-6.1-sol", transport_for(body))
    assert attempt.outcome == "failure"
    assert attempt.safe_error_category == "output_cap_reached"
    assert attempt.usage.provider_output_tokens == cap  # usage kept


def test_length_stop_below_the_cap_is_partial() -> None:
    body = deepseek_ok(output=300)
    body["choices"][0]["finish_reason"] = "length"
    attempt = run("deepseek-flash", transport_for(body))
    assert attempt.outcome == "failure"
    assert attempt.safe_error_category == "partial_response"


def test_over_target_is_measured_on_the_shared_meter_not_truncated() -> None:
    counter, _id, _sha = tokenization.shared_visible_counter()
    from evidence_layer.synthetic_long_output import exact_visible_text
    text = exact_visible_text(850, counter)
    attempt = run("claude-opus-5-5", transport_for(anthropic_ok(text=text, output=900)), counter=counter)
    assert attempt.outcome == "success"
    assert attempt.usage.visible_output_tokens == 850
    assert attempt.visible_target_status == "over_target"


# ---- HTTP error surfaces ---------------------------------------------------

@pytest.mark.parametrize("status,category", [(401, "request_rejected"), (400, "request_rejected"),
                                             (429, "rate_limited"), (503, "transport_error")])
def test_http_status_maps_to_safe_category(status, category) -> None:
    attempt = run("gpt-6.1-sol", transport_for({"error": {"message": f"secret {KEY}", "code": "some_code"}}, status))
    assert attempt.outcome == "failure"
    assert attempt.safe_error_category == category
    assert attempt.http_status == status
    assert attempt.provider_error_code == "some_code"
    assert KEY not in repr(attempt)  # sanitized: provider text never surfaces


def test_timeout_maps_to_timeout() -> None:
    attempt = run("gpt-6.1-sol", transport_for(raises=httpx.ReadTimeout("slow")))
    assert attempt.safe_error_category == "timeout"


def test_transport_failure_is_sanitized() -> None:
    attempt = run("gpt-6.1-sol", transport_for(raises=httpx.ConnectError(f"refused {KEY}")))
    assert attempt.safe_error_category == "transport_error"
    assert KEY not in repr(attempt)


def test_non_json_body_is_unknown_error() -> None:
    attempt = run("gpt-6.1-sol", transport_for(b"<html>gateway</html>", 200))
    assert attempt.safe_error_category == "unknown_error"


# ---- activation gate -------------------------------------------------------

def test_live_calls_are_off_by_default() -> None:
    assert live_enabled({}) is False
    assert live_enabled({LIVE_FLAG: "true"}) is False  # only the exact opt-in value


def test_from_environment_refuses_without_opt_in() -> None:
    with pytest.raises(RuntimeError, match="PHASE6_LIVE_SDK=1"):
        LiveProviderClient.from_environment("gpt-6.1-sol", environ={"OPENAI_API_KEY": KEY})


def test_from_environment_requires_the_provider_key() -> None:
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        LiveProviderClient.from_environment("gpt-6.1-sol", environ={LIVE_FLAG: "1"})


def test_from_environment_uses_the_right_key_per_family() -> None:
    env = {LIVE_FLAG: "1", "ANTHROPIC_API_KEY": "anth", "OPENAI_API_KEY": "oai", "DEEPSEEK_API_KEY": "ds"}
    assert "anth" not in repr(LiveProviderClient.from_environment("claude-haiku-5-5", environ=env))
    client = LiveProviderClient.from_environment("deepseek-v4-pro", environ=env)
    assert "ds" not in repr(client)


def test_client_repr_never_contains_the_key() -> None:
    assert KEY not in repr(LiveProviderClient("gpt-6.1-sol", api_key=KEY))


def test_live_smoke_refuses_without_opt_in() -> None:
    import os
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "phase6_live_smoke.py"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PHASE6_LIVE", "OPENAI_", "ANTHROPIC_", "DEEPSEEK_"))}
    result = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 2
    assert "refused" in result.stderr
    assert result.stdout == ""  # no network, no output
