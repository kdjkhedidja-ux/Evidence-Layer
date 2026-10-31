#!/usr/bin/env python3
"""Build the Phase 6 targeted extension evaluation set.

The base 100-case set (``build_dataset.py``) is the frozen baseline and is
never modified. This module adds 51 cases that target the measured gaps in
the base set:

* single-publisher coverage (the base corpus is one publisher's docs),
* no crafted near-tie candidates (hard negatives with subtly wrong values),
* binary-only relevance (adds graded partial relevance),
* at most 2 gold passages per case (adds 3-gold coverage cases),
* short single-clause queries (adds compound and context-dependent queries).

All extension passages live in ``data/corpus/extension_passages.jsonl`` —
original synthetic content across three fictional publishers — so extension
gold passages are disjoint from the base set by construction, and the base
split (SPLIT_SEED=2, Phase 5B held-out membership) is untouched.

The extension splits itself with the same component-disjoint algorithm
(``build_dataset.stratified_split``) under its own pinned seed. Outputs go to
``data/extension_dev/cases.jsonl`` and ``data/extension_test/cases.jsonl``
(regenerable, gitignored like the base splits).
"""

from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_dataset  # noqa: E402

EXTENSION_CORPUS_PATH = ROOT / "data" / "corpus" / "extension_passages.jsonl"
DEV_OUT = ROOT / "data" / "extension_dev" / "cases.jsonl"
TEST_OUT = ROOT / "data" / "extension_test" / "cases.jsonl"

TOTAL_CANDIDATES_PER_CASE = 5
DEV_FRACTION = 0.8
#: Independent pin for the extension split. Like the base SPLIT_SEED, this is
#: a compatibility seed: it reproduces this extension's held-out membership,
#: and changing it changes the extension split.
EXTENSION_SPLIT_SEED = 20261031

#: Relevance grades used by partial_relevance cases.
FULL = 2
PARTIAL = 1
NONE = 0


def X(
    type_: str,
    q: str,
    gold: list[str],
    facts: list[str],
    *,
    hard: list[str] | None = None,
    partial: list[str] | None = None,
    domain: str,
) -> dict:
    """One extension spec. ``hard`` lists crafted near-tie distractor ids;
    ``partial`` lists partially-relevant candidate ids (grade 1)."""
    spec = {
        "type": type_,
        "q": q,
        "gold": gold,
        "facts": facts,
        "domain": domain,
    }
    if hard:
        spec["hard"] = hard
    if partial:
        spec["partial"] = partial
    return spec


MRN = "operations_runbook"
SP = "protocol_specification"
CT = "cloud_storage_guide"
MIX = "mixed_technical_documentation"

