"""Measure baseline prompt size vs Evidence Layer selected-context size.

Corpus JSONL: one object per document: {"id": "...", "text": "..."}
Queries JSONL: one object per query: {"query": "..."}

Use --api-url/--api-key/--workspace-id to call the API-key retrieve endpoint.
Without them, the harness runs the same lexical selection locally.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Callable
from uuid import UUID, uuid5, NAMESPACE_URL

import httpx

from evidence_layer.tokenization import shared_visible_counter
from evidence_service.evidence_repository import EvidenceRecord
from evidence_service.retrieval_service import select_evidence


def counter() -> tuple[Callable[[str], int], str]:
    try:
        fn, tokenizer_id, _ = shared_visible_counter()
        return fn, tokenizer_id
    except Exception:
        return lambda text: max(1, ceil(len(text) / 4)), "chars_div_4"


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("JSONL rows must be objects")
            rows.append(value)
    return rows


def local_retrieve(
    corpus: list[dict], workspace: UUID, query: str, top_k: int, max_tokens: int, max_chars: int
) -> dict:
    records = [
        EvidenceRecord(
            id=uuid5(NAMESPACE_URL, str(row["id"])),
            workspace_id=workspace,
            content=str(row["text"]),
            source_label=row.get("source_label"),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        for row in corpus
    ]
    result = select_evidence(
        records, query=query, top_k=top_k, max_tokens=max_tokens, max_chars=max_chars
    )
    return {
        "status": result.status,
        "used_tokens": result.used_tokens,
        "used_chars": result.used_chars,
        "selected_count": len(result.selected),
        "candidate_count": result.candidate_count,
        "token_estimator": result.token_estimator,
    }


def api_retrieve(
    base_url: str, api_key: str, workspace_id: str, query: str, top_k: int, max_tokens: int, max_chars: int
) -> dict:
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/v1/personal-api-keys/workspaces/{workspace_id}/retrieve",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            json={
                "query": query,
                "top_k": top_k,
                "max_tokens": max_tokens,
                "max_chars": max_chars,
            },
        )
    response.raise_for_status()
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--api-url", default=os.environ.get("EVIDENCE_API_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("EVIDENCE_API_KEY", ""))
    parser.add_argument("--workspace-id", default=os.environ.get("EVIDENCE_WORKSPACE_ID", ""))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--max-chars", type=int, default=6000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    corpus = read_jsonl(args.corpus)
    queries = read_jsonl(args.queries)
    for row in corpus:
        if not isinstance(row.get("id"), str) or not isinstance(row.get("text"), str):
            raise SystemExit("corpus rows require string id and text")
    query_values = [str(row["query"]) for row in queries if isinstance(row.get("query"), str)]
    if not query_values:
        raise SystemExit("queries rows require a non-empty query string")

    token_count, estimator = counter()
    baseline_tokens = sum(token_count(str(row["text"])) for row in corpus)
    baseline_chars = sum(len(str(row["text"])) for row in corpus)
    workspace = UUID(args.workspace_id) if args.workspace_id else uuid5(NAMESPACE_URL, "local-harness")
    api_mode = bool(args.api_url and args.api_key and args.workspace_id)

    rows = []
    for query in query_values:
        if api_mode:
            result = api_retrieve(
                args.api_url, args.api_key, args.workspace_id, query,
                args.top_k, args.max_tokens, args.max_chars,
            )
        else:
            result = local_retrieve(
                corpus, workspace, query, args.top_k, args.max_tokens, args.max_chars
            )
        rows.append({
            "query": query,
            "status": result.get("status", "error"),
            "baseline_tokens": baseline_tokens,
            "selected_tokens": int(result.get("used_tokens", 0)),
            "selected_chars": int(result.get("used_chars", 0)),
            "selected_count": int(result.get("selected_count", 0)),
            "candidate_count": int(result.get("candidate_count", len(corpus))),
        })

    output = args.output or Path("reports") / f"retrieval-token-reduction-{datetime.now(timezone.utc):%Y-%m-%d}.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Retrieval token-reduction report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Mode: {'Evidence Layer API' if api_mode else 'local lexical selection'}",
        f"Corpus documents: {len(corpus)}; queries: {len(rows)}",
        f"Baseline: all corpus documents; selected: top_k={args.top_k}, max_tokens={args.max_tokens}, max_chars={args.max_chars}",
        f"Token estimator: {estimator}",
        "",
        "| Query | Status | Baseline tokens | Selected tokens | Reduction | Selected docs |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    reductions = []
    for row in rows:
        reduction = (
            (row["baseline_tokens"] - row["selected_tokens"]) / row["baseline_tokens"] * 100
            if row["baseline_tokens"] else 0.0
        )
        reductions.append(reduction)
        lines.append(
            f"| {row['query']} | {row['status']} | {row['baseline_tokens']} | "
            f"{row['selected_tokens']} | {reduction:.1f}% | {row['selected_count']} |"
        )
    lines += [
        "",
        f"Baseline chars: {baseline_chars}",
        f"Mean token reduction: {sum(reductions) / len(reductions):.1f}%" if reductions else "No queries",
        "",
        "This report measures selected context size, not answer quality. It does not claim semantic retrieval or JEV.",
    ]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
