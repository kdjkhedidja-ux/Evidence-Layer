from __future__ import annotations

import unittest
from uuid import UUID

from evidence_service.evidence_repository import EvidenceRecord
from evidence_service.retrieval_service import select_evidence

WORKSPACE = UUID("22222222-2222-4222-8222-222222222222")


def record(text: str, ident: str) -> EvidenceRecord:
    return EvidenceRecord(
        id=UUID(ident),
        workspace_id=WORKSPACE,
        content=text,
        source_label="Source",
        created_at="2026-11-12T00:00:00Z",  # type: ignore[arg-type]
        updated_at="2026-11-12T00:00:00Z",  # type: ignore[arg-type]
    )


class RetrievalSelectionTests(unittest.TestCase):
    def test_selects_ranked_budgeted_evidence(self):
        result = select_evidence(
            [
                record("evidence layer lexical retrieval", "11111111-1111-4111-8111-111111111111"),
                record("unrelated cooking note", "22222222-2222-4222-8222-222222222222"),
            ],
            query="lexical retrieval",
            top_k=2,
            max_tokens=100,
            max_chars=1000,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.selected), 1)
        self.assertEqual(result.selected[0].record.id, UUID("11111111-1111-4111-8111-111111111111"))

    def test_no_overlap_returns_no_results(self):
        result = select_evidence(
            [record("unrelated cooking note", "22222222-2222-4222-8222-222222222222")],
            query="quantum",
            top_k=2,
            max_tokens=100,
            max_chars=1000,
        )
        self.assertEqual(result.status, "no_results")
        self.assertEqual(result.selected, [])

    def test_budget_skips_oversized_record(self):
        result = select_evidence(
            [record("x " * 100, "33333333-3333-4333-8333-333333333333")],
            query="x",
            top_k=2,
            max_tokens=1,
            max_chars=10,
        )
        self.assertEqual(result.status, "no_results")

    def test_explicit_related_record_can_be_selected_without_lexical_match(self):
        seed_id = UUID("11111111-1111-4111-8111-111111111111")
        related_id = UUID("22222222-2222-4222-8222-222222222222")
        unrelated_id = UUID("33333333-3333-4333-8333-333333333333")
        result = select_evidence(
            [
                record("release deployment review", str(seed_id)),
                record("emergency recovery procedure", str(related_id)),
                record("unrelated cooking notes", str(unrelated_id)),
            ],
            query="release review",
            top_k=2,
            max_tokens=100,
            max_chars=1000,
            seed_record_ids={seed_id},
            related_record_scores={related_id: 0.2},
        )
        self.assertEqual([item.record.id for item in result.selected], [seed_id, related_id])
        self.assertEqual(result.matched_chunk_count, 1)

    def test_related_selection_stays_within_context_budgets(self):
        seed_id = UUID("11111111-1111-4111-8111-111111111111")
        related_id = UUID("22222222-2222-4222-8222-222222222222")
        result = select_evidence(
            [
                record("release", str(seed_id)),
                record("recovery " * 20, str(related_id)),
            ],
            query="release",
            top_k=2,
            max_tokens=100,
            max_chars=10,
            seed_record_ids={seed_id},
            related_record_scores={related_id: 0.2},
        )
        self.assertEqual([item.record.id for item in result.selected], [seed_id])
        self.assertLessEqual(result.used_chars, 10)
        self.assertEqual(result.status, "budget_exhausted")
