import json
import hashlib
import sys

import pytest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_phase6_fake_e2e import write_fake_run  # noqa: E402
from evidence_layer.tokenization import tokenizer_dir  # noqa: E402

_TOKENIZER_ARTIFACTS = tokenizer_dir() / "deepseek_v4_tokenizer" / "tokenizer.json"
pytestmark = pytest.mark.skipif(
    not _TOKENIZER_ARTIFACTS.is_file(),
    reason="run scripts/fetch_phase6_tokenizers.py to install pinned tokenizers",
)
from evidence_layer.usage_measurement import PricingSnapshot, UsageEvent, cost_for_event  # noqa: E402


def _pricing(payload):
    values = dict(payload)
    for key, value in values.items():
        if key.endswith("_per_million") and value is not None:
            values[key] = Decimal(value)
    return PricingSnapshot(**values)


def _cost_json(cost):
    return {
        "amount_usd": str(cost.amount_usd) if cost.amount_usd is not None else None,
        "status": cost.status,
        "pricing_version": cost.pricing_version,
        "missing_reason": cost.missing_reason,
    }


def test_full_fake_pipeline_is_linked_and_byte_reproducible(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_fake_run(first, "phase6-fake-e2e-test")
    write_fake_run(second, "phase6-fake-e2e-test")

    filenames = (
        "manifest.json",
        "evidence_results.json",
        "answer_results.json",
        "usage_events.jsonl",
        "attempts.jsonl",
    )
    for filename in filenames:
        assert (first / filename).read_bytes() == (second / filename).read_bytes()

    manifest = json.loads((first / "manifest.json").read_text())
    evidence = json.loads((first / "evidence_results.json").read_text())
    answers = json.loads((first / "answer_results.json").read_text())
    attempts = [json.loads(line) for line in (first / "attempts.jsonl").read_text().splitlines()]
    usage = [json.loads(line) for line in (first / "usage_events.jsonl").read_text().splitlines()]

    assert manifest["execution"] == "offline-fake-e2e"
    assert manifest["network"] == "none"
    assert manifest["jev"] == "not_run"
    assert manifest["pricing_basis"] == "synthetic-fixtures-not-registry-prices"
    assert len(evidence["results"]) == 80
    assert len(answers) == 240
    assert len(attempts) == 241
    assert len(usage) == 321
    assert manifest["counts"]["cases"] == 40
    assert manifest["counts"]["evidence_results"] == 80
    assert manifest["counts"]["virtual_models"] == 3
    assert manifest["counts"]["answer_rows"] == 240
    assert manifest["counts"]["attempts"] == 241
    assert manifest["counts"]["usage_events"] == 321
    for filename, hash_field in (
        ("evidence_results.json", "evidence_results_sha256"),
        ("answer_results.json", "answer_results_sha256"),
        ("usage_events.jsonl", "usage_events_sha256"),
        ("attempts.jsonl", "attempts_sha256"),
    ):
        assert hashlib.sha256((first / filename).read_bytes()).hexdigest() == manifest[hash_field]

    for artifact, key in (
        ("evidence_results.json", "evidence_results_sha256"),
        ("answer_results.json", "answer_results_sha256"),
        ("usage_events.jsonl", "usage_events_sha256"),
        ("attempts.jsonl", "attempts_sha256"),
    ):
        assert hashlib.sha256((first / artifact).read_bytes()).hexdigest() == manifest[key]

    attempt_ids = {attempt["attempt_id"] for attempt in attempts}
    answer_attempt_ids = {
        attempt_id for row in answers for attempt_id in row["attempt_ids"]
    }
    assert attempt_ids == answer_attempt_ids
    answer_usage_ids = {
        row["attempt_id"] for row in usage
        if row["usage_kind"] == "synthetic_answer_attempt"
    }
    assert answer_usage_ids == attempt_ids
    assert len({attempt["attempt_ordinal"] for attempt in attempts}) == len(attempts)
    assert all(row["quality_status"] == "not_evaluated_fake_answer" for row in answers)
    assert all(attempt["usage"]["token_status"] in {"estimated", "unknown"} for attempt in attempts)
    assert all(
        attempt["usage"]["token_status"] == "estimated"
        for attempt in attempts if attempt["outcome"] == "success"
    )
    assert all(
        attempt["cost"]["status"] != "actual"
        for attempt in attempts
    )
    synthetic_pricing = {
        model: _pricing(pricing)
        for model, pricing in manifest["synthetic_pricing_snapshots"].items()
    }
    for attempt in attempts:
        usage_event = UsageEvent(**attempt["usage"])
        recomputed = cost_for_event(
            usage_event, synthetic_pricing[attempt["requested_model_id"]]
        )
        assert _cost_json(recomputed) == attempt["cost"]
    pricing_by_model = {
        model_id: _pricing(snapshot)
        for model_id, snapshot in manifest["synthetic_pricing_snapshots"].items()
    }
    for attempt in attempts:
        event = UsageEvent(**attempt["usage"])
        recomputed = cost_for_event(event, pricing_by_model[attempt["requested_model_id"]])
        expected_amount = str(recomputed.amount_usd) if recomputed.amount_usd is not None else None
        assert expected_amount == attempt["cost"]["amount_usd"]
        assert recomputed.status == attempt["cost"]["status"]
    assert any(attempt["safe_error_category"] == "timeout" for attempt in attempts)
    assert all(not any(field in attempt for field in ("output_text", "prompt", "evidence")) for attempt in attempts)


def test_output_measurement_fields_follow_documented_provider_schemas(tmp_path: Path) -> None:
    write_fake_run(tmp_path / "run", "phase6-output-schema-test")
    attempts = [
        json.loads(line)
        for line in (tmp_path / "run" / "attempts.jsonl").read_text().splitlines()
    ]
    successes = [a for a in attempts if a["outcome"] == "success"]
    assert successes

    for attempt in successes:
        usage = attempt["usage"]
        provider = attempt["provider_id"]
        # Visible text is present for every provider shape used by the fakes.
        assert usage["visible_output_tokens"] is not None
        expected = "o200k_base-tiktoken-0.14.0"  # shared meter for every provider
        assert usage["visible_output_tokenizer_id"] == expected
        assert attempt["visible_target_status"] in {"within_target", "over_target"}
        assert attempt["cap_status"] == "within_cap"
        assert usage["output_token_measurement_status"] == "synthetic"
        if provider == "anthropic":
            # The reviewed Messages usage schema has no reasoning sub-count: never invented.
            assert usage["reasoning_tokens"] is None
        else:
            assert usage["reasoning_tokens"] is not None
            # Reasoning is a subset of provider output, never additive to it.
            assert 0 <= usage["reasoning_tokens"] <= usage["provider_output_tokens"]
        assert usage["provider_output_tokens"] is not None
        assert attempt["cost"]["status"] != "actual"
