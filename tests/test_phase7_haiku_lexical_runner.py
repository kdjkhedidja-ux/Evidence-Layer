"""Offline checks that the Haiku lexical-only arm never reads JEV scores."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_phase7_haiku_lexical_e2e as runner
from evidence_layer.scoring.lexical import LexicalScorer


def _case() -> dict:
    texts = {
        "c1": "alpha beta gamma delta",
        "c2": "alpha beta epsilon",
        "c3": "zeta eta theta",
        "c4": "iota kappa lambda",
        "c5": "mu nu xi omicron",
    }
    return {
        "case_id": "case-1",
        "task": {"input": "alpha beta"},
        "candidates": [
            {
                "candidate_id": cid,
                "text": text,
                "source_id": f"src-{cid}",
                "source_url": f"https://example.test/{cid}",
                "metadata": {"section": "s"},
            }
            for cid, text in texts.items()
        ],
        "relevance_scores": {cid: 1 for cid in texts},
    }


def test_arms_use_lexical_top_five_and_top_three_without_jev() -> None:
    rows, traditional, evidence_layer, latency = runner._rows(_case(), LexicalScorer())
    assert len(rows) == 5
    assert len(traditional) == 5
    assert len(evidence_layer) == 3
    assert [row["candidate_id"] for row in traditional] == [row["candidate_id"] for row in rows]
    assert [row["candidate_id"] for row in evidence_layer] == [row["candidate_id"] for row in rows[:3]]
    assert all(row["jev_score"] is None for row in rows)
    assert latency >= 0


def test_runner_has_no_jev_client_or_transfer_dependency() -> None:
    source = Path(runner.__file__).read_text().lower()
    for forbidden in ("typesafe", "jev_records", "accept_transfer", "request_authorization"):
        assert forbidden not in source
