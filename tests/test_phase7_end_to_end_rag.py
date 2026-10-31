from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_phase7_end_to_end_rag as phase7  # noqa: E402
from verify_phase7_end_to_end_rag import verify_saved_run  # noqa: E402
from evidence_layer.scoring.lexical import LexicalScorer


@pytest.fixture(scope="module")
def fixed_cases_and_jev():
    cases = phase7._split_cases()
    passages = phase7._load_jsonl(phase7.PASSAGES_PATH)
    phase7._verify_candidates_against_snapshot(cases, passages)
    report, records = phase7._validate_jev_records(cases)
    return cases, report, records


def test_jev_scores_are_live_saved_phase6_outputs_for_same_200_candidates(fixed_cases_and_jev):
    cases, report, records = fixed_cases_and_jev
    assert len(records) == 200
    assert report["run"]["models_returned"] == ["jev-1.14.0"]
    assert report["run"]["counts"]["successful_scores"] == 200
    assert report["run"]["counts"]["failed_scores"] == 0
    assert report["run"]["usage"]["financial_cost_usd"] is None
    assert len([case for split in cases.values() for case in split]) == 40
    case = cases["dev"][0]
    assert phase7._historical_jev_latency_ms(case, records, "traditional_rag") == 0
    assert phase7._historical_jev_latency_ms(case, records, "evidence_layer") == sum(
        float(records[(case["case_id"], item["candidate_id"])].get("latency_ms") or 0)
        for item in case["candidates"]
    )


def test_baseline_and_evidence_layer_use_same_five_candidates_and_fixed_selection(fixed_cases_and_jev):
    cases_by_split, _report, jev_records = fixed_cases_and_jev
    scorer = LexicalScorer()
    for cases in cases_by_split.values():
        for case in cases:
            baseline_retrieved, baseline_selected, _ = phase7._evidence_rows(
                case,
                scorer=scorer,
                jev_records=jev_records,
                route="traditional_rag",
            )
            el_retrieved, el_selected, _ = phase7._evidence_rows(
                case,
                scorer=scorer,
                jev_records=jev_records,
                route="evidence_layer",
            )
            candidate_ids = {row["candidate_id"] for row in baseline_retrieved}
            assert len(candidate_ids) == 5
            assert candidate_ids == {row["candidate_id"] for row in el_retrieved}
            assert {row["candidate_id"] for row in baseline_selected} == candidate_ids
            assert len(el_selected) == 3
            assert [row["jev_score"] for row in el_retrieved] == sorted(
                (row["jev_score"] for row in el_retrieved), reverse=True
            )


def test_pair_builder_keeps_all_80_provider_case_pairs():
    cases = phase7._split_cases()
    rows = [
        {"provider": provider, "case_id": case["case_id"], "route": route}
        for provider in ("openai", "deepseek")
        for split_cases in cases.values()
        for case in split_cases
        for route in ("traditional_rag", "evidence_layer")
    ]
    pairs = phase7._pair_answers(rows)
    assert len(pairs) == 80
    assert all(set(pair) == {"traditional_rag", "evidence_layer"} for pair in pairs.values())


def test_quality_scores_and_overall_paired_deltas_are_included():
    assert phase7._metric_vector({"judge": {"correctness": 2}})["correctness"] == 2
    rows = []
    for provider in ("openai", "deepseek"):
        for case_id, split in (("rw_0001", "dev"), ("rw_0023", "validation")):
            for route, score in (("traditional_rag", 0), ("evidence_layer", 2)):
                rows.append(
                    {
                        "provider": provider,
                        "case_id": case_id,
                        "split": split,
                        "route": route,
                        "status": "ok",
                        "judge": {"correctness": score},
                    }
                )
    comparisons = phase7._build_comparisons(rows)
    for provider in ("openai", "deepseek"):
        assert comparisons[provider]["delta_summary"]["overall"]["correctness"] == 2
        assert comparisons[provider]["paired_case_bootstrap"]["splits"]["overall"]["metrics"]["correctness"]["point_estimate"] == 2


