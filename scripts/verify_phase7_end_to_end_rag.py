#!/usr/bin/env python3
"""Offline verifier for the saved Phase 7 RAG run.

This module reads local benchmark artifacts only. It does not import a provider
SDK, read API secrets, or open a network connection.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import run_phase7_end_to_end_rag as phase7  # noqa: E402
import build_real_world_dataset as rw  # noqa: E402
from evidence_layer.scoring.lexical import LexicalScorer  # noqa: E402


def _same(left: object, right: object, message: str) -> None:
    if left != right:
        raise ValueError(message)


def verify_saved_run() -> dict:
    if not phase7.RESULT_PATH.is_file() or not phase7.MANIFEST_PATH.is_file():
        raise ValueError("Phase 7 result and independent manifest must both exist")
    result_bytes = phase7.RESULT_PATH.read_bytes()
    result = json.loads(result_bytes)
    manifest = json.loads(phase7.MANIFEST_PATH.read_text(encoding="utf-8"))
    _same(
        phase7.sha256_bytes(result_bytes),
        manifest.get("results_sha256"),
        "Phase 7 result file hash does not match its independent manifest",
    )
    _same(manifest.get("results_path"), phase7.RESULT_PATH.relative_to(ROOT).as_posix(), "manifest results path mismatch")
    report_path = ROOT / "docs/phase7_end_to_end_benchmark.md"
    _same(manifest.get("report_path"), report_path.relative_to(ROOT).as_posix(), "manifest report path mismatch")
    _same(manifest.get("report_sha256"), phase7.file_sha256(report_path), "Phase 7 report hash does not match its manifest")

    input_hashes = result["run"]["input_sha256"]
    _same(input_hashes, manifest.get("input_sha256"), "manifest input fingerprint set differs")
    for relative, digest in input_hashes.items():
        path = ROOT / relative
        if not path.is_file() or phase7.file_sha256(path) != digest:
            raise ValueError(f"Phase 7 input fingerprint changed: {relative}")

    # The real-world verifier is offline and recomputes the fixed split and
    # lexical results. Then verify actual Phase 7 evidence came from that same
    # immutable source text and the exact Phase 6 JEV query/candidate universe.
    phase7._verify_real_world_offline()
    cases_by_split = phase7._split_cases()
    cases = {
        case["case_id"]: (split, case)
        for split, split_cases in cases_by_split.items()
        for case in split_cases
    }
    passages = phase7._load_jsonl(phase7.PASSAGES_PATH)
    phase7._verify_candidates_against_snapshot(cases_by_split, passages)
    phase6_jev, jev_records = phase7._validate_jev_records(cases_by_split)

    _same(len(passages), 51, "expected 51 source passages")
    _same(len(cases), 40, "expected the fixed 40 cases")
    _same(sum(len(case["candidates"]) for _, case in cases.values()), 200, "expected 200 candidates")
    _same(result["run"]["counts"]["passages"], 51, "saved passage count mismatch")
    _same(result["run"]["counts"]["cases"], 40, "saved case count mismatch")
    _same(result["run"]["counts"]["candidate_records"], 200, "saved candidate count mismatch")
    _same(result["run"]["secure_snapshot_sha256"], phase7.file_sha256(phase7.PASSAGES_PATH), "secure source snapshot digest mismatch")
    _same(result["run"]["phase6_jev_report_sha256"], phase7.file_sha256(phase7.PHASE6_JEV_PATH), "Phase 6 JEV artifact changed")
    _same(result["run"]["synthetic_freeze_manifest_sha256"], phase7.file_sha256(phase7.PHASE6_FREEZE_PATH), "synthetic freeze manifest changed")
    _same(result["run"]["synthetic_result_sha256"], phase7.file_sha256(phase7.SYNTHETIC_RESULT_PATH), "synthetic evaluation result changed")

    answers = result.get("answers", [])
    _same(len(answers), 160, "expected four saved answers for each of 40 cases")
    by_key = {}
    expected_keys = {
        (provider, case_id, route)
        for provider in ("openai", "deepseek")
        for case_id in cases
        for route in ("traditional_rag", "evidence_layer")
    }
    for row in answers:
        key = (row["provider"], row["case_id"], row["route"])
        if key in by_key:
            raise ValueError("duplicate provider/case/route answer")
        if key not in expected_keys:
            raise ValueError("answer refers to a case or route outside the fixed benchmark")
        by_key[key] = row
        split, case = cases[row["case_id"]]
        _same(row["split"], split, "answer split does not match the fixed membership")
        _same(row["task_id"], case["task"]["task_id"], "answer task ID mismatch")
        if row["status"] != "ok" or not isinstance(row.get("answer"), str) or not row["answer"]:
            raise ValueError(f"unsuccessful generation remains for {row['provider']} {row['case_id']} {row['route']}")
        _same(
            row["request_input_sha256"],
            phase7.sha256_bytes(phase7._build_answer_prompt(case, row["selected_evidence"])[0].encode()),
            "generation request input hash does not reproduce",
        )
        _same(
            row["instructions_sha256"],
            phase7.sha256_bytes(phase7.GENERATION_INSTRUCTIONS.encode()),
            "generation instruction fingerprint mismatch",
        )
        _same(
            row["response_sha256"],
            phase7.sha256_bytes(row["answer"].encode("utf-8")),
            "saved answer fingerprint mismatch",
        )
        expected_model = phase7.OPENAI_MODEL if row["provider"] == "openai" else phase7.DEEPSEEK_MODEL
        _same(row["model_requested"], expected_model, "model ID changed during experiment")
        _same(row["model_returned"], expected_model, "provider served a model ID other than the pinned model")
        _same(row["phase6_jev_score_source"], "reports/phase6_real_world_jev.json", "JEV source mismatch")

        scorer = LexicalScorer()
        retrieved, selected, _ = phase7._evidence_rows(
            case,
            scorer=scorer,
            jev_records=jev_records,
            route=row["route"],
        )
        expected_jev_latency = phase7._historical_jev_latency_ms(case, jev_records, row["route"])
        _same(
            row["historical_phase6_jev_latency_ms_for_five_scores"],
            expected_jev_latency,
            "estimated live JEV overhead does not match the route design",
        )
        expected_cached_e2e = row["api_latency_ms"] + row["preparation_latency_ms"]
        _same(row["end_to_end_latency_ms_cached_jev"], expected_cached_e2e, "cached-score end-to-end latency does not reproduce")
        _same(
            row["end_to_end_latency_ms_estimated_live_jev"],
            expected_cached_e2e + expected_jev_latency,
            "estimated live-JEV end-to-end latency does not reproduce",
        )
        _same(row["retrieved_evidence"], retrieved, "retrieved evidence does not reproduce")
        _same(row["selected_evidence"], selected, "selected Evidence Layer context does not reproduce")
        _same(row["selected_candidate_ids"], [item["candidate_id"] for item in selected], "selected evidence IDs mismatch")
        _, expected_map = phase7._build_answer_prompt(case, selected)
        _same(row["citation_map"], expected_map, "citation map does not match selected context")
        _same(row["evidence_metrics"], phase7._evidence_metrics(case, retrieved, selected), "evidence coverage metrics do not reproduce")

        citation_copy = dict(row)
        citation_copy["citation_ids"] = []
        citation_copy["citation_validation"] = {}
        phase7._finalize_citations(citation_copy)
        _same(row["citation_ids"], citation_copy["citation_ids"], "citation IDs do not reproduce")
        _same(row["citation_validation"], citation_copy["citation_validation"], "citation reference metrics do not reproduce")
        usage = row["usage"]
        _same(row["llm_cost_usd"], round(phase7._cost(row["provider"], usage), 12), "LLM cost does not reproduce from provider usage and pinned prices")

    _same(set(by_key), expected_keys, "same four-arm, 40-case answer universe is required")
    for provider in ("openai", "deepseek"):
        for case_id in cases:
            base = by_key[(provider, case_id, "traditional_rag")]
            evidence = by_key[(provider, case_id, "evidence_layer")]
            _same(
                {item["candidate_id"] for item in base["retrieved_evidence"]},
                {item["candidate_id"] for item in evidence["retrieved_evidence"]},
                "paired routes do not share the same candidate pool",
            )
            _same(base["task_id"], evidence["task_id"], "paired routes do not share the same question")

    _same(
        result["run"]["answer_outputs_sha256"],
        phase7.sha256_bytes(phase7.canonical_bytes(answers)),
        "answer output aggregate hash mismatch",
    )
    judge_rows = result["judge"]["pairs"]
    _same(len(judge_rows), 80, "expected 80 paired blind judge records")
    _same(
        result["run"]["judge_outputs_sha256"],
        phase7.sha256_bytes(phase7.canonical_bytes(judge_rows)),
        "judge output aggregate hash mismatch",
    )
    judge_key_set = {(row["provider_pair"], row["case_id"]) for row in judge_rows}
    expected_judge_key_set = {(provider, case_id) for provider in ("openai", "deepseek") for case_id in cases}
    _same(judge_key_set, expected_judge_key_set, "judge records do not cover all paired outputs")
    if any(row.get("status") != "ok" for row in judge_rows):
        raise ValueError("one or more paired automated judge calls failed")
    for pair in judge_rows:
        for route in ("traditional_rag", "evidence_layer"):
            answer = by_key[(pair["provider_pair"], pair["case_id"], route)]
            _same(answer["judge"], pair["scores"][route], "blind judge score is not attached to the matching answer")

    reproduced_comparisons = phase7._build_comparisons(answers)
    _same(result["comparisons"], reproduced_comparisons, "per-split summary, paired deltas, or bootstrap does not reproduce")

    expected_counts = {
        "passages": 51,
        "cases": 40,
        "candidate_records": 200,
        "generation_answers_expected": 160,
        "generation_answers_saved": 160,
        "judge_pairs_expected": 80,
        "judge_pairs_saved": 80,
    }
    for key, value in expected_counts.items():
        _same(result["run"]["counts"].get(key), value, f"saved run count mismatch for {key}")
    for split, count in {"dev": 88, "validation": 40, "exposed_test": 32}.items():
        _same(result["run"]["counts"]["case_arm_records_by_split"][split], count, f"saved arm count mismatch for {split}")

    request_counts = result["run"]["counts"]["http_requests_by_kind"]
    for provider in ("openai", "deepseek"):
        provider_rows = [row for row in answers if row["provider"] == provider]
        _same(
            request_counts[f"{provider}_generation_requests"],
            sum(len(row.get("attempts", [])) for row in provider_rows),
            f"{provider} generation request count does not reproduce from attempt ledgers",
        )
        _same(
            request_counts[f"{provider}_generation_retries"],
            sum(max(0, len(row.get("attempts", [])) - 1) for row in provider_rows),
            f"{provider} retry count does not reproduce from attempt ledgers",
        )
        recovered_errors = sum(len(row.get("recovered_failure_history", [])) for row in provider_rows)
        attempt_errors = sum(
            bool(attempt.get("error_category"))
            for row in provider_rows
            for attempt in row.get("attempts", [])
        )
        _same(
            request_counts[f"{provider}_generation_errors"],
            recovered_errors + attempt_errors,
            f"{provider} generation error count does not reproduce from attempt ledgers",
        )
    _same(request_counts["judge_requests"], sum(len(row.get("attempts", [])) for row in judge_rows), "judge request count mismatch")
    _same(request_counts["judge_retries"], sum(max(0, len(row.get("attempts", [])) - 1) for row in judge_rows), "judge retry count mismatch")
    _same(request_counts["judge_errors"], 0, "saved run includes an unsuccessful judge response")
    model_check = result["run"].get("model_listing_check_sessions") or {}
    if not isinstance(model_check.get("sessions"), int) or model_check["sessions"] < 1:
        raise ValueError("provider model availability check session count is missing")
    _same(model_check.get("providers_checked_per_session"), 2, "both provider accounts must be checked in each run session")
    _same(
        request_counts["provider_model_listing_requests"],
        model_check["sessions"] * model_check["providers_checked_per_session"],
        "model listing request count mismatch",
    )

    _same(phase6_jev["run"]["counts"]["successful_scores"], 200, "Phase 6 JEV score set no longer verifies")
    _same(result["cost_status"]["total_system_cost_usd"], None, "JEV cost must remain unknown unless provider reports it")
    _same(result["cost_status"]["new_jev_calls_during_phase7"], 0, "Phase 7 must document that it reused Phase 6 JEV outputs")
    _same(result["judge"]["classification"].startswith("automated exploratory evaluator"), True, "automated judge limitation is missing")
    _same(result["run"]["synthetic_inputs_unchanged"], True, "synthetic artifact preservation flag is false")

    return {
        "status": "verified",
        "offline": True,
        "network_calls": 0,
        "answers": len(answers),
        "judge_pairs": len(judge_rows),
        "cases": len(cases),
        "candidate_records": 200,
        "phase6_jev_rows_verified": 200,
        "synthetic_manifest_sha256": result["run"]["synthetic_freeze_manifest_sha256"],
        "synthetic_result_sha256": result["run"]["synthetic_result_sha256"],
        "overall": {
            provider: {
                route: result["comparisons"][provider]["routes"][route]["overall"]["mean"]
                for route in ("traditional_rag", "evidence_layer")
            }
            for provider in ("openai", "deepseek")
        },
    }


def main() -> None:
    summary = verify_saved_run()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
