from __future__ import annotations

import json
import re
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_phase7b_live_cost_validation as phase7b
from verify_phase7b_live_cost_validation import verify_saved_run


def _case() -> dict:
    candidates = [
        {
            "candidate_id": f"p{i}",
            "source_id": "source",
            "source_url": "https://example.test/source",
            "metadata": {"section": f"Section {i}"},
            "text": text,
        }
        for i, text in enumerate(("alpha beta", "alpha", "beta", "unrelated", "alpha beta beta"), 1)
    ]
    return {
        "case_id": "rw_0001",
        "task": {"task_id": "q-1", "input": "alpha beta"},
        "candidates": candidates,
        "gold_candidate_ids": ["p1", "p5"],
        "relevance_scores": {f"p{i}": 2 if i in {1, 5} else 0 for i in range(1, 6)},
    }


def test_phase7b_routes_share_candidate_set_and_select_only_three_for_evidence_layer():
    case = _case()
    live_scores = {
        f"p{i}": {"score": i / 5, "estimated_cost_usd": 0.000001}
        for i in range(1, 6)
    }
    scorer = phase7b.LexicalScorer()
    base_retrieved, base_selected, _ = phase7b._route_evidence(case, scorer, "traditional_rag", live_scores)
    el_retrieved, el_selected, _ = phase7b._route_evidence(case, scorer, "evidence_layer", live_scores)
    assert {row["candidate_id"] for row in base_retrieved} == {row["candidate_id"] for row in el_retrieved}
    assert [row["candidate_id"] for row in el_retrieved] == ["p5", "p4", "p3", "p2", "p1"]
    assert len(base_selected) == 5
    assert [row["candidate_id"] for row in el_selected] == ["p5", "p4", "p3"]
    assert all(row["jev_score"] is None for row in base_retrieved)


def test_jev_published_price_formula_uses_input_tokens_only():
    assert phase7b._jev_input_cost_usd(125_000) == pytest.approx(0.00525)
    assert phase7b._jev_input_cost_usd(0) == 0.0
    assert phase7b._jev_input_cost_usd(None) is None
    assert phase7b.JEV_OUTPUT_USD_PER_MILLION == 0.0


def test_phase7b_paired_bootstrap_is_case_paired_and_deterministic():
    answers = []
    for provider in phase7b.PROVIDERS:
        for route, score, tokens, cost, latency in (
            ("traditional_rag", 1.0, 100, 0.001, 200.0),
            ("evidence_layer", 2.0, 70, 0.0009, 240.0),
        ):
            answers.append({
                "provider": provider,
                "case_id": "rw_0001",
                "split": "dev",
                "route": route,
                "status": "ok",
                "judge": {"correctness": score, "completeness": score, "groundedness": score, "citation_correctness": score},
                "citation_validation": {"citation_reference_validity": 1.0, "citation_relevance_precision": 1.0},
                "evidence_metrics": {"gold_passage_recall": score / 2},
                "usage": {"input_tokens": tokens, "output_tokens": 20, "total_tokens": tokens + 20},
                "llm_cost_usd": cost,
                "jev_cost_usd": 0.0 if route == "traditional_rag" else 0.0001,
                "system_api_cost_usd": cost if route == "traditional_rag" else cost + 0.0001,
                "end_to_end_latency_ms": latency,
            })
    first = phase7b._paired_analysis(answers)
    second = phase7b._paired_analysis(answers)
    assert first == second
    for provider in phase7b.PROVIDERS:
        bootstrap = first[provider]["paired_case_bootstrap"]
        assert bootstrap["resampling_unit"].startswith("case_id")
        assert bootstrap["replicates"] == 10_000
        assert bootstrap["splits"]["dev"]["metrics"]["total_tokens"]["point_estimate"] == -30
        assert bootstrap["splits"]["dev"]["metrics"]["system_api_cost_usd"]["point_estimate"] == pytest.approx(0.0000)


def test_phase7b_case_arm_count_reports_overall_total():
    rows = [
        {"split": "dev"},
        {"split": "validation"},
        {"split": "exposed_test"},
    ]
    assert phase7b._case_arm_counts(rows) == {
        "dev": 1,
        "validation": 1,
        "exposed_test": 1,
        "overall": 3,
    }


def test_phase7b_offline_verifier_recomputes_saved_live_run_without_api_calls(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("offline verifier attempted a provider call")

    monkeypatch.setattr(phase7b, "_model_listing", fail_if_called)
    monkeypatch.setattr(phase7b, "_jev_model_listing", fail_if_called)
    monkeypatch.setattr(phase7b, "_new_jev_transport", fail_if_called)
    monkeypatch.setattr(phase7b.phase7, "_post_response", fail_if_called)
    monkeypatch.setattr(phase7b.phase7, "_judge_request", fail_if_called)
    verified = verify_saved_run()
    assert verified["status"] == "verified"
    assert verified["offline"] is True
    assert verified["network_calls"] == 0
    assert verified["passages"] == 51
    assert verified["cases"] == 40
    assert verified["candidate_records"] == 200
    assert verified["answer_arms"] == 160
    assert verified["live_jev_score_keys"] == 400
    assert verified["judge_pairs"] == 80


def test_phase7b_saved_artifacts_do_not_contain_api_secrets_or_auth_headers():
    blobs = (
        phase7b.RESULT_PATH.read_bytes(),
        phase7b.MANIFEST_PATH.read_bytes(),
        phase7b.REPORT_PATH.read_bytes(),
    )
    joined = b"\n".join(blobs)
    assert re.search(rb"(?i)authorization\s*:\s*bearer\s+", joined) is None
    assert re.search(rb"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{24,}", joined) is None
    assert all(name not in joined for name in (b"OPENAI_API_KEY=", b"DEEPSEEK_API_KEY=", b"TYPESAFE_API_KEY="))
    result = json.loads(blobs[0])
    assert result["cost_status"]["jev_account_invoice_cost_usd"] is None
    assert result["held_out_status"] == "exposed previously; not a blind test"
