#!/usr/bin/env python3
"""Phase 7 paired RAG benchmark for the three Anthropic roster models.

Traditional RAG passes the lexical top-5 candidates; Evidence Layer passes the
JEV top-3. Both arms use the same instructions, question, and answer model.

Live calls require PHASE7_LIVE_ANTHROPIC=1 plus ANTHROPIC_API_KEY and
OPENAI_API_KEY (judge). Nothing is sent without the opt-in. Per-model partials
allow resuming; --finalize merges them into the report and manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import statistics
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import httpx  # noqa: E402
import build_real_world_dataset as rw  # noqa: E402
from evidence_layer.scoring.lexical import LexicalScorer  # noqa: E402
from run_phase7_end_to_end_rag import (  # noqa: E402
    GENERATION_INSTRUCTIONS,
    JUDGE_MODEL,
    QUALITY_METRICS,
    _build_answer_prompt,
    _cost as _openai_judge_cost,
    _evidence_metrics,
    _evidence_rows,
    _finalize_citations,
    _judge_request,
    _rank,
    _split_cases,
    _validate_jev_records,
    _verify_real_world_offline,
)

LIVE_FLAG = "PHASE7_LIVE_ANTHROPIC"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2026-10-21"
MAX_TOKENS = 8192
REQUEST_TIMEOUT_SECONDS = 180
MAX_RETRIES = 2
MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
PARTIAL_TEMPLATE = "reports/phase7_anthropic_{model}.json"
REPORT_PATH = ROOT / "reports/phase7_anthropic_end_to_end.json"
MANIFEST_PATH = ROOT / "reports/phase7_anthropic_end_to_end_manifest.json"
PASSAGES_PATH = ROOT / "data/real_world/passages.jsonl"
PHASE6_JEV_TOKENS = 76184
PHASE6_JEV_CALLS = 200
PHASE6_JEV_CASES = 40
JEV_USD_PER_MILLION_INPUT = 0.042

# Anthropic first-party standard rates (phase6-pricing-registry.md, retrieved 2026-11-06).
MODEL_PRICING = {
    "claude-opus-5-5": {
        "input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write_5m": 5.00,
        "source": "https://platform.claude.com/docs/en/about-claude/pricing",
    },
    "claude-sonnet-5-5": {
        "input": 2.00, "output": 10.00, "cache_read": 0.10, "cache_write_5m": 2.50,
        "source": "https://platform.claude.com/docs/en/about-claude/pricing",
    },
    "claude-haiku-5-5": {
        "input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write_5m": 0.125,
        "source": "https://platform.claude.com/docs/en/about-claude/pricing",
    },
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _passages() -> tuple[list[dict], dict]:
    """Read the frozen passages directly and verify them against the cases.

    Phase 7 used an isolated local Supabase secure-read handoff. That stack is
    not re-run here; this runner verifies the file bytes and every candidate
    against the frozen case universe, and records the deviation.
    """
    raw = PASSAGES_PATH.read_bytes()
    passages = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line]
    if len(passages) != 51:
        raise RuntimeError("frozen passages must contain 51 rows")
    by_id = {row["passage_id"]: row for row in passages}
    if len(by_id) != 51:
        raise RuntimeError("passage ids are not unique")
    return passages, {
        "path": str(PASSAGES_PATH.relative_to(ROOT)),
        "sha256": _sha256(raw),
        "bytes": len(raw),
        "secure_snapshot_read_path": "not re-run; frozen file verified by hash and case match",
    }


def _verify_candidates(cases_by_split: dict[str, list[dict]], passages: list[dict]) -> None:
    by_id = {row["passage_id"]: row for row in passages}
    for cases in cases_by_split.values():
        for case in cases:
            for candidate in case["candidates"]:
                passage = by_id.get(candidate["candidate_id"])
                if passage is None or any((
                    candidate["text"] != passage["text"],
                    candidate["source_id"] != passage["source_id"],
                    candidate["source_url"] != passage["source_url"],
                    candidate["metadata"].get("section") != passage["section"],
                )):
                    raise RuntimeError("candidate does not match the frozen passage snapshot")


def anthropic_cost(model: str, usage: dict) -> float:
    price = MODEL_PRICING[model]
    return (
        usage["uncached_input_tokens"] * price["input"]
        + usage["cache_read_tokens"] * price["cache_read"]
        + usage["cache_write_tokens"] * price["cache_write_5m"]
        + usage["output_tokens"] * price["output"]
    ) / 1_000_000


def _anthropic_usage(payload: dict) -> dict:
    usage = payload.get("usage") or {}
    uncached = int(usage.get("input_tokens") or 0)
    read = int(usage.get("cache_read_input_tokens") or 0)
    creation = int(usage.get("cache_creation_input_tokens") or 0)
    output = int(usage.get("output_tokens") or 0)
    return {
        "uncached_input_tokens": uncached,
        "cache_read_tokens": read,
        "cache_write_tokens": creation,
        "total_input_tokens": uncached + read + creation,
        "output_tokens": output,
    }


def _answer_text(payload: dict) -> str:
    blocks = payload.get("content")
    if not isinstance(blocks, list):
        return ""
    return "\n".join(
        block.get("text", "") for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ).strip()


def _anthropic_call(
    client: httpx.Client, model: str, api_key: str, instructions: str, user_input: str,
) -> dict:
    payload = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": instructions,
        "messages": [{"role": "user", "content": user_input}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "medium"},
    }
    attempts = []
    for attempt_index in range(MAX_RETRIES + 1):
        started = time.perf_counter()
        try:
            response = client.post(
                ANTHROPIC_URL,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json=payload,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError:
            attempts.append({"error": "transport_error", "latency_ms": (time.perf_counter() - started) * 1000})
            if attempt_index >= MAX_RETRIES:
                return {"answer": None, "attempts": attempts, "error_category": "transport_error"}
            time.sleep(min(2 ** attempt_index, 8))
            continue
        latency = (time.perf_counter() - started) * 1000
        attempts.append({"http_status": response.status_code, "latency_ms": latency})
        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                return {"answer": None, "attempts": attempts, "error_category": "invalid_json_response"}
            attempts[-1]["usage"] = _anthropic_usage(data)
            attempts[-1]["cost_usd"] = round(anthropic_cost(model, attempts[-1]["usage"]), 12)
            return {
                "answer": _answer_text(data),
                "returned_model": data.get("model"),
                "stop_reason": data.get("stop_reason"),
                "usage": attempts[-1]["usage"],
                "cost_usd": attempts[-1]["cost_usd"],
                "attempts": attempts,
                "api_latency_ms": sum(item["latency_ms"] for item in attempts),
                "retry_count": len(attempts) - 1,
                "error_category": None,
            }
        if response.status_code in (429,) or response.status_code >= 500:
            if attempt_index >= MAX_RETRIES:
                return {"answer": None, "attempts": attempts, "error_category": "provider_unavailable"}
            time.sleep(min(2 ** attempt_index, 8))
            continue
        return {"answer": None, "attempts": attempts, "error_category": "provider_request_rejected"}


def run_model(model: str, limit: int | None, anthropic_key: str, openai_key: str) -> dict:
    cases_by_split = _split_cases()
    jev_report, jev_records = _validate_jev_records(cases_by_split)
    passages, passage_meta = _passages()
    _verify_candidates(cases_by_split, passages)
    scorer = LexicalScorer()
    cases = [case for split in ("dev", "validation", "exposed_test") for case in cases_by_split[split]]
    if limit:
        cases = cases[:limit]
    run_id = str(uuid.uuid4())
    answers: list[dict] = []
    judges: list[dict] = []
    partial_path = ROOT / PARTIAL_TEMPLATE.format(model=model)
    if partial_path.is_file():
        prior = json.loads(partial_path.read_text())
        if prior.get("case_limit") == limit and prior.get("model") == model:
            run_id = prior["run_id"]
            answers = prior.get("answers", [])
            judges = prior.get("judges", [])
    completed = {(a["case_id"], a["route"]) for a in answers}
    judged_cases = {j["case_id"] for j in judges}

    def checkpoint() -> None:
        partial_path.write_bytes((json.dumps({
            "schema_version": 1,
            "run_id": run_id,
            "model": model,
            "created_at_utc": _now(),
            "case_limit": limit,
            "cases": len(cases),
            "answers": answers,
            "judges": judges,
            "jev_basis": {
                "phase6_input_tokens": jev_report["run"]["usage"]["input_tokens_reported"],
                "phase6_calls": jev_report["run"]["counts"]["sdk_calls"],
                "phase6_cases": PHASE6_JEV_CASES,
                "unit_price_usd_per_million_input": JEV_USD_PER_MILLION_INPUT,
                "model_returned": jev_report["run"]["models_returned"],
            },
            "passages": passage_meta,
            "pricing": MODEL_PRICING[model],
            "settings": {
                "endpoint": ANTHROPIC_URL,
                "anthropic_version": ANTHROPIC_VERSION,
                "max_tokens": MAX_TOKENS,
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "medium"},
                "judge_model": JUDGE_MODEL,
                "max_retries": MAX_RETRIES,
            },
            "generation_instructions": GENERATION_INSTRUCTIONS,
        }, sort_keys=True, indent=2) + "\n").encode())

    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, trust_env=False, follow_redirects=False) as client:
        for case in cases:
            pair: dict[str, dict] = {}
            for route in ("traditional_rag", "evidence_layer"):
                if (case["case_id"], route) in completed:
                    continue
                retrieved, selected, rerank_ms = _evidence_rows(
                    case, scorer=scorer, jev_records=jev_records, route=route
                )
                user_input, labels = _build_answer_prompt(case, selected)
                result = _anthropic_call(client, model, anthropic_key, GENERATION_INSTRUCTIONS, user_input)
                record = {
                    "run_id": run_id,
                    "model": model,
                    "case_id": case["case_id"],
                    "split": next(s for s, rows in cases_by_split.items() if case in rows),
                    "route": route,
                    "answer": result["answer"],
                    "returned_model": result.get("returned_model"),
                    "stop_reason": result.get("stop_reason"),
                    "citation_map": labels,
                    "selected_evidence": selected,
                    "evidence_metrics": _evidence_metrics(case, retrieved, selected),
                    "usage": result.get("usage"),
                    "llm_cost_usd": result.get("cost_usd"),
                    "latency_ms": result.get("api_latency_ms"),
                    "retry_count": result.get("retry_count"),
                    "error_category": result.get("error_category"),
                    "rerank_ms": round(rerank_ms, 3),
                }
                _finalize_citations(record)
                answers.append(record)
                completed.add((case["case_id"], route))
                pair[route] = record
            if case["case_id"] not in judged_cases:
                existing = {a["route"]: a for a in answers if a["case_id"] == case["case_id"]}
                if all(existing.get(route, {}).get("answer") for route in ("traditional_rag", "evidence_layer")):
                    judged = _judge_request(existing, case, client, openai_key)
                    judged["case_id"] = case["case_id"]
                    judged["model"] = model
                    judges.append(judged)
                    judged_cases.add(case["case_id"])
            checkpoint()
    return {
        "schema_version": 1,
        "run_id": run_id,
        "model": model,
        "created_at_utc": _now(),
        "case_limit": limit,
        "cases": len(cases),
        "ji": 0,
        "answers": answers,
        "judges": judges,
        "jev_basis": {
            "phase6_input_tokens": jev_report["run"]["usage"]["input_tokens_reported"],
            "phase6_calls": jev_report["run"]["counts"]["sdk_calls"],
            "phase6_cases": PHASE6_JEV_CASES,
            "unit_price_usd_per_million_input": JEV_USD_PER_MILLION_INPUT,
            "model_returned": jev_report["run"]["models_returned"],
        },
        "passages": passage_meta,
        "pricing": MODEL_PRICING[model],
        "settings": {
            "endpoint": ANTHROPIC_URL,
            "anthropic_version": ANTHROPIC_VERSION,
            "max_tokens": MAX_TOKENS,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "medium"},
            "judge_model": JUDGE_MODEL,
            "max_retries": MAX_RETRIES,
        },
        "generation_instructions": GENERATION_INSTRUCTIONS,
    }


def jev_estimate(total_cases: int, model_count: int = 1) -> dict:
    """Estimate from Phase 6 measured tokens; per-case, then multiplied."""
    per_call = PHASE6_JEV_TOKENS / PHASE6_JEV_CALLS
    per_case_tokens = per_call * 5
    per_case_usd = per_case_tokens / 1_000_000 * JEV_USD_PER_MILLION_INPUT
    return {
        "basis": "Phase 6 measured input tokens / calls, 5 candidate scores per case",
        "phase6_input_tokens": PHASE6_JEV_TOKENS,
        "phase6_calls": PHASE6_JEV_CALLS,
        "per_call_tokens": per_call,
        "per_case_tokens": per_case_tokens,
        "per_case_estimated_usd": per_case_usd,
        "total_estimated_usd": per_case_usd * total_cases,
        "dedicated_per_model": True,
        "shared_across_models_note": (
            "JEV scores are per (case, candidate) and independent of the answer model; "
            "a shared scoring pass costs this amount once, not once per model."
        ),
        "model_count": model_count,
        "invoiced_usd": None,
        "billing_status": "estimate_only",
    }


def _mean(values):
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def summarize(partial: dict) -> dict:
    model = partial["model"]
    cases_total = partial["cases"]
    by_route = {"traditional_rag": [], "evidence_layer": []}
    for record in partial["answers"]:
        by_route[record["route"]].append(record)
    judge_by_case = {j["case_id"]: j for j in partial["judges"] if j.get("status") == "ok"}
    summary = {}
    for route, records in by_route.items():
        metrics = {}
        metrics["cases"] = len(records)
        metrics["answers"] = sum(1 for r in records if r.get("answer") or r.get("answer_sha256"))
        metrics["errors"] = sum(1 for r in records if r["error_category"])
        metrics["mean_input_tokens"] = _mean([(r["usage"] or {}).get("total_input_tokens") for r in records])
        metrics["mean_output_tokens"] = _mean([(r["usage"] or {}).get("output_tokens") for r in records])
        metrics["mean_total_tokens"] = _mean([
            ((r["usage"] or {}).get("total_input_tokens") or 0) + ((r["usage"] or {}).get("output_tokens") or 0)
            if r["usage"] else None for r in records
        ])
        metrics["llm_cost_usd"] = sum(r["llm_cost_usd"] or 0.0 for r in records)
        metrics["mean_latency_ms"] = _mean([r["latency_ms"] for r in records])
        metrics["mean_rerank_ms"] = _mean([r["rerank_ms"] for r in records])
        metrics["gold_passage_recall"] = _mean([(r["evidence_metrics"] or {}).get("gold_passage_recall") for r in records])
        metrics["graded_evidence_coverage"] = _mean([(r["evidence_metrics"] or {}).get("graded_evidence_coverage") for r in records])
        metrics["citation_reference_validity"] = _mean([(r["citation_validation"] or {}).get("citation_reference_validity") for r in records])
        metrics["citation_relevance_precision"] = _mean([(r["citation_validation"] or {}).get("citation_relevance_precision") for r in records])
        for metric in QUALITY_METRICS:
            metrics[f"judge_{metric}"] = _mean([
                judge_by_case[r["case_id"]]["scores"][route].get(metric)
                for r in records if r["case_id"] in judge_by_case
            ])
        summary[route] = metrics
    jev = jev_estimate(cases_total)
    summary["evidence_layer"]["jev_estimated_usd"] = jev["total_estimated_usd"]
    summary["evidence_layer"]["system_estimated_usd"] = (
        summary["evidence_layer"]["llm_cost_usd"] + jev["total_estimated_usd"]
    )
    summary["traditional_rag"]["jev_estimated_usd"] = 0.0
    summary["traditional_rag"]["system_estimated_usd"] = summary["traditional_rag"]["llm_cost_usd"]
    summary["jev_estimate"] = jev
    summary["judge_cost_usd"] = sum(j.get("llm_cost_usd") or 0.0 for j in partial["judges"])
    summary["judge_failures"] = sum(1 for j in partial["judges"] if j.get("status") != "ok")
    return summary


def paired_bootstrap(partial: dict, metric: str, field: str, *, replicates: int = 10000, seed: int = 20261106) -> dict:
    per_case: dict[str, dict[str, float]] = {}
    judge_by_case = {j["case_id"]: j for j in partial["judges"] if j.get("status") == "ok"}
    for record in partial["answers"]:
        case_id = record["case_id"]
        if field.startswith("judge_"):
            if case_id not in judge_by_case:
                continue
            value = judge_by_case[case_id]["scores"][record["route"]].get(field[len("judge_"):])
        elif field == "total_tokens":
            if not record["usage"]:
                continue
            value = record["usage"]["total_input_tokens"] + record["usage"]["output_tokens"]
        elif field == "llm_cost_usd":
            value = record["llm_cost_usd"]
        else:
            raise ValueError(f"unsupported field {field}")
        if value is None:
            continue
        per_case.setdefault(case_id, {})[record["route"]] = float(value)
    pairs = [(v["evidence_layer"] - v["traditional_rag"]) for v in per_case.values()
             if "evidence_layer" in v and "traditional_rag" in v]
    if len(pairs) < 2:
        return {"metric": metric, "cases": len(pairs), "point_estimate": None, "ci_lower": None, "ci_upper": None}
    rng = random.Random(seed)
    estimates = []
    for _ in range(replicates):
        sample = [pairs[rng.randrange(len(pairs))] for _ in range(len(pairs))]
        estimates.append(statistics.fmean(sample))
    estimates.sort()
    def pct(p):
        position = (len(estimates) - 1) * p
        low = int(position)
        high = min(low + 1, len(estimates) - 1)
        frac = position - low
        return estimates[low] * (1 - frac) + estimates[high] * frac
    return {
        "metric": metric,
        "cases": len(pairs),
        "point_estimate": statistics.fmean(pairs),
        "ci_lower": pct(0.025),
        "ci_upper": pct(0.975),
    }


def _sanitize(partial: dict) -> dict:
    """Remove answer and passage text; keep hashes and metadata only."""
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


def finalize(limit: int | None) -> dict:
    partials = []
    for model in MODELS:
        path = ROOT / PARTIAL_TEMPLATE.format(model=model)
        if path.is_file():
            partial = _sanitize(json.loads(path.read_text()))
            path.write_bytes((json.dumps(partial, sort_keys=True, indent=2) + "\n").encode())
            partials.append(partial)
    if not partials:
        raise SystemExit("no partial runs found")
    models = {}
    for partial in partials:
        summary = summarize(partial)
        summary["bootstrap"] = {
            "judge_correctness": paired_bootstrap(partial, "judge_correctness", "judge_correctness"),
            "judge_completeness": paired_bootstrap(partial, "judge_completeness", "judge_completeness"),
            "judge_groundedness": paired_bootstrap(partial, "judge_groundedness", "judge_groundedness"),
            "judge_citation_correctness": paired_bootstrap(partial, "judge_citation_correctness", "judge_citation_correctness"),
            "total_tokens": paired_bootstrap(partial, "total_tokens", "total_tokens"),
            "llm_cost_usd": paired_bootstrap(partial, "llm_cost_usd", "llm_cost_usd"),
        }
        models[partial["model"]] = summary
    report = {
        "schema_version": 1,
        "benchmark": "phase7-anthropic-paired-rag-v1",
        "created_at_utc": _now(),
        "case_limit": limit,
        "models": models,
        "method": "traditional_rag = lexical top-5; evidence_layer = JEV top-3; same instructions and model per pair; blinded paired judge",
        "pricing_source": MODEL_PRICING,
        "jev_estimate_basis": jev_estimate(max(m["traditional_rag"]["cases"] for m in models.values())),
        "limitations": [
            "Relevance judgments and required facts are developer-authored; no independent human adjudication.",
            "The judge is an automated OpenAI model, not independent of the tested OpenAI provider, though Anthropic answers are judged here.",
            "The 8-case exposed split is not blind and should not be used as a future blind JEV test.",
            "JEV USD is an estimate from Phase 6 measured tokens at list price; it is not invoiced.",
            "The isolated local Supabase secure-read path was not re-run; frozen passage bytes were verified by hash and case match.",
            "Answers use the frozen Anthropic profile (adaptive thinking, effort medium, max_tokens 8192), not Phase 7's 800 total output cap.",
        ],
    }
    result_bytes = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode()
    REPORT_PATH.write_bytes(result_bytes)
    manifest = {
        "schema_version": 1,
        "manifest_kind": "phase7_anthropic_end_to_end",
        "created_at_utc": report["created_at_utc"],
        "results_path": str(REPORT_PATH.relative_to(ROOT)),
        "results_sha256": _sha256(result_bytes),
        "models": list(models),
        "passages": {p["model"]: p["passages"] for p in partials},
        "jev_source": "reports/phase6_real_world_jev.json",
        "jev_source_sha256": _sha256((ROOT / "reports/phase6_real_world_jev.json").read_bytes()),
        "phase6_freeze_sha256": _sha256((ROOT / "data/phase6_freeze_manifest.json").read_bytes()),
    }
    MANIFEST_PATH.write_bytes((json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode())
    print(f"wrote {REPORT_PATH.relative_to(ROOT)} and manifest")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 7 Anthropic paired RAG benchmark")
    parser.add_argument("--model", action="append", choices=MODELS)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--finalize", action="store_true")
    parser.add_argument("--run-authorized", action="store_true")
    args = parser.parse_args()

    if args.finalize:
        finalize(args.limit)
        return 0
    if not args.run_authorized:
        parser.error("--run-authorized is required for live Anthropic calls")
    if os.environ.get(LIVE_FLAG) != "1":
        parser.error(f"{LIVE_FLAG}=1 is required")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not anthropic_key or not openai_key:
        parser.error("ANTHROPIC_API_KEY and OPENAI_API_KEY are required (judge uses OpenAI)")
    _verify_real_world_offline()
    for model in (args.model or list(MODELS)):
        print(f"running {model} ...", flush=True)
        partial = run_model(model, args.limit, anthropic_key, openai_key)
        path = ROOT / PARTIAL_TEMPLATE.format(model=model)
        path.write_bytes((json.dumps(partial, sort_keys=True, indent=2) + "\n").encode())
        summary = summarize(partial)
        print(json.dumps({
            "model": model,
            "cases": partial["cases"],
            "answers": summary["traditional_rag"]["answers"] + summary["evidence_layer"]["answers"],
            "judge_failures": summary["judge_failures"],
            "llm_usd_traditional": round(summary["traditional_rag"]["llm_cost_usd"], 6),
            "llm_usd_evidence": round(summary["evidence_layer"]["llm_cost_usd"], 6),
        }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
