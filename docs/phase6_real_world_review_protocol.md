# Real-World Benchmark v1 — Blind Relevance Review Protocol

## Current judgment status

The current 0/1/2 relevance judgments were authored by the developer before
scorer output was consulted. Passage text and provenance were later corrected
during audit; the grades were carried forward and were not re-authored. No
independent reviewer has rated the set. They are provisional and must not be
described as independent human judgments. No JEV scores were generated.

The current held-out membership is public in the manifests, and lexical results
are public in the benchmark report. It is exposed and is not suitable as a blind
test for a future JEV comparison. Independent review of relevance grades does
not make this exposed split a blind system-evaluation set.

## Prepare a blind batch

A coordinator runs:

```sh
python scripts/prepare_real_world_review.py \
  --pack /private/path/real-world-review-pack.json \
  --key /private/path/real-world-review-key.json
```

The script creates a random private shuffle seed unless `--seed` is supplied.
The reviewer-facing pack contains only an opaque case token, the question,
opaque candidate tokens, and candidate text. It omits case IDs and types,
passage IDs, source labels and sections, required-fact lists, gold membership,
and developer-authored grades. The coordinator key contains the mapping and
developer grades and must not be shared with reviewers or committed.

At least two reviewers who did not author the existing grades independently
rate every candidate without seeing the key or discussing ratings. They return
JSONL rows of this form:

```json
{"case_token":"case_…","candidate_token":"item_…","grade":0}
```

Allowed grades are 0 (not relevant), 1 (partially relevant), and 2 (directly
relevant). Reviewers must submit exactly one row for every candidate.

## Resolve disagreements

The coordinator runs:

```sh
python scripts/reconcile_real_world_review.py \
  --key /private/path/real-world-review-key.json \
  --reviewer-a /private/path/reviewer-a.jsonl \
  --reviewer-b /private/path/reviewer-b.jsonl \
  --output /private/path/reconciliation.json
```

The coordinator report counts exact agreement and queues two-review
disagreements. Its third-review queue contains only the question, candidate
text, and opaque tokens; it does not show the first reviewers' grades or the
developer grade. A third reviewer, who did not author the grades, rates those
items independently. Submit that JSONL with `--third-review`.

If the third reviewer agrees with either first reviewer, the majority grade is
final. If all three grades differ (0, 1, and 2), the item remains unresolved and
is placed in `disagreements_for_panel`. A separate adjudicator or panel reviews
the question and passage, sees the three ratings, remains blind to the
developer grade and original IDs, and records one grade plus a rationale in
JSONL, for example:

```json
{"case_token":"case_…","candidate_token":"item_…","grade":2,"rationale":"..."}
```

Supply it with `--panel-adjudication`. No grades are averaged, and the report
stays `pending` while any third review or panel decision is missing.

After resolution, the coordinator-only report joins independently adjudicated
grades to original IDs and developer grades so agreement can be measured.
Preserve the public reviewer pack, each reviewer response, panel rationale,
and coordinator report as review records. Until this procedure is completed,
the benchmark metadata remains pending independent blind review.

## Future JEV evaluation

Do not use the exposed v1 held-out split as a blind JEV test. A confirmatory
JEV comparison needs a new, previously unseen query/candidate set, independently
reviewed judgments, and a sealed membership key held by someone outside system
tuning. Freeze the JEV procedure and all comparison choices before revealing
that test set. No provider request or egress is part of this review protocol.
