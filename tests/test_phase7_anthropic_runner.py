"""Offline tests for the Phase 7 Anthropic runner helpers. No network."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_phase7_anthropic_e2e as m


def test_anthropic_cost_uses_uncached_output_and_cache_dimensions() -> None:
    usage = {
        "uncached_input_tokens": 1_000, "cache_read_tokens": 0,
        "cache_write_tokens": 0, "output_tokens": 100,
    }
    # opus: 1000 * $4/M + 100 * $20/M
    assert m.anthropic_cost("claude-opus-5-5", usage) == 0.006
    # haiku: 1000 * $0.10/M + 100 * $0.50/M
    assert m.anthropic_cost("claude-haiku-5-5", usage) == 0.00015


def test_jev_estimate_derives_from_measured_phase6_tokens() -> None:
    estimate = m.jev_estimate(40)
    assert estimate["phase6_input_tokens"] == 76_184
    assert estimate["phase6_calls"] == 200
    assert round(estimate["per_call_tokens"], 2) == 380.92
    assert round(estimate["per_case_tokens"], 1) == 1904.6
    assert round(estimate["total_estimated_usd"], 9) == 0.003199728
    assert estimate["billing_status"] == "estimate_only"
    assert estimate["invoiced_usd"] is None


def test_sanitize_removes_answer_and_passage_text() -> None:
    partial = {
        "answers": [{
            "answer": "secret answer text",
            "citation_map": {"E1": {"candidate_id": "c1"}},
            "selected_evidence": [{"candidate_id": "c1", "text": "passage body"}],
        }],
        "judges": [{"status": "ok", "raw_output": "{...}"}],
    }
    clean = m._sanitize(partial)
    record = clean["answers"][0]
    assert "answer" not in record and "citation_map" not in record
    assert "text" not in record["selected_evidence"][0]
    assert record["answer_chars"] == len("secret answer text")
    assert len(record["answer_sha256"]) == 64
    assert "raw_output" not in clean["judges"][0]