SPECS_X: list[dict] = [
    # ------------------------------------------------------------------
    # hard_negative (16): gold vs a crafted near-tie with a subtly wrong value
    # ------------------------------------------------------------------
    X("hard_negative", "What is the default worker execution timeout per job attempt in Meridian?",
      ["mrn_timeout_worker"], ["30 seconds"], hard=["hn_mrn_timeout_legacy", "mrn_timeout_batch"], domain=MRN),
    X("hard_negative", "How many jobs does a Meridian batch accept in legacy compatibility mode?",
      ["mrn_limits_legacy"], ["2000 jobs"], hard=["hn_mrn_limits_legacy_bulk", "mrn_limits_batch"], domain=MRN),
    X("hard_negative", "How long are dead-letter entries retained in Meridian?",
      ["mrn_retry_dlq"], ["14 days"], hard=["hn_mrn_dlq_archive", "mrn_auth_mtls"], domain=MRN),
    X("hard_negative", "What is the maximum size of a single Meridian job payload?",
      ["mrn_limits_payload"], ["256 KiB"], hard=["hn_mrn_payload_bulk", "mrn_limits_batch"], domain=MRN),
    X("hard_negative", "How many batches per minute may a Meridian workspace submit?",
      ["mrn_limits_rate"], ["50 batches per minute"], hard=["hn_mrn_rate_operator", "mrn_limits_legacy"], domain=MRN),
    X("hard_negative", "What is the initial flow-control window size in StreamProto?",
      ["sp_flow_window"], ["65535 octets"], hard=["hn_sp_flow_extended", "sp_frames_length"], domain=SP),
    X("hard_negative", "What is the default maximum frame payload size in StreamProto 2.1?",
      ["sp_frames_length"], ["16383 octets"], hard=["hn_sp_frame_compat", "sp_flow_window"], domain=SP),
    X("hard_negative", "How many concurrent streams does StreamProto allow per connection by default?",
      ["sp_limits_streams"], ["100"], hard=["hn_sp_streams_dev", "sp_priority_weights"], domain=SP),
    X("hard_negative", "How long is a StreamProto frame header?",
      ["sp_frames_header"], ["9 bytes"], hard=["hn_sp_header_trailer", "sp_frames_length"], domain=SP),
    X("hard_negative", "Which StreamProto frame signals a connection-level error?",
      ["sp_errors_goaway"], ["GOAWAY"], hard=["hn_sp_errors_drain", "sp_errors_rst"], domain=SP),
    X("hard_negative", "What minimum storage duration does CloudTier Cool storage bill?",
      ["ct_classes_cool"], ["30 days"], hard=["hn_ct_cool_archive_min", "ct_classes_archive"], domain=CT),
    X("hard_negative", "How long does a CloudTier Archive restore request take?",
      ["ct_classes_archive"], ["3 to 5 hours"], hard=["hn_ct_archive_expedited", "ct_lifecycle_eval"], domain=CT),
    X("hard_negative", "How many buckets does a CloudTier account start with?",
      ["ct_quota_buckets"], ["1000 buckets"], hard=["hn_ct_quota_legacy", "ct_quota_requests"], domain=CT),
    X("hard_negative", "What read throughput can each CloudTier bucket sustain by default?",
      ["ct_quota_requests"], ["5000 read requests per second"], hard=["hn_ct_requests_partitioned", "ct_quota_buckets"], domain=CT),
    X("hard_negative", "What weight range do StreamProto streams carry?",
      ["sp_priority_weights"], ["1 to 256"], hard=["hn_sp_priority_extended", "sp_limits_streams"], domain=SP),
    X("hard_negative", "How often do Meridian service tokens rotate?",
      ["mrn_auth_mtls"], ["every 24 hours"], hard=["hn_mrn_token_breakglass", "mrn_retry_dlq"], domain=MRN),

    # ------------------------------------------------------------------
    # partial_relevance (10): one full answer (grade 2) + one partial (grade 1)
    # ------------------------------------------------------------------
    X("partial_relevance", "How do Meridian workers authenticate, and how often do service tokens rotate?",
      ["mrn_auth_mtls"], ["mutual TLS required", "tokens rotate every 24 hours"],
      partial=["mrn_auth_tokens"], domain=MRN),
    X("partial_relevance", "What happens when a Meridian job keeps failing?",
      ["mrn_retry_dlq"], ["moves to the dead-letter queue", "retained for 14 days"],
      partial=["mrn_retry_default"], domain=MRN),
    X("partial_relevance", "How does StreamProto control how much data a sender may transmit?",
      ["sp_flow_window"], ["flow-control window", "65535 octets initial", "WINDOW_UPDATE"],
      partial=["sp_flow_zero"], domain=SP),
    X("partial_relevance", "How are StreamProto streams prioritized relative to each other?",
      ["sp_priority_weights"], ["weights 1 to 256", "bandwidth in proportion to weights"],
      partial=["sp_priority_tree"], domain=SP),
    X("partial_relevance", "What TLS configuration does StreamProto 2.1 require?",
      ["sp_tls_requirement"], ["TLS 1.3", "ALPN token sp2"],
      partial=["sp_tls_alpn_legacy"], domain=SP),
    X("partial_relevance", "How does CloudTier bill short-lived objects stored in the Cool class?",
      ["ct_classes_cool"], ["minimum 30 days billed", "early deletion still charges 30 days"],
      partial=["ct_lifecycle_size"], domain=CT),
    X("partial_relevance", "Can I read a CloudTier Archive object online, and what do other classes cost to read?",
      ["ct_classes_archive"], ["not readable online", "restore takes 3 to 5 hours"],
      partial=["ct_classes_hot"], domain=CT),
    X("partial_relevance", "Is a CloudTier bucket listing guaranteed to show a just-written object?",
      ["ct_consistency_list"], ["listing is strongly consistent since version 4"],
      partial=["ct_consistency_writes"], domain=CT),
    X("partial_relevance", "How do I permanently delete a specific CloudTier object version safely?",
      ["ct_versioning_mfa"], ["MFA delete requires a one-time code"],
      partial=["ct_versioning_list"], domain=CT),
    X("partial_relevance", "Does CloudTier replication copy object deletions to the replica region?",
      ["ct_replication_delete"], ["delete markers replicate only when explicitly enabled", "version-specific deletes never replicate"],
      partial=["ct_replication_crr"], domain=CT),

    # ------------------------------------------------------------------
    # coverage (10): the full answer needs 3 gold passages
    # ------------------------------------------------------------------
    X("coverage", "Describe Meridian's retry behavior end to end: attempt timeouts, retry count and backoff, and what happens after exhaustion.",
      ["mrn_timeout_worker", "mrn_retry_default", "mrn_retry_dlq"],
      ["30 seconds per attempt", "3 retries with exponential backoff from 2 to 60 seconds", "dead-letter queue retained 14 days"],
      domain=MRN),
    X("coverage", "What limits apply when submitting a large Meridian batch?",
      ["mrn_limits_batch", "mrn_limits_rate", "mrn_limits_payload"],
      ["500 jobs per batch", "50 batches per minute", "256 KiB per job payload"],
      domain=MRN),
    X("coverage", "What should a Meridian operator verify before a production upgrade: authentication, rollback safety, and observability?",
      ["mrn_auth_mtls", "mrn_upgrade_rollback", "mrn_metrics_prom"],
      ["mutual TLS with rotating tokens", "rollback only before the schema migration", "Prometheus metrics with cardinality cap"],
      domain=MRN),
    X("coverage", "Which StreamProto settings govern how much data a connection can carry concurrently?",
      ["sp_flow_window", "sp_limits_streams", "sp_frames_length"],
      ["65535-octet initial window", "100 concurrent streams by default", "16383-octet default frame limit"],
      domain=SP),
    X("coverage", "What changed between StreamProto 2.0 and 2.1?",
      ["sp_version_21", "sp_version_push", "sp_tls_requirement"],
      ["PRIORITY_UPDATE added", "server push removed", "TLS 1.3 required"],
      domain=SP),
    X("coverage", "How does CloudTier manage object lifecycles, and what constraints apply?",
      ["ct_lifecycle_eval", "ct_lifecycle_size", "ct_classes_cool"],
      ["rules evaluated once per day", "objects under 128 KiB never transition", "Cool bills a 30-day minimum"],
      domain=CT),
    X("coverage", "What data-protection features does CloudTier provide?",
      ["ct_encryption_kms", "ct_versioning_mfa", "ct_replication_crr"],
      ["server-side encryption by default", "MFA delete for versions", "cross-region replication with optional 15-minute SLA"],
      domain=CT),
    X("coverage", "Compare CloudTier storage classes for cost-sensitive workloads.",
      ["ct_classes_hot", "ct_classes_cool", "ct_classes_archive"],
      ["Hot has no minimum duration or retrieval fee", "Cool bills a 30-day minimum", "Archive requires a 3 to 5 hour restore"],
      domain=CT),
    X("coverage", "What must an operator know before upgrading Meridian from 2026.1 to 2026.2?",
      ["mrn_upgrade_schema", "mrn_upgrade_rollback", "mrn_region_auto"],
      ["schema migration with about two minutes of write downtime", "rollback unsupported after migration", "2026.2 makes failover automatic"],
      domain=MRN),
    X("coverage", "How does StreamProto signal and scope errors?",
      ["sp_errors_goaway", "sp_errors_rst", "sp_flow_zero"],
      ["GOAWAY for connection errors with last stream id", "RST_STREAM terminates one stream only", "sending on a zero window is a connection error"],
      domain=SP),

    # ------------------------------------------------------------------
    # compound_query (10): multi-constraint questions over 1-2 golds
    # ------------------------------------------------------------------
    X("compound_query", "If my Meridian batch has 600 jobs with 300 KiB payloads, which limits do I violate and what are the exact caps?",
      ["mrn_limits_batch", "mrn_limits_payload"],
      ["500-job batch cap", "256 KiB payload cap"],
      domain=MRN),
    X("compound_query", "A StreamProto client needs to reprioritize an active stream and also use server push; which protocol versions support each?",
      ["sp_version_21", "sp_version_push"],
      ["2.1 adds PRIORITY_UPDATE", "server push exists in 2.0 but was removed in 2.1"],
      domain=SP),
    X("compound_query", "Which CloudTier class fits objects read twice a week but deleted after 10 days, and why?",
      ["ct_classes_hot", "ct_classes_cool"],
      ["Hot has no minimum duration", "Cool would still bill 30 days"],
      domain=CT),
    X("compound_query", "Meridian job attempts keep dying around 45 seconds; which timeout governs attempts and what does the retry policy then do?",
      ["mrn_timeout_worker", "mrn_retry_default"],
      ["default worker timeout is 30 seconds per attempt", "up to 3 retries with exponential backoff"],
      domain=MRN),
    X("compound_query", "How long would an urgent read of an archived CloudTier object take, including when lifecycle rules next run?",
      ["ct_classes_archive", "ct_lifecycle_eval"],
      ["restore takes 3 to 5 hours", "lifecycle rules evaluate once per day"],
      domain=CT),
    X("compound_query", "Which ALPN tokens can a dual-stack StreamProto deployment offer, and which framing does each negotiate?",
      ["sp_tls_requirement", "sp_tls_alpn_legacy"],
      ["sp2 negotiates 2.1", "sp1 negotiates 2.0 framing"],
      domain=SP),
    X("compound_query", "If a Meridian worker loses its service token, can it still connect, and what else does a connection require?",
      ["mrn_auth_mtls", "mrn_auth_tokens"],
      ["bearer tokens alone are rejected", "mutual TLS client certificate is required"],
      domain=MRN),
    X("compound_query", "Which CloudTier consistency guarantees hold when I PUT an object and immediately list the bucket on version 4?",
      ["ct_consistency_writes", "ct_consistency_list"],
      ["strong read-after-write for PUTs", "strongly consistent listing since version 4"],
      domain=CT),
    X("compound_query", "How does StreamProto share bandwidth among sibling streams, and what can block a stream?",
      ["sp_priority_weights", "sp_priority_tree"],
      ["bandwidth proportional to weights", "blocked while any ancestor is blocked"],
      domain=SP),
    X("compound_query", "If I enable delete-marker replication in CloudTier, does deleting a specific object version also replicate, and what does replicate?",
      ["ct_replication_delete"],
      ["only delete markers replicate when enabled", "version-specific deletes never replicate"],
      hard=["ct_replication_crr"], domain=CT),

    # ------------------------------------------------------------------
    # context_dependent (5): the correct answer depends on version context
    # ------------------------------------------------------------------
    X("context_dependent", "In Meridian 2026.2, is regional failover manual or automatic?",
      ["mrn_region_auto"], ["automatic after 90 seconds of failed health checks"],
      hard=["mrn_region_failover"], domain=MRN),
    X("context_dependent", "In Meridian 2026.1, is regional failover manual or automatic?",
      ["mrn_region_failover"], ["manual operator runbook step"],
      hard=["mrn_region_auto"], domain=MRN),
    X("context_dependent", "Under StreamProto 2.1, how do I reprioritize a stream that is already running?",
      ["sp_version_21"], ["send a PRIORITY_UPDATE frame"],
      hard=["sp_version_push"], domain=SP),
    X("context_dependent", "Under StreamProto 2.0, can I use PRIORITY_UPDATE to reprioritize a running stream?",
      ["sp_version_21"], ["2.0 has no PRIORITY_UPDATE; abort and reopen instead"],
      hard=["sp_priority_weights"], domain=SP),
    X("context_dependent", "On CloudTier version 4, are bucket listings strongly consistent, and did earlier versions behave the same?",
      ["ct_consistency_list"], ["strongly consistent since version 4", "earlier versions were eventually consistent"],
      hard=["ct_consistency_writes"], domain=CT),
]

