# ADR 0008 — Unified Independent Deletion and Recovery Journal

- **Status:** Local contract and prototype only; provider, operator plane, and restore proof remain blocked.
- **Date:** 2026-11-03
- **Scope:** A single append-only event protocol for C0 deletion requests and Recovery Fence state, plus a local validator/replay prototype.
- **Does not:** amend C0, satisfy its external-journal requirement, implement a provider, authorize a cloud resource, complete B2, or permit B3/data ingress.

## Decision summary

Use one logical, globally ordered, signed event history for deletion and Recovery events. Keep separate per-aggregate sequences and domain state machines inside that history. A deletion event binds an immutable server-generated scope hash and encrypted-manifest reference; a Recovery event binds the Recovery transition and reconciliation evidence. The Durable Object is only a cacheable operational projection and never the history authority.

Google Cloud Storage (GCS) remains the first provider candidate for an isolated proof, not an approved provider. No project, bucket, retention policy, key, identity, or service is created by this ADR. Do not lock a bucket until the complete restore horizon and legal/operational retention consequences have been reviewed and separately approved.

## Trust boundaries

```text
Evidence Service (Railway)                         Operator / approver
  | read-only, fresh signed checkpoint                | authenticated human identity
  v                                                   | separate approval record
Recovery read adapter (Cloudflare Worker)             v
  | no append/admin authority                  Private Admin API (Cloud Run)
  |                                            private IAM + narrow application role
  +-------------------- read -----------------------+ append only after authorization
                                                     v
                                           Serialized ledger writer
                                            |             |
                                  mutable projection   immutable event objects
                                  (rebuildable)         (GCS candidate, separate project)
                                            |             |
                                            +------ verify/replay ------+

Supabase: account and product data; not the Recovery or journal authority.
Durable Object SQLite: low-latency mirror; restore or loss means closed until reconciled.
```

The logical control plane must use separate projects/accounts and administrators from Supabase, Railway, and Cloudflare. That separation is a target property, not a current fact. The GCS reader, writer, retention administrator, operator, and signing-key administrator are distinct principals. No identity may both approve a Recovery open and unilaterally weaken retention or rewrite/delete journal events.

### GCS candidate permissions

- **Evidence/Recovery reader:** may read the signed current checkpoint and list/get immutable event objects. No create, delete, overwrite, retention, IAM, or signing permission.
- **Ledger writer:** may create event objects with create-only preconditions and read them back. It cannot delete/overwrite retained events, alter retention, change IAM, or administer the project. A separate transactional coordinator serializes append attempts and is rebuildable from the event log.
- **Operator API:** private Cloud Run service, IAM authenticated, with a dedicated service identity. It accepts a closed command schema and derives the human actor from the authenticated principal. A Recovery open also requires a separately stored, auditable reconciliation approval. No unauthenticated/public write route exists.
- **Retention/key administrators:** separate accountable group, unavailable to the writer and operator runtime identities. Key destruction/rotation and retention changes are audited and require independent review.

Cloud Run supports private IAM invocation and recommends a dedicated service identity with minimum permissions for service-to-service calls. Network ingress restrictions do not replace IAM authentication. The identity path from Railway/Cloudflare to a GCS-backed read API, and the human approval system, have not been proved; they remain provider-proof blockers. Do not substitute a long-lived shared API key or a signed command for operator authorization.

## Provider evaluation: GCS first, not selected

Google documents strong global consistency for object listing and object read-after-write. It also supports `ifGenerationMatch=0`, which lets a create-only write proceed only when no live object with that name exists. These are useful building blocks for immutable event objects and sequence collision detection; they do not by themselves provide a transaction spanning an idempotency reservation, event object, checkpoint, and coordinator state. A proof must exercise writer races and crash recovery against the actual API, not infer linearizability from an in-memory mock.

GCS Bucket Lock permanently prevents reducing or removing a locked retention policy; retained objects cannot be replaced/deleted and the bucket cannot be deleted while objects remain within retention. Locking is irreversible and adds a project lien. This protects the journal from routine or privileged deletion, but creates real costs for test data, key-loss recovery, privacy requests, mistaken events, account closure, and migration to another provider. A retention policy retroactively applies to existing objects. Before any lock, inventory every database, Storage, replica, archive, export, and backup restore point; retention must cover the longest restore horizon plus the C0 retirement margin. If that horizon is unknown or unbounded, no finite lock period is safe.

