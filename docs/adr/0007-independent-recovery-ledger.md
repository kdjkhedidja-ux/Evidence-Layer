# ADR 0007 — Independent Recovery Ledger and Restore Reconciliation

- **Status:** Local protocol implementation; provider selection, independent durability, and restore readiness remain blocked.
- **Date:** 2026-11-03
- **Scope:** External Recovery Fence event history and replay of a stale Durable Object mirror.
- **Does not:** close B2, satisfy the C0 deletion journal, authorize data ingress, or permit Production use.

## Context

ADR 0006 correctly treats the SQLite-backed Durable Object (DO) as a mirror and requires a separately administered ledger to be authoritative. That ledger has not been selected, provisioned, or tested. A signed response from a test double does not prove independent durability or resistance to a real platform restore.

C0 §11 additionally requires an external deletion journal to preserve `prepared`, `accepted`, `completed`, and `aborted` events, their immutable scope bindings, and the restore watermark. This ADR's local protocol covers Recovery state transitions only. It does not yet implement or replace those deletion-journal records.

## Provider assessment

| Candidate | Documented protection | Independence and limits | Assessment |
| --- | --- | --- | --- |
| Cloudflare R2 Bucket Locks | Retention rules can prevent overwrites/deletes for a duration or indefinitely. | The rule can be removed in the dashboard, Wrangler, or API. It shares Cloudflare's control plane with the DO. Separate accounts and administrators would reduce common-mode risk, but the rule's removability still requires explicit threat-model acceptance. | Do not use as the sole independent journal authority. |
| Amazon S3 Object Lock, Compliance mode | A protected object version cannot be deleted by a user, including the account root user, before its retention date. Object Lock requires versioning. | Retention is time-bounded; the bucket, IAM, audit, account-separation, and per-object retention configuration still need proof. S3's conditional writes and strong read-after-write/list behavior can support a serialized append protocol. | Viable independent-provider candidate; not selected. |
| Google Cloud Storage Bucket Lock | After locking, the bucket retention period cannot be reduced or removed; bucket deletion is blocked while retained objects remain. Object listing and read-after-write are strongly consistent. Generation-match `0` can conditionally create an object only when no live object exists. | Locking is irreversible, and retention expiry still matters. Project, service-account, KMS, audit, and organization separation must be proven. The retention horizon is unknown, so locking a bucket now would be unsafe. | Preferred candidate for a separately authorized isolated proof, not selected or provisioned. |

R2's feature is named **Bucket Locks** in current Cloudflare documentation. It is useful against routine object deletion but its rule can be removed by a principal with configuration access. That does not meet this design's independent event-history bar when the same Cloudflare control plane can also restore the DO.

GCS is the preferred candidate for a future proof because Bucket Lock can make the retention policy itself irreversible and Cloud Storage documents strong global object-list consistency. This is a recommendation, not an approval to create a project, bucket, KMS key, service, or credentials. S3 remains a reasonable alternative if its separate-account controls and per-object Compliance retention are easier to prove. No provider can be accepted until actual IAM, retention, recovery, and restore behavior are inspected in a separately authorized environment.

Before any irreversible retention lock, inventory every database, Storage, replica, archive, and export restore point. C0 requires journal replay coverage to outlive every permitted restore point, including at least 30 days after retirement of the final restore point and at least 365 days after completion. The effective horizon is whichever is later. If the restore horizon is unknown or unbounded, do not set a finite retention period and do not lock a bucket.

## Proposed trust and control-plane boundaries

```text
Evidence Service
  | status read only; fresh request authentication
  v
Recovery Worker (Cloudflare)
  | fresh signed head + immutable event pages
  v
Ledger API and append service (separate provider/account)
  | create-only event objects; read current head; sign receipts
  v
Locked object store + separately administered signing key

Private operator control plane
  | provider IAM authentication + role/approval policy + audit
  +--> ledger append API --> Recovery Worker/DO reconciliation

SQLite DO: fast current mirror only; never the independent authority.
Supabase: Account Lifecycle and data; never a Recovery state source.
```

The GCS candidate separates these identities and permissions:

- **Recovery reader:** can list and read event objects and call the read-only head API. It cannot create objects, sign, change retention, or edit IAM.
- **Ledger writer:** can create and read the event and idempotency objects and use one non-exportable signing key. It cannot delete/overwrite objects, change retention, edit IAM, or administer the bucket/project. Conditional creation is mandatory.
- **Retention administrators:** a separate organization group can administer the bucket retention policy and KMS configuration. They do not run the writer service or approve Recovery transitions.
- **Operator control plane:** a private Cloud Run service accepts only provider-authenticated operator invocations. It checks a narrow operator role and a separate approval record, derives the actor from the authenticated principal, and submits only the closed transition schema. It has no public unauthenticated write route.

Evidence Service credentials cannot append, administer retention, change IAM, or sign. Caller-supplied `actor` text is audit data only and cannot grant authority. Opening Recovery requires a separately recorded reconciliation approval; a valid Ed25519 signature alone is not operator authorization. Provider audit logs and the signed immutable event are both required evidence. Exact IAM policy, Cloud Run ingress, service identities, KMS key policy, and separation of the retention administrators must be inspected in the isolated provider proof; this ADR does not assert that they already exist.

The admin route is private-service/IAM-only and has no public unauthenticated write handler. Provider audit logs and the append-only event itself record who approved and performed each transition. The current local callback does not provide a deployed operator identity, approval store, or private route. Those remain unimplemented and blocked pending a selected provider and authorization design.

## Ledger record and append protocol

The provider implementation must expose:

- A nonce-bound `GET /v1/head` that returns the latest state, generation, sequence, state version, event ID, event hash, and a signed checkpoint.
- A read-only `GET /v1/events?after_sequence=N&through_sequence=M` that returns every signed event in the closed interval, with explicit bounds and no cache.
- An authenticated `POST /v1/events/append` used only by the private administrative control plane. It rejects stale expected values, invalid state transitions, missing approvals, and conflicting idempotency keys.
- Durable receipts signed only after the event object is protected by the configured retention policy and the exact bytes can be read back and verified.

Each event binds a canonical payload hash, previous-event hash, event hash, unique event ID, actor, reason, state transition, expected and new generation/sequence/version, reconciliation reference, timestamp, schema version, and signed receipt. Define:

```text
event_hash = SHA256("evidence-recovery-event-v1\n" + previous_event_hash + "\n" + payload_hash)
```

Sequence creation must be linearizable. For a GCS candidate, use one immutable object per sequence, `ifGenerationMatch=0`, and strongly consistent listing to identify the latest sequence. A separate idempotency reservation object keyed by a hash of `event_id` prevents a retry from reusing that ID with another payload; an incomplete reservation never receives a durable event receipt. The S3 implementation must demonstrate an equivalent conditional-create and ordering protocol. Listing, event writes, and reads must bypass caches.

If a write times out, the API rereads by event ID and sequence before retrying. Same ID plus same canonical payload returns the original receipt. Same ID with different payload is a conflict. The service never issues a receipt based only on a local database transaction or an object-store upload attempt whose durability/retention status is unknown.

The authoritative head is derived from retained immutable event objects, not a mutable pointer that can silently roll back. The API verifies the head event and its chain binding before signing a fresh nonce response. Startup/recovery integrity checks validate sequence continuity and chain hashes. Missing events, retention mismatch, unavailable history, signature uncertainty, or a lost signing key keeps Recovery closed.

The production C0 journal also needs encrypted, fixed target manifests and the `prepared`/`accepted`/`completed`/`aborted` outcomes. Those event types, key recovery, privacy minimization, and retention schedule are not present in the current Recovery Worker protocol. They require a separate schema and authorization review before this ledger can satisfy C0.

## Recovery transitions and reconciliation

Recovery state remains `closed`, `reconciling`, or `open`; missing state is closed. Every accepted transition increments sequence and state version exactly once; generation increments only on a verified `reconciling → open`. There is no direct `closed → open` transition.

When a DO restore moves its mirror behind the ledger:

1. The signed current ledger head and restored DO differ, so every protected read fails closed. No route may return `open` from the DO alone.
2. An authorized administrator appends a state transition that leaves the authoritative ledger `closed` or `reconciling`. If the authoritative head is `open`, the first allowed recovery action is `open → closed`. A stale DO cannot authorize an `open` transition.
3. The Recovery Worker fetches the immutable signed events from the DO's last event through the new non-open head. It validates signatures, sequence continuity, expected prior state/version, previous-event hash, payload hash, and calculated event hash before applying each event transactionally to SQLite.
4. During partial replay, the signed authoritative head remains ahead of the DO, so protected reads continue to fail. Replay stops on a gap, signature mismatch, unexpected branch, DO-ahead state, provider error, or more than the bounded replay window. It never rewinds a DO or guesses a missing event.
5. The worker obtains a fresh signed head after replay and returns success only if every field and the final event hash exactly match. It does not open the system.
6. The restore coordinator separately verifies the restored database, Storage, access policies, deletion records, grants, exports, and legacy links under C0. Only then may an authorized, audited `reconciling → open` event reference that verified restore epoch and watermark.

