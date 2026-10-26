"""Routers rank authorized sources for a query; they never authorize anything.

``Router`` implementations return untrusted identifiers. The pipeline validates
every output against the catalog that was sent to the router, so a router can
neither add a source nor widen access.

``DeterministicRouter`` is the baseline and the safe fallback: the same inputs
always give the same ordered output, with ties broken by ``source_id``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from evidence_layer.routing.catalog import AuthorizedCatalog
from evidence_layer.scoring.lexical import DEFAULT_TOKEN_PATTERN, STOPWORDS

MAX_QUERY_LENGTH = 2000
MAX_SOURCES_CAP = 20

_TOKEN = re.compile(DEFAULT_TOKEN_PATTERN)

TAG_WEIGHT = 3
ALIAS_WEIGHT = 2
DESCRIPTION_WEIGHT = 1


@dataclass(frozen=True)
class RoutingRequest:
    query: str = field(repr=False)
    max_sources: int = 3

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or not self.query.strip():
            raise ValueError("query must be a non-empty string")
        if len(self.query) > MAX_QUERY_LENGTH:
            raise ValueError(f"query exceeds {MAX_QUERY_LENGTH} characters")
        if (
            isinstance(self.max_sources, bool)
            or not isinstance(self.max_sources, int)
            or not 1 <= self.max_sources <= MAX_SOURCES_CAP
        ):
            raise ValueError(f"max_sources must be between 1 and {MAX_SOURCES_CAP}")


class Router(Protocol):
    name: str

    def route(
        self, request: RoutingRequest, catalog: AuthorizedCatalog
    ) -> Sequence[str]: ...


def _tokens(text: str) -> frozenset[str]:
    return frozenset(
        token for token in _TOKEN.findall(text.lower()) if token not in STOPWORDS
    )


class DeterministicRouter:
    """Weighted token overlap: tags (3) > alias (2) > description (1).

    Each distinct query token counts once, at the highest weight of the field
    that contains it. Descriptions carry the lowest weight because they are
    free text controlled by the tenant. A source with no overlap is never
    selected.
    """

    name = "deterministic"

    def route(
        self, request: RoutingRequest, catalog: AuthorizedCatalog
    ) -> Sequence[str]:
        query_tokens = _tokens(request.query)
        scored: list[tuple[int, str]] = []
        for source in catalog.sources:
            tag_tokens = frozenset(
                token for tag in source.scope_tags for token in _tokens(tag)
            )
            alias_tokens = _tokens(source.alias)
            description_tokens = _tokens(source.description)
            score = 0
            for token in query_tokens:
                if token in tag_tokens:
                    score += TAG_WEIGHT
                elif token in alias_tokens:
                    score += ALIAS_WEIGHT
                elif token in description_tokens:
                    score += DESCRIPTION_WEIGHT
            if score > 0:
                scored.append((score, source.source_id))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [source_id for _, source_id in scored[: request.max_sources]]
