#!/usr/bin/env python3
"""Phase 7 paired benchmark: Traditional RAG vs Evidence Layer, lexical only.

Both arms use the lexical scorer. No JEV call is made and no saved JEV score is
read. Evidence Layer keeps the budget (top-3) while Traditional passes top-5.

Live calls require PHASE7_LIVE_ANTHROPIC=1 plus ANTHROPIC_API_KEY and
OPENAI_API_KEY (judge).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import httpx  # noqa: E402
from evidence_layer.scoring.lexical import LexicalScorer  # noqa: E402
from run_phase7_end_to_end_rag import (  # noqa: E402
    GENERATION_INSTRUCTIONS,
    JUDGE_MODEL,
    QUALITY_METRICS,
    _build_answer_prompt,
    _evidence_metrics,
    _finalize_citations,
    _judge_request,
    _rank,
    _split_cases,
    _verify_real_world_offline,
)
from run_phase7_anthropic_e2e import (  # noqa: E402
    LIVE_FLAG,
    MODEL_PRICING,
    _anthropic_call,
    _passages,
    _verify_candidates,
    paired_bootstrap,
    summarize as _summarize,
)

MODEL = "claude-haiku-5-5"
MAX_TRADITIONAL = 5
MAX_EVIDENCE_LAYER = 3
PARTIAL_PATH = ROOT / "reports/phase7_haiku_lexical.json"
REPORT_PATH = ROOT / "reports/phase7_haiku_lexical_e2e.json"
MANIFEST_PATH = ROOT / "reports/phase7_haiku_lexical_e2e_manifest.json"
JUDGE_PRICING = {  # Phase 7 judge is gpt-6.1-sol; usage-based, reported separately
    "input_per_million": 2.00,
    "output_per_million": 10.00,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rows(case: dict, scorer: LexicalScorer) -> tuple[list[dict], list[dict], list[dict], float]:
    started = time.perf_counter()
    scores = {
        candidate["candidate_id"]: scorer.score(case["task"]["input"], candidate["text"])
        for candidate in case["candidates"]
    }
    order = _rank(scores)
    candidate_map = {item["candidate_id"]: item for item in case["candidates"]}
    rows: list[dict] = []
    for rank, candidate_id in enumerate(order, start=1):
        candidate = candidate_map[candidate_id]
        rows.append({
            "rank": rank,
            "candidate_id": candidate_id,
            "source_id": candidate["source_id"],
            "source_url": candidate["source_url"],
            "section": candidate["metadata"].get("section"),
            "text": candidate["text"],
            "lexical_score": scores[candidate_id],
            "jev_score": None,
            "relevance_grade": case["relevance_scores"][candidate_id],
        })
    latency_ms = (time.perf_counter() - started) * 1000
    return rows, rows[:MAX_TRADITIONAL], rows[:MAX_EVIDENCE_LAYER], latency_ms


def run(limit: int | None, anthropic_key: str, openai_key: str) -> dict:
    cases_by_split = _split_cases()
    _verify_real_world_offline()
    passages, passage_meta = _passages()
    _verify_candidates(cases_by_split, passages)
    cases = [case for split in ("dev", "validation", "exposed_test") for case in cases_by_split[split]]
    if limit:
        cases = cases[:limit]

    run_id = str(uuid.uuid4())
    answers: list[dict] = []
    judges: list[dict] = []
    completed = set()
    if PARTIAL_PATH.is_file():
        prior = json.loads(PARTIAL_PATH.read_text())
        if prior.get("case_limit") == limit and prior.get("model") == MODEL:
            run_id = prior["run_id"]
            answers = prior.get("answers", [])
            judges = prior.get("judges", [])
    completed = {(a["case_id"], a["route"]) for a in answers}
    judged_cases = {j["case_id"] for j in judges}

    def checkpoint() -> None:
        PARTIAL_PATH.write_bytes((json.dumps({
            "schema_version": 1,
            "run_id": run_id,
            "model": MODEL,
            "scorer": "lexical",
            "jev": "not_used",
            "created_at_utc": _now(),
            "case_limit": limit,
            "cases": len(cases),
            "answers": answers,
            "judges": judges,
            "passages": passage_meta,
            "pricing": MODEL_PRICING[MODEL],
            "settings": {
                "max_tokens": 8192,
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "medium"},
                "judge_model": JUDGE_MODEL,
                "traditional_context": MAX_TRADITIONAL,
                "evidence_layer_context": MAX_EVIDENCE_LAYER,
            },
            "generation_instructions": GENERATION_INSTRUCTIONS,
        }, sort_keys=True, indent=2) + "\n").encode())

    scorer = LexicalScorer()
    with httpx.Client(timeout=180, trust_env=False, follow_redirects=False) as client:
        for case in cases:
            rows, trad_selected, el_selected, rerank_ms = _rows(case, scorer)
            for route, selected in (("traditional_rag", trad_selected), ("evidence_layer", el_selected)):
                if (case["case_id"], route) in completed:
                    continue
                user_input, labels = _build_answer_prompt(case, selected)
                result = _anthropic_call(client, MODEL, anthropic_key, GENERATION_INSTRUCTIONS, user_input)
                record = {
                    "run_id": run_id,
                    "model": MODEL,
                    "case_id": case["case_id"],
                    "route": route,
                    "scorer": "lexical",
                    "jev": "not_used",
                    "answer": result["answer"],
                    "returned_model": result.get("returned_model"),
                    "stop_reason": result.get("stop_reason"),
                    "citation_map": labels,
                    "selected_evidence": selected,
                    "evidence_metrics": _evidence_metrics(case, rows, selected),
                    "usage": result.get("usage"),
                    "llm_cost_usd": result.get("cost_usd"),
                    "latency_ms": result.get("api_latency_ms"),
                    "rerank_ms": round(rerank_ms, 3),
                    "retry_count": result.get("retry_count"),
                    "error_category": result.get("error_category"),
                }
                _finalize_citations(record)
                answers.append(record)
                completed.add((case["case_id"], route))
            if case["case_id"] not in judged_cases:
                pair = {a["route"]: a for a in answers if a["case_id"] == case["case_id"]}
                if all(pair.get(route, {}).get("answer") for route in ("traditional_rag", "evidence_layer")):
                    judged = _judge_request(pair, case, client, openai_key)
                    judged["case_id"] = case["case_id"]
                    judged["model"] = MODEL
                    judges.append(judged)
                    judged_cases.add(case["case_id"])
            checkpoint()
    return json.loads(PARTIAL_PATH.read_text())


def _sanitize(partial: dict) -> dict:
    for record in partial.get("answers", []):
        answer = record.pop("answer", None)
        if answer is not None:
            record["answer_sha256"] = _sha256(answer.encode("utf-8"))
            record["answer_chars"] = len(answer)
        record.pop("citation_map", None)
        for row in record.get("selected_evidence") or []:
            row.pop("text", None)
    for judge in partial.get("judges", []):
        judge.pop("raw_output", None)
    return partial


def finalize() -> dict:
    partial = _sanitize(json.loads(PARTIAL_PATH.read_text()))
    PARTIAL_PATH.write_bytes((json.dumps(partial, sort_keys=True, indent=2) + "\n").encode())
    summary = _summarize(partial)
    # No JEV on this path: system cost equals measured LLM cost.
    for route in ("traditional_rag", "evidence_layer"):
        summary[route]["jev_estimated_usd"] = 0.0
        summary[route]["jev_status"] = "not_used"
        summary[route]["system_estimated_usd"] = summary[route]["llm_cost_usd"]
    summary["jev_estimate"] = {
        "status": "not_used",
        "scorer": "lexical",
        "note": "No JEV scoring was requested or charged for this path.",
    }
    summary["bootstrap"] = {
        "judge_correctness": paired_bootstrap(partial, "judge_correctness", "judge_correctness"),
        "judge_completeness": paired_bootstrap(partial, "judge_completeness", "judge_completeness"),
        "judge_groundedness": paired_bootstrap(partial, "judge_groundedness", "judge_groundedness"),
        "judge_citation_correctness": paired_bootstrap(partial, "judge_citation_correctness", "judge_citation_correctness"),
        "total_tokens": paired_bootstrap(partial, "total_tokens", "total_tokens"),
        "llm_cost_usd": paired_bootstrap(partial, "llm_cost_usd", "llm_cost_usd"),
    }
    report = {
        "schema_version": 1,
        "benchmark": "phase7-haiku-lexical-only-v1",
        "created_at_utc": _now(),
        "model": MODEL,
        "scorer": "lexical",
        "jev": "not_used",
        "cases": partial["cases"],
        "traditional_context": MAX_TRADITIONAL,
        "evidence_layer_context": MAX_EVIDENCE_LAYER,
        "routes": {
            "traditional_rag": summary["traditional_rag"],
            "evidence_layer": summary["evidence_layer"],
        },
        "bootstrap": summary["bootstrap"],
        "judge_cost_usd": summary["judge_cost_usd"],
        "judge_failures": summary["judge_failures"],
        "pricing": MODEL_PRICING[MODEL],
        "limitations": [
            "Relevance judgments and required facts are developer-authored; no independent human adjudication.",
            "The judge is an automated OpenAI model, not an independent human reviewer.",
            "The 8-case exposed split is not blind.",
            "No JEV scoring was used; the Evidence Layer arm selects with the lexical scorer and a top-3 budget.",
            "Answers use the frozen Anthropic profile (adaptive thinking, effort medium, 8192 cap).",
        ],
    }
    result_bytes = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode()
    REPORT_PATH.write_bytes(result_bytes)
    manifest = {
        "schema_version": 1,
        "manifest_kind": "phase7_haiku_lexical_e2e",
        "created_at_utc": report["created_at_utc"],
        "results_path": str(REPORT_PATH.relative_to(ROOT)),
        "results_sha256": _sha256(result_bytes),
        "model": MODEL,
        "scorer": "lexical",
        "jev": "not_used",
        "cases": partial["cases"],
        "passages": partial["passages"],
        "settings": partial["settings"],
        "pricing": MODEL_PRICING[MODEL],
        "generation_instructions_sha256": _sha256(GENERATION_INSTRUCTIONS.encode()),
        "scorer_source_sha256": _sha256((ROOT / "src/evidence_layer/scoring/lexical.py").read_bytes()),
    }
    MANIFEST_PATH.write_bytes((json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode())
    print(f"wrote {REPORT_PATH.name} and manifest")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 7 Haiku lexical-only paired benchmark")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--finalize", action="store_true")
    parser.add_argument("--run-authorized", action="store_true")
    args = parser.parse_args()
    if args.finalize:
        finalize()
        return 0
    if not args.run_authorized:
        parser.error("--run-authorized is required for live calls")
    if os.environ.get(LIVE_FLAG) != "1":
        parser.error(f"{LIVE_FLAG}=1 is required")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not anthropic_key or not openai_key:
        parser.error("ANTHROPIC_API_KEY and OPENAI_API_KEY are required")
    partial = run(args.limit, anthropic_key, openai_key)
    print(json.dumps({"answers": len(partial["answers"]), "judges": len(partial["judges"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
