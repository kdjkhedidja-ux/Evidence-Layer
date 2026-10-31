#!/usr/bin/env python3
"""Run Phase 7B with fresh JEV scoring inside each Evidence Layer arm.

This creates a separate exploratory result set and never writes Phase 6/7
artifacts or enables the production retrieval route.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import os
import random
import statistics
import sys
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import httpx
import run_phase7_end_to_end_rag as phase7
import verify_phase7_end_to_end_rag as phase7_verifier
import run_real_world_jev_evaluation as phase6_jev
from evidence_layer.scoring.jev import (
    DEFAULT_JEV_API_URL,
    DEFAULT_JEV_MODEL,
    JevEvidenceScorer,
    JevScorerError,
    _QUESTION_TEXT,
    _issue_real_world_benchmark_permit,
)
from evidence_layer.scoring.lexical import LexicalScorer

RESULT_PATH = ROOT / "reports/phase7b_live_end_to_end_rag.json"
MANIFEST_PATH = ROOT / "reports/phase7b_live_end_to_end_rag_manifest.json"
PARTIAL_PATH = ROOT / "reports/phase7b_live_end_to_end_rag.partial.json"
REPORT_PATH = ROOT / "docs/phase7b_live_cost_validation.md"

JEV_INPUT_USD_PER_MILLION = 0.042
JEV_OUTPUT_USD_PER_MILLION = 0.0
JEV_TIMEOUT_SECONDS = 60.0
JEV_MAX_ATTEMPTS = 2
ORDER_SEED = 727031
BOOTSTRAP_SEED = 727032
BOOTSTRAP_REPLICATES = 10_000
ROUTES = ("traditional_rag", "evidence_layer")
PROVIDERS = ("openai", "deepseek")
SPLITS = ("dev", "validation", "exposed_test")
METRICS = (
    "correctness",
    "completeness",
    "groundedness",
    "citation_correctness",
    "citation_reference_validity",
    "citation_relevance_precision",
    "evidence_coverage",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "llm_cost_usd",
    "jev_cost_usd",
    "system_api_cost_usd",
    "end_to_end_latency_ms",
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _jev_input_cost_usd(input_tokens: int | None) -> float | None:
    if isinstance(input_tokens, bool) or not isinstance(input_tokens, int) or input_tokens < 0:
        return None
    return round(input_tokens * JEV_INPUT_USD_PER_MILLION / 1_000_000, 12)


def _model_listing(client: httpx.Client, provider: str) -> dict:
    if provider == "openai":
        return phase7._api_models(client, provider, os.environ["OPENAI_API_KEY"])
    if provider == "deepseek":
        return phase7._api_models(client, provider, os.environ["DEEPSEEK_API_KEY"])
    raise ValueError("unknown provider")


def _jev_model_listing(client: httpx.Client) -> dict:
    response = client.get(
        f"{DEFAULT_JEV_API_URL}/v1/models",
        headers={"authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}"},
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"TypeSafe model availability check failed with HTTP {response.status_code}")
    payload = response.json()
    models = payload.get("models", [])
    names = sorted(
        row.get("name") for row in models
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    )
    if DEFAULT_JEV_MODEL not in names:
        raise RuntimeError("The configured JEV model alias is not listed for this API account")
    return {
        "endpoint": f"{DEFAULT_JEV_API_URL}/v1/models",
        "verified_available": True,
        "requested_model_id": DEFAULT_JEV_MODEL,
        "listed_models": names,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def _input_hashes() -> dict[str, str]:
    hashes = {
        **phase6_jev._verify_local_inputs()[1],
        **phase7._input_hashes(),
    }
    references = (
        phase7.RESULT_PATH,
        phase7.MANIFEST_PATH,
        ROOT / "docs/phase7_end_to_end_benchmark.md",
        ROOT / "scripts/run_phase7_end_to_end_rag.py",
        ROOT / "scripts/verify_phase7_end_to_end_rag.py",
        Path(__file__).resolve(),
    )
    hashes.update({path.relative_to(ROOT).as_posix(): _file_sha256(path) for path in references})
    return dict(sorted(hashes.items()))


def _route_evidence(case: dict, scorer: LexicalScorer, route: str, live_scores: dict[str, dict]) -> tuple[list[dict], list[dict], float]:
    started = time.perf_counter()
    candidates = case["candidates"]
    lexical = {
        item["candidate_id"]: scorer.score(case["task"]["input"], item["text"])
        for item in candidates
    }
    jev = {candidate_id: float(record["score"]) for candidate_id, record in live_scores.items()}
    if route == "traditional_rag":
        ordered = phase7._rank(lexical)
        selected_ids = ordered
    elif route == "evidence_layer":
        ordered = phase7._rank(jev)
        selected_ids = ordered[: phase7.MAX_JEV_EVIDENCE]
    else:
        raise ValueError("unknown RAG route")
    candidate_by_id = {item["candidate_id"]: item for item in candidates}
    rows = []
    for rank, candidate_id in enumerate(ordered, start=1):
        candidate = candidate_by_id[candidate_id]
        rows.append({
            "rank": rank,
            "candidate_id": candidate_id,
            "source_id": candidate["source_id"],
            "source_url": candidate["source_url"],
            "section": candidate["metadata"].get("section"),
            "text": candidate["text"],
            "lexical_score": lexical[candidate_id],
            "jev_score": jev[candidate_id] if route == "evidence_layer" else None,
            "relevance_grade": case["relevance_scores"][candidate_id],
        })
    by_id = {item["candidate_id"]: item for item in rows}
    selected = [by_id[candidate_id] for candidate_id in selected_ids]
    return rows, selected, (time.perf_counter() - started) * 1000


def _new_jev_transport():
    import httpx2
    from typesafe_sdk import RetryPolicy as SDKRetryPolicy, TypeSafeClient

    statuses: list[int] = []

    def on_response(response) -> None:
        statuses.append(int(response.status_code))

    transport = httpx2.Client(
        timeout=JEV_TIMEOUT_SECONDS,
        event_hooks={"response": [on_response]},
    )
    client = TypeSafeClient(
        base_url=DEFAULT_JEV_API_URL,
        model=DEFAULT_JEV_MODEL,
        timeout=JEV_TIMEOUT_SECONDS,
        retry=SDKRetryPolicy(max_retries=0),
        http_client=transport,
    )
    return client, statuses


def _score_case(
    *,
    client: TypeSafeClient,
    permit_factory,
    provider: str,
    split: str,
    case: dict,
    previous: dict[tuple[str, str, str], dict],
    output_rows: list[dict],
    response_statuses: list[int],
    save_partial,
) -> tuple[dict[str, dict], float, int]:
    from typesafe_sdk import Noul, RetryPolicy as SDKRetryPolicy

    scores: dict[str, dict] = {}
    started = time.perf_counter()
    attempts = 0
    prior_case_rows = [
        row for row in output_rows
        if row["provider_arm"] == provider and row["case_id"] == case["case_id"]
    ]
    for candidate in case["candidates"]:
        candidate_id = candidate["candidate_id"]
        key = (provider, case["case_id"], candidate_id)
        if key in previous and previous[key].get("score") is not None:
            record = previous[key]
        else:
            record = None
            prior_attempts = [
                row["attempt"] for row in output_rows
                if (row["provider_arm"], row["case_id"], row["candidate_id"]) == key
            ]
            attempt_offset = max(prior_attempts, default=0)
            for retry_number in range(JEV_MAX_ATTEMPTS):
                attempt_index = attempt_offset + retry_number
                if retry_number:
                    time.sleep(min(0.5 * (2 ** (retry_number - 1)), 2.0))
                attempts += 1
                permit = permit_factory(provider, attempt_index)
                scorer = JevEvidenceScorer(
                    model=DEFAULT_JEV_MODEL,
                    timeout=JEV_TIMEOUT_SECONDS,
                    split=split,
                    case_id=case["case_id"],
                    candidate_id=candidate_id,
                    transfer_permit=permit,
                    client=client,
                    noul_factory=lambda instructions: Noul(instructions=instructions),
                    sdk_retry_policy=SDKRetryPolicy(max_retries=0),
                )
                call_started = time.perf_counter()
                response_status_start = len(response_statuses)
                try:
                    score = scorer.score(case["task"]["input"], candidate["text"])
                    error_category = None
                except JevScorerError as exc:
                    score = None
                    error_category = exc.category
                elapsed_ms = (time.perf_counter() - call_started) * 1000
                source_record = scorer.last_record
                record = {
                    "provider_arm": provider,
                    "split": split,
                    "case_id": case["case_id"],
                    "candidate_id": candidate_id,
                    "query_sha256": _sha256_bytes(case["task"]["input"].encode()),
                    "candidate_text_sha256": _sha256_bytes(candidate["text"].encode()),
                    "score": score,
                    "model_requested": source_record.model_requested if source_record else DEFAULT_JEV_MODEL,
                    "model_returned": source_record.model_returned if source_record else None,
                    "sdk_version": source_record.sdk_version if source_record else None,
                    "input_tokens": source_record.input_tokens if source_record else None,
                    "output_tokens": source_record.output_tokens if source_record else None,
                    "latency_ms": source_record.latency_ms if source_record else round(elapsed_ms),
                    "retry_count": 0,
                    "attempt": attempt_index + 1,
                    "failure_category": error_category,
                    "http_statuses": response_statuses[response_status_start:],
                    "estimated_cost_usd": _jev_input_cost_usd(source_record.input_tokens if source_record else None),
                    "price_basis": "TypeSafe published input price; output tokens free; estimate, not an invoice",
                }
                output_rows.append(record)
                save_partial()
                if score is not None:
                    break
            if record is None or record.get("score") is None:
                raise RuntimeError(
                    f"Live JEV scoring failed for {provider} {case['case_id']} {candidate_id}; "
                    "partial evidence is retained and no case was omitted."
                )
            previous[key] = record
        scores[candidate_id] = record
    case_records = [
        row for row in output_rows
        if row["provider_arm"] == provider and row["case_id"] == case["case_id"]
    ]
    prior_attempt_keys = {
        (row["provider_arm"], row["case_id"], row["candidate_id"], row["attempt"])
        for row in prior_case_rows
    }
    prior_elapsed = sum(float(row.get("latency_ms") or 0) for row in case_records if (
        row["provider_arm"], row["case_id"], row["candidate_id"], row["attempt"]
    ) in prior_attempt_keys)
    elapsed_ms = (time.perf_counter() - started) * 1000 + prior_elapsed
    return scores, elapsed_ms, len(case_records)


def _load_partial(input_hashes: dict[str, str]) -> dict:
    if not PARTIAL_PATH.exists():
        return {"run_id": str(uuid.uuid4()), "answers": [], "jev_outputs": [], "judge_pairs": [], "input_sha256": input_hashes}
    partial = json.loads(PARTIAL_PATH.read_text(encoding="utf-8"))
    if partial.get("input_sha256") != input_hashes:
        raise RuntimeError("Phase 7B checkpoint input fingerprints differ; refusing resume")
    if partial.get("benchmark") != "phase7b-live-real-world-v1" or not partial.get("run_id"):
        raise RuntimeError("Phase 7B checkpoint schema is invalid")
    return partial


def _write_partial(partial: dict) -> None:
    PARTIAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = PARTIAL_PATH.with_suffix(PARTIAL_PATH.suffix + ".tmp")
    temporary.write_text(json.dumps(partial, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(PARTIAL_PATH)


def _answer_arm(
    *,
    transport: httpx.Client,
    case: dict,
    split: str,
    provider: str,
    route: str,
    live_scores: dict[str, dict],
    jev_elapsed_ms: float,
    jev_attempts: int,
    jev_cost_usd: float | None,
) -> dict:
    route_started = time.perf_counter()
    retrieved, selected, selection_ms = _route_evidence(
        case, LexicalScorer(), route, live_scores
    )
    user_input, citation_map = phase7._build_answer_prompt(case, selected)
    model = phase7.OPENAI_MODEL if provider == "openai" else phase7.DEEPSEEK_MODEL
    key = os.environ["OPENAI_API_KEY"] if provider == "openai" else os.environ["DEEPSEEK_API_KEY"]
    response = phase7._post_response(
        transport,
        provider=provider,
        model=model,
        api_key=key,
        instructions=phase7.GENERATION_INSTRUCTIONS,
        user_input=user_input,
        max_output_tokens=phase7.GENERATION_MAX_OUTPUT_TOKENS,
    )
    usage = response["usage"]
    jev_cost = jev_cost_usd if route == "evidence_layer" else 0.0
    answer = {
        "case_id": case["case_id"],
        "task_id": case["task"]["task_id"],
        "split": split,
        "provider": provider,
        "route": route,
        "status": "ok" if response["answer"] else "failed",
        "error_category": response["error_category"],
        "model_requested": model,
        "model_returned": response["payload"].get("model") if response["payload"] else None,
        "service_tier_returned": response["payload"].get("service_tier") if response["payload"] else None,
        "reasoning_effort_returned": (response["payload"].get("reasoning") or {}).get("effort") if response["payload"] else None,
        "retrieved_evidence": retrieved,
        "selected_candidate_ids": [row["candidate_id"] for row in selected],
        "selected_evidence": selected,
        "citation_map": citation_map,
        "answer": response["answer"],
        "citation_ids": [],
        "citation_validation": {},
        "evidence_metrics": phase7._evidence_metrics(case, retrieved, selected),
        "usage": usage,
        "llm_cost_usd": round(phase7._cost(provider, usage), 12),
        "jev_cost_usd": round(jev_cost, 12) if jev_cost is not None else None,
        "system_api_cost_usd": (
            round(phase7._cost(provider, usage) + jev_cost, 12)
            if jev_cost is not None else None
        ),
        "jev_request_count": jev_attempts if route == "evidence_layer" else 0,
        "jev_case_latency_ms": jev_elapsed_ms if route == "evidence_layer" else 0.0,
        "attempts": response["attempts"],
        "retry_count": response["retry_count"],
        "api_latency_ms": response["api_latency_ms"],
        "retrieval_and_selection_latency_ms": selection_ms,
        "end_to_end_latency_ms": (time.perf_counter() - route_started) * 1000 + (jev_elapsed_ms if route == "evidence_layer" else 0.0),
        "request_input_sha256": _sha256_bytes(user_input.encode()),
        "instructions_sha256": _sha256_bytes(phase7.GENERATION_INSTRUCTIONS.encode()),
        "response_sha256": _sha256_bytes(response["answer"].encode()) if response["answer"] else None,
        "unpriced_http_200_attempts": response["unpriced_http_200_attempts"],
        "judge": None,
    }
    if answer["status"] == "ok":
        phase7._finalize_citations(answer)
    return answer


def _metric_row(row: dict) -> dict[str, float | None]:
    judge = row.get("judge") or {}
    usage = row.get("usage") or {}
    return {
        "correctness": judge.get("correctness"),
        "completeness": judge.get("completeness"),
        "groundedness": judge.get("groundedness"),
        "citation_correctness": judge.get("citation_correctness"),
        "citation_reference_validity": (row.get("citation_validation") or {}).get("citation_reference_validity"),
        "citation_relevance_precision": (row.get("citation_validation") or {}).get("citation_relevance_precision"),
        "evidence_coverage": (row.get("evidence_metrics") or {}).get("gold_passage_recall"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "llm_cost_usd": row.get("llm_cost_usd"),
        "jev_cost_usd": row.get("jev_cost_usd"),
        "system_api_cost_usd": row.get("system_api_cost_usd"),
        "end_to_end_latency_ms": row.get("end_to_end_latency_ms"),
    }


def _case_arm_counts(answers: list[dict]) -> dict[str, int]:
    counts = {split: sum(row["split"] == split for row in answers) for split in SPLITS}
    counts["overall"] = len(answers)
    return counts


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _paired_analysis(answers: list[dict]) -> dict:
    output = {}
    by_key = {(row["provider"], row["case_id"], row["route"]): row for row in answers}
    for provider in PROVIDERS:
        per_case = []
        for case_id in sorted({row["case_id"] for row in answers if row["provider"] == provider}):
            base = by_key[(provider, case_id, "traditional_rag")]
            evidence = by_key[(provider, case_id, "evidence_layer")]
            base_metrics, el_metrics = _metric_row(base), _metric_row(evidence)
            per_case.append({
                "case_id": case_id,
                "split": base["split"],
                "delta_evidence_layer_minus_baseline": {
                    name: el_metrics[name] - base_metrics[name]
                    for name in METRICS
                    if isinstance(el_metrics.get(name), (float, int))
                    and isinstance(base_metrics.get(name), (float, int))
                },
            })
        split_summaries = {}
        for split in (*SPLITS, "overall"):
            selected = per_case if split == "overall" else [row for row in per_case if row["split"] == split]
            metric_results = {}
            for metric in METRICS:
                values = [row["delta_evidence_layer_minus_baseline"][metric] for row in selected if metric in row["delta_evidence_layer_minus_baseline"]]
                if not values or len(values) != len(selected):
                    metric_results[metric] = {"point_estimate": None, "ci_lower": None, "ci_upper": None, "cases": len(values)}
                    continue
                rng = random.Random(BOOTSTRAP_SEED ^ int(hashlib.sha256(f"{provider}:{split}:{metric}".encode()).hexdigest()[:8], 16))
                boot = [statistics.fmean(values[rng.randrange(len(values))] for _ in values) for _ in range(BOOTSTRAP_REPLICATES)]
                metric_results[metric] = {
                    "point_estimate": statistics.fmean(values),
                    "ci_lower": _percentile(boot, 0.025),
                    "ci_upper": _percentile(boot, 0.975),
                    "cases": len(values),
                }
            split_summaries[split] = {"cases": len(selected), "metrics": metric_results}
        route_summaries = {}
        for route in ROUTES:
            route_rows = [row for row in answers if row["provider"] == provider and row["route"] == route]
            by_split = {}
            for split in (*SPLITS, "overall"):
                rows = route_rows if split == "overall" else [row for row in route_rows if row["split"] == split]
                metric_values = defaultdict(list)
                for row in rows:
                    for name, value in _metric_row(row).items():
                        if isinstance(value, (float, int)):
                            metric_values[name].append(float(value))
                by_split[split] = {
                    "cases": len(rows),
                    "successful_answers": sum(row["status"] == "ok" for row in rows),
                    "failed_answers": sum(row["status"] != "ok" for row in rows),
                    "mean": {name: _mean(values) for name, values in sorted(metric_values.items())},
                    "p95_end_to_end_latency_ms": _percentile([float(row["end_to_end_latency_ms"]) for row in rows], 0.95) if rows else None,
                    "total_llm_cost_usd": round(sum(float(row["llm_cost_usd"]) for row in rows), 12),
                    "total_jev_cost_usd": (
                        round(sum(float(row["jev_cost_usd"]) for row in rows), 12)
                        if all(row.get("jev_cost_usd") is not None for row in rows) else None
                    ),
                    "total_system_api_cost_usd": (
                        round(sum(float(row["system_api_cost_usd"]) for row in rows), 12)
                        if all(row.get("system_api_cost_usd") is not None for row in rows) else None
                    ),
                    "total_input_tokens": sum(int((row.get("usage") or {}).get("input_tokens", 0)) for row in rows),
                    "total_output_tokens": sum(int((row.get("usage") or {}).get("output_tokens", 0)) for row in rows),
                    "total_tokens": sum(int((row.get("usage") or {}).get("total_tokens", 0)) for row in rows),
                    "total_end_to_end_latency_ms": round(sum(float(row["end_to_end_latency_ms"]) for row in rows), 3),
                    "api_attempts": sum(len(row.get("attempts", [])) for row in rows),
                    "api_retries": sum(int(row.get("retry_count", 0)) for row in rows),
                    "api_errors": sum(
                        bool(attempt.get("error_category"))
                        for row in rows for attempt in row.get("attempts", [])
                    ),
                }
            route_summaries[route] = by_split
        output[provider] = {
            "routes": route_summaries,
            "per_case_deltas": per_case,
            "paired_case_bootstrap": {
                "method": "paired percentile bootstrap",
                "resampling_unit": "case_id; route pair resampled together",
                "replicates": BOOTSTRAP_REPLICATES,
                "confidence_level": 0.95,
                "seed": BOOTSTRAP_SEED,
                "splits": split_summaries,
            },
        }
    return output


def _compare_with_phase7(answers: list[dict]) -> dict:
    previous = json.loads(phase7.RESULT_PATH.read_text(encoding="utf-8"))
    current_by_key = {(row["provider"], row["case_id"], row["route"]): row for row in answers}
    previous_by_key = {(row["provider"], row["case_id"], row["route"]): row for row in previous["answers"]}
    metrics = ("correctness", "completeness", "groundedness", "citation_correctness", "evidence_coverage", "total_tokens", "llm_cost_usd")
    output = {}
    for provider in PROVIDERS:
        output[provider] = {}
        for route in ROUTES:
            rows = []
            latency_deltas = []
            case_ids = sorted(key[1] for key in current_by_key if key[0] == provider)
            for case_id in case_ids:
                current_row = current_by_key[(provider, case_id, route)]
                previous_row = previous_by_key[(provider, case_id, route)]
                current = _metric_row(current_row)
                prior = _metric_row(previous_row)
                prior_latency = (
                    previous_row.get("end_to_end_latency_ms_cached_jev")
                    if route == "traditional_rag"
                    else previous_row.get("end_to_end_latency_ms_estimated_live_jev")
                )
                latency_delta = (
                    float(current_row["end_to_end_latency_ms"]) - float(prior_latency)
                    if isinstance(prior_latency, (float, int)) else None
                )
                if latency_delta is not None:
                    latency_deltas.append(latency_delta)
                rows.append({
                    "case_id": case_id,
                    "end_to_end_latency_delta_ms": latency_delta,
                    "deltas_phase7b_minus_phase7": {
                        name: current[name] - prior[name]
                        for name in metrics
                        if isinstance(current.get(name), (float, int)) and isinstance(prior.get(name), (float, int))
                    },
                })
            output[provider][route] = {
                "cases": len(rows),
                "mean_deltas": {
                    name: _mean([row["deltas_phase7b_minus_phase7"][name] for row in rows if name in row["deltas_phase7b_minus_phase7"]])
                    for name in metrics
                },
                "mean_end_to_end_latency_delta_ms": _mean(latency_deltas),
                "latency_comparability": (
                    "Phase 7B actual end-to-end latency versus Phase 7 cached-JEV latency for traditional_rag"
                    if route == "traditional_rag"
                    else "Phase 7B actual live-JEV end-to-end latency versus Phase 7 historical Phase 6 JEV estimate"
                ),
                "per_case": rows,
            }
    return {
        "phase7_results_sha256": _file_sha256(phase7.RESULT_PATH),
        "phase7_manifest_sha256": _file_sha256(phase7.MANIFEST_PATH),
        "metrics_are_descriptive_only": True,
        "providers": output,
    }


def _render_report(result: dict) -> str:
    lines = [
        "# Phase 7B — Live End-to-End Cost Validation",
        "",
        f"Run ID: `{result['run']['run_id']}` · completed `{result['run']['finished_at_utc']}`",
        "",
        "## Scope and limits",
        "",
        "This is a separate live rerun of all four arms. Each Evidence Layer arm makes fresh JEV calls for its own provider/case/candidate set; Phase 7 results are unchanged. Real-World v1 relevance grades were authored by the developer, and the exposed held-out split was disclosed earlier. This is exploratory evidence, not a blind test or independent scientific validation. The automated paired judge is not human review and uses OpenAI, which is also one of the tested generators.",
        "",
        f"Input set: {result['run']['counts']['passages']} passages, {result['run']['counts']['cases']} cases, {result['run']['counts']['candidate_records']} candidates. JEV: {result['run']['counts']['jev_requests']} requests; LLM answers: {result['run']['counts']['generation_answers_saved']}; paired judge calls: {result['run']['counts']['judge_pairs_saved']}.",
        "Per-query end-to-end latency runs from ranking/retrieval start through the provider answer. The Evidence Layer arm includes the five sequential JEV calls; the one-time authenticated ACL snapshot read is measured separately in `secure_source_read`.",
        "",
        "## Per-provider results by split",
        "",
        "All cost values below are provider API usage estimates from token usage and published prices; they are not invoice exports. `system API cost` = LLM + JEV. Automated quality metrics are 0–2 judge scores; evidence coverage uses the fixed provisional gold judgments.",
        "",
        "| Provider | Split | Route | Correct | Complete | Grounded | Citation score | Evidence coverage | Mean input/output/total tokens | LLM API $ | JEV API $ | System API $ | Mean E2E ms | P95 E2E ms |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for provider in PROVIDERS:
        overall = result["comparisons"][provider]["routes"]
        for split in (*SPLITS, "overall"):
            for route in ROUTES:
                row = overall[route][split]
                means = row["mean"]
                def cell(value):
                    return "n/a" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)
                lines.append(
                    f"| {provider} | {split} | {route} | {cell(means.get('correctness'))} | {cell(means.get('completeness'))} | {cell(means.get('groundedness'))} | {cell(means.get('citation_correctness'))} | {cell(means.get('evidence_coverage'))} | {cell(means.get('input_tokens'))}/{cell(means.get('output_tokens'))}/{cell(means.get('total_tokens'))} | {cell(row.get('total_llm_cost_usd'))} | {cell(row.get('total_jev_cost_usd'))} | {cell(row.get('total_system_api_cost_usd'))} | {cell(means.get('end_to_end_latency_ms'))} | {cell(row.get('p95_end_to_end_latency_ms'))} |"
                )
    lines += [
        "",
        "## Overall route comparison",
        "",
        "Cost percentages compare the fixed 40-case run and use published token prices. Quality deltas are automated-judge/evidence metrics; the paired intervals below show uncertainty on this dataset.",
        "",
        "| Provider | Mean tokens base → EL | LLM cost base → EL | EL JEV cost | System API cost base → EL | System cost change (EL vs base; negative saves) | E2E latency base → EL (ms) | Correctness Δ | Evidence coverage Δ |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for provider in PROVIDERS:
        routes = result["comparisons"][provider]["routes"]
        base, evidence = routes["traditional_rag"]["overall"], routes["evidence_layer"]["overall"]
        base_system = base["total_system_api_cost_usd"]
        evidence_system = evidence["total_system_api_cost_usd"]
        system_change_pct = (evidence_system / base_system - 1.0) * 100.0 if base_system else None
        def compact(value):
            return "n/a" if value is None else f"{value:.4f}"
        lines.append(
            f"| {provider} | {compact(base['mean'].get('total_tokens'))} → {compact(evidence['mean'].get('total_tokens'))} | ${compact(base['total_llm_cost_usd'])} → ${compact(evidence['total_llm_cost_usd'])} | ${compact(evidence['total_jev_cost_usd'])} | ${compact(base_system)} → ${compact(evidence_system)} | {compact(system_change_pct)}% | {compact(base['mean'].get('end_to_end_latency_ms'))} → {compact(evidence['mean'].get('end_to_end_latency_ms'))} | {compact(evidence['mean'].get('correctness') - base['mean'].get('correctness'))} | {compact(evidence['mean'].get('evidence_coverage') - base['mean'].get('evidence_coverage'))} |"
        )
    lines += ["", "## Paired differences", "", "The table reports Evidence Layer minus Traditional RAG. Confidence intervals resample paired cases, not candidates.", "", "| Provider | Split | Metric | Δ mean | 95% paired bootstrap CI |", "|---|---|---|---:|---:|"]
    for provider in PROVIDERS:
        for split in (*SPLITS, "overall"):
            metrics = result["comparisons"][provider]["paired_case_bootstrap"]["splits"][split]["metrics"]
            for name in ("correctness", "completeness", "groundedness", "citation_correctness", "evidence_coverage", "total_tokens", "llm_cost_usd", "jev_cost_usd", "system_api_cost_usd", "end_to_end_latency_ms"):
                row = metrics[name]
                ci = "n/a" if row["ci_lower"] is None else f"[{row['ci_lower']:.4f}, {row['ci_upper']:.4f}]"
                point = "n/a" if row["point_estimate"] is None else f"{row['point_estimate']:.4f}"
                lines.append(f"| {provider} | {split} | {name} | {point} | {ci} |")
    correction = result["run"].get("postrun_artifact_correction")
    correction_text = (
        "none"
        if not correction
        else f"{correction['scope']}; benchmark answers, JEV scores, judge outputs, costs, and timings unchanged"
    )
    lines += [
        "",
        "## Practical readout",
        "",
        "These labels describe paired bootstrap intervals on this fixed, previously exposed dataset. They do not establish generalization.",
        "",
        "| Provider | Question | Paired interval readout |",
        "|---|---|---|",
    ]
    quality_metrics = ("correctness", "completeness", "groundedness", "citation_correctness", "evidence_coverage")
    efficiency_metrics = ("total_tokens", "system_api_cost_usd", "end_to_end_latency_ms")
    for provider in PROVIDERS:
        overall_metrics = result["comparisons"][provider]["paired_case_bootstrap"]["splits"]["overall"]["metrics"]
        for metric in quality_metrics:
            row = overall_metrics[metric]
            if row["ci_lower"] is None:
                label = "not measurable from saved pairs"
            elif row["ci_lower"] > 0:
                label = "Evidence Layer higher; interval excludes zero"
            elif row["ci_upper"] < 0:
                label = "Evidence Layer lower; interval excludes zero"
            else:
                label = "no direction established; interval includes zero"
            lines.append(f"| {provider} | {metric} | {label} |")
        for metric in efficiency_metrics:
            row = overall_metrics[metric]
            if row["ci_lower"] is None:
                label = "not measurable from saved pairs"
            elif row["ci_upper"] < 0:
                label = "lower with Evidence Layer; interval excludes zero"
            elif row["ci_lower"] > 0:
                label = "higher with Evidence Layer; interval excludes zero"
            else:
                label = "no direction established; interval includes zero"
            lines.append(f"| {provider} | {metric} | {label} |")
    lines += [
        "",
        "## Phase 7 comparison",
        "",
        "These are descriptive Phase 7B-minus-Phase 7 differences over the same fixed cases. Phase 7 reused Phase 6 scores; Phase 7B made fresh JEV calls. Latency uses different measurement bases and is not a randomized time-only comparison.",
        "",
        "| Provider | Route | Metric | Mean Phase 7B − Phase 7 |",
        "|---|---|---|---:|",
    ]
    for provider in PROVIDERS:
        for route in ROUTES:
            deltas = result["phase7_reference_comparison"]["providers"][provider][route]["mean_deltas"]
            for name in ("correctness", "completeness", "groundedness", "citation_correctness", "evidence_coverage", "total_tokens", "llm_cost_usd", "end_to_end_latency_ms"):
                value = (
                    result["phase7_reference_comparison"]["providers"][provider][route]["mean_end_to_end_latency_delta_ms"]
                    if name == "end_to_end_latency_ms" else deltas.get(name)
                )
                lines.append(f"| {provider} | {route} | {name} | {'n/a' if value is None else f'{value:.4f}'} |")
    lines += [
        "",
        "## Model and cost provenance",
        "",
        f"- OpenAI exact API model ID: `{result['models']['openai']['model_id']}`; endpoint `{result['models']['openai']['endpoint']}`; `{result['models']['openai']['parameters']}`.",
        f"- DeepSeek exact requested API model ID: `{result['models']['deepseek']['model_id']}`; returned IDs: `{result['models']['deepseek']['served_model_ids']}`; endpoint `{result['models']['deepseek']['endpoint']}`.",
        f"- JEV requested `{result['models']['jev']['requested_model_id']}`; served IDs: `{result['models']['jev']['served_model_ids']}`; SDK `{result['models']['jev']['sdk_version']}`; endpoint `{result['models']['jev']['endpoint']}`.",
        f"- Published price snapshot: `{result['pricing']['checked_at_utc']}`. JEV price is ${JEV_INPUT_USD_PER_MILLION}/million input tokens with free output, computed from returned token usage. No account billing/invoice endpoint was available in the provider API schema, so this is a published-rate estimate, not the account's posted charge.",
        f"- Combined published-rate API cost estimate, including live JEV requests, all answer generations/retries, and automated judge calls: `{result['cost_status']['published_rate_combined_experiment_api_cost_estimate_usd']}` USD. The account invoice total is unavailable.",
        f"- Failed request attempts: JEV {result['run']['counts']['jev_errors']}, LLM {result['run']['counts']['generation_errors']}, judge {result['run']['counts']['judge_errors']}; final answer failures: {sum(row['status'] != 'ok' for row in result['answers'])}; LLM retries: {sum(row['retry_count'] for row in result['answers'])}; unpriced HTTP 200 attempts: {result['cost_status']['unpriced_http_200_attempts']}. The three DeepSeek failures were incomplete HTTP 200 responses during Traditional RAG and all were recovered by retries; their reported token use and cost remain included.",
        "- JEV/LLM API calls ran from this isolated benchmark environment. No production Egress, `/retrieve`, Supabase production, Railway, or deployment path was touched.",
        "",
        "## Reproduction",
        "",
        f"The API execution used runner source SHA-256 `{result['run']['runner_source_sha256_at_api_execution']}`. The final artifact builder used `{result['run']['artifact_builder_source_sha256']}`. Post-run artifact correction: {correction_text}.",
        "",
        "`python scripts/verify_phase7b_live_cost_validation.py` verifies saved input/output hashes and recomputes rankings, answer metrics, paired differences, bootstrap intervals, and costs offline without provider access.",
        "",
        "## Official pricing and API references",
        "",
        "- [OpenAI GPT-6.1 Sol model and pricing](https://developers.openai.com/api/docs/models/gpt-6.1-sol)",
        "- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing)",
        "- [DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)",
        "- [DeepSeek Responses API](https://api-docs.deepseek.com/api/create-response/)",
        "- [TypeSafe API reference](https://api.typesafe.ai/redoc)",
        "- [TypeSafe official published JEV pricing](https://typesafe.ai/)",
        "",
    ]
    return "\n".join(lines)


def _run_live() -> tuple[dict, dict]:
    for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "TYPESAFE_API_KEY"):
        if not os.environ.get(name):
            raise RuntimeError(f"Required API secret {name} is unavailable")
    if RESULT_PATH.exists() or MANIFEST_PATH.exists():
        raise RuntimeError("Phase 7B final artifacts already exist; refusing to overwrite")
    cases_by_split, _phase6_hashes, preflight = phase6_jev._verify_local_inputs()
    phase7_verifier.verify_saved_run()
    cases = [(split, case) for split in SPLITS for case in cases_by_split[split]]
    if len(cases) != 40 or sum(len(case["candidates"]) for _, case in cases) != 200:
        raise RuntimeError("Real-World v1 must contain the fixed 40 cases and 200 candidate records")
    passages, secure_read = phase7._read_passages_through_acl_path()
    phase7._verify_candidates_against_snapshot(cases_by_split, passages)
    input_hashes = _input_hashes()
    partial = _load_partial(input_hashes)
    resumed_from_checkpoint = PARTIAL_PATH.exists() and bool(partial.get("answers") or partial.get("jev_outputs"))
    partial.setdefault("started_at_utc", datetime.now(timezone.utc).isoformat())
    partial["resume_count"] = int(partial.get("resume_count", 0)) + int(resumed_from_checkpoint)
    if not partial.get("secure_source_read"):
        partial["benchmark"] = "phase7b-live-real-world-v1"
        partial["secure_source_read"] = secure_read
        _write_partial(partial)
    elif partial["secure_source_read"].get("snapshot_sha256") != secure_read.get("snapshot_sha256"):
        raise RuntimeError("Resumed Phase 7B run has a different secure snapshot digest")

    logger = logging.getLogger("typesafe_sdk")
    logger.disabled = True
    logger.propagate = False
    sdk_version = importlib.metadata.version("typesafe-sdk")
    if sdk_version != "0.7.2":
        raise RuntimeError("Installed typesafe-sdk version differs from the Phase 6/7 pin")
    jev_model_prompt = f"{_QUESTION_TEXT} Candidate identifier: candidate_0."
    expected_jev_records = {
        (provider, case["case_id"], candidate["candidate_id"])
        for provider in PROVIDERS for _, case in cases for candidate in case["candidates"]
    }
    jev_rows = partial.setdefault("jev_outputs", [])
    previous_jev = {}
    for record in jev_rows:
        key = (record["provider_arm"], record["case_id"], record["candidate_id"])
        prior = previous_jev.get(key)
        if prior is None or record.get("score") is not None or record.get("attempt", 0) > prior.get("attempt", 0):
            previous_jev[key] = record
    if not set(previous_jev) <= expected_jev_records:
        raise RuntimeError("Phase 7B checkpoint contains a candidate outside the fixed set")
    answers = partial.setdefault("answers", [])
    answer_map = {(row["provider"], row["case_id"], row["route"]): row for row in answers}
    judge_rows = partial.setdefault("judge_pairs", [])
    api_events = Counter(partial.setdefault("api_events", {}))

    scope_sha = _sha256_bytes(_canonical_bytes({
        "run_id": partial["run_id"],
        "input_sha256": input_hashes,
        "generated_cases_sha256": preflight["generated_cases_sha256"],
        "jev_model": DEFAULT_JEV_MODEL,
        "jev_prompt_sha256": _sha256_bytes(jev_model_prompt.encode()),
        "max_requests_per_arm": 200,
    }))
    permits = {}
    def permit_factory(provider: str, attempt: int):
        key = (provider, attempt)
        if key not in permits:
            permits[key] = _issue_real_world_benchmark_permit(
                cases_by_split,
                run_id=f"{partial['run_id']}-{provider}-{attempt}",
                input_sha256=scope_sha,
            )
        return permits[key]

    safe_client = httpx.Client(timeout=phase7.REQUEST_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False)
    try:
        model_availability = {
            "openai": _model_listing(safe_client, "openai"),
            "deepseek": _model_listing(safe_client, "deepseek"),
            "jev": _jev_model_listing(safe_client),
        }
    finally:
        safe_client.close()
    if partial.get("model_availability") and partial["model_availability"].get("jev", {}).get("requested_model_id") != DEFAULT_JEV_MODEL:
        raise RuntimeError("JEV availability changed while resuming Phase 7B")
    partial["model_availability"] = model_availability
    _write_partial(partial)

    jev_client, jev_statuses = _new_jev_transport()
    transport = httpx.Client(timeout=phase7.REQUEST_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False)
    run_started = datetime.fromisoformat(partial["started_at_utc"])
    rng = random.Random(ORDER_SEED)
    plan = [(split, case, provider, route) for split, case in cases for provider in PROVIDERS for route in ROUTES]
    rng.shuffle(plan)
    judge_started = {
        (row["provider_pair"], row["case_id"])
        for row in judge_rows if row.get("status") == "ok"
    }
    try:
        for ordinal, (split, case, provider, route) in enumerate(plan, 1):
            answer_key = (provider, case["case_id"], route)
            if answer_key in answer_map and answer_map[answer_key].get("status") == "ok":
                continue
            score_map: dict[str, dict] = {}
            jev_elapsed_ms = 0.0
            jev_attempts = 0
            jev_cost_usd = 0.0
            if route == "evidence_layer":
                score_map, jev_elapsed_ms, jev_attempts = _score_case(
                    client=jev_client,
                    permit_factory=permit_factory,
                    provider=provider,
                    split=split,
                    case=case,
                    previous=previous_jev,
                    output_rows=jev_rows,
                    response_statuses=jev_statuses,
                    save_partial=lambda: _write_partial(partial),
                )
                case_jev_rows = [
                    row for row in jev_rows
                    if row["provider_arm"] == provider and row["case_id"] == case["case_id"]
                ]
                jev_attempts = len(case_jev_rows)
                jev_cost_usd = (
                    sum(float(row["estimated_cost_usd"]) for row in case_jev_rows)
                    if all(row.get("estimated_cost_usd") is not None for row in case_jev_rows)
                    else None
                )
                api_events["jev_requests"] = len(jev_rows)
                api_events["jev_retries"] = sum(max(0, row["attempt"] - 1) for row in jev_rows)
                api_events["jev_errors"] = sum(row.get("failure_category") is not None for row in jev_rows)
                _write_partial(partial)
            row = _answer_arm(
                transport=transport,
                case=case,
                split=split,
                provider=provider,
                route=route,
                live_scores=score_map,
                jev_elapsed_ms=jev_elapsed_ms,
                jev_attempts=jev_attempts,
                jev_cost_usd=jev_cost_usd,
            )
            prior_failed = answer_map.get(answer_key)
            if prior_failed and prior_failed.get("status") != "ok":
                row["attempts"] = prior_failed.get("attempts", []) + row.get("attempts", [])
                row["retry_count"] = max(0, len(row["attempts"]) - 1)
                row["usage"] = phase7._sum_attempt_usage(row["attempts"])
                row["llm_cost_usd"] = round(phase7._cost(provider, row["usage"]), 12)
                row["unpriced_http_200_attempts"] = sum(
                    attempt.get("http_status") == 200 and not attempt.get("usage")
                    for attempt in row["attempts"]
                )
                row["api_latency_ms"] = sum(float(attempt.get("latency_ms") or 0) for attempt in row["attempts"])
                row["end_to_end_latency_ms"] += float(prior_failed.get("api_latency_ms") or 0)
                if row.get("system_api_cost_usd") is not None:
                    row["system_api_cost_usd"] = round(row["llm_cost_usd"] + float(row["jev_cost_usd"] or 0), 12)
            answer_map[answer_key] = row
            answers[:] = list(answer_map.values())
            provider_answers = [item for item in answers if item["provider"] == provider]
            api_events[f"{provider}_generation_requests"] = sum(len(item.get("attempts", [])) for item in provider_answers)
            api_events[f"{provider}_generation_retries"] = sum(int(item.get("retry_count", 0)) for item in provider_answers)
            api_events[f"{provider}_generation_errors"] = sum(
                bool(attempt.get("error_category"))
                for item in provider_answers for attempt in item.get("attempts", [])
            )
            partial["api_events"] = dict(api_events)
            _write_partial(partial)
            print(f"answer {ordinal:03d}/160 {provider} {route} {case['case_id']} {row['status']} e2e_ms={row['end_to_end_latency_ms']:.1f}", flush=True)

        if len(answer_map) != 160:
            raise RuntimeError("Phase 7B has incomplete route arms; checkpoint retained")
        if any(row.get("status") != "ok" for row in answer_map.values()):
            raise RuntimeError("Phase 7B contains an unsuccessful generation; checkpoint retained")

        pair_map = phase7._pair_answers(answers)
        cases_by_id = {case["case_id"]: case for _, case in cases}
        for (provider, case_id), pair in sorted(pair_map.items()):
            if (provider, case_id) in judge_started:
                continue
            judged = phase7._judge_request(pair, cases_by_id[case_id], transport, os.environ["OPENAI_API_KEY"])
            pair["traditional_rag"]["judge"] = judged.get("scores", {}).get("traditional_rag")
            pair["evidence_layer"]["judge"] = judged.get("scores", {}).get("evidence_layer")
            prior_failures = [
                old for old in judge_rows
                if old["provider_pair"] == provider and old["case_id"] == case_id and old.get("status") != "ok"
            ]
            if prior_failures:
                prior = prior_failures[-1]
                judged["attempts"] = prior.get("attempts", []) + judged.get("attempts", [])
                judged["retry_count"] = max(0, len(judged["attempts"]) - 1)
                judged["api_latency_ms"] = sum(float(item.get("latency_ms") or 0.0) for item in judged["attempts"])
                judged["usage"] = phase7._sum_attempt_usage(judged["attempts"])
                judged["llm_cost_usd"] = round(phase7._cost("openai", judged["usage"]), 12)
                judged["unpriced_http_200_attempts"] = sum(
                    item.get("http_status") == 200 and not item.get("usage")
                    for item in judged["attempts"]
                )
                judge_rows.remove(prior)
            judge_rows.append({"provider_pair": provider, "case_id": case_id, **judged})
            if judged.get("status") == "ok":
                judge_started.add((provider, case_id))
            api_events["judge_requests"] = sum(len(item.get("attempts", [])) for item in judge_rows)
            api_events["judge_retries"] = sum(int(item.get("retry_count", 0)) for item in judge_rows)
            api_events["judge_errors"] = sum(
                bool(attempt.get("error_category"))
                for item in judge_rows for attempt in item.get("attempts", [])
            )
            partial["api_events"] = dict(api_events)
            _write_partial(partial)
            print(f"judge {provider} {case_id} {judged.get('status')}", flush=True)
    finally:
        transport.close()
        jev_client.close()

    if len(judge_rows) != 80 or any(row.get("status") != "ok" for row in judge_rows):
        raise RuntimeError("Phase 7B paired judging is incomplete; checkpoint retained")
    answers = sorted(answers, key=lambda row: (row["provider"], row["case_id"], row["route"]))
    jev_rows = sorted(jev_rows, key=lambda row: (row["provider_arm"], row["case_id"], row["candidate_id"], row["attempt"]))
    successful_jev_keys = {
        (row["provider_arm"], row["case_id"], row["candidate_id"])
        for row in jev_rows if row.get("score") is not None
    }
    if successful_jev_keys != expected_jev_records:
        raise RuntimeError("Each provider's Evidence Layer arm must save all live JEV scores")
    served_jev_models = sorted({row["model_returned"] for row in jev_rows if row.get("score") is not None and row.get("model_returned")})
    if len(served_jev_models) != 1:
        raise RuntimeError("JEV served model ID was absent or changed during Phase 7B")
    finished_at = datetime.now(timezone.utc)
    post_hashes = _input_hashes()
    if post_hashes != input_hashes:
        raise RuntimeError("Phase 7B input artifacts changed during the run")
    comparisons = _paired_analysis(answers)
    answer_sha = _sha256_bytes(_canonical_bytes(answers))
    jev_sha = _sha256_bytes(_canonical_bytes(jev_rows))
    judge_sha = _sha256_bytes(_canonical_bytes(judge_rows))
    all_jev_costs_known = all(row.get("estimated_cost_usd") is not None for row in jev_rows)
    unpriced_http_200_attempts = (
        sum(int(row.get("unpriced_http_200_attempts", 0)) for row in answers)
        + sum(row.get("input_tokens") is None for row in jev_rows)
    )
    combined_cost_estimate = (
        round(
            sum(float(row["estimated_cost_usd"]) for row in jev_rows)
            + sum(float(row.get("llm_cost_usd") or 0) for row in answers)
            + sum(float(row.get("llm_cost_usd") or 0) for row in judge_rows),
            12,
        )
        if all_jev_costs_known and unpriced_http_200_attempts == 0 else None
    )
    result = {
        "schema_version": 1,
        "benchmark": "phase7b-live-real-world-v1",
        "result_classification": "exploratory live end-to-end benchmark; not blind or independent scientific validation",
        "judgment_status": "developer-authored relevance grades; answer judge is automated and not independent human review",
        "held_out_status": "exposed previously; not a blind test",
        "run": {
            "run_id": partial["run_id"],
            "started_at_utc": run_started.isoformat(),
            "finished_at_utc": finished_at.isoformat(),
            "resumed_from_checkpoint": resumed_from_checkpoint,
            "resume_count": partial["resume_count"],
            "input_sha256": input_hashes,
            "api_execution_input_sha256": input_hashes,
            "artifact_builder_input_sha256": input_hashes,
            "runner_source_sha256_at_api_execution": input_hashes[Path(__file__).resolve().relative_to(ROOT).as_posix()],
            "artifact_builder_source_sha256": input_hashes[Path(__file__).resolve().relative_to(ROOT).as_posix()],
            "postrun_artifact_correction": None,
            "generated_cases_sha256": preflight["generated_cases_sha256"],
            "secure_snapshot_sha256": secure_read["snapshot_sha256"],
            "case_ids": sorted(case["case_id"] for _, case in cases),
            "counts": {
                "passages": len(passages),
                "cases": len(cases),
                "candidate_records": sum(len(case["candidates"]) for _, case in cases),
                "generation_answers_expected": 160,
                "generation_answers_saved": len(answers),
                "judge_pairs_expected": 80,
                "judge_pairs_saved": len(judge_rows),
                "jev_scores_expected": 400,
                "jev_scores_saved": len(successful_jev_keys),
                "jev_requests": sum(1 for row in jev_rows),
                "jev_errors": sum(row.get("failure_category") is not None for row in jev_rows),
                "generation_errors": sum(sum(bool(attempt.get("error_category")) for attempt in row.get("attempts", [])) for row in answers),
                "judge_errors": sum(sum(bool(attempt.get("error_category")) for attempt in row.get("attempts", [])) for row in judge_rows),
                "provider_model_listing_requests": 3,
                "jev_http_response_statuses": dict(Counter(
                    str(code) for record in jev_rows for code in record.get("http_statuses", [])
                )),
                "case_arm_records_by_split": _case_arm_counts(answers),
                "api_events": dict(api_events),
            },
            "answer_sha256": answer_sha,
            "jev_output_sha256": jev_sha,
            "judge_output_sha256": judge_sha,
            "synthetic_freeze_manifest_sha256": _file_sha256(phase7.PHASE6_FREEZE_PATH),
            "synthetic_result_sha256": _file_sha256(phase7.SYNTHETIC_RESULT_PATH),
            "synthetic_artifacts_unchanged": True,
        },
        "secure_source_read": secure_read,
        "retrieval_design": {
            "same_query_and_candidate_pool_each_arm": True,
            "traditional_rag": "existing lexical scorer; all top-five passages passed to generator",
            "evidence_layer": "fresh live JEV scores for five candidates per query; top three selected; JEV is called separately for OpenAI and DeepSeek arms",
            "only_intended_route_difference": "lexical top-five pass-through versus live JEV top-three selection",
            "generation_prompt_version": phase7.PROMPT_VERSION,
            "generation_instruction_sha256": _sha256_bytes(phase7.GENERATION_INSTRUCTIONS.encode()),
            "judge_prompt_version": phase7.JUDGE_PROMPT_VERSION,
            "route_order_seed": ORDER_SEED,
        },
        "models": {
            "openai": {
                **model_availability["openai"],
                "model_id": phase7.OPENAI_MODEL,
                "endpoint": f"{phase7.OPENAI_BASE}/responses",
                "sdk": "none; direct HTTPX transport",
                "httpx_version": httpx.__version__,
                "parameters": {"reasoning.effort": "low", "max_output_tokens": phase7.GENERATION_MAX_OUTPUT_TOKENS, "service_tier": "default", "store": False, "temperature": "not set"},
                "price": phase7.PRICES["openai"],
            },
            "deepseek": {
                **model_availability["deepseek"],
                "model_id": phase7.DEEPSEEK_MODEL,
                "official_version": "DeepSeek-V4-Pro-0813 per official model pricing page",
                "endpoint": f"{phase7.DEEPSEEK_BASE}/responses",
                "sdk": "none; direct HTTPX transport",
                "httpx_version": httpx.__version__,
                "parameters": {"reasoning.effort": "low", "max_output_tokens": phase7.GENERATION_MAX_OUTPUT_TOKENS, "store": False, "temperature": "not set"},
                "price": phase7.PRICES["deepseek"],
            },
            "jev": {
                **model_availability["jev"],
                "served_model_ids": sorted({row["model_returned"] for row in jev_rows if row.get("model_returned")}),
                "endpoint": f"{DEFAULT_JEV_API_URL}/v1/systemone",
                "authentication": "Bearer token from TYPESAFE_API_KEY; value omitted",
                "sdk_version": sdk_version,
                "question_prompt": jev_model_prompt,
                "prompt_version": phase6_jev.PROMPT_VERSION,
                "timeout_seconds": JEV_TIMEOUT_SECONDS,
                "sdk_retries": 0,
                "manual_max_attempts": JEV_MAX_ATTEMPTS,
                "published_price": {"input_usd_per_million": JEV_INPUT_USD_PER_MILLION, "output_usd_per_million": JEV_OUTPUT_USD_PER_MILLION, "source": "https://typesafe.ai/", "checked_at_utc": finished_at.isoformat()},
            },
        },
        "judge": {
            "provider": "openai",
            "model": phase7.JUDGE_MODEL,
            "endpoint": f"{phase7.OPENAI_BASE}/responses",
            "classification": "automated exploratory paired judge; not independent or human; OpenAI is also an evaluated generator",
            "instruction_sha256": _sha256_bytes(phase7.JUDGE_INSTRUCTIONS.encode()),
            "pairs": judge_rows,
            "overhead_cost_usd": round(sum(float(row.get("llm_cost_usd") or 0.0) for row in judge_rows), 12),
        },
        "answers": answers,
        "jev_outputs": jev_rows,
        "comparisons": comparisons,
        "phase7_reference_comparison": _compare_with_phase7(answers),
        "pricing": {
            "checked_at_utc": finished_at.isoformat(),
            "openai": phase7.PRICES["openai"],
            "deepseek": phase7.PRICES["deepseek"],
            "jev": {"input_usd_per_million": JEV_INPUT_USD_PER_MILLION, "output_usd_per_million": JEV_OUTPUT_USD_PER_MILLION, "source": "https://typesafe.ai/", "actual_invoice_charge_available": False},
        },
        "cost_status": {
            "jev_token_price_estimate_usd": round(sum(float(row["estimated_cost_usd"]) for row in jev_rows if row.get("estimated_cost_usd") is not None), 12) if all_jev_costs_known else None,
            "jev_account_invoice_cost_usd": None,
            "jev_cost_basis": "200 fresh live scores per provider arm, priced from returned input tokens and official published rate; not an account invoice",
            "evidence_layer_processing_cost_usd": 0.0,
            "evidence_layer_processing_cost_basis": "local deterministic ranking/selection; no separate billable service",
            "unpriced_http_200_attempts": unpriced_http_200_attempts,
            "known_cost_is_lower_bound": unpriced_http_200_attempts > 0 or not all_jev_costs_known,
            "judge_cost_separate_from_serving": True,
            "total_system_api_cost_usd": {provider: {route: comparisons[provider]["routes"][route]["overall"]["total_system_api_cost_usd"] for route in ROUTES} for provider in PROVIDERS},
            "published_rate_combined_experiment_api_cost_estimate_usd": combined_cost_estimate,
            "actual_combined_experiment_spend_usd": None,
            "actual_combined_experiment_spend_note": "Provider invoice/billing API totals are not exposed by the model response endpoints; published-rate usage estimates are reported instead.",
        },
    }
    report = _render_report(result)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    report_sha = _file_sha256(REPORT_PATH)
    result_bytes = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode()
    manifest = {
        "schema_version": 1,
        "benchmark": result["benchmark"],
        "manifest_kind": "independent Phase 7B live-run manifest; separate from Phase 7 and Phase 6",
        "run_id": result["run"]["run_id"],
        "results_path": RESULT_PATH.relative_to(ROOT).as_posix(),
        "results_sha256": _sha256_bytes(result_bytes),
        "report_path": REPORT_PATH.relative_to(ROOT).as_posix(),
        "report_sha256": report_sha,
        "answers_sha256": answer_sha,
        "jev_outputs_sha256": jev_sha,
        "judge_outputs_sha256": judge_sha,
        "input_sha256": input_hashes,
        "api_execution_input_sha256": input_hashes,
        "artifact_builder_input_sha256": input_hashes,
        "fixed_data_counts": {"passages": 51, "cases": 40, "candidate_records": 200},
        "phase7_reference": {"results_sha256": input_hashes[phase7.RESULT_PATH.relative_to(ROOT).as_posix()], "manifest_sha256": input_hashes[phase7.MANIFEST_PATH.relative_to(ROOT).as_posix()]},
        "pricing_checked_at_utc": finished_at.isoformat(),
        "nonclaims": ["not blind: held-out split was exposed", "relevance judgments are developer-authored", "automated judge is not independent human review", "API usage-price estimates are not account invoice charges"],
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    RESULT_PATH.write_bytes(result_bytes)
    MANIFEST_PATH.write_bytes(manifest_bytes)
    if PARTIAL_PATH.exists():
        PARTIAL_PATH.unlink()
    return result, manifest


def main() -> int:
    result, manifest = _run_live()
    print(f"Phase 7B results written; answers={result['run']['counts']['generation_answers_saved']} JEV scores={result['run']['counts']['jev_scores_saved']} judges={result['run']['counts']['judge_pairs_saved']}")
    print(f"Results SHA-256: {manifest['results_sha256']}")
    print("Secrets omitted; no production retrieval path was enabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