The current decision is **evaluate in a later separately authorized isolated proof**. No retention duration is chosen. No cost or latency number is claimed: rates depend on location, object count/size, list/read frequency, retention duration, audit logging, coordinator database, Cloud Run, and cross-provider traffic. The proof should report p50/p95/p99 read and append latency, concurrency/throughput, outage denial, restore duration, and a monthly estimate from measured traffic and current price sheets.

References: [GCS consistency](https://cloud.google.com/storage/docs/consistency), [GCS request preconditions](https://cloud.google.com/storage/docs/request-preconditions), [GCS Bucket Lock](https://cloud.google.com/storage/docs/bucket-lock), [Cloud Run authentication](https://cloud.google.com/run/docs/authenticating/overview), [Cloud Run service-to-service authentication](https://cloud.google.com/run/docs/authenticating/service-to-service), and [Cloud Run ingress](https://cloud.google.com/run/docs/securing/ingress).

## Unified event contract

Each immutable journal event contains:

| Field | Meaning |
|---|---|
| `journal_id`, `schema_version` | Fixed journal namespace and versioned canonicalization rules. |
| `ledger_sequence` | Global, contiguous sequence across deletion and Recovery events. |
| `event_id`, `idempotency_key_hash` | Stable retry identity; same event ID and same canonical payload returns the original receipt; a different payload is a conflict. For deletion, the idempotency hash is derived from the verified requester plus the client key, is fixed across that request's events, and cannot bind a second request ID. |
| `aggregate_kind`, `aggregate_id`, `aggregate_sequence` | `deletion/request_id` or the singleton Recovery aggregate, with its own contiguous sequence. |
| `event_kind`, `actor_principal`, `actor_class`, `reason_code`, `occurred_at` | Closed event vocabulary and auditable actor/reference metadata. The principal comes from trusted authentication, never the request body. |
| `scope_hash` | For deletion events, the immutable hash of scope type, subject/tenant, inventory and owner revisions, exact server-derived targets, trusted Storage bindings, and dependent copies. Identical for every event in one request. Null for Recovery events. |
| `payload_hash` | Hash of the canonical domain payload and evidence references. Raw user content, bearer tokens, signed URLs, and unrestricted target instructions are not journal payloads. |
| `recovery_transition` | For Recovery events only: previous/new state, `generation`, `state_version`, and reconciliation-reference hash. |
| `previous_event_hash`, `event_hash`, `signing_key_id`, `signature` | Hash-chain and signer evidence over a versioned canonical encoding. |

Hashing uses explicit field order and domain separation, not arbitrary JSON serialization:

```text
event_hash = SHA256("evidence-unified-journal-v1\n" + previous_event_hash + "\n" + canonical_event_fields)
signature = Sign(journal_signing_key, "evidence-unified-journal-signature-v1\n" + event_hash)
```

The local prototype in `services/recovery-fence/src/unified-journal-contract.ts` checks the closed envelope, hash chain, signatures through an injected verifier, global/per-aggregate sequences, scope immutability, and domain transitions. It does not implement durable writes, actor authentication/authorization, key custody, retention, or concurrency control.

### Candidate append and current-head protocol

The GCS proof must use immutable event objects with fixed-width global sequence names and `ifGenerationMatch=0`. Cloud Storage documents lexicographic object listing and `startOffset` filtering; the proof must check the exact API pagination behavior and sequence naming. A dedicated serialized writer holds one durable coordinator transaction/lock while it verifies the latest GCS cursor, assigns the next sequence, creates the event object, reads back and verifies its exact bytes, then commits its rebuildable idempotency/head projection. The coordinator is not authoritative: after loss or restore, it must replay and verify the GCS chain before accepting new appends. A `412` create collision, unknown write outcome, missing object, sequence gap, or coordinator/GCS mismatch stops writes and keeps readers closed until reconciliation.

To validate a current head without trusting a possibly restored coordinator or DO cursor, a reader verifies that the supplied cursor object exists and its signed hash matches, then performs a strongly consistent list beginning at the next fixed-width sequence. It follows every returned contiguous event through the current end of the list, verifies signatures and hashes, and rejects gaps or a cursor beyond the retained log. If the cursor is absent, it must replay from genesis. A concurrent append after the list operation is ordered after that read's freshness point; an already authorized in-flight transfer retains the C0 V1 limitation. The implementation must demonstrate GCS listing order/start-offset behavior and pagination under concurrent appends before this is accepted. Replaying from genesis after a cold start is potentially expensive; caching may supply only an untrusted cursor hint, never a positive availability decision.

This candidate needs a durable serialized coordinator (for example, a separately protected transactional database) because GCS conditional object creation alone does not atomically coordinate the idempotency index and event sequence. That coordinator is a rebuildable projection and a failure point, not the journal authority. A coordinator transaction must serialize appenders; the GCS create-only precondition is a second collision guard. Cloud KMS signing permission is granted only to the writer runtime; key administration and destruction are separate. If the coordinator projection is restored behind GCS, replay it before writes; if GCS cannot prove a contiguous latest head, fail closed. Provider-backed crash/concurrency tests remain mandatory.

### Deletion lifecycle and failure order

1. **`deletion.prepared`:** append and read-back verify a durable event for the fixed scope hash and encrypted manifest reference before local acceptance. It does not deny access or authorize purge.
2. **Local acceptance transaction:** revalidate the prepared receipt and all current approvals/revisions. In one local transaction freeze the exact scope, set `revocation_pending` and `journal_ack_pending`, and write the outbox. This commit is the local deny point.
3. **`deletion.accepted`:** append/read-back verify the exact accepted event. Only after verifying its durable receipt may the API expose an accepted receipt or the worker claim a step. If this write/ack is delayed after the local transaction, all gates remain denied; the outbox retries the same `event_id`, idempotency hash, and scope hash. Reconciliation completes the acknowledgement; it never changes `revocation_pending` back to `available`.
4. **`deletion.completed`:** append only after exact manifest targets and dependent copies are verified absent. The event binds verification evidence hashes; it contains no content.
5. **`deletion.aborted`:** allowed only after `prepared` and before `accepted`, with independently verifiable evidence that the local acceptance transaction did not commit. If commit outcome is uncertain, do not append `aborted`; keep access denied and reconcile. After `accepted`, abort is forbidden. Any later cancellation/recovery is a distinct, reviewed transition and cannot erase the accepted event.

If preparation succeeds but the local transaction fails, keep the prepared record. Retry the same request/idempotency key and exact scope; if authoritative evidence proves the local commit never happened, append `aborted` or continue the same request according to the approved request policy. A changed scope under the same key is a conflict. If the transaction outcome is unknown, fail closed. If local commit succeeds but the accepted journal acknowledgement fails, the deny marker and outbox survive; only a retry/reconciliation can acknowledge it. No exception path reopens access because an external write failed.

The local contract can enforce event ordering and scope-hash equality. It cannot prove that the opaque encrypted manifest exists, that an `aborted` proof is truthful, or that a local database transaction committed. Those proofs require the production adapters and are not implemented here.

### Recovery state

The authoritative Recovery projection is `closed` when there is no verified Recovery event or when verification is uncertain. Allowed transitions are `closed → reconciling`, `reconciling → closed`, `reconciling → open` with an approved reconciliation-reference hash, and `open → closed`. Direct `closed → open` is invalid. `state_version` increments once for every Recovery event; `generation` increments only on a valid open transition. `aggregate_sequence` increments per Recovery event; `ledger_sequence` increments for every event of any kind. A missing/gapped event, wrong signature/hash, stale generation, stale sequence, missing event history, or uncertain head keeps the read gate closed.

The Durable Object is a projection. After restore, compare its cursor to the independently read journal. If the journal is ahead, deny reads, append/verify a close or reconciling event through the private operator flow, then replay a contiguous signed range. Never infer `open` from the DO or reopen automatically. Only a separate restore reconciliation approval may authorize `reconciling → open` after all C0 data, policies, grants, and alternate access paths are checked.

## Local contract prototype and what it proves

Local tests exercise a deterministic mixed deletion/Recovery event history, signatures using ephemeral test keys, hash/sequence/scope rejection, duplicate idempotency, invalid deletion ordering, a stale Recovery projection, and closed-by-default Recovery projection. This validates the contract code path only. It is not a GCS test, does not prove two concurrent writers serialize, does not prove Cloudflare PITR or GCS restore independence, and does not establish durable event acknowledgements. Cloudflare's official SQLite DO docs state that PITR can restore SQL and KV state within the previous 30 days and is not supported in local development; therefore a real restore proof cannot be replaced with the local Workers harness.

## Private administration design

The future admin route is a distinct private Cloud Run service protected by IAM; it is not mounted on the public Recovery Worker. Per-operator identity must be attributable to an organization-managed identity, and a narrow application role must separately authorize each command. Opening Recovery requires a second-person or equivalent separately persisted reconciliation approval bound to the restore epoch, ledger head, and event hash. The service derives `actor_principal` from authenticated identity, enforces command schema and expected sequence/version, writes an audit event, and passes only an approved command to the serialized writer. Service identity has append/read permissions only. Retention, IAM, and signing-key administration use separate principals and audit trails.

Exact identity provider, separation-of-duties policy, approval store, Cloud Run ingress path, and Railway/Cloudflare workload identity are unresolved. A valid Ed25519 signature or HMAC alone proves possession of a key; it does not prove the actor is allowed to perform that transition.

## Real Restore proof plan

Only after separate authorization and provider/IAM review, create a dedicated non-production Cloudflare account/namespace and an independently administered GCS test project/bucket. Use synthetic events and preserve resources until review:

1. Write and independently read-back verify at least one deletion lifecycle and Recovery lifecycle, recording ledger and DO cursors.
2. Produce a supported older Durable Object SQLite restore point and restore to it using Cloudflare's documented platform process. Record exactly what data was restored and the restore timestamp.
3. Before any repair, call the real Evidence Service boundary: Recovery mismatch/unavailable history must deny and make zero protected Repository/Catalog/Storage calls.
4. Show the GCS history/head remains ahead and validates across a fresh process/identity. Test missing event, stale generation, altered signature, lost coordinator projection, lost idempotency projection, and inaccessible event history.
5. Through private IAM-authenticated administration, append a close/reconciling event. Replay exact signed events to the DO and prove reads remain denied.
6. Reject an open command without independent approval; allow it only after explicit restore reconciliation binds the verified epoch, data restore watermark, current journal head, and all C0 checks.
7. Verify writer/readers cannot delete, overwrite, change retention/IAM, or sign; verify operator cannot alter retained history; retain audit evidence without recording secret material.
8. Measure latency, throughput, outage behavior, restore time, and costs. Repeat concurrent appends and crash injection at every boundary.

A local harness cannot satisfy this plan. A failed, incomplete, or ambiguous restore proof leaves Recovery closed and B2 blocked.

## Status and follow-up gates

- **Unified local contract:** implemented in this change and locally tested.
- **GCS properties:** official product documentation reviewed; actual isolated configuration and API behavior untested.
- **Independent event durability/source of truth:** blocked pending provider, account/project separation, retention horizon, IAM, and restore proof.
- **C0 deletion journal:** still not implemented; C0 remains binding and this ADR does not supersede it.
- **Private administrator authentication/authorization/audit:** design specified; implementation and real identity/approval proof blocked.
- **Concurrent append/receipt linearizability:** not established by local validator; requires provider-backed serialized writer proof.
- **Cloudflare Restore/PITR:** not tested. The real drill requires a separately authorized live test account and resources.
- **B2:** remains blocked; this design/prototype does not establish Recovery readiness or authorize B3/data ingress.

## References

- C0 `docs/data-control-phase8-design.md`, especially §§6, 8, 11, 13.4, and 13.6.
- ADR 0006 `docs/adr/0006-phase8-recovery-fence.md`.
- ADR 0007 `docs/adr/0007-independent-recovery-ledger.md`.
- [Cloud Storage consistency](https://cloud.google.com/storage/docs/consistency).
- [Cloud Storage request preconditions](https://cloud.google.com/storage/docs/request-preconditions).
- [Cloud Storage object-list API](https://cloud.google.com/storage/docs/json_api/v1/objects/list).
- [Cloud Storage Bucket Lock](https://cloud.google.com/storage/docs/bucket-lock).
- [Cloud Run authentication overview](https://cloud.google.com/run/docs/authenticating/overview).
- [Cloud Run service-to-service authentication](https://cloud.google.com/run/docs/authenticating/service-to-service).
- [Cloud Run ingress restrictions](https://cloud.google.com/run/docs/securing/ingress).
- [Cloudflare SQLite Durable Object storage and PITR](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/).
