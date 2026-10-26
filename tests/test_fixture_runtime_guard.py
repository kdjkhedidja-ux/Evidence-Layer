from datetime import datetime, timedelta, timezone

import pytest

from evidence_layer.retrieval import LocalCorpusRetriever
from evidence_layer.retrieval.synthetic_fixtures import (
    SYNTHETIC_SOURCE_ID,
    SyntheticFixtureReadBoundary,
)
from evidence_layer.runtime_policy import (
    RUNTIME_MODE_ENV,
    SYNTHETIC_FIXTURE_OPT_IN_ENV,
    require_synthetic_fixture_opt_in,
)
from evidence_layer.source_access import (
    DenyAllSourceReadBoundary,
    SourceReadDenialReason,
    SourceReadError,
    SourceReadIntent,
    SourceRequestContext,
)


def _context():
    return SourceRequestContext(
        request_id="request",
        operation_id="operation",
        principal_id="test-principal",
        tenant_id="test-tenant",
        policy_version="test-policy",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )


def _intent():
    return (SourceReadIntent(SYNTHETIC_SOURCE_ID, "retrieve"),)


def _set_runtime_policy(monkeypatch, mode, opt_in):
    monkeypatch.delenv(RUNTIME_MODE_ENV, raising=False)
    monkeypatch.delenv(SYNTHETIC_FIXTURE_OPT_IN_ENV, raising=False)
    if mode is not None:
        monkeypatch.setenv(RUNTIME_MODE_ENV, mode)
    if opt_in is not None:
        monkeypatch.setenv(SYNTHETIC_FIXTURE_OPT_IN_ENV, opt_in)


@pytest.mark.parametrize(
    ("mode", "opt_in"),
    [
        ("production", "false"),
        ("production", "true"),
        (None, None),
        (None, "false"),
        (None, "true"),
        ("unknown", None),
        ("unknown", "false"),
        ("unknown", "true"),
        ("development", None),
        ("development", "false"),
        ("development", "TRUE"),
        ("test", None),
        ("test", "false"),
        ("Development", "true"),
    ],
)
def test_fixture_factory_fails_closed_unless_mode_and_opt_in_are_explicit(
    monkeypatch, mode, opt_in
):
    monkeypatch.delenv(RUNTIME_MODE_ENV, raising=False)
    monkeypatch.delenv(SYNTHETIC_FIXTURE_OPT_IN_ENV, raising=False)
    if mode is not None:
        monkeypatch.setenv(RUNTIME_MODE_ENV, mode)
    if opt_in is not None:
        monkeypatch.setenv(SYNTHETIC_FIXTURE_OPT_IN_ENV, opt_in)

    with pytest.raises(RuntimeError, match="disabled by runtime policy"):
        LocalCorpusRetriever.for_synthetic_development_fixture()


@pytest.mark.parametrize("mode", ["development", "test"])
def test_fixture_factory_allows_only_explicit_development_or_test_opt_in(
    monkeypatch, mode
):
    monkeypatch.setenv(RUNTIME_MODE_ENV, mode)
    monkeypatch.setenv(SYNTHETIC_FIXTURE_OPT_IN_ENV, "true")

    retriever = LocalCorpusRetriever.for_synthetic_development_fixture()

    assert isinstance(retriever, LocalCorpusRetriever)
    assert retriever._source_ids == (SYNTHETIC_SOURCE_ID,)
    assert isinstance(retriever._source_read_boundary, SyntheticFixtureReadBoundary)


def test_direct_fixture_boundary_cannot_bypass_production_guard(monkeypatch):
    _set_runtime_policy(monkeypatch, "production", "true")

    with pytest.raises(RuntimeError, match="disabled by runtime policy"):
        SyntheticFixtureReadBoundary().open_sources(
            _context(),
            _intent(),
        )


@pytest.mark.parametrize(
    ("mode", "opt_in"),
    [
        ("production", "true"),
        (None, "true"),
        ("unknown", "true"),
        ("development", None),
        ("development", "false"),
        ("test", None),
        ("test", "false"),
    ],
)
def test_direct_fixture_boundary_rejects_disallowed_runtime_policy(
    monkeypatch, mode, opt_in
):
    _set_runtime_policy(monkeypatch, mode, opt_in)

    with pytest.raises(RuntimeError, match="disabled by runtime policy"):
        SyntheticFixtureReadBoundary().open_sources(_context(), _intent())


@pytest.mark.parametrize("mode", ["development", "test"])
def test_direct_fixture_boundary_allows_explicit_nonproduction_opt_in(
    monkeypatch, mode
):
    _set_runtime_policy(monkeypatch, mode, "true")

    opened = SyntheticFixtureReadBoundary().open_sources(_context(), _intent())
    assert isinstance(opened, tuple)
    assert len(opened) == 1
    try:
        records = list(opened[0].iter_records())
    finally:
        opened[0].close()

    assert records


@pytest.mark.parametrize(
    ("mode", "opt_in"),
    [
        ("production", "true"),
        (None, "true"),
        ("unknown", "true"),
        ("development", "false"),
        ("test", "false"),
    ],
)
def test_retriever_rechecks_runtime_policy_at_fixture_read_boundary(
    monkeypatch, mode, opt_in
):
    _set_runtime_policy(monkeypatch, "development", "true")
    retriever = LocalCorpusRetriever.for_synthetic_development_fixture()
    _set_runtime_policy(monkeypatch, mode, opt_in)

    with pytest.raises(SourceReadError) as error:
        retriever.retrieve("btree index lookup", request_context=_context())

    assert error.value.denial.reason is SourceReadDenialReason.STORAGE_FAILURE


def test_fixture_open_has_no_separate_unguarded_helper():
    assert not hasattr(SyntheticFixtureReadBoundary, "_open_fixed_fixture")


def test_runtime_policy_does_not_accept_caller_supplied_environment():
    with pytest.raises(TypeError):
        require_synthetic_fixture_opt_in(
            {
                RUNTIME_MODE_ENV: "development",
                SYNTHETIC_FIXTURE_OPT_IN_ENV: "true",
            }
        )


def test_ordinary_retriever_still_uses_deny_all(monkeypatch):
    monkeypatch.delenv(RUNTIME_MODE_ENV, raising=False)
    monkeypatch.delenv(SYNTHETIC_FIXTURE_OPT_IN_ENV, raising=False)

    retriever = LocalCorpusRetriever((SYNTHETIC_SOURCE_ID,))

    assert isinstance(retriever._source_read_boundary, DenyAllSourceReadBoundary)
