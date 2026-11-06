import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from run_phase6_offline_arms import build_run, write_run


def test_both_offline_arms_have_every_case_without_jev() -> None:
    manifest, usage, results = build_run("test-run")
    assert manifest["network"] == "none"
    assert manifest["jev"] == "not_run"
    assert manifest["answer_model"] is None
    assert {row["arm_id"] for row in results["results"]} == {"lexical_rag", "evidence_layer"}
    assert len(results["results"]) == 80
    assert len(usage) == 80
    assert all(row["provider_id"] == "local" for row in usage)
    assert all(row["cost_usd"] is None for row in usage)
    assert all(row["cost_status"] == "unknown" for row in usage)
    assert all(row["missing_cost_reason"] == "pricing_missing" for row in usage)


def test_same_run_is_byte_reproducible(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_run(first, "stable-run")
    write_run(second, "stable-run")
    assert (first / "results.json").read_bytes() == (second / "results.json").read_bytes()
    assert (first / "usage_events.jsonl").read_bytes() == (second / "usage_events.jsonl").read_bytes()
    first_manifest = json.loads((first / "manifest.json").read_text())
    second_manifest = json.loads((second / "manifest.json").read_text())
    assert first_manifest == second_manifest
