"""JEV parser and fail-closed boundary tests; clients are always fake."""

from __future__ import annotations

import csv
import hashlib
import io
import os
from types import SimpleNamespace

import pytest

from evidence_layer.jev_benchmark import parse_benchmark_config
from evidence_layer.scoring.base import Scorer
from evidence_layer.scoring.jev import (
    DEFAULT_JEV_MODEL,
    JevEvidenceScorer,
    JevResponseError,
    JevTransferDeniedError,
    RetryPolicy,
    _extract_score,
    _issue_real_world_benchmark_permit,
    render_benchmark_csv,
    write_benchmark_csv,
)
from evidence_layer.scoring import jev as jev_module
from evidence_layer.scoring.jev_request import (
    JevRequestCandidate,
    JevRequestScorer,
    _issue_jev_request_transfer_permit,
)


class FakeClient:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or SimpleNamespace(
            nouls={"candidate_0": SimpleNamespace(noul=0.625)},
            model="jev-latest",
            usage=SimpleNamespace(input_tokens=11, output_tokens=4),
        )

    def system_one(self, state, questions, **kwargs):
        self.calls.append((state, questions, kwargs))
        return self.response


def make_scorer(client: FakeClient | None = None) -> JevEvidenceScorer:
    return JevEvidenceScorer(
        client=client,
        noul_factory=lambda instructions: object(),
        sdk_retry_policy=SimpleNamespace(max_retries=0),
    )


def make_full_real_world_case_set():
    splits = {"dev": 22, "validation": 10, "exposed_test": 8}
    return {
        split: [
            {
                "case_id": f"{split}_{index:02d}",
                "task": {"input": f"query {split} {index}"},
                "candidates": [
                    {
                        "candidate_id": f"{split}_{index:02d}_c{candidate_index}",
                        "text": f"candidate {split} {index} {candidate_index}",
                    }
                    for candidate_index in range(5)
                ],
            }
            for index in range(count)
        ]
        for split, count in splits.items()
    }


def make_permit(cases_by_split=None):
    cases = cases_by_split or make_full_real_world_case_set()
    return _issue_real_world_benchmark_permit(
        cases,
        run_id="rw-test-run",
        input_sha256=hashlib.sha256(b"test inputs").hexdigest(),
    )


@pytest.mark.parametrize("value", [0, 0.625, 1, "0.625", "1e-1"])
def test_score_parser_accepts_valid_scores_without_invoking_provider(value):
    response = SimpleNamespace(
        nouls={"candidate_0": SimpleNamespace(noul=value)}
    )
    assert _extract_score(response, "candidate_0") == float(value)


@pytest.mark.parametrize(
    "value", [-0.01, 1.01, float("nan"), float("inf"), True, "not-a-score"]
)
def test_score_parser_rejects_invalid_scores_without_provider_access(value):
    response = SimpleNamespace(
        nouls={"candidate_0": SimpleNamespace(noul=value)}
    )
    with pytest.raises(JevResponseError) as error:
        _extract_score(response, "candidate_0")
    assert str(error.value) == "The Jev provider returned an invalid relevance response."


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(),
        SimpleNamespace(nouls={}),
        SimpleNamespace(nouls={"candidate_0": object()}),
        SimpleNamespace(nouls=None),
    ],
)
def test_score_parser_rejects_missing_or_malformed_results(response):
    with pytest.raises(JevResponseError):
        _extract_score(response, "candidate_0")


def test_direct_scorer_denies_before_using_credentials_sdk_or_injected_client(
    monkeypatch,
):
    query = "PRIVATE_QUERY_TEXT"
    candidate = "PRIVATE_CANDIDATE_TEXT"
    secret = "SYNTHETIC_API_SECRET"
    client = FakeClient()
    scorer = make_scorer(client)
    monkeypatch.setenv("TYPESAFE_API_KEY", secret)

    def unexpected_resolve():
        raise AssertionError("credential resolution must not occur on denial")

    monkeypatch.setattr(scorer, "_resolve_client", unexpected_resolve)
    with pytest.raises(JevTransferDeniedError) as error:
        scorer.score(query, candidate)

    assert "benchmark authorization" in str(error.value)
    assert scorer.last_record is not None
    assert scorer.last_record.failure_category == "transfer_denied"
    serialized = repr(scorer.last_record.to_dict())
    for sensitive in (query, candidate, secret):
        assert sensitive not in str(error.value)
        assert sensitive not in serialized
    assert client.calls == []


