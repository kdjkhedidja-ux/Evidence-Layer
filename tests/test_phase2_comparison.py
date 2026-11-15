from __future__ import annotations

import json

from scripts.build_phase2_comparison_pack import build_pack
from scripts.reconcile_phase2_comparison import reconcile


def write_cases(path, question="What is the product?"):
    path.write_text(
        json.dumps(
            {
                "case_id": "rw_test",
                "task": {"input": question},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def write_report(path):
    answers = []
    for provider in ("alpha", "beta"):
        answers.append({
            "provider": provider,
            "case_id": "rw_test",
            "task_id": "task_test",
            "route": "evidence_layer",
            "status": "ok",
            "answer": "First answer",
        })
        answers.append({
            "provider": provider,
            "case_id": "rw_test",
            "task_id": "task_test",
            "route": "traditional_rag",
            "status": "ok",
            "answer": "Second answer",
        })
    path.write_text(
        json.dumps({"benchmark": "phase7", "answers": answers}),
        encoding="utf-8",
    )


def test_pack_hides_route_and_provider_labels(tmp_path):
    report = tmp_path / "report.json"
    cases = tmp_path / "cases.jsonl"
    write_report(report)
    write_cases(cases)
    pack, key, summary = build_pack(report_path=report, case_paths=[cases], seed=7)
    assert summary["pairs"] == 2
    assert len(pack["pairs"]) == 2
    serialized = json.dumps(pack)
    assert "evidence_layer" not in serialized
    assert "traditional_rag" not in serialized
    assert "alpha" not in serialized
    assert "beta" not in serialized
    assert all(pair["left"] and pair["right"] for pair in pack["pairs"])
    for pair in pack["pairs"]:
        entry = key["pairs"][pair["pair_token"]]
        assert entry["left_route"] in ("evidence_layer", "traditional_rag")
        assert entry["right_route"] != entry["left_route"]


def test_pack_is_deterministic_for_the_same_seed(tmp_path):
    report = tmp_path / "report.json"
    cases = tmp_path / "cases.jsonl"
    write_report(report)
    write_cases(cases)
    first, first_key, _ = build_pack(report_path=report, case_paths=[cases], seed=11)
    second, second_key, _ = build_pack(report_path=report, case_paths=[cases], seed=11)
    assert first == second
    assert first_key == second_key


def test_reconcile_maps_displayed_votes_and_requires_a_third_for_disagreement():
    key = {
        "pairs": {
            "pair_agree": {"case_id": "c1", "provider": "p", "left_route": "evidence_layer", "right_route": "traditional_rag"},
            "pair_disagree": {"case_id": "c2", "provider": "p", "left_route": "traditional_rag", "right_route": "evidence_layer"},
            "pair_pending": {"case_id": "c3", "provider": "p", "left_route": "evidence_layer", "right_route": "traditional_rag"},
            "pair_tie": {"case_id": "c4", "provider": "p", "left_route": "evidence_layer", "right_route": "traditional_rag"},
        }
    }
    reviewer_a = {
        "pair_agree": "A",
        "pair_disagree": "A",
        "pair_pending": "A",
        "pair_tie": "Tie",
    }
    reviewer_b = {
        "pair_agree": "A",
        "pair_disagree": "B",
        "pair_pending": "B",
        "pair_tie": "Tie",
    }
    third = {"pair_disagree": "B"}
    result = reconcile(key=key, reviewer_a=reviewer_a, reviewer_b=reviewer_b, third=third)
    assert result["summary"]["exact_agreement"] == 2
    assert result["summary"]["resolved"] == 3
    assert result["summary"]["pending"] == 1
    routes = {row["pair_token"]: row["chosen_route"] for row in result["resolved"]}
    assert routes["pair_agree"] == "evidence_layer"
    assert routes["pair_disagree"] == "evidence_layer"
    assert routes["pair_tie"] == "tie"
    assert result["pending"][0]["method"] == "third_required"
    assert result["summary"]["routes"]["tie"] == 1
