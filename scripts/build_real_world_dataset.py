#!/usr/bin/env python3
"""Build the Phase 6 real-world benchmark v1.

An independent benchmark of real, licensed documents — never mixed with the
PostgreSQL baseline or the synthetic extension:

* Passages: verbatim excerpts and paraphrases from four documents by three
  publishers (IETF RFCs 7515 and 7519, Supabase docs, PostgreSQL RLS
  documentation), tracked in ``data/real_world/passages.jsonl`` with
  per-passage source URLs, sections, quote status, and license metadata.
  Licensing is documented in ``data/real_world/NOTICE.md``.
* Queries: 40 cases authored to represent realistic technical needs — direct,
  multi-source (including cross-publisher), ambiguous, near-answer (several
  documents answer similarly), and 3-gold coverage.
* Relevance judgments: graded 0/1/2 for **every** candidate in every case,
  developer-authored and pending independent blind review (recorded in the
  manifest). The current held-out membership and lexical results are exposed;
  this split must not be used as a blind future JEV test.
* Split: three disjoint sets — development (60%), validation (20%), held-out
  test (20%) — with the same gold-passage component-disjoint discipline as the
  other benchmarks, under its own pinned seed.

Outputs (regenerable, gitignored): ``data/real_world_dev/cases.jsonl``,
``data/real_world_validation/cases.jsonl``, ``data/real_world_test/cases.jsonl``.
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

CORPUS_PATH = ROOT / "data" / "real_world" / "passages.jsonl"
DEV_OUT = ROOT / "data" / "real_world_dev" / "cases.jsonl"
VALIDATION_OUT = ROOT / "data" / "real_world_validation" / "cases.jsonl"
TEST_OUT = ROOT / "data" / "real_world_test" / "cases.jsonl"

TOTAL_CANDIDATES_PER_CASE = 5
#: Independent pin for the real-world three-way split; changing it changes
#: the split membership.
REAL_WORLD_SPLIT_SEED = 20261101
DEV_FRACTION = 0.6
VALIDATION_FRACTION = 0.2

FULL, PARTIAL, NONE = 2, 1, 0

JWT = "jwt_rfc7519"
SB = "supabase_docs"
PG = "postgresql_rls_docs"
MIXED = "mixed_technical_documentation"


def R(
    type_: str,
    q: str,
    gold: list[str],
    facts: list[str],
    *,
    partial: list[str] | None = None,
    domain: str,
) -> dict:
    spec = {
        "type": type_,
        "q": q,
        "gold": gold,
        "facts": facts,
        "domain": domain,
    }
    if partial:
        spec["partial"] = partial
    return spec


SPECS_RW: list[dict] = [
    # ---------------- direct_factual (12) ----------------
    R("direct_factual", "What does the exp claim in a JWT mean?",
      ["jwt_exp_claim"], ["expiration time after which the JWT must not be accepted"],
      partial=["jwt_exp_leeway"], domain=JWT),
    R("direct_factual", "How is a NumericDate value defined in JWT?",
      ["jwt_numeric_date"], ["seconds since 1970-01-01T00:00:00Z UTC ignoring leap seconds"],
      partial=["jwt_iat_claim"], domain=JWT),
    R("direct_factual", "Which JWT claim identifies the principal that issued the token?",
      ["jwt_iss_claim"], ["iss identifies the issuing principal"],
      partial=["jwt_sub_claim"], domain=JWT),
    R("direct_factual", "What must happen when a JWT's audience does not include the recipient processing it?",
      ["jwt_aud_claim"], ["the JWT must be rejected"],
      partial=["jwt_validation_rules"], domain=JWT),
    R("direct_factual", "When must a JWT carrying the nbf claim be refused?",
      ["jwt_nbf_claim"], ["before the not-before time"],
      partial=["jwt_exp_claim"], domain=JWT),
    R("direct_factual", "What is an Unsecured JWT and can it be trusted on its own?",
      ["jwt_alg_none"], ["alg none with empty signature", "not integrity protected"],
      partial=["jwt_structure"], domain=JWT),
    R("direct_factual", "Can a JWT legally contain the same claim name twice?",
      ["jwt_claims_duplicate"], ["claim names must be unique", "reject duplicates or use only the lexically last duplicate"],
      partial=["jwt_claims_set_def"], domain=JWT),
    R("direct_factual", "Which PostgreSQL roles always bypass row security?",
      ["pgrls_bypassrls"], ["superusers and BYPASSRLS roles"],
      partial=["pgrls_owner_bypass"], domain=PG),
    R("direct_factual", "How are multiple permissive RLS policies on one table combined in PostgreSQL?",
      ["pgrls_permissive_or"], ["combined with OR"],
      partial=["pgrls_or_membership"], domain=PG),
    R("direct_factual", "What does a PostgreSQL policy that omits WITH CHECK do?",
      ["pgrls_with_check"], ["implicit WITH CHECK identical to USING"],
      partial=["pgrls_current_user"], domain=PG),
    R("direct_factual", "What does the sub field hold in a Supabase-issued JWT?",
      ["sb_jwt_exp_field"], ["unique ID of the user represented by the token"],
      partial=["jwt_sub_claim"], domain=SB),
    R("direct_factual", "How does Supabase recommend writing auth.uid() in policies for performance?",
      ["sb_rls_initplan"], ["wrap in a select to get an initPlan cached per statement"],
      partial=["sb_rls_where_clause"], domain=SB),

    # ---------------- multi_source (10) ----------------
    R("multi_source", "How should my API validate an incoming JWT before trusting it?",
      ["jwt_validation_rules", "jwt_exp_claim"],
      ["verify issuer and audience", "validate the signature", "reject on any failure", "current time must be before exp"],
      partial=["sb_jwt_foundation_rls"], domain=JWT),
    R("multi_source", "Compare what the Supabase guide and the PostgreSQL docs say about a table that has grants but no RLS.",
      ["sb_rls_exposed_schema", "pgrls_grant_system"],
      ["readable and writable by any role with a grant", "all rows equally available without row security"],
      partial=["pgrls_enable"], domain=MIXED),
    R("multi_source", "How do role membership and row-level security interact in PostgreSQL?",
      ["pgrls_or_membership", "pgrls_current_user"],
      ["a role has the privileges of all roles it belongs to", "policies can compare a column to current_user"],
      partial=["pgrls_permissive_or"], domain=PG),
    R("multi_source", "Which claims together identify a JWT's issuer, subject, and intended recipients?",
      ["jwt_iss_claim", "jwt_sub_claim", "jwt_aud_claim"],
      ["iss is the issuer", "sub is the subject", "aud names intended recipients"],
      domain=JWT),
    R("multi_source", "What makes a JWT tamper-resistant, and how does Supabase verify tokens it receives?",
      ["sb_jwt_signing_keys", "sb_jwt_foundation_rls"],
      ["signed with a cryptographic key", "Supabase decodes and verifies before authorizing"],
      partial=["jwt_mac_sig"], domain=SB),
    R("multi_source", "How do refresh-token sessions work in Supabase Auth, and what does sign-out revoke?",
      ["sb_auth_sessions", "sb_auth_signout_scope"],
      ["access token plus refresh token", "new pair on refresh", "revokes affected refresh tokens"],
      domain=SB),
    R("multi_source", "How do I write a PostgreSQL policy that lets managers see only the rows they manage?",
      ["pgrls_current_user", "pgrls_with_check"],
      ["USING (manager = current_user)", "implicit WITH CHECK applies to writes too"],
      domain=PG),
    R("multi_source", "What is the difference between the exp and nbf claims during JWT processing?",
      ["jwt_exp_claim", "jwt_nbf_claim"],
      ["exp rejects on or after expiration", "nbf rejects before its time"],
      partial=["jwt_exp_leeway"], domain=JWT),
    R("multi_source", "How do permissive and restrictive RLS policies differ when both apply to a query?",
      ["pgrls_permissive_or", "pgrls_restrictive_and"],
      ["permissive combined with OR", "restrictive combined with AND on top"],
      domain=PG),
    R("multi_source", "What should I do to secure a brand-new table in an exposed Supabase schema?",
      ["sb_rls_exposed_schema", "sb_rls_test_db"],
      ["enable RLS and revoke default grants", "set grants, add policies, verify with supabase test db"],
      partial=["sb_rls_grants_not_removed"], domain=SB),

    # ---------------- ambiguous (6) ----------------
    R("ambiguous", "When is my token considered expired?",
      ["jwt_exp_claim"], ["on or after the exp time"],
      partial=["sb_jwt_exp_field"], domain=JWT),
    R("ambiguous", "How long do sessions last?",
      ["sb_auth_sessions"], ["access token is short-lived and refreshed"],
      partial=["sb_jwt_continuous_issue"], domain=SB),
    R("ambiguous", "What happens when access is revoked?",
      ["sb_auth_signout_scope"], ["affected refresh tokens can no longer be used"],
      partial=["pgrls_drop_policy"], domain=SB),
    R("ambiguous", "Can the table owner always see all rows?",
      ["pgrls_owner_bypass"], ["owner typically bypasses unless FORCE ROW LEVEL SECURITY"],
      partial=["pgrls_bypassrls"], domain=PG),
    R("ambiguous", "What role does the audience play?",
      ["jwt_aud_claim"], ["recipients must identify with an aud value or reject"],
      partial=["jwt_iss_claim"], domain=JWT),
    R("ambiguous", "How are policies applied to updates?",
      ["pgrls_with_check"], ["WITH CHECK constrains inserted or updated rows"],
      partial=["pgrls_command_scope"], domain=PG),

    # ---------------- near_answer (6) ----------------
    R("near_answer", "What is the structure of a JWT string?",
      ["jwt_structure", "sb_jwt_structure"],
      ["URL-safe parts separated by periods", "header.payload.signature, each base64url encoded"],
      partial=["jwt_base64url"], domain=MIXED),
    R("near_answer", "How does base64url encoding differ from standard base64?",
      ["jwt_base64url"], ["URL-safe alphabet, padding omitted, no line breaks"],
      partial=["jwt_structure"], domain=JWT),
    R("near_answer", "Who can read a table that has grants but no row security?",
      ["sb_rls_exposed_schema", "pgrls_grant_system"],
      ["any role with a grant", "all rows equally available"],
      partial=["pgrls_enable"], domain=MIXED),
    R("near_answer", "Which claim should a JWT carry so it can be uniquely tracked?",
      ["jwt_jti_claim"], ["jti is a unique identifier with negligible collision probability"],
      partial=["jwt_sub_claim"], domain=JWT),
    R("near_answer", "Does the typ header parameter need to be present in a JWT?",
      ["jwt_typ_header"], ["OPTIONAL; RECOMMENDED value JWT when present"],
      partial=["jwt_cty_header"], domain=JWT),
    R("near_answer", "When does a Supabase Auth access token stop being trusted?",
      ["sb_jwt_exp_field", "jwt_exp_claim"],
      ["after the exp time limit even if properly signed", "exp marks when the JWT must not be accepted"],
      partial=["sb_auth_sessions"], domain=MIXED),

    # ---------------- coverage (6): three golds each ----------------
    R("coverage", "Explain the full lifecycle of a Supabase user session: issuance, expiry, and refresh.",
      ["sb_jwt_continuous_issue", "sb_jwt_exp_field", "sb_auth_sessions"],
      ["new JWT issued while signed in", "exp limits trust", "refresh token obtains a new pair"],
      domain=SB),
    R("coverage", "What does RFC 7519 require for the temporal validity of a JWT?",
      ["jwt_exp_claim", "jwt_nbf_claim", "jwt_numeric_date"],
      ["before exp", "at or after nbf", "times are NumericDate seconds"],
      domain=JWT),
    R("coverage", "How do I create, scope, and later remove an RLS policy in PostgreSQL?",
      ["pgrls_create_policy", "pgrls_command_scope", "pgrls_drop_policy"],
      ["CREATE POLICY with unique name per table", "per-command or ALL scope", "DROP POLICY keeps default-deny while RLS is enabled"],
      domain=PG),
    R("coverage", "What must hold before a row is visible when both permissive and restrictive policies exist?",
      ["pgrls_restrictive_and", "pgrls_permissive_or", "pgrls_enable"],
      ["passes every restrictive policy", "passes at least one permissive policy", "row security must be enabled"],
      domain=PG),
    R("coverage", "Compare the trust models of a signed JWT, an Unsecured JWT, and a Nested JWT.",
      ["jwt_mac_sig", "jwt_alg_none", "jwt_nested"],
      ["signed or MAC integrity protected", "alg none has no integrity protection", "nested signs then encrypts"],
      domain=JWT),
    R("coverage", "Which Supabase Auth controls limit token misuse: rate limits, MFA, and sign-out scope?",
      ["sb_auth_rate_limits", "sb_auth_mfa", "sb_auth_signout_scope"],
      ["rate limits on sign-up and refresh", "TOTP or passkeys to reach aal2", "scoped sign-out revokes refresh tokens"],
      domain=SB),
]

assert len(SPECS_RW) == 40, f"real-world spec count drifted: {len(SPECS_RW)}"


def load_corpus() -> list[dict]:
    return [json.loads(line) for line in CORPUS_PATH.read_text().splitlines() if line.strip()]


def publisher_of(passage_id: str) -> str:
    if passage_id == "jwt_base64url":
        return "ietf-rfc/7515"
    if passage_id.startswith("jwt_"):
        return "ietf-rfc/7519"
    if passage_id.startswith("sb_"):
        return "supabase/docs"
    return "postgresql-docs/rls"


def pick_fillers(taken: set[str], corpus: list[dict], n: int, prefer: str) -> list[dict]:
    same = [p for p in corpus if p["passage_id"] not in taken and publisher_of(p["passage_id"]) == prefer]
    other = [p for p in corpus if p["passage_id"] not in taken and publisher_of(p["passage_id"]) != prefer]
    return (same + other)[:n]


def build_case(spec: dict, corpus_map: dict[str, dict], corpus: list[dict], idx: int) -> dict:
    case_id = f"rw_{idx:04d}"
    gold_ids = list(spec["gold"])
    partial_ids = list(spec.get("partial", []))

    candidates = [dict(
        candidate_id=p["passage_id"],
        source_id=p["source_id"],
        source_url=p["source_url"],
        text=p["text"],
        metadata={"section": p["section"]},
    ) for p in [corpus_map[g] for g in gold_ids]]
    candidates += [dict(
        candidate_id=p["passage_id"],
        source_id=p["source_id"],
        source_url=p["source_url"],
        text=p["text"],
        metadata={"section": p["section"]},
    ) for p in [corpus_map[p_id] for p_id in partial_ids]]

    taken = set(gold_ids) | set(partial_ids)
    fillers = pick_fillers(
        taken, corpus, TOTAL_CANDIDATES_PER_CASE - len(candidates), publisher_of(gold_ids[0])
    )
    candidates += [dict(
        candidate_id=p["passage_id"],
        source_id=p["source_id"],
        source_url=p["source_url"],
        text=p["text"],
        metadata={"section": p["section"]},
    ) for p in fillers]
    if len(candidates) != TOTAL_CANDIDATES_PER_CASE:
        raise ValueError(f"{case_id}: expected {TOTAL_CANDIDATES_PER_CASE} candidates")

    # Graded judgments for EVERY candidate: gold=2, partial=1, filler=0.
    relevance_scores = {c["candidate_id"]: NONE for c in candidates}
    for g in gold_ids:
        relevance_scores[g] = FULL
    for p_id in partial_ids:
        relevance_scores[p_id] = PARTIAL

    random.Random(REAL_WORLD_SPLIT_SEED + idx).shuffle(candidates)

    task = {
        "task_id": f"rwtask_{idx:04d}",
        "task_type": "knowledge_query",
        "domain": spec["domain"],
        "input": spec["q"],
        "language": "en",
        "metadata": {"case_type": spec["type"], "corpus": "real-world/2026-10-31"},
    }
    return {
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


def three_way_split(
    cases: list[dict], seed: int
) -> tuple[list[dict], list[dict], list[dict]]:
    """Split into dev/validation/test with gold-passage-disjoint components.

    Same union-find discipline as the two-way split: cases sharing any gold
    passage stay in the same set. Per-type targets follow the configured
    fractions, with a deterministic top-up pass.
    """
    rng = random.Random(seed)
    parent = {c["case_id"]: c["case_id"] for c in cases}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    passage_cases: dict[str, list[str]] = defaultdict(list)
    for c in cases:
        for g in c["gold_candidate_ids"]:
            passage_cases[g].append(c["case_id"])
    for g, cids in passage_cases.items():
        for i in range(1, len(cids)):
            union(cids[0], cids[i])

    components_map: dict[str, list[dict]] = defaultdict(list)
    for c in cases:
        components_map[find(c["case_id"])].append(c)
    components = list(components_map.values())
    rng.shuffle(components)
    components.sort(key=len)

    by_type: dict[str, list[dict]] = defaultdict(list)
    for c in cases:
        by_type[c["case_type"]].append(c)
    n = len(cases)
    test_target = {t: round(len(g) * (1 - DEV_FRACTION - VALIDATION_FRACTION)) for t, g in by_type.items()}
    val_target = {t: round(len(g) * VALIDATION_FRACTION) for t, g in by_type.items()}

    test: list[dict] = []
    validation: list[dict] = []
    test_count: Counter = Counter()
    val_count: Counter = Counter()
    assigned: set[str] = set()

    for comp in components:
        comp_types = Counter(c["case_type"] for c in comp)
        if all(test_count[t] + comp_types[t] <= test_target[t] for t in comp_types):
            test.extend(comp)
            test_count.update(comp_types)
            assigned.update(c["case_id"] for c in comp)
    for comp in components:
        comp_types = Counter(c["case_type"] for c in comp)
        if any(c["case_id"] in assigned for c in comp):
            continue
        if all(val_count[t] + comp_types[t] <= val_target[t] for t in comp_types):
            validation.extend(comp)
            val_count.update(comp_types)
            assigned.update(c["case_id"] for c in comp)

    # Deterministic top-up to hit the split sizes (components stay disjoint).
    test_goal = round(n * (1 - DEV_FRACTION - VALIDATION_FRACTION))
    val_goal = round(n * VALIDATION_FRACTION)
    for comp in components:
        if len(test) >= test_goal:
            break
        if any(c["case_id"] in assigned for c in comp):
            continue
        test.extend(comp)
        assigned.update(c["case_id"] for c in comp)
    for comp in components:
        if len(validation) >= val_goal:
            break
        if any(c["case_id"] in assigned for c in comp):
            continue
        validation.extend(comp)
        assigned.update(c["case_id"] for c in comp)

    dev = [c for c in cases if c["case_id"] not in assigned]
    rng.shuffle(dev)
    rng.shuffle(validation)
    rng.shuffle(test)
    return dev, validation, test


def build_real_world_cases() -> tuple[list[dict], list[dict], list[dict]]:
    corpus = load_corpus()
    corpus_map = {p["passage_id"]: p for p in corpus}
    for spec in SPECS_RW:
        unknown = [
            pid
            for pid in spec["gold"] + spec.get("partial", [])
            if pid not in corpus_map
        ]
        if unknown:
            raise ValueError(f"Unknown passage id(s) {unknown} in spec: {spec['q']}")
    cases = [build_case(spec, corpus_map, corpus, i) for i, spec in enumerate(SPECS_RW)]
    return three_way_split(cases, REAL_WORLD_SPLIT_SEED)


def main() -> None:
    dev, validation, test = build_real_world_cases()
    for path, rows in ((DEV_OUT, dev), (VALIDATION_OUT, validation), (TEST_OUT, test)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(c) for c in rows) + "\n")
    print(f"wrote {len(dev)} real-world dev cases -> {DEV_OUT}")
    print(f"wrote {len(validation)} real-world validation cases -> {VALIDATION_OUT}")
    print(f"wrote {len(test)} real-world held-out cases -> {TEST_OUT}")


if __name__ == "__main__":
    main()