def test_request_scorer_requires_both_server_gate_and_explicit_request_consent():
    candidates = [JevRequestCandidate("record:0:10", "source-1", "candidate text")]
    for server_enabled, permitted_by_request in (
        (False, True),
        (True, False),
        (False, False),
    ):
        with pytest.raises(JevTransferDeniedError):
            _issue_jev_request_transfer_permit(
                request_id="request-1",
                principal_id="principal-1",
                tenant_id="tenant-1",
                query="query",
                candidates=candidates,
                server_enabled=server_enabled,
                permitted_by_request=permitted_by_request,
            )


def test_request_scorer_uses_exact_one_use_batch_with_sdk_retries_disabled():
    query = "task query"
    candidates = [
        JevRequestCandidate("record:0:10", "source-1", "first candidate"),
        JevRequestCandidate("record:10:20", "source-2", "second candidate"),
    ]
    permit = _issue_jev_request_transfer_permit(
        request_id="request-1",
        principal_id="principal-1",
        tenant_id="tenant-1",
        query=query,
        candidates=candidates,
        server_enabled=True,
        permitted_by_request=True,
    )
    client = FakeClient(SimpleNamespace(
        nouls={
            item.candidate_id: SimpleNamespace(noul=0.625)
            for item in candidates
        },
        model="jev-latest",
        usage=SimpleNamespace(input_tokens=22, output_tokens=8),
    ))
    scorer = JevRequestScorer(
        client=client,
        noul_factory=lambda instructions: object(),
        sdk_retry_policy=SimpleNamespace(max_retries=0),
    )

    scores = scorer.score_candidates(query, candidates, transfer_permit=permit)

    assert scores == (0.625, 0.625)
    assert len(client.calls) == 1
    assert client.calls[0][0]["candidates"] == {
        item.candidate_id: item.text for item in candidates
    }
    assert set(client.calls[0][1]) == {item.candidate_id for item in candidates}
    assert client.calls[0][2]["retry"].max_retries == 0
    with pytest.raises(JevTransferDeniedError):
        scorer.score_candidates(query, candidates, transfer_permit=permit)


def test_request_permit_rejects_changed_query_or_candidate_before_provider_call():
    query = "task query"
    candidates = [JevRequestCandidate("record:0:10", "source-1", "candidate text")]
    permit = _issue_jev_request_transfer_permit(
        request_id="request-1",
        principal_id="principal-1",
        tenant_id="tenant-1",
        query=query,
        candidates=candidates,
        server_enabled=True,
        permitted_by_request=True,
    )
    client = FakeClient()
    scorer = JevRequestScorer(
        client=client,
        noul_factory=lambda instructions: object(),
        sdk_retry_policy=SimpleNamespace(max_retries=0),
    )

    with pytest.raises(JevTransferDeniedError):
        scorer.score_candidates(
            query + " changed", candidates, transfer_permit=permit
        )
    changed_candidate = [
        JevRequestCandidate("record:0:10", "source-1", "changed candidate text")
    ]
    with pytest.raises(JevTransferDeniedError):
        scorer.score_candidates(
            query, changed_candidate, transfer_permit=permit
        )
    assert client.calls == []


def test_request_scorer_rejects_incomplete_candidate_score_batch():
    query = "task query"
    candidates = [
        JevRequestCandidate("record:0:10", "source-1", "first candidate"),
        JevRequestCandidate("record:10:20", "source-2", "second candidate"),
    ]
    permit = _issue_jev_request_transfer_permit(
        request_id="request-1",
        principal_id="principal-1",
        tenant_id="tenant-1",
        query=query,
        candidates=candidates,
        server_enabled=True,
        permitted_by_request=True,
    )
    client = FakeClient(
        SimpleNamespace(nouls={"record:0:10": SimpleNamespace(noul=0.75)})
    )
    scorer = JevRequestScorer(
        client=client,
        noul_factory=lambda instructions: object(),
        sdk_retry_policy=SimpleNamespace(max_retries=0),
    )

    with pytest.raises(JevResponseError):
        scorer.score_candidates(query, candidates, transfer_permit=permit)
    assert len(client.calls) == 1


