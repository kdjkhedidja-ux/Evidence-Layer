"""Index whole connector files: large-file parts and recorded non-indexed files.

Principle: index everything, select little at query time, never exclude
silently, protect customer privacy.

* A text file of at most ``SINGLE_RECORD_MAX_CHARS`` characters stays one
  evidence record with the file's own external id (unchanged behaviour).
* A larger text file (up to the 2 MiB fetch limit) is split on line
  boundaries into parts of at most ``PART_MAX_CHARS`` characters with 2,000 to
  3,000 characters of overlap (``PART_OVERLAP_CHARS`` plus line alignment). Each part is an evidence record whose
  external id is ``<file id>#part-NNN`` and whose label carries the part's real
  character offsets in the original file::

      GitHub: owner/repo/path/to/file.py #part=2/3 chars=58000-118000

  ``chars=start-end`` is a half-open range: ``original[start:end]`` is exactly
  the part's content.
* A file that is not indexed (over the fetch limit, binary, fetch error, empty,
  too many parts) is recorded in ``connector_documents`` with its revision and
  a reason code in ``mime_type`` and **no content**. It is not fetched again
  until its revision changes, and its plain path is shown only to its owner.
* Logs carry a truncated SHA-256 of ``"<connection id>:<path>"`` and the
  reason code, never the plain path or any content.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Existing single-record bound (evidence_records content check, 1..65536).
SINGLE_RECORD_MAX_CHARS = 65_536
PART_MAX_CHARS = 60_000
PART_OVERLAP_CHARS = 2_000
# A part ends on the last newline within its final PART_CUT_WINDOW_CHARS
# characters (else exactly at PART_MAX_CHARS), and the next part starts at most
# PART_OVERLAP_CHARS + PART_OVERLAP_CHARS // 2 before that end. Each part
# therefore advances at least 60,000 - 6,000 - 3,000 = 51,000 characters, so a
# file at the 2 MiB fetch limit needs at most ceil(2,097,152 / 51,000) = 42
# parts; MAX_PARTS leaves headroom. Typical source files advance ~58,000.
PART_CUT_WINDOW_CHARS = 6_000
MAX_PARTS = 48

NOT_INDEXED_MIME_PREFIX = "application/x-evidence-not-indexed"
NOT_INDEXED_REASONS = (
    "too_large",       # over the 2 MiB fetch limit; never fetched
    "binary",          # not a text type
    "fetch_error",     # the provider refused or failed to return the file
    "empty",           # no non-whitespace text
    "too_many_parts",  # more than MAX_PARTS parts (not reachable under 2 MiB)
)
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
LOG_PATH_HASH_CHARS = 16

_PART_LABEL = re.compile(
    r"^(?P<base>.+) #part=(?P<index>\d+)/(?P<total>\d+) chars=(?P<start>\d+)-(?P<end>\d+)$"
)


@dataclass(frozen=True)
class PartLocation:
    base_label: str
    index: int
    total: int
    start: int
    end: int


def split_parts(text: str) -> list[tuple[int, int]]:
    """Half-open (start, end) ranges covering ``text``.

    One range when the text fits a single record; otherwise parts of at most
    ``PART_MAX_CHARS`` cut after a newline when possible, each starting about
    ``PART_OVERLAP_CHARS`` before the previous end (also on a line start).
    """
    length = len(text)
    if length <= SINGLE_RECORD_MAX_CHARS:
        return [(0, length)]
    ranges: list[tuple[int, int]] = []
    start = 0
    while True:
        end = min(start + PART_MAX_CHARS, length)
        if end < length:
            cut = text.rfind("\n", end - PART_CUT_WINDOW_CHARS, end)
            if cut != -1:
                end = cut + 1
        ranges.append((start, end))
        if end >= length:
            return ranges
        next_start = max(start + 1, end - PART_OVERLAP_CHARS)
        line_start = text.rfind("\n", next_start - PART_OVERLAP_CHARS // 2, next_start)
        if line_start != -1 and line_start + 1 > start:
            next_start = line_start + 1
        start = next_start


def part_external_id(file_external_id: str, index: int) -> str:
    return f"{file_external_id}#part-{index:03d}"


def possible_part_ids(file_external_id: str) -> set[str]:
    """Every part id a file may own (used to keep existing parts on skip)."""
    return {part_external_id(file_external_id, index) for index in range(1, MAX_PARTS + 1)}


def part_label(base_label: str, index: int, total: int, start: int, end: int) -> str:
    return f"{base_label} #part={index}/{total} chars={start}-{end}"


def parse_part_label(source_label: str | None) -> PartLocation | None:
    if not source_label:
        return None
    match = _PART_LABEL.match(source_label.strip())
    if not match:
        return None
    return PartLocation(
        base_label=match.group("base"),
        index=int(match.group("index")),
        total=int(match.group("total")),
        start=int(match.group("start")),
        end=int(match.group("end")),
    )


def not_indexed_mime(reason: str) -> str:
    if reason not in NOT_INDEXED_REASONS:
        raise ValueError(f"unknown not-indexed reason: {reason}")
    return f"{NOT_INDEXED_MIME_PREFIX}; reason={reason}"


def not_indexed_reason(mime_type: str | None) -> str | None:
    if not mime_type or not mime_type.startswith(NOT_INDEXED_MIME_PREFIX):
        return None
    reason = mime_type.rsplit("reason=", 1)[-1].strip()
    return reason if reason in NOT_INDEXED_REASONS else None


def log_path_hash(connection_id: object, path: str) -> str:
    """Truncated SHA-256 of ``"<connection id>:<path>"`` for content-free logs.

    The owner can recompute it from a path they see in the UI; common file
    names are not linkable across connections.
    """
    digest = hashlib.sha256(f"{connection_id}:{path}".encode("utf-8")).hexdigest()
    return digest[:LOG_PATH_HASH_CHARS]
