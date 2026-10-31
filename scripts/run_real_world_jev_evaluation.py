#!/usr/bin/env python3
"""Run the fixed Real-World v1 benchmark through the existing JEV SDK scorer.

This is an explicitly authorized, one-run benchmark host. It sends each public
query/candidate pair once to TypeSafe, records only scores and safe metadata,
and never enables JEV in the service provider-selection path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import statistics
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import build_real_world_dataset as rw  # noqa: E402
from evidence_layer.metrics import (  # noqa: E402
    average_precision,
    graded_ndcg_at_k,
    mrr_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from evidence_layer.scoring.jev import (  # noqa: E402
    DEFAULT_JEV_API_URL,
    DEFAULT_JEV_MODEL,
    JevBenchmarkRecord,
    JevEvidenceScorer,
    JevScorerError,
    _QUESTION_TEXT,
    _issue_real_world_benchmark_permit,
)
from evidence_layer.scoring.lexical import LexicalScorer  # noqa: E402
from run_real_world_evaluation import K_VALUES, _evaluate, _rank  # noqa: E402

REPORT_PATH = ROOT / "reports" / "phase6_real_world_jev.json"
MANIFEST_PATH = ROOT / "reports" / "phase6_real_world_jev_manifest.json"
LEXICAL_PATH = ROOT / "reports" / "phase6_real_world_lexical.json"
VERIFY_PATH = ROOT / "scripts" / "verify_real_world_benchmark.py"
FREEZE_PATH = ROOT / "data" / "phase6_freeze_manifest.json"
SYNTHETIC_RESULT_PATH = ROOT / "reports" / "phase6_quality_evaluation.json"
EXPECTED_SPLIT_COUNTS = {"dev": 22, "validation": 10, "exposed_test": 8}
EXPECTED_METRIC_NAMES = (
    "average_precision",
    "mrr",
    "precision_at_1",
    "recall_at_5",
    "ndcg_at_3",
    "ndcg_at_5",
    "graded_ndcg_at_5",
)
PROMPT_VERSION = "jev-real-world-relevance-noul-v1"
QUESTION_ID = "candidate_0"
TIMEOUT_SECONDS = 60.0


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _verify_local_inputs() -> tuple[dict[str, list[dict]], dict[str, str], dict]:
    result = subprocess.run(
        [sys.executable, str(VERIFY_PATH), "--verify"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Real-World v1 deterministic verification failed")

    dev, validation, exposed_test = rw.build_real_world_cases()
    cases_by_split = {
        "dev": dev,
        "validation": validation,
        "exposed_test": exposed_test,
    }
    if {name: len(cases) for name, cases in cases_by_split.items()} != EXPECTED_SPLIT_COUNTS:
        raise RuntimeError("Real-World v1 case counts do not match the frozen set")

    lexical_report = json.loads(LEXICAL_PATH.read_text(encoding="utf-8"))
    scorer = LexicalScorer()
    fresh = {
        "dev": _evaluate(dev, scorer),
        "validation": _evaluate(validation, scorer),
        "exposed_test": _evaluate(exposed_test, scorer),
        "overall": _evaluate(dev + validation + exposed_test, scorer),
    }
    for split, evaluation in fresh.items():
        if evaluation["lexical_summary"] != lexical_report[split]["lexical_summary"]:
            raise RuntimeError("Recomputed lexical baseline differs from the frozen report")

    verification_manifest = json.loads(
        (ROOT / "data/real_world/verification_manifest.json").read_text(encoding="utf-8")
    )
    freeze_manifest = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    expected_hashes = {
        **verification_manifest["input_sha256"],
        **freeze_manifest["input_sha256"],
        **freeze_manifest["generated_sha256"],
    }
    input_paths = set(expected_hashes)
    input_paths.update(
        {
            "data/real_world/verification_manifest.json",
            "data/phase6_freeze_manifest.json",
            "reports/phase6_quality_evaluation.json",
            "reports/phase6_real_world_lexical.json",
            "scripts/run_real_world_evaluation.py",
            "scripts/run_real_world_jev_evaluation.py",
            "src/evidence_layer/scoring/jev.py",
            "pyproject.toml",
            "poetry.lock",
        }
    )
    input_hashes = {
        relative: _file_sha256(ROOT / relative) for relative in sorted(input_paths)
    }
    if any(input_hashes[path] != digest for path, digest in expected_hashes.items()):
        raise RuntimeError("A Real-World v1 or frozen synthetic input hash differs")
    generated_cases_sha256 = _sha256(_canonical_bytes(cases_by_split))
    lexical_fresh = {
        split: value["lexical_summary"] for split, value in fresh.items()
    }
    return cases_by_split, input_hashes, {
        "generated_cases_sha256": generated_cases_sha256,
        "lexical_report": lexical_report,
        "lexical_recomputed": lexical_fresh,
        "freeze_manifest_sha256": _file_sha256(FREEZE_PATH),
        "synthetic_result_sha256": _file_sha256(SYNTHETIC_RESULT_PATH),
    }


def _metric_values(case: dict, ranked: list[str]) -> dict[str, float]:
    grades = case["relevance_scores"]
    gold = case["gold_candidate_ids"]
    values = {
        "average_precision": average_precision(gold, ranked),
        "mrr": mrr_at_k(gold, ranked),
        "precision_at_1": precision_at_k(gold, ranked[:1]),
        "recall_at_5": recall_at_k(gold, ranked[:5]),
        "ndcg_at_3": ndcg_at_k(gold, ranked, 3),
        "ndcg_at_5": ndcg_at_k(gold, ranked, 5),
        "graded_ndcg_at_5": graded_ndcg_at_k(
            {candidate_id: float(grade) for candidate_id, grade in grades.items()},
            ranked,
            5,
        ),
    }
    return values


def _mean(values: Iterable[float]) -> float | None:
    sequence = list(values)
    return sum(sequence) / len(sequence) if sequence else None


def _summarize_jev_split(
    cases: list[dict], records: list[dict], *, split: str
) -> dict:
    record_map = {
        (record["case_id"], record["candidate_id"]): record for record in records
    }
    complete = all(
        all(
            record_map.get((case["case_id"], candidate["candidate_id"]), {}).get(
                "score"
            )
            is not None
            for candidate in case["candidates"]
        )
        for case in cases
    )
    per_case = []
    for case in cases:
        case_scores = {
            candidate["candidate_id"]: record_map.get(
                (case["case_id"], candidate["candidate_id"]), {}
            ).get("score")
            for candidate in case["candidates"]
        }
        complete_case = all(score is not None for score in case_scores.values())
        ranked = (
            _rank({key: float(value) for key, value in case_scores.items()})
            if complete_case
            else None
        )
        per_case.append(
            {
                "case_id": case["case_id"],
                "case_type": case["case_type"],
                "complete": complete_case,
                "ranked_candidate_ids": ranked,
                # Do not publish subset-only metrics when any case in this split failed.
                "metrics": _metric_values(case, ranked) if complete else None,
            }
        )
    summary = (
        {
            name: _mean(row["metrics"][name] for row in per_case)
            for name in EXPECTED_METRIC_NAMES
        }
        if complete
        else {name: None for name in EXPECTED_METRIC_NAMES}
    )
    return {
        "cases": len(cases),
        "complete": complete,
        "case_metrics_scope": "all cases in split" if complete else None,
        "jev_summary": summary,
        "per_case": per_case,
    }


def _compare_summaries(jev: dict, lexical: dict) -> dict:
    comparison = {}
    for name in EXPECTED_METRIC_NAMES:
        jev_value = jev.get(name)
        lexical_value = lexical.get(name)
        comparison[name] = {
            "jev": jev_value,
            "lexical": lexical_value,
            "delta_jev_minus_lexical": (
                jev_value - lexical_value
                if jev_value is not None and lexical_value is not None
                else None
            ),
        }
    return comparison


def build_comparison_report(
    cases_by_split: dict[str, list[dict]],
    lexical_report: dict,
    candidate_outputs: list[dict],
) -> dict:
    by_split_outputs = {
        split: [row for row in candidate_outputs if row["split"] == split]
        for split in EXPECTED_SPLIT_COUNTS
    }
    by_split_outputs["overall"] = candidate_outputs
    all_cases = {
        "dev": cases_by_split["dev"],
        "validation": cases_by_split["validation"],
        "exposed_test": cases_by_split["exposed_test"],
        "overall": (
            cases_by_split["dev"]
            + cases_by_split["validation"]
            + cases_by_split["exposed_test"]
        ),
    }
    splits = {}
    for split, cases in all_cases.items():
        jev = _summarize_jev_split(cases, by_split_outputs[split], split=split)
        lexical = lexical_report[split]["lexical_summary"]
        splits[split] = {
            **jev,
            "lexical_summary": lexical,
            "comparison": _compare_summaries(jev["jev_summary"], lexical),
        }
    return {
        "schema_version": 1,
        "benchmark": "phase6-real-world-v1",
        "result_classification": "exploratory; not independent blind evidence",
        "judgment_status": "developer-authored; independent blind review pending",
        "held_out_status": (
            "exposed; the current exposed_test split is not a blind test for JEV"
        ),
        "data_status": "fixed Real-World v1 inputs; no cases, candidates, or grades changed",
        "splits": splits,
        "candidate_outputs": candidate_outputs,
    }


def _record_to_output(
    record: JevBenchmarkRecord | None,
    *,
    split: str,
    case: dict,
    candidate: dict,
    query: str,
    score: float | None,
) -> dict:
    return {
        "split": split,
        "case_id": case["case_id"],
        "candidate_id": candidate["candidate_id"],
        "query_sha256": _sha256(query.encode("utf-8")),
        "candidate_text_sha256": _sha256(candidate["text"].encode("utf-8")),
        "score": score,
        "model_requested": record.model_requested if record else DEFAULT_JEV_MODEL,
        "model_returned": record.model_returned if record else None,
        "sdk_version": record.sdk_version if record else None,
        "input_tokens": record.input_tokens if record else None,
        "output_tokens": record.output_tokens if record else None,
        "latency_ms": record.latency_ms if record else None,
        "retry_count": record.retry_count if record else None,
        "failure_category": record.failure_category if record else "provider_failure",
    }


def _run_calls(
    cases_by_split: dict[str, list[dict]],
    permit,
    client,
    noul_factory,
    sdk_retry_policy,
):
    records = []
    total_calls = sum(
        len(case["candidates"])
        for cases in cases_by_split.values()
        for case in cases
    )
    completed = 0
    started = time.monotonic()
    for split, cases in cases_by_split.items():
        for case in cases:
            query = case["task"]["input"]
            for candidate in case["candidates"]:
                scorer = JevEvidenceScorer(
                    client=client,
                    noul_factory=noul_factory,
                    model=DEFAULT_JEV_MODEL,
                    timeout=TIMEOUT_SECONDS,
                    split=split,
                    case_id=case["case_id"],
                    candidate_id=candidate["candidate_id"],
                    transfer_permit=permit,
                    sdk_retry_policy=sdk_retry_policy,
                )
                try:
                    score = scorer.score(query, candidate["text"])
                except JevScorerError:
                    score = None
                record = scorer.last_record
                records.append(
                    _record_to_output(
                        record,
                        split=split,
                        case=case,
                        candidate=candidate,
                        query=query,
                        score=score,
                    )
                )
                completed += 1
                if completed % 10 == 0 or completed == total_calls:
                    print(f"JEV benchmark progress: {completed}/{total_calls} candidates")
    return records, time.monotonic() - started


def _latency_summary(records: list[dict]) -> dict:
    values = sorted(
        row["latency_ms"] for row in records if row["latency_ms"] is not None
    )
    if not values:
        return {"count": 0, "mean_ms": None, "median_ms": None, "p95_ms": None}
    p95_index = min(len(values) - 1, int(0.95 * len(values)))
    return {
        "count": len(values),
        "mean_ms": statistics.fmean(values),
        "median_ms": statistics.median(values),
        "p95_ms": values[p95_index],
    }


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-authorized",
        action="store_true",
        help="confirm this invocation may send the fixed public benchmark to TypeSafe",
    )
    args = parser.parse_args()
    if not args.run_authorized:
        parser.error("--run-authorized is required for external JEV requests")
    if not os.environ.get("TYPESAFE_API_KEY"):
        parser.error("TYPESAFE_API_KEY is not available in this environment")
    if REPORT_PATH.exists() or MANIFEST_PATH.exists():
        parser.error("result files already exist; refusing to overwrite evidence")

    cases_by_split, input_hashes, preflight = _verify_local_inputs()
    run_id = str(uuid.uuid4())
    prompt_text = f"{_QUESTION_TEXT} Candidate identifier: {QUESTION_ID}."
    prompt_sha256 = _sha256(prompt_text.encode("utf-8"))
    scope_payload = {
        "run_id": run_id,
        "input_sha256": input_hashes,
        "generated_cases_sha256": preflight["generated_cases_sha256"],
        "model": DEFAULT_JEV_MODEL,
        "prompt_sha256": prompt_sha256,
        "sdk_version": "0.7.2",
        "request_limit": 200,
    }
    scope_sha256 = _sha256(_canonical_bytes(scope_payload))
    permit = _issue_real_world_benchmark_permit(
        cases_by_split,
        run_id=run_id,
        input_sha256=scope_sha256,
    )

    # Prevent the SDK's optional wire logging from writing query/candidate bodies.
    sdk_logger = logging.getLogger("typesafe_sdk")
    sdk_logger.disabled = True
    sdk_logger.propagate = False

    import httpx2
    from typesafe_sdk import Noul, RetryPolicy as SDKRetryPolicy, TypeSafeClient
    from importlib.metadata import version

    try:
        sdk_version = version("typesafe-sdk")
    except Exception:
        parser.error("typesafe-sdk version metadata is unavailable")
    if sdk_version != "0.7.2":
        parser.error("the installed typesafe-sdk version differs from the pinned SDK")

    transport_counts: dict[str, object] = {
        "request_events": 0,
        "response_statuses": [],
    }

    def on_request(_request) -> None:
        transport_counts["request_events"] = int(transport_counts["request_events"]) + 1

    def on_response(response) -> None:
        statuses = transport_counts["response_statuses"]
        assert isinstance(statuses, list)
        statuses.append(int(response.status_code))

    http_client = httpx2.Client(
        timeout=TIMEOUT_SECONDS,
        event_hooks={"request": [on_request], "response": [on_response]},
    )
    client = TypeSafeClient(
        base_url=DEFAULT_JEV_API_URL,
        model=DEFAULT_JEV_MODEL,
        timeout=TIMEOUT_SECONDS,
        retry=SDKRetryPolicy(max_retries=0),
        http_client=http_client,
    )
    run_started_at = datetime.now(timezone.utc)
    print(
        "JEV run started: 40 cases, 200 candidates, official TypeSafe endpoint; "
        "request and response bodies are not logged."
    )
    try:
        candidate_outputs, wall_seconds = _run_calls(
            cases_by_split,
            permit,
            client,
            lambda instructions: Noul(instructions=instructions),
            SDKRetryPolicy(max_retries=0),
        )
    finally:
        client.close()

    end_time = datetime.now(timezone.utc)
    postrun_hashes = {
        relative: _file_sha256(ROOT / relative) for relative in sorted(input_hashes)
    }
    if postrun_hashes != input_hashes:
        raise RuntimeError("An input artifact changed during the JEV evaluation")
    outputs_sha256 = _sha256(_canonical_bytes(candidate_outputs))
    comparison = build_comparison_report(
        cases_by_split,
        preflight["lexical_report"],
        candidate_outputs,
    )
    status_codes = transport_counts["response_statuses"]
    assert isinstance(status_codes, list)
    requests = int(transport_counts["request_events"])
    successes = sum(row["score"] is not None for row in candidate_outputs)
    errors = len(candidate_outputs) - successes
    input_counts = [row["input_tokens"] for row in candidate_outputs]
    output_counts = [row["output_tokens"] for row in candidate_outputs]
    models_returned = sorted(
        {row["model_returned"] for row in candidate_outputs if row["model_returned"]}
    )
    result = {
        **comparison,
        "run": {
            "run_id": run_id,
            "started_at_utc": run_started_at.isoformat(),
            "finished_at_utc": end_time.isoformat(),
            "run_scope_sha256": scope_sha256,
            "input_sha256": input_hashes,
            "generated_cases_sha256": preflight["generated_cases_sha256"],
            "synthetic_freeze_manifest_sha256": preflight["freeze_manifest_sha256"],
            "synthetic_result_sha256": preflight["synthetic_result_sha256"],
            "synthetic_artifacts_touched": False,
            "inputs_unchanged_after_run": postrun_hashes == input_hashes,
            "model_requested": DEFAULT_JEV_MODEL,
            "models_returned": models_returned,
            "sdk_version": sdk_version,
            "endpoint": f"{DEFAULT_JEV_API_URL}/v1/systemone",
            "authentication": "HTTP Bearer token from TYPESAFE_API_KEY; value not logged or stored",
            "prompt_version": PROMPT_VERSION,
            "prompt_sha256": prompt_sha256,
            "prompt_template": prompt_text,
            "ranking": "score descending, candidate_id ascending",
            "score_primitive": "one Noul relevance score per candidate, range 0..1",
            "request_settings": {
                "timeout_seconds": TIMEOUT_SECONDS,
                "sdk_retries": 0,
                "concurrency": 1,
                "temperature": "not set by the Typesafe SDK System One request",
                "model_alias_note": "jev-latest is an alias and may resolve to a changing model",
            },
            "counts": {
                "expected_cases": 40,
                "expected_candidates": 200,
                "sdk_calls": len(candidate_outputs),
                "http_request_events": requests,
                "http_responses": len(status_codes),
                "http_status_counts": {
                    str(status): status_codes.count(status)
                    for status in sorted(set(status_codes))
                },
                "successful_scores": successes,
                "failed_scores": errors,
                "unused_authorized_calls": permit.remaining_calls,
            },
            "latency": {
                **_latency_summary(candidate_outputs),
                "total_wall_seconds": wall_seconds,
            },
            "usage": {
                "input_tokens_reported": sum(v for v in input_counts if v is not None),
                "input_token_records_missing": sum(v is None for v in input_counts),
                "output_tokens_reported": sum(v for v in output_counts if v is not None),
                "output_token_records_missing": sum(v is None for v in output_counts),
                "financial_cost_usd": None,
                "financial_cost_status": "not reported by the System One response; no invoice/billing API queried",
            },
            "outputs_sha256": outputs_sha256,
            "privacy": "Only scores, ids, content hashes, timings, token counts, model ids, and error categories are saved; no API key or request/response body is stored.",
            "limitations": [
                "All current relevance judgments are developer-authored; independent blind adjudication has not been performed.",
                "The exposed_test split has been disclosed previously and is not a blind test for future JEV comparison.",
                "Results are exploratory and do not establish scientific superiority or generalization.",
                "The model alias may change; response model ids are recorded when available.",
                "The API response reports token usage but not financial charge; actual USD cost is unavailable here.",
            ],
        },
    }
    report_bytes = json.dumps(result, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    manifest = {
        "schema_version": 1,
        "benchmark": "phase6-real-world-v1",
        "manifest_kind": "live JEV exploratory run; independent from synthetic freeze manifest",
        "run_id": run_id,
        "run_scope_sha256": scope_sha256,
        "results_path": str(REPORT_PATH.relative_to(ROOT)),
        "results_sha256": _sha256(report_bytes),
        "raw_score_outputs_sha256": outputs_sha256,
        "input_sha256": input_hashes,
        "generated_cases_sha256": preflight["generated_cases_sha256"],
        "synthetic_freeze_manifest_sha256": preflight["freeze_manifest_sha256"],
        "synthetic_result_sha256": preflight["synthetic_result_sha256"],
    }
    _atomic_write(REPORT_PATH, report_bytes)
    _atomic_write(
        MANIFEST_PATH,
        json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8") + b"\n",
    )
    print(
        f"JEV run finished: http_requests={requests}, successful_scores={successes}, "
        f"errors={errors}; report={REPORT_PATH.relative_to(ROOT)}"
    )
    return 0 if errors == 0 and requests == 200 and permit.remaining_calls == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
