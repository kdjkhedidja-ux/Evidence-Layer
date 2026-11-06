# ADR 0006 — Phase 8 External Recovery Fence

- **Status:** Local implementation only; production authority and durable-ledger provider are blocked pending separate approval and proof.
- **Date:** 2026-11-03
- **Scope:** Evidence Layer recovery availability gate, before Account Lifecycle.

## Decision

Use one SQLite-backed Cloudflare Durable Object as the operational mirror for the Evidence Layer Recovery state. A separately administered, independently durable append-only event ledger is the authoritative sequence and checkpoint source. The Worker returns an open state only when the DO mirror exactly matches a fresh, nonce-bound, signed ledger head. Missing configuration, stale or mismatched state, bad signatures, ledger/Worker/DO errors, and ambiguous restore state all deny protected access.

No independent ledger provider has been selected or implemented in this change. The local Worker therefore has no open-by-default or self-authorizing path. Local Vitest uses a protocol-only ledger double to test the integration contract; this is not evidence that a durable independent ledger exists.

## Trust boundaries and responsibilities

```text
Evidence Service on Railway
  | signed, nonce-bound read (read-only HMAC key)
  v
Read-only Recovery Worker on Cloudflare
  | private service binding: current signed head
  +-------------------------------> Independent event-ledger service
  |                                 (provider and recovery domain TBD)
  | RPC: nonce + SQLite snapshot
  v
SQLite-backed RecoveryState Durable Object

Future private operator control plane (not implemented or routable here)
  | internal callback, Ed25519 administrative signature, durable append
  +-------------------------------> event ledger + RecoveryState DO

Supabase Auth/Postgres/Storage: user identity, Account Lifecycle and data;
not authoritative for the global recovery state and not consulted to open it.
```

- **Railway Evidence Service:** verifies Supabase JWT as before, then makes a new signed recovery read. It cannot call the administrative transition endpoint or write the DO/ledger. It evaluates Recovery before `account_lifecycle`, then existing Workspace, Source, and Snapshot gates. No positive Recovery cache is permitted.
- **Cloudflare public Recovery Worker:** exposes only `GET /v1/recovery`, authenticates the read caller, checks freshness and ledger signatures, and has no user-data, Supabase, Storage, or deletion-worker authority. It has no state-changing HTTP route.
- **Administrative control:** the code contains a separately signed transition handler that is not mounted by the public Worker and has no Internet route in this change. A future private operator control plane must invoke it through a private service boundary. There is no configured operator plane here, so no live transition can be made by this local implementation.
- **Durable Object:** stores only the operational state, applied event identities/hashes, and short-lived request nonces in SQLite. Its role is serialization and a strongly consistent current mirror; it is not an immutable or rollback-proof journal.
- **Supabase:** remains the authority for Account Lifecycle and user data. An available lifecycle row can never override a closed or unverified Recovery fence.
- **Independent event ledger:** once selected, it owns the monotonic global sequence, durable transition history, and signed current head. It must be outside the DO's administrative and PITR/restore domain.

## Authoritative state and freshness

The authoritative state is the latest valid signed head from the independent ledger, not an in-memory Worker value or a DO row by itself. For every protected request, the Worker:

1. Verifies an Evidence Service HMAC using a current or overlap-period previous key ID, timestamp, exact route, and random nonce.
2. Atomically reserves that nonce in DO SQLite and reads the DO snapshot.
3. Fetches the current ledger head for that same nonce. No cache or stale checkpoint is accepted.
4. Verifies the ledger's asymmetric signature, nonce, 30-second freshness window, and exact state/generation/sequence/state-version/event-ID match with SQLite.
5. Returns `verified: true` only after all checks. The Evidence Service accepts only `state == "open"`, `verified == true`, and its own nonce echoed exactly.

The state read is on every protected gate check. Availability is not inferred from JWT claims, account metadata, a prior request, or the SQLite row alone. The Python reader validates the Worker response and performs a new HTTP request for each call.

## States and transition contract

