# Phase 3 source-read contracts

This increment defines testable server-side contracts only. The HTTP
application, standard MCP server, and normal retriever are not wired to the
version-pinned boundary; `DenyAllSourceReadBoundary` remains the runtime
default. Synthetic fixture behavior is unchanged.

## Read decision and open order

The internal boundary expects a server-verified principal and server-selected
workspace. It validates the request context, resolves the complete source set
to immutable version IDs, and checks the server-owned ACL for each
`principal/workspace/source/operation/purpose/version` tuple. Purpose is
derived from the server-side operation mapping, not accepted as an ACL grant
from the client. An omitted `expected_version` is allowed as a selection hint
only: the catalog-resolved immutable version is always pinned into the
approval and passed to `open_exact`. A supplied expected version must match.

Only after every source passes preflight does the boundary issue and consume
the entire batch of internal approvals. It then rechecks ACL for every source
before opening any source. A denial or ACL error in this final group check
returns a denial with zero storage opens. The store must advertise
exact-version support and the opened reader's version must match. Any failed
open or version check denies the whole batch; already-opened handles are closed
before a denial is returned, and records are not iterated until all opens
succeed.

The local approval store serializes batch consumption with a process-local
lock. This prevents two threads in this process from consuming one approval
twice. It does not coordinate workers, processes, containers, or services; a
production implementation would require shared atomic compare-and-set or an
equivalent durable transaction. No such implementation is included.

ACL checks and storage opens are separate operations. After consuming
approvals, the boundary checks ACL for the complete batch again; only if all
checks pass does it begin opening exact versions. This batch-wide check
prevents a denial for a later member from occurring after an earlier member
has been opened. It does not make the ACL decision and storage opens atomic: a
revocation after the final group check and before or during the sequential
opens may not be observed before one or more opens. During streaming, ACL and
lease expiry are checked before and after each record is fetched; once a
revocation is observed, the reader closes and no later record is yielded. The
final check and yield are not atomic, so a revocation racing between them may
allow one more record through. A blocking store read cannot be interrupted
until it returns. A record already delivered to a consumer cannot be recalled.
The 30-second lease is also capped by the request-context expiry.

These tests use in-memory fakes. They establish the Python contract and local
ordering only; they do not prove that a production catalog returns immutable
versions, a production ACL is fresh, a production store honors conditional
version opens, or a distributed approval store is atomic.

## Provider-transfer separation

A source-read approval is not a provider-transfer grant and is never passed to
JEV or another external provider. Provider transfer remains governed by the
separate transfer-authorization contract and its deny-all default.

## Secure snapshot bytes path (Gate 2 local increment)

`evidence_service.secure_snapshot_read.SecureSnapshotReadPath` is a separate,
injected local path. It accepts a server-authenticated request, a selected
workspace, and source/version IDs; it does not accept an object key. On each
read it verifies the token again, resolves active membership from the trusted
workspace directory, resolves that exact published version, checks source ACL
before Storage access, and repeats identity, membership, expiry, and ACL checks
before opening and before returning bytes. Storage returns a complete buffered
payload; the path checks its declared length and SHA-256 before returning it.
No partial payload is exposed on failure.

`SnapshotPublisher` derives an object key from workspace/source/version UUIDs,
requires a create-only object write, reads the object back, and publishes the
manifest only after length and digest match. A failed check leaves no manifest;
an uploaded object may remain as an unpublished orphan. Catalog publication
must be atomic in a durable implementation. The included in-memory catalog and
object store are test implementations only.

`LocalSupabaseStorageObjectStore` talks only to an HTTP loopback URL and uses a
public API key plus the request user's JWT. Its upload sends `x-upsert: false`.
The adapter is not wired into `app.py`, MCP, the normal retriever, or any
production setting. `DenyAllSourceReadBoundary` remains the runtime default.

The focused contract tests exercise the full decision order with test doubles;
they do not establish Storage API behavior. The opt-in live integration at
`tests/integration/test_local_supabase_storage_source_read.py` signs up a
synthetic user against an already isolated local Auth API. It asks that Auth
API to validate the user's JWT, then sends read/write requests to the actual
local Storage API with that JWT. It requires a pre-created bucket with that
user's insert/select policy and an overwrite-denial policy. It creates a local
user and uniquely named objects and does not delete them. The workspace
directory and source ACL are test dependencies in this integration test; those
contracts have separate tests. A pass is evidence only for that local
image/configuration and identity; it does not establish privileged-role
immutability, provider-native versioning, or production readiness. The adapter
test needs a pre-created permissive bucket and is separate from the snapshot
policy checks below.

