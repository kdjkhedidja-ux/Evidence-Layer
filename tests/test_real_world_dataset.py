"""Invariants for the Phase 6 real-world benchmark v1."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import build_dataset  # noqa: E402
import build_extension_dataset  # noqa: E402
import build_real_world_dataset as rw  # noqa: E402
from evidence_layer.schemas import EvidenceCandidate, Task  # noqa: E402


@pytest.fixture(scope="module")
def rw_cases():
    return rw.build_real_world_cases()


def test_real_world_size_and_three_way_split(rw_cases):
    dev, validation, test = rw_cases
    assert len(dev) + len(validation) + len(test) == 40
    assert len(validation) >= 8 and len(test) >= 8
    ids = [{c["case_id"] for c in part} for part in (dev, validation, test)]
    assert ids[0].isdisjoint(ids[1]) and ids[0].isdisjoint(ids[2]) and ids[1].isdisjoint(ids[2])


def test_real_world_schema_and_judgments_valid(rw_cases):
    dev, validation, test = rw_cases
    for raw in dev + validation + test:
        task = Task(**raw["task"])
        candidates = [EvidenceCandidate(**candidate) for candidate in raw["candidates"]]
        assert task.input.strip()
        assert raw["required_facts"]
        candidate_ids = {candidate.candidate_id for candidate in candidates}
        assert set(raw["gold_candidate_ids"]) <= candidate_ids
        assert len(candidates) == 5
        # Every candidate carries an explicit grade; golds are grade 2.
        assert set(raw["relevance_scores"]) == candidate_ids
        assert all(raw["relevance_scores"][g] == 2 for g in raw["gold_candidate_ids"])
        assert any(grade == 0 for grade in raw["relevance_scores"].values())


def test_real_world_case_type_coverage(rw_cases):
    dev, validation, test = rw_cases
    counts = Counter(c["case_type"] for c in dev + validation + test)
    assert counts == {
        "direct_factual": 12,
        "multi_source": 10,
        "ambiguous": 6,
        "near_answer": 6,
        "coverage": 6,
    }


def test_real_world_golds_disjoint_from_other_benchmarks(rw_cases):
    dev, validation, test = rw_cases
    rw_golds = {g for c in dev + validation + test for g in c["gold_candidate_ids"]}
    base_dev, base_test = build_dataset.build_cases()
    base_golds = {g for c in base_dev + base_test for g in c["gold_candidate_ids"]}
    ext_dev, ext_test = build_extension_dataset.build_extension_cases()
    ext_golds = {g for c in ext_dev + ext_test for g in c["gold_candidate_ids"]}
    assert rw_golds.isdisjoint(base_golds)
    assert rw_golds.isdisjoint(ext_golds)


def test_real_world_splits_keep_golds_disjoint(rw_cases):
    dev, validation, test = rw_cases
    golds = [
        {g for c in part for g in c["gold_candidate_ids"]} for part in (dev, validation, test)
    ]
    assert golds[0].isdisjoint(golds[1])
    assert golds[0].isdisjoint(golds[2])
    assert golds[1].isdisjoint(golds[2])


def test_real_world_held_out_membership_is_pinned(rw_cases):
    _, _, test = rw_cases
    assert sorted(c["case_id"] for c in test) == [
        "rw_0007",
        "rw_0011",
        "rw_0016",
        "rw_0025",
        "rw_0028",
        "rw_0029",
        "rw_0031",
        "rw_0036",
    ]


def test_real_world_uses_three_real_publishers(rw_cases):
    dev, validation, test = rw_cases
    publishers = {
        candidate["source_id"]
        for case in dev + validation + test
        for candidate in case["candidates"]
    }
    assert publishers == {
        "ietf-rfc/7515",
        "ietf-rfc/7519",
        "supabase/docs",
        "postgresql-docs/rls",
    }
    for case in dev + validation + test:
        for candidate in case["candidates"]:
            assert ".example" not in candidate["source_url"]


def test_real_world_sources_and_manifest_documented():
    sources = (ROOT / "data" / "real_world" / "sources.json")
    manifest = (ROOT / "data" / "real_world" / "manifest.json")
    notice = (ROOT / "data" / "real_world" / "NOTICE.md")
    for path in (sources, manifest, notice):
        assert path.exists(), path
    import json

    src = json.loads(sources.read_text())
    assert {s["source_id"] for s in src["sources"]} == {
        "ietf-rfc/7515",
        "ietf-rfc/7519",
        "supabase/docs",
        "postgresql-docs/rls",
    }
    assert all(s.get("license") and s.get("url") for s in src["sources"])
    man = json.loads(manifest.read_text())
    assert man["split"]["seed"] == rw.REAL_WORLD_SPLIT_SEED
    assert man["counts"]["passages"] == 51
    assert man["counts"]["cases"] == 40
    assert "developer-authored" in man["judgment_protocol"]
    assert "have not been independently reviewed" in man["judgment_protocol"]
    assert "EXPOSED" in man["held_out_status"]
    assert "blind test" in man["usage"]


def test_every_passage_has_quote_type_section_source_and_license():
    import json

    passage_path = ROOT / "data" / "real_world" / "passages.jsonl"
    sources = json.loads((ROOT / "data" / "real_world" / "sources.json").read_text())
    source_map = {row["source_id"]: row for row in sources["sources"]}
    source_ids = set(source_map)
    passages = [json.loads(line) for line in passage_path.read_text().splitlines()]

    assert len(passages) == 51
    assert all(row["source_id"] in source_ids for row in passages)
    assert all(row["source_url"] and row["section"] for row in passages)
    assert all(row["license"] and row["license_reference"] for row in passages)
    assert all(
        row["license"] == source_map[row["source_id"]]["license"]
        and row["license_reference"] == source_map[row["source_id"]]["license_reference"]
        for row in passages
    )
    assert {row["quote_status"] for row in passages} == {"verbatim", "paraphrase"}
    assert Counter(row["quote_status"] for row in passages) == {
        "verbatim": 15,
        "paraphrase": 36,
    }
    assert all(
        row.get("quote_note")
        for row in passages
        if row["quote_status"] == "paraphrase"
    )
    assert all("quote_note" not in row for row in passages if row["quote_status"] == "verbatim")


def test_rfc_section_and_base64url_source_are_correct():
    import json

    passages = {
        row["passage_id"]: row
        for row in map(
            json.loads,
            (ROOT / "data" / "real_world" / "passages.jsonl").read_text().splitlines(),
        )
    }
    assert passages["jwt_structure"]["section"] == "3. JSON Web Token (JWT) Overview"
    assert passages["jwt_base64url"]["source_id"] == "ietf-rfc/7515"
    assert passages["jwt_base64url"]["section"] == "2. Terminology"
    assert "lexically last" in passages["jwt_claims_duplicate"]["text"]
    duplicate_case = next(
        case for split in rw.build_real_world_cases() for case in split
        if case["task"]["input"] == "Can a JWT legally contain the same claim name twice?"
    )
    assert "reject duplicates or use only the lexically last duplicate" in duplicate_case[
        "required_facts"
    ]


def test_review_status_does_not_call_developer_judgments_independent():
    import json

    manifest = json.loads((ROOT / "data" / "real_world" / "manifest.json").read_text())
    assert "developer-authored" in manifest["judgment_protocol"]
    assert "have not been independently reviewed" in manifest["judgment_protocol"]
    assert "independent blind review has not occurred" in json.loads(
        (ROOT / "data" / "real_world" / "sources.json").read_text()
    )["judgment_protocol"]["review_status"].lower()


def test_verification_manifest_is_independent_from_synthetic_freeze():
    import hashlib
    import json

    real_world = ROOT / "data" / "real_world" / "verification_manifest.json"
    synthetic_freeze = ROOT / "data" / "phase6_freeze_manifest.json"
    assert real_world.exists()
    assert hashlib.sha256(synthetic_freeze.read_bytes()).hexdigest() == (
        "0082546fed1b623a89ab032fd39b5d9aca413817867d34690a1babaa51244f31"
    )
    manifest = json.loads(real_world.read_text())
    assert manifest["manifest_kind"] == "independent deterministic verification"
    assert manifest["evaluation_protocol"]["held_out_status"].startswith("exposed")
    assert manifest["verified_content"]["passages"] == 51
    assert manifest["verified_content"]["quote_status_counts"] == {
        "verbatim": 15,
        "paraphrase": 36,
    }


def test_real_world_deterministic(rw_cases):
    assert rw_cases == rw.build_real_world_cases()
