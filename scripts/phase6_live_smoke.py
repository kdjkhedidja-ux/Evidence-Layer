#!/usr/bin/env python3
"""Opt-in live smoke for Phase 6 roster v1. Refuses to run without PHASE6_LIVE_SDK=1.

Sends one non-sensitive synthetic prompt per selected candidate and prints one
JSON line per candidate with statuses, counts, and the answer hash. The answer
text is never printed or stored. Provider keys come only from the environment.

Usage (only when authorized):
  PHASE6_LIVE_SDK=1 OPENAI_API_KEY=... ANTHROPIC_API_KEY=... DEEPSEEK_API_KEY=... \
    python scripts/phase6_live_smoke.py [--model MODEL ...]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from evidence_layer import tokenization  # noqa: E402
from evidence_layer.output_policy import OUTPUT_POLICIES  # noqa: E402
from evidence_layer.provider_execution import ProviderJob, run_provider_job  # noqa: E402
from evidence_layer.provider_sdk import ROSTER_PROVIDER, LiveProviderClient, live_enabled  # noqa: E402

SYNTHETIC_PROMPT = "In one short sentence, state a fact about the number seven. No personal data."


def main() -> int:
    parser = argparse.ArgumentParser(description="Opt-in Phase 6 live smoke (roster v1)")
    parser.add_argument("--model", action="append", choices=sorted(ROSTER_PROVIDER))
    args = parser.parse_args()
    if not live_enabled():
        print("refused: set PHASE6_LIVE_SDK=1 to make authorized live calls", file=sys.stderr)
        return 2
    models = args.model or sorted(ROSTER_PROVIDER)
    shared_counter, shared_id, _ = tokenization.shared_visible_counter()
    failures = 0
    for model in models:
        try:
            client = LiveProviderClient.from_environment(model)
        except RuntimeError as exc:
            print(json.dumps({"model": model, "status": "config_error", "detail": str(exc)}))
            failures += 1
            continue
        native = tokenization.native_counter(model)
        job = ProviderJob(
            job_ordinal=0, run_id="phase6-live-smoke", case_id="smoke-1", arm_id="smoke",
            provider_id=ROSTER_PROVIDER[model], requested_model_id=model, pricing=None,
            client=client, usage_source="provider_reported",
            visible_token_counter=shared_counter, visible_output_tokenizer_id=shared_id,
            output_policy=OUTPUT_POLICIES[model], prompt=SYNTHETIC_PROMPT,
            native_token_counter=native[0] if native else None,
            native_tokenizer_id=native[1] if native else None,
        )
        attempt = asyncio.run(run_provider_job(job, max_attempts=1))[0]
        if attempt.outcome != "success":
            failures += 1
        print(json.dumps({
            "model": model,
            "outcome": attempt.outcome,
            "safe_error_category": attempt.safe_error_category,
            "requested_model": model,
            "returned_model": attempt.returned_model_id,
            "provider_output_tokens": attempt.usage.provider_output_tokens,
            "reasoning_tokens": attempt.usage.reasoning_tokens,
            "visible_output_tokens": attempt.usage.visible_output_tokens,
            "visible_target_status": attempt.visible_target_status,
            "cap_status": attempt.cap_status,
            "answer_sha256": attempt.answer_sha256,
            "http_status": attempt.http_status,
            "provider_error_code": attempt.provider_error_code,
        }, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
