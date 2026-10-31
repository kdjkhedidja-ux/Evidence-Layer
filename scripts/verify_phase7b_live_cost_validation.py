#!/usr/bin/env python3
"""Recompute Phase 7B artifacts offline without contacting any provider."""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import run_phase7b_live_cost_validation as phase7b
import run_phase7_end_to_end_rag as phase7
import run_real_world_jev_evaluation as phase6_jev
from evidence_layer.scoring.lexical import LexicalScorer


def _same(actual: object, expected: object, message: str) -> None:
    if actual != expected:
        raise ValueError(message)


def _near(actual: float, expected: float, message: str, *, tolerance: float = 1e-10) -> None:
    if not math.isfinite(actual) or abs(actual - expected) > tolerance:
        raise ValueError(message)


def verify_saved_run() -> dict:
    if not phase7b.RESULT_PATH.is_file() or not phase7b.MANIFEST_PATH.is_file() or not phase7b.REPORT_PATH.is_file():
        raise ValueError("Phase 7B result, manifest, and report are all required")
    result_bytes = phase7b.RESULT_PATH.read_bytes()
    result = json.loads(result_bytes)
    manifest = json.loads(phase7b.MANIFEST_PATH.read_text(encoding="utf-8"))
    _same(phase7b._sha256_bytes(result_bytes), manifest.get("results_sha256"), "results hash does not match manifest")
    _same(phase7b._file_sha256(phase7b.REPORT_PATH), manifest.get("report_sha256"), "report hash does not match manifest")
    _same(phase7b._file_sha256(phase7b.REPORT_PATH), phase7b._sha256_bytes(phase7b._render_report(result).encode()), "report does not reproduce from results")
    _same(manifest.get("benchmark"), result.get("benchmark"), "manifest benchmark mismatch")
    _same(manifest.get("run_id"), result["run"]["run_id"], "manifest run ID mismatch")

    # This preflight is local only: deterministic benchmark verifier, no JEV
    # requests, no model provider calls, and no writes to frozen artifacts.
    phase7._verify_real_world_offline()
    current_inputs = phase7b._input_hashes()
    _same(result["run"].get("input_sha256"), current_inputs, "Phase 7B input fingerprints do not reproduce")
    _same(manifest.get("input_sha256"), current_inputs, "manifest input fingerprints differ")
    _same(manifest.get("artifact_builder_input_sha256"), current_inputs, "manifest artifact builder fingerprints differ")
    runner_path = Path(phase7b.__file__).resolve().relative_to(phase7b.ROOT).as_posix()
    _same(result["run"].get("artifact_builder_input_sha256"), current_inputs, "artifact builder fingerprints do not reproduce")
    _same(result["run"].get("artifact_builder_source_sha256"), current_inputs[runner_path], "artifact builder source hash mismatch")
    _same(result["run"].get("runner_source_sha256_at_api_execution"), result["run"].get("api_execution_input_sha256", {}).get(runner_path), "API execution source hash mismatch")
    execution_inputs = result["run"].get("api_execution_input_sha256")
    _same(manifest.get("api_execution_input_sha256"), execution_inputs, "manifest API execution fingerprints differ")
    correction = result["run"].get("postrun_artifact_correction")
    if correction:
        expected_execution_inputs = dict(current_inputs)
        expected_execution_inputs[runner_path] = result["run"]["runner_source_sha256_at_api_execution"]
        _same(execution_inputs, expected_execution_inputs, "post-run correction changed inputs beyond the recorded runner source")
        _same(correction.get("benchmark_outputs_unchanged"), True, "post-run correction must preserve API responses and metric values")
    else:
        _same(execution_inputs, current_inputs, "API execution and artifact builder inputs differ without a correction record")
    cases_by_split = phase7._split_cases()
    passages = phase7._load_jsonl(phase7.PASSAGES_PATH)
    phase7._verify_candidates_against_snapshot(cases_by_split, passages)
    cases = {
        case["case_id"]: (split, case)
        for split, split_cases in cases_by_split.items()
        for case in split_cases
    }
    _same(len(passages), 51, "expected 51 passages")
    _same(len(cases), 40, "expected 40 cases")
    _same(sum(len(case["candidates"]) for _, case in cases.values()), 200, "expected 200 candidate records")
    _same(result["run"]["counts"]["passages"], 51, "saved passage count mismatch")
    _same(result["run"]["counts"]["cases"], 40, "saved case count mismatch")
    _same(result["run"]["counts"]["candidate_records"], 200, "saved candidate count mismatch")
    _same(result["run"]["counts"]["case_arm_records_by_split"], phase7b._case_arm_counts(result.get("answers", [])), "answer arm split counts do not reproduce")
    _same(result["run"]["synthetic_freeze_manifest_sha256"], current_inputs["data/phase6_freeze_manifest.json"], "synthetic freeze manifest hash mismatch")
    _same(result["run"]["synthetic_result_sha256"], current_inputs["reports/phase6_quality_evaluation.json"], "synthetic result hash mismatch")
    _same(result["run"]["secure_snapshot_sha256"], phase7b._file_sha256(phase7.PASSAGES_PATH), "secure snapshot hash mismatch")

    expected_jev = {
        (provider, case_id, candidate["candidate_id"]): (split, case, candidate)
        for provider in phase7b.PROVIDERS
        for case_id, (split, case) in cases.items()
        for candidate in case["candidates"]
    }
    jev_rows = result.get("jev_outputs", [])
    _same(phase7b._sha256_bytes(phase7b._canonical_bytes(jev_rows)), result["run"].get("jev_output_sha256"), "JEV aggregate hash mismatch")
    by_jev_key: dict[tuple[str, str, str], list[dict]] = {}
    for row in jev_rows:
        key = (row["provider_arm"], row["case_id"], row["candidate_id"])
        if key not in expected_jev:
            raise ValueError("JEV output refers to a query/candidate outside Real-World v1")
        split, case, candidate = expected_jev[key]
        _same(row["split"], split, "JEV split mismatch")
        _same(row["query_sha256"], phase7b._sha256_bytes(case["task"]["input"].encode()), "JEV query hash mismatch")
        _same(row["candidate_text_sha256"], phase7b._sha256_bytes(candidate["text"].encode()), "JEV candidate hash mismatch")
        _same(row["model_requested"], phase7b.DEFAULT_JEV_MODEL, "JEV requested model changed")
        _same(row["sdk_version"], "0.7.2", "JEV SDK version mismatch")
        if row.get("score") is not None and (isinstance(row["score"], bool) or not 0 <= float(row["score"]) <= 1):
            raise ValueError("JEV score must be between zero and one")
        expected_cost = phase7b._jev_input_cost_usd(row.get("input_tokens"))
        _same(row.get("estimated_cost_usd"), expected_cost, "JEV price computation mismatch")
        by_jev_key.setdefault(key, []).append(row)
    if set(by_jev_key) != set(expected_jev):
        raise ValueError("live JEV requests do not cover all 400 provider/case/candidate records")
    jev_score_maps = {}
    for key, rows in by_jev_key.items():
        attempts = [row["attempt"] for row in rows]
        _same(attempts, sorted(set(attempts)), "JEV attempts are duplicated or out of order")
        successes = [row for row in rows if row.get("score") is not None]
        _same(len(successes), 1, "each live JEV candidate must have exactly one successful score")
        jev_score_maps[key] = successes[0]
    _same(len(jev_score_maps), 400, "expected 400 successful live JEV scores")

    answers = result.get("answers", [])
    _same(len(answers), 160, "expected 160 answer records")
    _same(phase7b._sha256_bytes(phase7b._canonical_bytes(answers)), result["run"].get("answer_sha256"), "answer aggregate hash mismatch")
    expected_answer_keys = {
        (provider, case_id, route)
        for provider in phase7b.PROVIDERS for case_id in cases for route in phase7b.ROUTES
    }
    by_answer_key = {}
    scorer = LexicalScorer()
    for row in answers:
        key = (row["provider"], row["case_id"], row["route"])
        if key in by_answer_key or key not in expected_answer_keys:
            raise ValueError("duplicate or unexpected Phase 7B answer arm")
        by_answer_key[key] = row
        split, case = cases[row["case_id"]]
        _same(row["split"], split, "answer split does not match fixed case membership")
        _same(row["task_id"], case["task"]["task_id"], "answer task ID mismatch")
        _same(row["status"], "ok", "saved live run has an unsuccessful answer")
        _same(row["model_requested"], phase7.OPENAI_MODEL if row["provider"] == "openai" else phase7.DEEPSEEK_MODEL, "answer model mismatch")
        _same(row["model_returned"], row["model_requested"], "provider served an unexpected answer model ID")
        scores = (
            {candidate["candidate_id"]: jev_score_maps[(row["provider"], row["case_id"], candidate["candidate_id"])] for candidate in case["candidates"]}
            if row["route"] == "evidence_layer" else {}
        )
        retrieved, selected, _selection_ms = phase7b._route_evidence(case, scorer, row["route"], scores)
        _same(row["retrieved_evidence"], retrieved, "retrieved passage order does not reproduce")
        _same(row["selected_evidence"], selected, "selected context does not reproduce")
        _same(row["selected_candidate_ids"], [item["candidate_id"] for item in selected], "selected IDs mismatch")
        user_input, citation_map = phase7._build_answer_prompt(case, selected)
        _same(row["citation_map"], citation_map, "citation source map mismatch")
        _same(row["request_input_sha256"], phase7b._sha256_bytes(user_input.encode()), "answer prompt hash mismatch")
        _same(row["instructions_sha256"], phase7b._sha256_bytes(phase7.GENERATION_INSTRUCTIONS.encode()), "answer prompt version mismatch")
        _same(row["response_sha256"], phase7b._sha256_bytes(row["answer"].encode()), "answer response hash mismatch")
        _same(row["evidence_metrics"], phase7._evidence_metrics(case, retrieved, selected), "evidence coverage metrics mismatch")
        _near(float(row["llm_cost_usd"]), round(phase7._cost(row["provider"], row["usage"]), 12), "LLM API price computation mismatch")
        if row["route"] == "traditional_rag":
            _near(float(row["jev_cost_usd"]), 0.0, "traditional RAG must have zero JEV cost")
        else:
            case_rows = [
                attempt for jkey, attempts in by_jev_key.items()
                if jkey[0] == row["provider"] and jkey[1] == row["case_id"]
                for attempt in attempts
            ]
            expected_jev_cost = (
                round(sum(float(attempt["estimated_cost_usd"]) for attempt in case_rows), 12)
                if case_rows and all(attempt.get("estimated_cost_usd") is not None for attempt in case_rows)
                else None
            )
            _same(row.get("jev_cost_usd"), expected_jev_cost, "live JEV cost does not recompute from attempt usage")
        expected_system = (
            round(float(row["llm_cost_usd"]) + float(row["jev_cost_usd"]), 12)
            if row.get("jev_cost_usd") is not None else None
        )
        _same(row.get("system_api_cost_usd"), expected_system, "system API cost mismatch")
        citation_copy = dict(row)
        citation_copy["citation_ids"] = []
        citation_copy["citation_validation"] = {}
        phase7._finalize_citations(citation_copy)
        _same(row["citation_ids"], citation_copy["citation_ids"], "citation references do not reproduce")
        _same(row["citation_validation"], citation_copy["citation_validation"], "citation validation does not reproduce")
        _near(float(row["end_to_end_latency_ms"]), float(row["end_to_end_latency_ms"]), "invalid end-to-end latency")
        minimum_latency = float(row["api_latency_ms"]) + float(row.get("jev_case_latency_ms", 0.0))
        if float(row["end_to_end_latency_ms"]) + 1.0 < minimum_latency:
            raise ValueError("end-to-end latency is shorter than its measured API components")
    _same(set(by_answer_key), expected_answer_keys, "all four arms must use the same 40-case set")
    for provider in phase7b.PROVIDERS:
        for case_id in cases:
            base = by_answer_key[(provider, case_id, "traditional_rag")]
            evidence = by_answer_key[(provider, case_id, "evidence_layer")]
            _same(
                {item["candidate_id"] for item in base["retrieved_evidence"]},
                {item["candidate_id"] for item in evidence["retrieved_evidence"]},
                "paired routes do not share the same five candidates",
            )

    judge_rows = result["judge"]["pairs"]
    _same(len(judge_rows), 80, "expected 80 paired judge records")
    _same(phase7b._sha256_bytes(phase7b._canonical_bytes(judge_rows)), result["run"].get("judge_output_sha256"), "judge aggregate hash mismatch")
    judged_keys = set()
    for judge in judge_rows:
        key = (judge["provider_pair"], judge["case_id"])
        if key in judged_keys or judge.get("status") != "ok":
            raise ValueError("duplicate or unsuccessful paired judge record")
        judged_keys.add(key)
        for route in phase7b.ROUTES:
            _same(by_answer_key[(key[0], key[1], route)]["judge"], judge["scores"][route], "judge score attached to wrong answer")
    _same(result["comparisons"], phase7b._paired_analysis(answers), "paired means or bootstrap intervals do not reproduce")
    _same(result["phase7_reference_comparison"], phase7b._compare_with_phase7(answers), "Phase 7 comparison does not reproduce")
    _same(result["run"]["counts"]["jev_scores_saved"], 400, "saved JEV success count mismatch")
    _same(result["run"]["counts"]["generation_answers_saved"], 160, "saved answer count mismatch")
    _same(result["run"]["counts"]["judge_pairs_saved"], 80, "saved judge count mismatch")

    joined = result_bytes + phase7b.MANIFEST_PATH.read_bytes() + phase7b.REPORT_PATH.read_bytes()
    if re.search(rb"(?i)authorization\s*:\s*bearer\s+", joined):
        raise ValueError("saved Phase 7B artifacts contain an authorization header")
    if re.search(rb"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{24,}", joined):
        raise ValueError("saved Phase 7B artifacts contain a key-like secret")
    for secret_name in (b"OPENAI_API_KEY=", b"DEEPSEEK_API_KEY=", b"TYPESAFE_API_KEY="):
        if secret_name in joined:
            raise ValueError("saved Phase 7B artifacts contain an API key variable assignment")
    return {
        "status": "verified",
        "offline": True,
        "network_calls": 0,
        "passages": 51,
        "cases": 40,
        "candidate_records": 200,
        "answer_arms": len(answers),
        "live_jev_score_keys": len(jev_score_maps),
        "live_jev_attempt_records": len(jev_rows),
        "judge_pairs": len(judge_rows),
        "phase7_reference_unchanged": True,
        "synthetic_artifacts_unchanged": True,
    }


def main() -> int:
    verified = verify_saved_run()
    print(json.dumps(verified, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
