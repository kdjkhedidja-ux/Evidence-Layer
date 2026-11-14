from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from evidence_service.chunking import (
    DOCS_MARKDOWN_BOOST,
    README_BOOST,
    adjusted_score,
    chunk_text,
    document_title,
    source_path,
)
from evidence_service.evidence_repository import EvidenceRecord
from evidence_service.retrieval_service import select_evidence

WORKSPACE = UUID("22222222-2222-4222-8222-222222222222")


def record(text: str, ident: str, label: str | None = "Source") -> EvidenceRecord:
    return EvidenceRecord(
        id=UUID(ident),
        workspace_id=WORKSPACE,
        content=text,
        source_label=label,
        created_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
        updated_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
    )


def test_short_document_stays_one_chunk():
    chunks = chunk_text("short evidence")
    assert len(chunks) == 1
    assert chunks[0].start == 0
    assert chunks[0].end == len("short evidence")
    assert chunks[0].content == "short evidence"


def test_chunk_boundaries_overlap_and_cover_the_document():
    text = ("alpha " * 400) + "\n\n" + ("beta " * 400) + "\n\n" + ("gamma " * 400)
    chunks = chunk_text(text, target_chars=1_000, overlap_chars=100)
    assert len(chunks) > 1
    assert chunks[0].end <= 1_000
    assert chunks[0].content.endswith(" ")
    assert chunks[1].start == chunks[0].end - 100
    assert chunks[-1].end == len(text)
    assert all(chunk.content for chunk in chunks)


def test_chunk_ids_are_stable_and_capped():
    text = "line of evidence\n" * 2_000
    first = chunk_text(text, target_chars=500, overlap_chars=50, max_chunks=7)
    second = chunk_text(text, target_chars=500, overlap_chars=50, max_chunks=7)
    assert len(first) == 7
    assert [(chunk.start, chunk.end) for chunk in first] == [
        (chunk.start, chunk.end) for chunk in second
    ]
    assert first[-1].end == len(text)


def test_source_path_and_title_are_derived_without_schema_changes():
    assert source_path("GitHub: owner/repo/docs/ARCHITECTURE.md") == "docs/ARCHITECTURE.md"
    assert source_path("GitHub: owner/repo/README.md") == "README.md"
    assert source_path(None) is None
    assert document_title("# Evidence Layer\n\nBody", "README.md") == "Evidence Layer"
    assert document_title("Body only", "docs/guide.md") == "guide"


def test_path_boost_prefers_readme_and_docs_over_neutral_source():
    query_terms = {"product", "architecture"}
    readme = adjusted_score(
        0.1, path="README.md", title="Product overview", query_terms=query_terms
    )
    docs = adjusted_score(
        0.1, path="docs/guide.md", title="Product overview", query_terms=query_terms
    )
    source = adjusted_score(
        0.1, path="src/main.py", title="Product overview", query_terms=query_terms
    )
    assert readme > docs > source
    assert readme == 0.1 * README_BOOST * 1.08
    assert docs == 0.1 * DOCS_MARKDOWN_BOOST * 1.08


def test_ops_noise_penalty_is_query_aware():
    product_query = {"product"}
    ops_query = {"deletion", "worker", "runbook"}
    product = adjusted_score(
        0.1,
        path="docs/operations/deletion-worker-closure.md",
        title="Deletion worker",
        query_terms=product_query,
    )
    ops = adjusted_score(
        0.1,
        path="docs/operations/deletion-worker-closure.md",
        title="Deletion worker",
        query_terms=ops_query,
    )
    assert ops > product


def test_long_readme_chunks_outrank_an_incidental_ops_file():
    readme_text = "# Evidence Layer\n\n" + (
        "Evidence Layer selects the smallest useful evidence context for a product answer. "
        * 80
    )
    ops_text = "Product context closure checklist. " * 120
    result = select_evidence(
        [
            record(
                ops_text,
                "33333333-3333-4333-8333-333333333333",
                "GitHub: owner/repo/docs/operations/product-context-closure.md",
            ),
            record(
                readme_text,
                "11111111-1111-4111-8111-111111111111",
                "GitHub: owner/repo/README.md",
            ),
        ],
        query="what is the product context",
        top_k=2,
        max_tokens=2_000,
        max_chars=12_000,
    )
    assert result.selected[0].record.id == UUID("11111111-1111-4111-8111-111111111111")
    assert result.selected[0].path == "README.md"
    assert result.selected[0].chunk_id.startswith("11111111-1111-4111-8111-111111111111:")


def test_definitional_query_prefers_readme_over_adr_and_plans():
    readme = "# Evidence Layer\n\n" + (
        "Evidence Layer is a provider-agnostic evidence-selection and context-reduction layer. "
        * 80
    )
    adr = "# ADR 0005 — Separate ranking from evidence selection\n\n" + (
        "The evidence problem is to solve ranking and selection with a complete candidate pool. "
        * 80
    )
    result = select_evidence(
        [
            record(adr, "44444444-4444-4444-8444-444444444444", "GitHub: owner/repo/docs/adr/0005-selection.md"),
            record(readme, "55555555-5555-4555-8555-555555555555", "GitHub: owner/repo/README.md"),
        ],
        query="What is Evidence Layer and what problem does it solve?",
        top_k=2,
        max_tokens=2_000,
        max_chars=12_000,
    )
    assert result.selected[0].path == "README.md"
    assert result.selected[0].chunk_index == 0


def test_packing_prefers_parent_path_diversity():
    alpha = "shared product context " * 1_000
    beta = "shared product context " * 1_000
    result = select_evidence(
        [
            record(alpha, "11111111-1111-4111-8111-111111111111", "GitHub: owner/repo/README.md"),
            record(beta, "22222222-2222-4222-8222-222222222222", "GitHub: owner/repo/docs/guide.md"),
        ],
        query="shared product context",
        top_k=3,
        max_tokens=4_000,
        max_chars=20_000,
    )
    parent_ids = [item.record.id for item in result.selected]
    assert parent_ids[0] != parent_ids[1]
    assert len(set(parent_ids[:2])) == 2
