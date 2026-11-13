"""Deterministic retrieve-time chunking and path/title weighting."""

from __future__ import annotations

import re
from dataclasses import dataclass

CHUNK_TARGET_CHARS = 2_400
CHUNK_OVERLAP_CHARS = 240
MAX_CHUNKS_PER_EVIDENCE = 48
CHUNKING_VERSION = "retrieve_time_v1"

README_BOOST = 1.50
ARCHITECTURE_BOOST = 1.50
PRODUCT_DOCS_BOOST = 1.35
DOCS_MARKDOWN_BOOST = 1.20
TOP_LEVEL_MARKDOWN_BOOST = 1.15
QUERY_PATH_BOOST = 1.08
QUERY_TITLE_BOOST = 1.08
OPS_NOISE_PENALTY = 0.70

_OPS_QUERY_TERMS = frozenset(
    {
        "audit", "backup", "checklist", "closure", "deletion", "deploy",
        "deployment", "migration", "operations", "ops", "production", "purge",
        "release", "restore", "retention", "runbook", "smoke", "verification",
        "verify", "worker",
    }
)
_OPS_NOISE_PATH = re.compile(
    r"(^|/)(?:[^/]*[-_])?"
    r"(closure|closed|cleanup|one[-_ ]?off|smoke|verification|verify|checklist)"
    r"[-_a-z0-9]*\.(md|mdx|txt)$",
    re.IGNORECASE,
)
_MARKDOWN_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
_TOKEN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class TextChunk:
    index: int
    start: int
    end: int
    content: str

    @property
    def chunk_id(self) -> str:
        return f"{self.start}:{self.end}"


def _boundary(text: str, start: int, end: int, target_chars: int) -> int:
    lower = start + max(1, int(target_chars * 0.6))
    window = text[lower:end]
    for separator in ("\n\n", "\n", ". ", " "):
        index = window.rfind(separator)
        if index >= 0:
            boundary = lower + index + len(separator)
            if boundary > start:
                return boundary
    return end


def chunk_text(
    text: str,
    *,
    target_chars: int = CHUNK_TARGET_CHARS,
    overlap_chars: int = CHUNK_OVERLAP_CHARS,
    max_chunks: int = MAX_CHUNKS_PER_EVIDENCE,
) -> list[TextChunk]:
    """Split text at readable boundaries with a stable offset-based identity."""
    if target_chars < 1:
        raise ValueError("target_chars must be positive")
    if overlap_chars < 0 or overlap_chars >= target_chars:
        raise ValueError("overlap_chars must be non-negative and smaller than target_chars")
    if max_chunks < 1:
        raise ValueError("max_chunks must be positive")
    if not text:
        return []

    chunks: list[TextChunk] = []
    start = 0
    while start < len(text):
        end = len(text) if len(chunks) == max_chunks - 1 else min(start + target_chars, len(text))
        if end < len(text):
            end = _boundary(text, start, end, target_chars)
        chunks.append(TextChunk(index=len(chunks), start=start, end=end, content=text[start:end]))
        if end >= len(text):
            break
        start = max(end - overlap_chars, start + 1)
    return chunks


def source_path(source_label: str | None) -> str | None:
    if not source_label:
        return None
    raw = source_label.strip()
    if raw.lower().startswith("github: "):
        raw = raw[8:].strip()
        parts = raw.split("/")
        if len(parts) >= 3:
            return "/".join(parts[2:])
    return raw or None


def document_title(content: str, path: str | None) -> str | None:
    for line in content[:2_000].splitlines():
        match = _MARKDOWN_HEADING.match(line)
        if match:
            title = " ".join(match.group(1).split())
            if title:
                return title
    if path:
        name = path.rsplit("/", 1)[-1]
        return name.rsplit(".", 1)[0] or name
    return None


def _is_ops_noise(path: str | None, query_terms: set[str]) -> bool:
    return bool(
        path
        and _OPS_NOISE_PATH.search(path.lower())
        and not (query_terms & _OPS_QUERY_TERMS)
    )


def baseline_path_multiplier(path: str | None, query_terms: set[str]) -> float:
    """Return the deterministic path prior; noisy ops paths are query-aware."""
    if not path:
        return 1.0
    lower = path.lower()
    name = lower.rsplit("/", 1)[-1]
    if name.startswith("readme"):
        multiplier = README_BOOST
    elif lower.startswith("docs/architecture") or "/architecture" in lower:
        multiplier = ARCHITECTURE_BOOST
    elif re.search(r"(^|/)docs/(product|design|overview|spec)(/|$|[-_.])", lower):
        multiplier = PRODUCT_DOCS_BOOST
    elif lower.startswith("docs/") and name.endswith((".md", ".mdx")):
        multiplier = DOCS_MARKDOWN_BOOST
    elif "/" not in lower and name.endswith((".md", ".mdx")):
        multiplier = TOP_LEVEL_MARKDOWN_BOOST
    else:
        multiplier = 1.0
    if _is_ops_noise(path, query_terms):
        multiplier *= OPS_NOISE_PENALTY
    return multiplier


def query_metadata_boost(
    *,
    path: str | None,
    title: str | None,
    query_terms: set[str],
) -> float:
    if _is_ops_noise(path, query_terms):
        return 1.0
    boost = 1.0
    if path and query_terms & set(_TOKEN.findall(path.lower())):
        boost *= QUERY_PATH_BOOST
    if title and query_terms & set(_TOKEN.findall(title.lower())):
        boost *= QUERY_TITLE_BOOST
    return boost


def adjusted_score(
    lexical_score: float,
    *,
    path: str | None,
    title: str | None,
    query_terms: set[str],
) -> float:
    return (
        lexical_score
        * baseline_path_multiplier(path, query_terms)
        * query_metadata_boost(path=path, title=title, query_terms=query_terms)
    )
