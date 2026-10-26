"""Source contract validation: opaque ids, bounded untrusted text, closed enums."""

from __future__ import annotations

import pytest

from evidence_layer.routing import (
    CONTRACT_VERSION,
    Capability,
    DeterministicRouter,
    Sensitivity,
    SourceStatus,
    SourceType,
)
from evidence_layer.routing.catalog import AuthorizedCatalog

from routing_helpers import make_request, make_source


def test_valid_descriptor_round_trips_defaults() -> None:
    source = make_source("src-1", alias="Finance", tags={"billing", "invoices"})
    assert source.contract_version == CONTRACT_VERSION
    assert source.capabilities == frozenset({Capability.LEXICAL_SEARCH})
    assert source.scope_tags == frozenset({"billing", "invoices"})


@pytest.mark.parametrize(
    "bad_id",
    ["", " ", "a b", "../secret", "a/b", "-leading", "x" * 129, "ünïcode", "a\nb", 7, None],
)
def test_identifiers_must_be_opaque(bad_id: object) -> None:
    with pytest.raises(ValueError):
        make_source(bad_id)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        make_source("src-1", workspace_id=bad_id)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        make_source("src-1", access_policy_ref=bad_id)


@pytest.mark.parametrize("field", ["alias", "description"])
def test_text_fields_reject_control_and_format_characters(field: str) -> None:
    for bad in ("line\nbreak", "tab\there", "nul\x00", "zero\u200bwidth", "bidi\u202eflip"):
        with pytest.raises(ValueError):
            make_source("src-1", **{field: bad})


def test_text_fields_enforce_length_limits() -> None:
    make_source("src-1", alias="a" * 80, description="d" * 500)
    with pytest.raises(ValueError):
        make_source("src-1", alias="a" * 81)
    with pytest.raises(ValueError):
        make_source("src-1", description="d" * 501)


@pytest.mark.parametrize(
    "bad_tags",
    [{"Upper"}, {"has space"}, {""}, {"-lead"}, {"x" * 41}, {f"t{i}" for i in range(21)}, "billing", {1}],
)
def test_tags_are_lowercase_bounded_identifiers(bad_tags: object) -> None:
    with pytest.raises(ValueError):
        make_source("src-1", tags=bad_tags)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [frozenset(), "lexical_search", {"lexical_search"}, {1}])
def test_capabilities_must_be_a_non_empty_set_of_enum_values(bad: object) -> None:
    with pytest.raises(ValueError):
        make_source("src-1", capabilities=bad)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("type", "local_files"),
        ("status", "active"),
        ("sensitivity", "standard"),
    ],
)
def test_enum_fields_reject_plain_strings(field: str, bad: str) -> None:
    with pytest.raises(ValueError):
        make_source("src-1", **{field: bad})


def test_contract_version_must_match() -> None:
    with pytest.raises(ValueError):
        make_source("src-1", contract_version=CONTRACT_VERSION + 1)


@pytest.mark.parametrize("bad", [-1, True, 1.5, "10", 10**9 + 1])
def test_estimated_tokens_is_bounded_and_typed(bad: object) -> None:
    with pytest.raises(ValueError):
        make_source("src-1", estimated_tokens=bad)
    assert make_source("src-1", estimated_tokens=0).estimated_tokens == 0


@pytest.mark.parametrize("bad", ["EN", "english", "e", "en-US", 5])
def test_language_is_a_lowercase_iso_code(bad: object) -> None:
    with pytest.raises(ValueError):
        make_source("src-1", language=bad)
    assert make_source("src-1", language="en").language == "en"


def test_estimated_tokens_never_influences_routing() -> None:
    small = make_source("src-a", tags={"billing"}, estimated_tokens=10)
    large = make_source("src-b", tags={"billing"}, estimated_tokens=10**8)
    catalog = AuthorizedCatalog("tenant-a", "policy-v1", (small, large))
    assert list(DeterministicRouter().route(make_request("billing"), catalog)) == [
        "src-a",
        "src-b",
    ]


def test_descriptor_repr_omits_untrusted_description() -> None:
    source = make_source("src-1", description="free text from the tenant")
    assert "free text" not in repr(source)


def test_enum_members_are_closed_lists() -> None:
    assert {t.value for t in SourceType} == {
        "local_files",
        "google_drive",
        "github",
        "structured_data",
    }
    assert {s.value for s in SourceStatus} == {
        "active",
        "degraded",
        "revoked",
        "disconnected",
    }
    assert {s.value for s in Sensitivity} == {"standard", "restricted"}
