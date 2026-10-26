"""The synthetic development fixture must be unreachable outside explicit dev/test opt-in.

These tests complement tests/test_fixture_runtime_guard.py: they pin the exact
mode strings that must be rejected, prove the HTTP service and MCP server never
reference the fixture or its opt-in switch, and show that the ordinary retriever
stays deny-all under a production configuration even with the opt-in set.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evidence_layer.retrieval import LocalCorpusRetriever
from evidence_layer.retrieval.synthetic_fixtures import (
    SYNTHETIC_SOURCE_ID,
    SyntheticFixtureReadBoundary,
)
from evidence_layer.runtime_policy import (
    RUNTIME_MODE_ENV,
    SYNTHETIC_FIXTURE_OPT_IN_ENV,
)
from evidence_layer.source_access import (
    SourceReadDenialReason,
    SourceReadError,
    SourceReadIntent,
    SourceRequestContext,
)

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SOURCES = (
    *sorted((ROOT / "services" / "evidence-service" / "evidence_service").glob("*.py")),
    *sorted((ROOT / "src" / "evidence_layer" / "mcp").rglob("*.py")),
)
FIXTURE_MARKERS = re.compile(
    r"for_synthetic_development_fixture|synthetic_fixtures|SyntheticFixtureReadBoundary"
    r"|ALLOW_SYNTHETIC_FIXTURE|SYNTHETIC_FIXTURE_OPT_IN|require_synthetic_fixture_opt_in"
)


def _context() -> SourceRequestContext:
    return SourceRequestContext(
        request_id="request",
        operation_id="operation",
        principal_id="test-principal",
        tenant_id="test-tenant",
        policy_version="test-policy",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )


def _policy(monkeypatch, mode: str | None, opt_in: str | None) -> None:
    monkeypatch.delenv(RUNTIME_MODE_ENV, raising=False)
    monkeypatch.delenv(SYNTHETIC_FIXTURE_OPT_IN_ENV, raising=False)
    if mode is not None:
        monkeypatch.setenv(RUNTIME_MODE_ENV, mode)
    if opt_in is not None:
        monkeypatch.setenv(SYNTHETIC_FIXTURE_OPT_IN_ENV, opt_in)


def test_runtime_source_files_are_discovered():
    names = {path.name for path in RUNTIME_SOURCES}
    assert {"app.py", "server.py", "tools.py"} <= names


@pytest.mark.parametrize("path", RUNTIME_SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_http_service_and_mcp_never_reference_the_synthetic_fixture(path):
    text = path.read_text(encoding="utf-8")
    assert FIXTURE_MARKERS.search(text) is None


@pytest.mark.parametrize(
    "mode",
    [
        "production",
        "PRODUCTION",
        "Production",
        "prod",
        "staging",
        "preview",
        "dev",
        "",
        " ",
        " test",
        "test ",
        "development ",
        "development\n",
        "development,production",
    ],
)
def test_every_non_exact_mode_is_rejected_even_with_the_opt_in_set(monkeypatch, mode):
    _policy(monkeypatch, mode, "true")

    with pytest.raises(RuntimeError, match="disabled by runtime policy"):
        LocalCorpusRetriever.for_synthetic_development_fixture()
    with pytest.raises(RuntimeError, match="disabled by runtime policy"):
        SyntheticFixtureReadBoundary().open_sources(
            _context(), (SourceReadIntent(SYNTHETIC_SOURCE_ID, "retrieve"),)
        )


@pytest.mark.parametrize("opt_in", ["True", "TRUE", "1", "yes", "true ", " true", ""])
def test_only_the_exact_lowercase_opt_in_value_is_accepted(monkeypatch, opt_in):
    _policy(monkeypatch, "development", opt_in)

    with pytest.raises(RuntimeError, match="disabled by runtime policy"):
        LocalCorpusRetriever.for_synthetic_development_fixture()


@pytest.mark.parametrize("opt_in", [None, "true", "false"])
def test_ordinary_retriever_stays_deny_all_under_a_production_configuration(
    monkeypatch, opt_in
):
    _policy(monkeypatch, "production", opt_in)
    retriever = LocalCorpusRetriever((SYNTHETIC_SOURCE_ID,))

    with pytest.raises(SourceReadError) as error:
        retriever.retrieve("btree index lookup", request_context=_context())

    assert error.value.denial.reason is SourceReadDenialReason.DENY_ALL