assert len(SPECS_X) == 51, f"extension spec count drifted: {len(SPECS_X)}"


def load_extension_corpus() -> list[dict]:
    return [
        json.loads(line)
        for line in EXTENSION_CORPUS_PATH.read_text().splitlines()
        if line.strip()
    ]


def publisher_of(passage_id: str) -> str:
    # mrn_* -> opsrunbook, sp_* -> streamspec, ct_* -> cloudtier
    return passage_id.split("_", 1)[0]


def pick_extension_fillers(
    taken: set[str], corpus: list[dict], n: int, prefer_publisher: str
) -> list[dict]:
    """Fill remaining candidate slots, preferring the gold's publisher."""
    same = [p for p in corpus if p["passage_id"] not in taken and publisher_of(p["passage_id"]) == prefer_publisher]
    other = [p for p in corpus if p["passage_id"] not in taken and publisher_of(p["passage_id"]) != prefer_publisher]
    return (same + other)[:n]


def build_extension_case(spec: dict, corpus_map: dict[str, dict], corpus: list[dict], idx: int) -> dict:
    case_id = f"xcase_{idx:04d}"
    gold_ids = list(spec["gold"])
    hard_ids = list(spec.get("hard", []))
    partial_ids = list(spec.get("partial", []))

    candidates = [build_dataset.passage_to_candidate(corpus_map[g]) for g in gold_ids]
    candidates += [build_dataset.passage_to_candidate(corpus_map[h]) for h in hard_ids]
    candidates += [build_dataset.passage_to_candidate(corpus_map[p]) for p in partial_ids]

    taken = set(gold_ids) | set(hard_ids) | set(partial_ids)
    fillers = pick_extension_fillers(
        taken, corpus, TOTAL_CANDIDATES_PER_CASE - len(candidates), publisher_of(gold_ids[0])
    )
    candidates += [build_dataset.passage_to_candidate(p) for p in fillers]
    if len(candidates) != TOTAL_CANDIDATES_PER_CASE:
        raise ValueError(f"{case_id}: expected {TOTAL_CANDIDATES_PER_CASE} candidates")

    relevance_scores: dict[str, int] = {}
    if spec["type"] == "partial_relevance":
        relevance_scores = {cid: NONE for cid in taken}
        for gold in gold_ids:
            relevance_scores[gold] = FULL
        for partial in partial_ids:
            relevance_scores[partial] = PARTIAL

    random.Random(20261031 + idx).shuffle(candidates)

    task = {
        "task_id": f"xtask_{idx:04d}",
        "task_type": build_dataset.TASK_TYPE,
        "domain": spec["domain"],
        "input": spec["q"],
        "language": build_dataset.LANGUAGE,
        "metadata": {"case_type": spec["type"], "corpus": "extension/2026-10"},
    }
    case = {
        "case_id": case_id,
        "case_type": spec["type"],
        "task": task,
        "candidates": candidates,
        "gold_candidate_ids": gold_ids,
        "required_facts": spec["facts"],
        "source_urls": [corpus_map[g]["source_url"] for g in gold_ids],
        "untrusted_candidate_ids": [],
        "relevance_scores": relevance_scores,
    }
    return case