def test_request_transfer_permit_caps_each_batch_at_fifty_candidates():
    candidates = [
        JevRequestCandidate(f"record-{index}", "source-1", f"candidate {index}")
        for index in range(51)
    ]
    with pytest.raises(JevTransferDeniedError):
        _issue_jev_request_transfer_permit(
            request_id="request-1",
            principal_id="principal-1",
            tenant_id="tenant-1",
            query="query",
            candidates=candidates,
            server_enabled=True,
            permitted_by_request=True,
        )


def test_default_scorer_denies_without_reading_environment_or_importing_sdk(
    monkeypatch,
):
    scorer = JevEvidenceScorer()

    def unexpected_get(*args, **kwargs):
        raise AssertionError("environment must not be read on denial")

    def unexpected_import(*args, **kwargs):
        raise AssertionError("SDK must not be imported on denial")

    monkeypatch.setattr(os.environ, "get", unexpected_get)
    monkeypatch.setattr(jev_module.importlib, "import_module", unexpected_import)
    with pytest.raises(JevTransferDeniedError):
        scorer.score("query", "candidate")


def test_client_resolution_is_disabled_even_with_credentials_and_injected_client(
    monkeypatch,
):
    client = FakeClient()
    scorer = make_scorer(client)
    monkeypatch.setenv("TYPESAFE_API_KEY", "SYNTHETIC_API_SECRET")
    monkeypatch.setattr(
        jev_module,
        "_load_sdk",
        lambda: (_ for _ in ()).throw(AssertionError("SDK must remain unused")),
    )

    with pytest.raises(JevTransferDeniedError):
        scorer._resolve_client("query", "candidate")
    assert client.calls == []


def test_final_system_one_guard_rejects_a_mismatched_exact_benchmark_scope():
    client = FakeClient()
    cases = make_full_real_world_case_set()
    case = cases["dev"][0]
    candidate = case["candidates"][0]
    permit = make_permit(cases)
    with pytest.raises(JevTransferDeniedError):
        JevEvidenceScorer._system_one(
            client,
            {"query": "PRIVATE_QUERY"},
            {"candidate_0": "PRIVATE_PAYLOAD"},
            model=DEFAULT_JEV_MODEL,
            retry=None,
            timeout=1.0,
            transfer_permit=permit,
            split="dev",
            case_id=case["case_id"],
            candidate_id=candidate["candidate_id"],
            query=case["task"]["input"],
            text="different candidate text",
            sdk_retry=SimpleNamespace(max_retries=0),
        )
    assert client.calls == []


def test_valid_permit_scores_exact_candidate_once_and_records_safe_usage():
    cases = make_full_real_world_case_set()
    case = cases["dev"][0]
    candidate = case["candidates"][0]
    permit = make_permit(cases)
    client = FakeClient()
    scorer = JevEvidenceScorer(
        client=client,
        noul_factory=lambda instructions: SimpleNamespace(instructions=instructions),
        split="dev",
        case_id=case["case_id"],
        candidate_id=candidate["candidate_id"],
        transfer_permit=permit,
        sdk_retry_policy=SimpleNamespace(max_retries=0),
    )

    assert scorer.score(case["task"]["input"], candidate["text"]) == 0.625
    assert scorer.last_record is not None
    assert scorer.last_record.candidate_id == candidate["candidate_id"]
    assert scorer.last_record.input_tokens == 11
    assert scorer.last_record.retry_count == 0
    assert scorer.last_record.estimated_input_cost_usd is None
    assert permit.remaining_calls == 199
    assert len(client.calls) == 1
    assert client.calls[0][0] == {
        "query": case["task"]["input"],
        "candidates": {"candidate_0": candidate["text"]},
    }
    assert "Return only its relevance score from 0 to 1." in client.calls[0][1]["candidate_0"].instructions
    assert client.calls[0][2]["retry"].max_retries == 0

    with pytest.raises(JevTransferDeniedError):
        scorer.score(case["task"]["input"], candidate["text"])
    assert len(client.calls) == 1