def test_provider_cost_reproduction_accounts_for_cache_hits_and_cache_writes():
    usage = {
        "input_tokens": 100,
        "cached_input_tokens": 10,
        "cache_write_tokens": 20,
        "uncached_input_tokens": 70,
        "output_tokens": 50,
    }
    expected = (70 * 2.00 + 10 * 0.10 + 20 * 2.50 + 50 * 10.00) / 1_000_000
    assert phase7._cost("openai", usage) == pytest.approx(expected)


def test_deepseek_cost_uses_pinned_v4_pro_off_peak_rate_not_flash_rate():
    usage = {
        "input_tokens": 100,
        "cached_input_tokens": 10,
        "cache_write_tokens": 0,
        "uncached_input_tokens": 90,
        "output_tokens": 50,
    }
    expected = (90 * 0.66 + 10 * 0.022 + 50 * 1.98) / 1_000_000
    assert phase7._cost("deepseek", usage) == pytest.approx(expected)
    assert phase7.PRICES["deepseek"]["pricing_tier"].startswith("off-peak")


def test_incomplete_http_200_is_retried_and_each_reported_usage_is_counted(monkeypatch):
    replies = iter(
        [
            {
                "id": "resp_incomplete",
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "usage": {"input_tokens": 40, "output_tokens": 12, "total_tokens": 52},
                "output": [],
            },
            {
                "id": "resp_complete",
                "status": "completed",
                "model": "deepseek-v4-pro",
                "usage": {"input_tokens": 40, "output_tokens": 20, "total_tokens": 60},
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "answer"}]}],
            },
        ]
    )
    monkeypatch.setattr(phase7.time, "sleep", lambda _seconds: None)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=next(replies))
        )
    )
    try:
        response = phase7._post_response(
            client,
            provider="deepseek",
            model=phase7.DEEPSEEK_MODEL,
            api_key="test-only-not-a-real-secret",
            instructions="test",
            user_input="test prompt",
            max_output_tokens=20,
        )
    finally:
        client.close()
    assert response["answer"] == "answer"
    assert response["retry_count"] == 1
    assert [attempt["provider_response_status"] for attempt in response["attempts"]] == [
        "incomplete",
        "completed",
    ]
    assert response["usage"]["input_tokens"] == 80
    assert response["usage"]["output_tokens"] == 32
    assert response["unpriced_http_200_attempts"] == 0
    assert all(attempt.get("cost_usd") is not None for attempt in response["attempts"])


def test_paired_bootstrap_is_deterministic_and_resamples_cases():
    per_case = [
        {
            "case_id": f"rw_{i:04d}",
            "split": "dev",
            "delta_evidence_layer_minus_baseline": {
                "correctness": value,
                "input_tokens": -10 * i,
            },
        }
        for i, value in enumerate((1.0, -1.0, 1.0), 1)
    ]
    first = phase7._paired_case_bootstrap(per_case)
    second = phase7._paired_case_bootstrap(per_case)
    assert first == second
    assert first["resampling_unit"].startswith("case_id")
    assert first["replicates"] == 10_000
    for metric in ("correctness", "input_tokens"):
        interval = first["splits"]["dev"]["metrics"][metric]
        assert interval["cases"] == 3
        assert interval["ci_lower"] <= interval["point_estimate"] <= interval["ci_upper"]


def test_offline_verifier_recomputes_all_saved_results_without_provider_access():
    verified = verify_saved_run()
    assert verified["status"] == "verified"
    assert verified["offline"] is True
    assert verified["network_calls"] == 0
    assert verified["answers"] == 160
    assert verified["judge_pairs"] == 80
    assert verified["phase6_jev_rows_verified"] == 200


def test_saved_artifacts_contain_no_authorization_headers_or_api_key_values():
    result_bytes = phase7.RESULT_PATH.read_bytes()
    manifest_bytes = phase7.MANIFEST_PATH.read_bytes()
    joined = result_bytes + manifest_bytes
    assert re.search(rb"(?i)authorization\s*:\s*bearer\s+", joined) is None
    assert re.search(rb"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{24,}", joined) is None
    assert b"OPENAI_API_KEY_present=true" not in joined
    assert b"DEEPSEEK_API_KEY_present=true" not in joined
    parsed = json.loads(result_bytes)
    assert parsed["cost_status"]["phase7_jev_actual_cost_usd"] is None
    assert parsed["judge"]["classification"].startswith("automated exploratory")