### Durable catalog, grants and trusted publication (Gate 2, local)

`supabase/migrations/20261022100000_source_snapshot_catalog.sql` and
`20261022110000_source_snapshot_storage_policies.sql` give the contracts above a
durable, local-only backing; they are not wired into `app.py`, MCP, or any
production setting, and `DenyAllSourceReadBoundary` remains the default.

- `source_snapshots` is keyed by `(workspace_id, source_id, version_id)`, derives
  `object_key` from those IDs, and stores the claimed `sha256`/`size_bytes`.
  Rows must be inserted `staged`. Only the `snapshot_verifier` role may move a
  row to `published`, and only with `verified_sha256`/`verified_size_bytes`
  equal to the staged claim; a trigger also requires the uploaded object to
  exist, to be uploaded by the staging user, and to have the declared size.
  Published rows cannot be updated or deleted.
- `source_grants` holds explicit read grants. Workspace membership and
  ownership never imply source access; catalog metadata and object bytes are
  both gated by an active membership and a read grant.
- The private `source-snapshots` bucket has INSERT (into a key the same active
  owner already staged) and SELECT (published record + active membership +
  grant) policies only. No UPDATE or DELETE policy exists, so `x-upsert` and
  deletes are denied, and an unpublished or uncataloged object is unreadable by
  clients.
- A failed publish burns the `version_id`: retry with a new version. There is no
  automatic cleanup of unpublished objects.

**Publication is closed by default.** No API role can assume `snapshot_verifier`;
opening it is an explicit operator action for a trusted verifier process. A JWT
signed with the project key and naming that role proves who called, not that an
object's SHA-256 was verified, and is never treated as verification evidence.
PostgreSQL also cannot recompute an object's SHA-256: it enforces that a
matching attestation from `snapshot_verifier` exists, not that the attestation
is honest. A dishonest or compromised verifier, or an RLS-bypassing credential,
can publish bytes that do not match the catalog digest; the secure read path
re-verifies size and SHA-256 before returning bytes. Evidence: the pgTAP files
`supabase/tests/source_snapshots.test.sql` and
`supabase/tests/source_snapshot_storage.test.sql`, and the opt-in
`tests/integration/test_local_storage_snapshot_policies.py`.

### Verifier identity (O4, amended; local design, not wired)

Migration `20261022120000_snapshot_verifier_identity.sql` replaces the
direct-UPDATE publication route with two RPCs, `begin_snapshot_verification`
and `publish_verified_snapshot`. They are `SECURITY DEFINER`, use an empty
`search_path`, are owned by the `NOLOGIN` role `snapshot_publisher_owner`, and
work only for a Supabase Auth user listed in
`private.snapshot_verifier_allowlist` **for the requested workspace**. An entry
is a `(user_id, workspace_id)` pair with both columns `NOT NULL`, so there is no
"all workspaces" entry; a verifier for several workspaces needs one row each.
The list is empty by default and edited only by an operator outside the API.
Both RPCs check the caller's entry against `p_workspace_id` first and raise
`42501` before any lookup (so a mismatch also reveals nothing about another
workspace's snapshots); the Storage policy and the publication trigger apply the
same scope to the staged row's workspace. Chosen because it is the smallest
change to the existing schema: one extra NOT NULL column and a join, no new
table, no wildcard value, and a missing scope can only deny.
Publication needs the object's `(id, version, size,
uploader)` to match what the verifier examined; this detects replacement made
through Storage, but it is not a lock on the object and cannot see bytes
replaced beneath Storage. The read path's SHA-256 check stays the integrity
control. `snapshot_verifier.py` computes size and SHA-256 from the bytes and is
local/test only; it holds no `service_role` credential.

Limits: the verifier credential is still one Auth user per workspace set and its
custody is undecided; a compromised verifier can read the staged objects of the
workspaces it is listed for. The pgTAP replacement checks
simulate a changed `version`/`id` by updating `storage.objects` directly; they
test the comparison logic only, not how Storage replaces objects.

Status: Python tests and static checks of the migration text ran locally; the
migration, the pgTAP files and the live test have NOT been run against a
database or live Storage, nor the assumptions about `storage.objects.id` and
`version` (and that `version` changes on every upload). The earlier
`source_snapshots.test.sql`, `source_snapshot_storage.test.sql` and
`tests/integration/test_local_storage_snapshot_policies.py` were updated (still
unrun) to publish through the scoped RPC and to assert that the legacy
`snapshot_verifier` route is closed.