def build_extension_cases() -> tuple[list[dict], list[dict]]:
    corpus = load_extension_corpus()
    corpus_map = {p["passage_id"]: p for p in corpus}
    known = set(corpus_map)
    for spec in SPECS_X:
        unknown = [pid for pid in spec["gold"] + spec.get("hard", []) + spec.get("partial", []) if pid not in known]
        if unknown:
            raise ValueError(f"Unknown passage id(s) {unknown} in extension spec: {spec['q']}")
    cases = [build_extension_case(spec, corpus_map, corpus, i) for i, spec in enumerate(SPECS_X)]
    return build_dataset.stratified_split(cases, DEV_FRACTION, EXTENSION_SPLIT_SEED)


def main() -> None:
    dev, test = build_extension_cases()
    DEV_OUT.parent.mkdir(parents=True, exist_ok=True)
    TEST_OUT.parent.mkdir(parents=True, exist_ok=True)
    DEV_OUT.write_text("\n".join(json.dumps(c) for c in dev) + "\n")
    TEST_OUT.write_text("\n".join(json.dumps(c) for c in test) + "\n")
    print(f"wrote {len(dev)} extension dev cases -> {DEV_OUT}")
    print(f"wrote {len(test)} extension test cases -> {TEST_OUT}")


if __name__ == "__main__":
    main()