Missing SQLite initialization is logically `closed`, generation 0, sequence 0, state_version 0, `initialized=false`. A missing ledger head is unavailable, not an implicit initial open record.

| Current | Next | Rule |
| --- | --- | --- |
| `closed` | `reconciling` | Authorized administrator and durable ledger receipt required. |
| `reconciling` | `closed` | Authorized close/cancel event; sequence and state_version advance. |
| `reconciling` | `open` | Authorized administrator, durable ledger receipt, and non-empty verified reconciliation ID required; generation, sequence, and state_version advance. |
| `open` | `closed` | Authorized close event; sequence and state_version advance. |
| Any other pair | — | Rejected. In particular, `closed → open` and `open → reconciling` are not transitions. Close, then reconcile, then explicitly reopen. |

Every event binds event ID, actor (which must equal the authenticated administrator key ID), reason, exact from/to states, expected previous generation/sequence/state_version, new sequence/version, generation, reconciliation ID, and issue time. Sequence and state_version increment exactly once per accepted event. Generation remains stable while closing/reconciling and increments exactly once on `reconciling → open`. Stale expected values conflict. Repeating the same event ID and same canonical payload is idempotent; reusing the ID with a different payload conflicts.

The ledger receipt must be a signed durable acknowledgement for the exact payload and sequence. The Worker appends that event before changing SQLite. If the ledger append succeeds but the local DO commit does not, the ledger/DO mismatch denies reads until exact reconciliation. If the DO commit succeeds but the response/head check fails, access remains denied until the current ledger head again matches. A request cannot reopen the gate by retrying with an old sequence or by changing the command payload.

## Authorization and key separation

- **Read:** Evidence Service signs GET requests with a purpose-specific HMAC key and key ID. The Worker accepts an active and one overlap-period previous key only, requires a short clock window, and atomically rejects nonce reuse. The key grants status reads only.
- **Admin transition:** a separate operator signs the internal callback request with Ed25519. The handler stores public verification keys only, accepts active/previous key IDs during controlled rotation, and requires the same timestamp/nonce protections. The callback is not registered as a public HTTP endpoint. No user token, service-role credential, or read key can make a transition.
- **Ledger signing:** only the independent ledger holds its Ed25519 private signing key. The Recovery Worker receives a public key and verifies receipts and current-head signatures. A ledger service binding is private to the Worker and is not exposed as an internet route.
- **Rotation:** deploy the next verifier key first, allow a bounded overlap, move the signer, verify traffic, then remove the prior key. This ADR authorizes no secret creation, Railway configuration, Cloudflare live setting change, or deployment.
- **Logging:** never log authorization headers, signatures, keys, request bodies, or ledger payloads. Operational metrics may record status code, duration, and safe opaque correlation IDs only.

## Durable event ledger requirements

The SQLite Durable Object alone does not meet C0's independent deletion-journal requirement. A production ledger must, before production activation:

1. Reside outside the application database/Storage and DO administrative and restore domains, with separately controlled operators and credentials.
2. Offer ordered, linearizable append and current-head reads, durable acknowledgements, unique immutable event IDs, payload hashes, and monotonic sequence/checkpoint values.
3. Enforce append-only retention for the approved replay horizon, with integrity checking, replicated durability, backup/restore controls, and no application/worker erase or reset permission.
4. Keep the signing private key, verification keys, event schema, and decryption/reconciliation materials recoverable independently for the same horizon.
5. Sign an event receipt only after the exact event is durable; sign a fresh head per request nonce; reject stale sequence, changed payload under an existing event ID, or an unverified reconciliation/open event.
6. Support idempotent retry, prepare/accepted/aborted/unknown outcomes as required by C0 §11, and content-free reconciliation reports bound to a restore epoch and watermark.

Provider choice, implementation, retention, restore exercises, key recovery, and actual durability proof are unresolved. Until then, read requests cannot produce a trusted open status. This local test double only exercises the wire protocol and failure behavior.

## PITR, restore, and reconciliation

