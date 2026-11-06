#!/usr/bin/env python3
"""Run the Phase 6 lexical and Evidence Layer arms without network access.

This runner writes only to an explicit output directory. It does not call JEV,
answer models, or providers, and it never overwrites frozen Real-World v1
reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from evidence_layer.adapters.documentation_rag import DocumentationRAGAdapter  # noqa: E402
from evidence_layer.metrics import average_precision, mrr_at_k, ndcg_at_k, precision_at_k, recall_at_k  # noqa: E402
from evidence_layer.scoring.lexical import LexicalScorer  # noqa: E402
from evidence_layer.schemas import Policy  # noqa: E402
from evidence_layer.usage_measurement import UsageEvent, cost_for_event  # noqa: E402

import build_real_world_dataset as rw  # noqa: E402


CONFIG = {
    "arms": ["lexical_rag", "evidence_layer"],
    "evidence_layer_policy": {
        "context_budget_tokens": 120,
        "min_evidence": 1,
        "max_evidence": 3,
        "policy_version": "phase6-offline-v1",
    },
    "answer_model": None,
    "jev": "not_run",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def _dataset_fingerprint() -> str:
    parts = []
    for relative in ("data/real_world/manifest.json", "data/real_world/passages.jsonl", "data/real_world/sources.json"):
        parts.append({"path": relative, "sha256": _sha256((ROOT / relative).read_bytes())})
    return _sha256(_canonical(parts))


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _rank(scores: dict[str, float]) -> list[str]:
    return [candidate_id for candidate_id, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]


def _metrics(case: dict, ranked: list[str]) -> dict[str, float]:
    gold = case["gold_candidate_ids"]
    return {
        "average_precision": average_precision(gold, ranked),
        "mrr": mrr_at_k(gold, ranked),
        "precision_at_5": precision_at_k(gold, ranked[:5]),
        "recall_at_5": recall_at_k(gold, ranked[:5]),
        "ndcg_at_5": ndcg_at_k(gold, ranked, 5),
    }


def _usage(run_id: str, arm_id: str, case_id: str, estimated_input: int) -> UsageEvent:
    return UsageEvent(
        run_id=run_id,
        case_id=case_id,
        arm_id=arm_id,
        model_id="offline-no-answer-model",
        provider_id="local",
        stage="retrieval" if arm_id == "lexical_rag" else "selection",
        attempt_id=f"{arm_id}:{case_id}",
        started_at="offline",
        finished_at="offline",
        input_tokens=estimated_input,
        provider_output_tokens=None,
        reasoning_tokens=None,
        visible_output_tokens=None,
        visible_output_tokenizer_id=None,
        output_token_measurement_status="not_applicable",
        cached_input_tokens=0,
        cache_write_5m_tokens=0,
        cache_write_1h_tokens=0,
        cache_write_30m_tokens=0,
        cache_write_12h_tokens=0,
        calls=0,
        retries=0,
        outcome="success",
        token_status="estimated",
    )


def _usage_dict(event: UsageEvent) -> dict:
    row = asdict(event)
    cost = cost_for_event(event, None)
    row.update({
        "cost_usd": cost.amount_usd,
        "cost_status": cost.status,
        "missing_cost_reason": cost.missing_reason,
        "pricing_version": cost.pricing_version,
    })
    return row


def build_run(run_id: str) -> tuple[dict, list[dict], dict]:
    dev, validation, exposed_test = rw.build_real_world_cases()
    cases = dev + validation + exposed_test
    scorer = LexicalScorer()
    adapter = DocumentationRAGAdapter()
    policy = Policy(**CONFIG["evidence_layer_policy"])
    results: list[dict] = []
    usage: list[dict] = []
    for case in cases:
        scores = {
            candidate["candidate_id"]: scorer.score(case["task"]["input"], candidate["text"])
            for candidate in case["candidates"]
        }
        ranked = _rank(scores)
        lexical_selected = ranked[:5]
        results.append({
            "case_id": case["case_id"],
            "case_type": case["case_type"],
            "arm_id": "lexical_rag",
            "ranked_candidate_ids": ranked,
            "selected_candidate_ids": lexical_selected,
            "metrics": _metrics(case, lexical_selected),
        })
        query_units = len(case["task"]["input"].split())
        candidate_units = sum(len(candidate["text"].split()) for candidate in case["candidates"])
        usage.append(_usage_dict(_usage(
            run_id, "lexical_rag", case["case_id"], query_units + candidate_units
        )))

        task = adapter.normalize_task({"task_id": case["case_id"], "input": case["task"]["input"], "task_type": "knowledge_query"})
        candidates = adapter.normalize_candidates(case["candidates"])
        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        ordered = [by_id[candidate_id] for candidate_id in ranked]
        selected = adapter.apply_policy(task, ordered, policy)
        selected_ids = selected.selected_candidate_ids
        results.append({
            "case_id": case["case_id"],
            "case_type": case["case_type"],
            "arm_id": "evidence_layer",
            "ranked_candidate_ids": ranked,
            "selected_candidate_ids": selected_ids,
            "decision": selected.decision,
            "reason_codes": selected.reason_codes,
            "context_tokens_before": selected.context_tokens_before,
            "context_tokens_after": selected.context_tokens_after,
            "metrics": _metrics(case, selected_ids),
        })
        usage.append(_usage_dict(_usage(
            run_id, "evidence_layer", case["case_id"], selected.context_tokens_before
        )))
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "benchmark": "phase6-real-world-v1",
        "execution": "offline-evidence-arms-only",
        "dataset_fingerprint": _dataset_fingerprint(),
        "configuration": CONFIG,
        "configuration_fingerprint": _sha256(_canonical(CONFIG)),
        "git_commit_sha": _git_sha(),
        "answer_model": None,
        "jev": "not_run",
        "pricing_references": {},
        "network": "none",
        "results_file": "results.json",
        "usage_file": "usage_events.jsonl",
    }
    return manifest, usage, {"schema_version": 1, "run_id": run_id, "benchmark": manifest["benchmark"], "results": results}


def write_run(output_dir: Path, run_id: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest, usage, results = build_run(run_id)
    results_bytes = (json.dumps(results, sort_keys=True, indent=2) + "\n").encode()
    usage_bytes = ("\n".join(json.dumps(row, sort_keys=True) for row in usage) + "\n").encode()
    (output_dir / "results.json").write_bytes(results_bytes)
    (output_dir / "usage_events.jsonl").write_bytes(usage_bytes)
    manifest["results_sha256"] = _sha256(results_bytes)
    manifest["usage_sha256"] = _sha256(usage_bytes)
    (output_dir / "manifest.json").write_bytes((json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", default="offline-phase6-real-world-v1")
    args = parser.parse_args()
    write_run(args.output_dir, args.run_id)
    print(f"wrote offline arms to {args.output_dir}")


if __name__ == "__main__":
    main()
