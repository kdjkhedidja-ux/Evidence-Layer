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
