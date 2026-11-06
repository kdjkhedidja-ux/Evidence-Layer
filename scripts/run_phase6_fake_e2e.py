#!/usr/bin/env python3
"""Offline end-to-end benchmark pipeline using deterministic fake clients only."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from evidence_layer.provider_execution import (  # noqa: E402
    ProviderJob,
    ProviderReply,
    ProviderRequestError,
    run_provider_batch,
)
from evidence_layer.output_policy import OUTPUT_POLICIES  # noqa: E402
from evidence_layer.tokenization import (  # noqa: E402
    NATIVE_TOKENIZERS,
    SHARED_VISIBLE_TOKENIZER_ID,
    TOKENIZERS,
    native_counter,
    shared_visible_counter,
)
from evidence_layer.usage_measurement import PricingSnapshot  # noqa: E402
from run_phase6_offline_arms import build_run  # noqa: E402

_SHARED: tuple | None = None
_NATIVE: dict[str, tuple | None] = {}


def _shared() -> tuple:
    """Shared visible meter (o200k_base): the classifier for the 800-token target."""
    global _SHARED
    if _SHARED is None:
        _SHARED = shared_visible_counter()
    return _SHARED


def _native_for(policy_model: str) -> tuple | None:
    """Provider-native telemetry, when the vendor publishes a tokenizer."""
    if policy_model not in _NATIVE:
        _NATIVE[policy_model] = native_counter(policy_model)
    return _NATIVE[policy_model]


MAX_ATTEMPTS = 2
# Virtual models keep fake names. Each borrows the real candidate's output
# policy and tokenizer mapping so the offline run exercises the same counting
# and limit logic that the real candidate would use. No provider is contacted.
FAKE_MODELS = (
    {"provider_id": "anthropic", "model_id": "fake-claude-a", "pricing_version": "synthetic-anthropic-v1", "policy_model": "claude-opus-5-5"},
    {"provider_id": "openai", "model_id": "fake-gpt-a", "pricing_version": "synthetic-openai-v1", "policy_model": "gpt-6.1-sol"},
    {"provider_id": "deepseek", "model_id": "fake-deepseek-a", "pricing_version": "synthetic-deepseek-v1", "policy_model": "deepseek-flash"},
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _jsonable(value):
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _pricing(model: dict) -> PricingSnapshot:
    common = {
        "input_per_million": Decimal("1"),
        "output_per_million": Decimal("2"),
        "cached_input_per_million": Decimal("0.1"),
    }
    if model["provider_id"] == "anthropic":
        return PricingSnapshot(
            model["pricing_version"], **common,
            cache_write_5m_per_million=Decimal("1.25"),
            cache_write_1h_per_million=Decimal("1.5"),
            cache_write_5m_billing="separately_metered",
            cache_write_1h_billing="separately_metered",
            cache_write_30m_billing="not_applicable",
            cache_write_12h_billing="not_applicable",
        )
    if model["provider_id"] == "openai":
        return PricingSnapshot(
            model["pricing_version"], **common,
            cache_write_30m_per_million=Decimal("1.25"),
            cache_write_12h_per_million=Decimal("2.5"),
            cache_write_5m_billing="not_applicable",
            cache_write_1h_billing="not_applicable",
            cache_write_30m_billing="separately_metered",
            cache_write_12h_billing="separately_metered",
        )
    return PricingSnapshot(
        model["pricing_version"], **common,
        cache_write_5m_billing="unknown",
        cache_write_1h_billing="unknown",
        cache_write_30m_billing="unknown",
        cache_write_12h_billing="unknown",
    )


class FakeProviderClient:
    def __init__(self, provider_id: str, model_id: str, *, timeout_first: bool = False) -> None:
        self.provider_id = provider_id
        self.model_id = model_id
        self.timeout_first = timeout_first
        self.calls = 0

    async def generate(self, request: dict) -> ProviderReply:
        self.calls += 1
        if self.timeout_first and self.calls == 1:
            raise ProviderRequestError("timeout")
        key = f"{request['case_id']}|{request['arm_id']}|{self.model_id}".encode()
        seed = int.from_bytes(hashlib.sha256(key).digest()[:4], "big")
        total_input = 96 + seed % 40
        output_tokens = 8 + seed % 13
        # Reasoning is a subset of provider output and only reported where the
        # documented usage schema exposes it (OpenAI, DeepSeek); Anthropic stays null.
        reasoning_tokens = min(output_tokens, seed % 7)
        answer = f"synthetic::{request['case_id']}::{request['arm_id']}::{self.model_id}"

        if self.provider_id == "anthropic":
            read = seed % 5
            write_5m = seed % 4
            write_1h = seed % 3
            creation = write_5m + write_1h
            usage = {
                "input_tokens": total_input - read - creation,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": read,
                "cache_creation_input_tokens": creation,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": write_5m,
                    "ephemeral_1h_input_tokens": write_1h,
                },
            }
        elif self.provider_id == "openai":
            cached = seed % 6
            write_30m = seed % 5
            write_12h = seed % 4
            usage = {
                "input_tokens": total_input,
                "output_tokens": output_tokens,
                "input_tokens_details": {
                    "cached_tokens": cached,
                    "cache_write_tokens": write_30m + write_12h,
                    "cache_write_tokens_details": {
                        "ttl_30m_tokens": write_30m,
                        "ttl_12h_tokens": write_12h,
                    },
                },
                "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
            }
        else:
            hit = seed % 7
            usage = {
                "prompt_tokens": total_input,
                "completion_tokens": output_tokens,
                "prompt_cache_hit_tokens": hit,
                "prompt_cache_miss_tokens": total_input - hit,
                "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
            }

        # Provider-shaped visible text: Anthropic content blocks, DeepSeek
        # choices[].message.content, OpenAI output_text. Reasoning is excluded.
        if self.provider_id == "anthropic":
            body = {"content": [{"type": "text", "text": answer}]}
        elif self.provider_id == "openai":
            body = {"output_text": answer}
        else:
            body = {"choices": [{"message": {"content": answer, "reasoning_content": "synthetic hidden"}}]}
        return ProviderReply({"model": self.model_id, "usage": usage, **body})


def _policy_model_for(requested_model_id: str) -> str:
    for model in FAKE_MODELS:
        if model["model_id"] == requested_model_id:
            return model["policy_model"]
    raise KeyError(requested_model_id)


def _timestamp_factory(job: ProviderJob, attempt_index: int, phase: str) -> str:
    base = datetime(2026, 11, 6, tzinfo=timezone.utc)
    second = job.job_ordinal * MAX_ATTEMPTS * 3 + attempt_index * 3 + (1 if phase == "finish" else 0)
    return (base + timedelta(seconds=second)).isoformat()


async def build_fake_run(run_id: str) -> tuple[dict, dict, list[dict]]:
    evidence_manifest, evidence_usage, evidence_payload = build_run(run_id)
    result_rows = evidence_payload["results"]
    jobs: list[ProviderJob] = []
    ordinal = 0
    retry_slot = None
    for result in result_rows:
        for model in FAKE_MODELS:
            retry_first = retry_slot is None
            client = FakeProviderClient(
                model["provider_id"], model["model_id"], timeout_first=retry_first
            )
            if retry_first:
                retry_slot = ordinal
            jobs.append(ProviderJob(
                job_ordinal=ordinal,
                run_id=run_id,
                case_id=result["case_id"],
                arm_id=result["arm_id"],
                provider_id=model["provider_id"],
                requested_model_id=model["model_id"],
                pricing=_pricing(model),
                client=client,
                usage_source="synthetic",
                visible_token_counter=_shared()[0],
                visible_output_tokenizer_id=_shared()[1],
                native_token_counter=(_native_for(model["policy_model"]) or (None,))[0],
                native_tokenizer_id=(_native_for(model["policy_model"]) or (None, None))[1],
                output_policy=OUTPUT_POLICIES[model["policy_model"]],
            ))
            ordinal += 1

    attempts = await run_provider_batch(
        jobs,
        max_attempts=MAX_ATTEMPTS,
        timestamp_factory=_timestamp_factory,
    )
    grouped_attempts: dict[tuple[str, str, str], list] = {}
    for attempt in attempts:
        key = (attempt.case_id, attempt.arm_id, attempt.requested_model_id)
        grouped_attempts.setdefault(key, []).append(attempt)
    answer_results = []
    for (case_id, arm_id, model_id), job_attempts in grouped_attempts.items():
        terminal = job_attempts[-1]
        answer_results.append({
            "run_id": run_id,
            "case_id": case_id,
            "arm_id": arm_id,
            "requested_model_id": model_id,
            "returned_model_id": terminal.returned_model_id,
            "attempt_ids": [attempt.attempt_id for attempt in job_attempts],
            "attempt_ordinals": [attempt.attempt_ordinal for attempt in job_attempts],
            "outcome": terminal.outcome,
            "quality_status": "not_evaluated_fake_answer",
            "answer_sha256": terminal.answer_sha256,
        })

    attempt_records = [{
        "attempt_id": attempt.attempt_id,
        "attempt_ordinal": attempt.attempt_ordinal,
        "attempt_index": attempt.attempt_index,
        "run_id": attempt.run_id,
        "case_id": attempt.case_id,
        "arm_id": attempt.arm_id,
        "requested_model_id": attempt.requested_model_id,
        "returned_model_id": attempt.returned_model_id,
        "provider_id": attempt.provider_id,
        "outcome": attempt.outcome,
        "safe_error_category": attempt.safe_error_category,
        "usage_parse_status": attempt.usage_parse_status,
        "usage": attempt.usage,
        "cost": attempt.cost,
        "answer_sha256": attempt.answer_sha256,
        "visible_target_status": attempt.visible_target_status,
        "cap_status": attempt.cap_status,
        "visible_tokenizer_role": "shared_meter",
        "visible_tokenizer_id": _shared()[1],
        "visible_tokenizer_sha256": _shared()[2],
        "native_tokenizer_id": (_native_for(_policy_model_for(attempt.requested_model_id)) or (None, None))[1],
    } for attempt in attempts]
    usage_records = [
        {"usage_kind": "offline_evidence", **row}
        for row in evidence_usage
    ] + [
        {"usage_kind": "synthetic_answer_attempt", **asdict(attempt.usage)}
        for attempt in attempts
    ]

    evidence_bytes = (json.dumps(evidence_payload, sort_keys=True, indent=2) + "\n").encode()
    answers_bytes = (json.dumps(answer_results, sort_keys=True, indent=2) + "\n").encode()
    usage_bytes = ("\n".join(json.dumps(_jsonable(row), sort_keys=True) for row in usage_records) + "\n").encode()
    attempts_bytes = ("\n".join(json.dumps(_jsonable(row), sort_keys=True) for row in attempt_records) + "\n").encode()

    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "benchmark": "phase6-real-world-v1",
        "execution": "offline-fake-e2e",
        "git_commit_sha": evidence_manifest["git_commit_sha"],
        "dataset_fingerprint": evidence_manifest["dataset_fingerprint"],
        "evidence_configuration": evidence_manifest["configuration"],
        "evidence_configuration_fingerprint": evidence_manifest["configuration_fingerprint"],
        "virtual_models": list(FAKE_MODELS),
        "output_policies": {
            model["policy_model"]: _jsonable(asdict(OUTPUT_POLICIES[model["policy_model"]]))
            for model in FAKE_MODELS
        },
        "shared_visible_tokenizer": {
            "tokenizer_id": SHARED_VISIBLE_TOKENIZER_ID,
            "role": "shared_meter",
            "artifact_sha256": TOKENIZERS[SHARED_VISIBLE_TOKENIZER_ID].artifact_sha256,
            "library_version": TOKENIZERS[SHARED_VISIBLE_TOKENIZER_ID].library_version,
        },
        "native_tokenizers": {
            model["policy_model"]: {
                "tokenizer_id": NATIVE_TOKENIZERS[model["policy_model"]],
                "role": "additional_telemetry",
                "artifact_sha256": TOKENIZERS[NATIVE_TOKENIZERS[model["policy_model"]]].artifact_sha256,
                "library_version": TOKENIZERS[NATIVE_TOKENIZERS[model["policy_model"]]].library_version,
            }
            for model in FAKE_MODELS if model["policy_model"] in NATIVE_TOKENIZERS
        },
        "pricing_basis": "synthetic-fixtures-not-registry-prices",
        "synthetic_pricing_snapshots": {
            model["model_id"]: _jsonable(asdict(_pricing(model)))
            for model in FAKE_MODELS
        },
        "network": "none",
        "jev": "not_run",
        "production": False,
        "external_answer_evaluation": False,
        "counts": {
            "cases": len({row["case_id"] for row in result_rows}),
            "evidence_results": len(result_rows),
            "virtual_models": len(FAKE_MODELS),
            "answer_rows": len(answer_results),
            "attempts": len(attempts),
            "usage_events": len(usage_records),
        },
        "evidence_results_sha256": _sha256(evidence_bytes),
        "answer_results_sha256": _sha256(answers_bytes),
        "usage_events_sha256": _sha256(usage_bytes),
        "attempts_sha256": _sha256(attempts_bytes),
    }
    files = {
        "evidence_results.json": evidence_bytes,
        "answer_results.json": answers_bytes,
        "usage_events.jsonl": usage_bytes,
        "attempts.jsonl": attempts_bytes,
    }
    return manifest, files, answer_results


def _jsonable(value):
    if hasattr(value, "__dataclass_fields__"):
        return _jsonable(asdict(value))
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def write_fake_run(output_dir: Path, run_id: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest, files, _ = asyncio.run(build_fake_run(run_id))
    for name, payload in files.items():
        (output_dir / name).write_bytes(payload)
    (output_dir / "manifest.json").write_bytes(
        (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase 6 end-to-end with fake clients only")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", default="phase6-fake-e2e-v1")
    args = parser.parse_args()
    write_fake_run(args.output_dir, args.run_id)
    print(f"wrote fake-client E2E run to {args.output_dir}")


if __name__ == "__main__":
    main()