If the ledger is unavailable, itself restored, missing a sequence, or cannot prove that its head is newer than every allowed restore point, this protocol cannot establish freshness; keep the gate closed. A test fixture that changes a head is not a PITR test. Replaying a simulated stale DO proves only the local protocol path, not Cloudflare restore behavior or object-store retention.

## Local implementation in this change

The Recovery Worker now stores `last_event_hash` in DO SQLite and verifies an event hash chain in addition to the signed event payload. Its internal administrative transition handler can handle a stale DO only through a ledger-validated transition that leaves the authoritative gate non-open, then replay the exact signed event range. The public Worker still exposes only its read route. Missing/incomplete pages, invalid signatures, gaps, chain mismatch, DO-ahead state, or an open target head fail closed.

The Workers test ledger remains an in-memory test double. It is not an independent provider, does not enforce object retention, and cannot prove a cloud restore boundary. No provider adapter, bucket, project, key, or operator system was provisioned in this work.

## Required real Restore drill before any readiness claim

After provider selection and a separate authorization to create isolated test resources, use a dedicated Cloudflare test account/namespace and a separately administered test ledger project/bucket. Use synthetic Recovery events only. Record the event head, restore the DO to a deliberately older supported point, and verify that (a) the read remains denied before any repair, (b) the independently retained event head remains ahead, (c) a close/reconciling event is durably acknowledged, (d) the exact event sequence is replayed, and (e) the gate remains closed until a separately reviewed reconciliation approval is appended. Repeat with a missing event, wrong signer, unavailable ledger, and a writer identity that is denied delete, overwrite, retention, and IAM operations. Preserve both test stacks for review. A local Miniflare snapshot simulation is not a substitute for this drill.

## Availability, latency, and cost

Every protected request requires a fresh external head check, plus the DO read. A GCS design would perform a strongly consistent latest-object listing and read, then sign a nonce-bound response; normal event append would perform idempotency and sequence conditional creates plus a signed receipt. A restore replay performs bounded page reads. The design intentionally fails closed during network and provider outages.

No latency, request capacity, or monthly cost estimate is claimed. Local Miniflare timing excludes Cloudflare-to-provider network delay, object listing and retention behavior, signing, IAM, and provider billing. A separate-provider test must measure p50/p95/p99 and outage denial from the actual service region, then calculate costs using expected gate reads, event writes, retention bytes, audit logs, and restore drills. One singleton DO remains a serialization point and must be load-tested separately.

## Evidence and blockers

The local tests exercise a stale-DO replay after a durable close, and reject a missing event page or invalid event signature. They do not execute a Cloudflare PITR restore, create an object-lock bucket, prove separate cloud administration, verify a locked retention period, test operator IAM, or exercise the C0 deletion-journal event types.

Therefore:

- **Local replay protocol:** implemented and locally testable.
- **Independent provider:** not selected or provisioned; blocked.
- **Rollback resistance:** not proven outside the local test harness; blocked.
- **Private admin authorization/audit:** design specified; deployed private control plane and real operator authorization are blocked.
- **C0 deletion journal:** still unsatisfied by this Recovery-only event protocol.
- **B2:** remains open; protected user-data ingress remains closed.

## References

- C0 `docs/data-control-phase8-design.md`, §11 and the backup restore reconciliation contract.
- ADR 0006 `docs/adr/0006-phase8-recovery-fence.md`.
- [Cloudflare R2 Bucket Locks](https://developers.cloudflare.com/r2/buckets/bucket-locks/).
- [Amazon S3 Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html) and [conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html).
- [Google Cloud Storage Bucket Lock](https://cloud.google.com/storage/docs/bucket-lock), [consistency](https://cloud.google.com/storage/docs/consistency), and [request preconditions](https://cloud.google.com/storage/docs/request-preconditions).
