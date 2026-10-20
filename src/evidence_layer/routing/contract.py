"""Versioned source contract for multi-source routing.

A source descriptor is routing metadata only. Its text fields (``alias``,
``description``, tags) are untrusted, tenant-controlled data: they may help
rank a source but never grant, widen, or prove access. Authorization comes only
from a trusted policy (see ``catalog``).

``estimated_tokens`` is a planning hint. It is never used for routing
decisions, savings claims, or billing; measured provider usage is the only
basis for those.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum

CONTRACT_VERSION = 1

MAX_ALIAS_LENGTH = 80
MAX_DESCRIPTION_LENGTH = 500
MAX_TAGS = 20
MAX_ESTIMATED_TOKENS = 10**9

_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_TAG_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
_LANGUAGE_PATTERN = re.compile(r"[a-z]{2,3}")


class SourceType(str, Enum):
    LOCAL_FILES = "local_files"
    GOOGLE_DRIVE = "google_drive"
    GITHUB = "github"
    STRUCTURED_DATA = "structured_data"


class SourceStatus(str, Enum):
    ACTIVE = "active"
    DEGRADED = "degraded"
    REVOKED = "revoked"
    DISCONNECTED = "disconnected"


ROUTABLE_STATUSES = frozenset({SourceStatus.ACTIVE, SourceStatus.DEGRADED})


class Sensitivity(str, Enum):
    """What may leave the trust boundary; ``RESTRICTED`` never goes to a provider."""

    STANDARD = "standard"
    RESTRICTED = "restricted"


class Capability(str, Enum):
    LEXICAL_SEARCH = "lexical_search"
    STRUCTURED_QUERY = "structured_query"
    FULL_CONTEXT = "full_context"


def validate_identifier(name: str, value: object) -> str:
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be an opaque identifier")
    return value


def _validate_text(name: str, value: object, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if len(value) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError(f"{name} must not contain control or format characters")
    return value


def _require_enum(name: str, enum_type: type[Enum], value: object) -> None:
    if not isinstance(value, enum_type):
        raise ValueError(f"{name} must be a {enum_type.__name__}")


@dataclass(frozen=True)
class SourceDescriptor:
    source_id: str
    workspace_id: str
    type: SourceType
    status: SourceStatus
    access_policy_ref: str
    sensitivity: Sensitivity
    capabilities: frozenset[Capability]
    alias: str = ""
    description: str = field(default="", repr=False)
    scope_tags: frozenset[str] = frozenset()
    language: str | None = None
    estimated_tokens: int | None = None
    contract_version: int = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION:
            raise ValueError("unsupported source contract version")
        validate_identifier("source_id", self.source_id)
        validate_identifier("workspace_id", self.workspace_id)
        validate_identifier("access_policy_ref", self.access_policy_ref)
        _require_enum("type", SourceType, self.type)
        _require_enum("status", SourceStatus, self.status)
        _require_enum("sensitivity", Sensitivity, self.sensitivity)
        object.__setattr__(
            self, "capabilities", _validated_capabilities(self.capabilities)
        )
        _validate_text("alias", self.alias, MAX_ALIAS_LENGTH)
        _validate_text("description", self.description, MAX_DESCRIPTION_LENGTH)
        object.__setattr__(self, "scope_tags", _validated_tags(self.scope_tags))
        if self.language is not None and (
            not isinstance(self.language, str)
            or _LANGUAGE_PATTERN.fullmatch(self.language) is None
        ):
            raise ValueError("language must be a lowercase ISO 639 code")
        if self.estimated_tokens is not None and (
            isinstance(self.estimated_tokens, bool)
            or not isinstance(self.estimated_tokens, int)
            or not 0 <= self.estimated_tokens <= MAX_ESTIMATED_TOKENS
        ):
            raise ValueError("estimated_tokens must be a bounded non-negative integer")


def _validated_capabilities(value: Iterable[Capability]) -> frozenset[Capability]:
    if isinstance(value, (str, bytes)):
        raise ValueError("capabilities must be a collection of Capability values")
    capabilities = frozenset(value)
    if not capabilities or not all(isinstance(c, Capability) for c in capabilities):
        raise ValueError("capabilities must be a non-empty set of Capability values")
    return capabilities


def _validated_tags(value: Iterable[str]) -> frozenset[str]:
    if isinstance(value, (str, bytes)):
        raise ValueError("scope_tags must be a collection of strings")
    tags = frozenset(value)
    if len(tags) > MAX_TAGS:
        raise ValueError(f"scope_tags exceeds {MAX_TAGS} entries")
    for tag in tags:
        if not isinstance(tag, str) or _TAG_PATTERN.fullmatch(tag) is None:
            raise ValueError("scope_tags must be lowercase identifiers")
    return tags