def test_permit_issuer_requires_every_case_and_candidate():
    cases = make_full_real_world_case_set()
    cases["exposed_test"].pop()
    with pytest.raises(ValueError, match="complete Real-World v1"):
        make_permit(cases)


def test_caller_flags_and_attached_scorers_are_not_transfer_authorization():
    class InjectedScorer(Scorer):
        def __init__(self):
            self.calls = []

        def score(self, query, text):
            self.calls.append((query, text))
            return 1.0

    injected = InjectedScorer()
    from evidence_layer.scoring.policy import build_provider_selection_policy

    policy = build_provider_selection_policy(
        jev_enabled=True,
        jev_permitted_by_request=True,
        jev_permitted_by_plan=True,
        jev_scorer=injected,
    )
    outcome = policy.score("query", ["candidate"])

    assert outcome.provider == "lexical"
    assert outcome.fallback_reason is not None
    assert outcome.fallback_reason.category == "transfer_denied"
    assert injected.calls == []


def test_service_provider_policy_remains_lexical_even_with_benchmark_permit():
    from evidence_layer.scoring.policy import build_provider_selection_policy

    cases = make_full_real_world_case_set()
    case = cases["dev"][0]
    candidate = case["candidates"][0]
    client = FakeClient()
    scorer = JevEvidenceScorer(
        client=client,
        noul_factory=lambda instructions: object(),
        split="dev",
        case_id=case["case_id"],
        candidate_id=candidate["candidate_id"],
        transfer_permit=make_permit(cases),
        sdk_retry_policy=SimpleNamespace(max_retries=0),
    )
    policy = build_provider_selection_policy(
        jev_enabled=True,
        jev_permitted_by_request=True,
        jev_permitted_by_plan=True,
        jev_scorer=scorer,
    )

    outcome = policy.score(case["task"]["input"], [candidate["text"]])

    assert outcome.provider == "lexical"
    assert policy.jev_selected is False
    assert client.calls == []


def test_retry_policy_is_configuration_only_while_transfer_is_disabled():
    retry_policy = RetryPolicy(max_retries=2, backoff_seconds=0)
    client = FakeClient()
    scorer = JevEvidenceScorer(
        client=client,
        retry_policy=retry_policy,
        sdk_retry_policy=SimpleNamespace(max_retries=0),
    )

    with pytest.raises(JevTransferDeniedError):
        scorer.score("query", "candidate")
    assert client.calls == []
    assert scorer.last_record is not None
    assert scorer.last_record.retry_count is None


def test_denial_audit_record_and_csv_contain_no_sensitive_payload():
    query = "PRIVATE_QUERY_TEXT"
    candidate = "PRIVATE_CANDIDATE_TEXT"
    secret = "SYNTHETIC_API_SECRET"
    scorer = make_scorer(FakeClient())

    with pytest.raises(JevTransferDeniedError):
        scorer.score(query, candidate)
    assert scorer.last_record is not None

    rendered = render_benchmark_csv([scorer.last_record])
    rows = list(csv.DictReader(io.StringIO(rendered)))
    assert len(rows) == 1
    assert "query" not in rows[0]
    assert "text" not in rows[0]
    for sensitive in (query, candidate, secret):
        assert sensitive not in rendered


def test_report_writer_rejects_paths_outside_reports():
    with pytest.raises(ValueError):
        write_benchmark_csv([], "../outside.csv")


def test_benchmark_configuration_defaults_and_test_split_gate():
    config = parse_benchmark_config([])

    assert config.split == "dev"
    assert config.allow_test is False
    assert config.max_cases == 10
    assert config.max_candidates_per_case == 5
    assert config.model == DEFAULT_JEV_MODEL
    assert config.timeout == 10.0
    assert config.retry_policy.max_retries == 2
    with pytest.raises(SystemExit):
        parse_benchmark_config(["--split", "test"])
    assert parse_benchmark_config(["--split", "test", "--allow-test"]).split == "test"