Cloudflare SQLite DO PITR can restore the entire embedded SQLite database to a point in the preceding 30 days. That is direct evidence that the SQLite mirror can move backwards; it is not an append-only journal. After any DO restore, incident, sequence discontinuity, missing event, key/signature uncertainty, or ledger outage, keep or force the gate closed. A restored DO is never authoritative merely because its row says `open`.

Reconciliation must isolate every data-serving surface, read and validate ledger continuity/current watermark, compare the restored DO state, replay authorized events idempotently, verify the affected data surfaces, then issue a new signed reconciliation event/epoch. The gate opens only when the independent ledger has durably recorded the exact `reconciling → open` event and its current signed head matches the updated DO. Missing ledger history, an unknown prepare outcome, or an uncovered restore point remains closed. Local tests can simulate an old DO snapshot versus a newer ledger head; the actual Cloudflare PITR operation is not available in local development and is not exercised here.

No recovery procedure can retract bytes already sent. A request that received a valid open decision before a close may finish in flight, consistent with the C2 V1 limitation. Every new request and the existing final Snapshot content boundary must recheck. Recovery Fence success is not proof that a signed link is revoked, and it does not authorize C4 completion or purge.

## Availability, latency, and cost

- A missing Worker, DO, or independent ledger; timeout; overload; stale response; or signature error produces a generic unavailable denial. This sacrifices availability for fail-closed access.
- Each read incurs one service-to-Worker request, one DO RPC (nonce+snapshot combined), and a fresh ledger-head lookup. An administrative transition uses two DO RPCs plus append and head operations. The ledger network is serial on the decision path.
- There is one singleton DO, so it is a deliberate serialization point and potential throughput bottleneck. Cloudflare documents an individual DO soft limit of about 1,000 requests/second before queuing/overload; this is a platform limit, not an Evidence Layer capacity result. One HTTP protected request can cause more than one Recovery read because repository, catalog, and final Snapshot boundaries recheck.
- No latency or sustained throughput is claimed from local Miniflare tests. The local run will report the observed concurrent-request latency only as a harness observation. Deployment requires a staged external benchmark measuring p50/p95/p99, queueing, ledger timeouts, and overload-denial rate from the Railway region.
- Cloudflare's published Paid-plan rates (checked 2026-11-03) list the first 1 million DO requests/month and 400,000 GB-s/month included; overage is $0.15/million requests and $12.50/million GB-s. SQLite includes 25 billion row reads and 50 million writes/month, then $0.001/million reads and $1/million writes; stored data includes 5 GB-month, then $0.20/GB-month. The Free plan has daily quotas, after which operations fail. The independent ledger, Worker ingress, network, logging, and retention costs are additional and unknown. Price and plan terms must be rechecked before any deployment.

## Completion gate and unresolved items

Local code can prove state-machine, authentication, SQLite, nonce, fail-closed, concurrency, and stale-head protocol behavior under Workers test harness. The internal admin callback is called directly by tests and is not publicly routable. It cannot prove:

- the independent ledger exists, is independently durable, linearizable, append-only, and operated with separate permissions;
- actual Cloudflare account/namespace permissions, live key rotation, Cloudflare PITR recovery, or production load/cost;
- Railway-to-Worker network latency and service identity deployment;
- end-to-end platform recovery, key survival, or C0 prepare/accept/reconciliation durability.

Therefore this ADR and local implementation do not close B2, enable data ingress, authorize production, or satisfy the C0 independent journal proof. B2 remains open until these items are reviewed and separately authorized.

## References

- C0: `docs/data-control-phase8-design.md`, especially §§11 and 13.3.
- [SQLite-backed Durable Object Storage and PITR](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/)
- [Cloudflare Durable Object limits](https://developers.cloudflare.com/durable-objects/platform/limits/)
- [Cloudflare Durable Object pricing](https://developers.cloudflare.com/durable-objects/platform/pricing/)
- [Cloudflare Workers Vitest integration](https://developers.cloudflare.com/workers/testing/vitest-integration/)
