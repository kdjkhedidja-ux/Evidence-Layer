# ADR 0004 — Trusted source read boundary wired to the retrieval path

- Status: Adopted (design). The read-path contracts and the `SecureSnapshotReadPath`
  are already present and unit-tested; the Supabase-backed catalog/ACL adapters and
  their composition into the authenticated HTTP service are the next slice.
- Scope: design + code. This ADR authorizes wiring the read path. It does **not**
  authorize running migrations, changing production settings, changing the MCP
  default, or flipping any default from deny-all.

## Context

Evidence-Layer reads source content only to select evidence for a consumer. Three
concerns must hold together: access control (who may read a source), source
integrity (the bytes are exactly the version a trusted verifier published), and
retrieval (only verified content may become returned evidence).

Prior decisions established the contracts:

- [ADR 0002](0002-multi-source-evidence-architecture.md) fixed tenant binding and
  default-deny source access.
- [ADR 0003](0003-cost-and-context-optimization-routing.md) fixed that `tenant_id`
  derives only from a verified `WorkspaceContext`, and that no allowing source
  policy may ship until a single trusted constructor exists.

This ADR fixes **where** the trusted boundary is composed, **how** identity flows
through it, and **what** must be proven before the feature is accepted.

Labels: **[verified]** read from code/migrations on 2026-10-30; **[decision]** the
decision recorded here; **[not yet]** not yet implemented or wired.

## Decisions

### 1. Where the trusted boundary is composed

- `src/evidence_layer` defines read contracts (`SourceRequestContext`,
  `SourceReadIntent`, `SourceReadDenial`, `SourceReadLease`,
  `DenyAllSourceReadBoundary`), verification contracts, and versioned source types.
  It **never** grants identity or authorization itself.
- `services/evidence-service` is the composition root. It binds token verification
  (`SupabaseTokenVerifier`), workspace membership (`WorkspaceDirectory`), the
  snapshot catalog, `source_grants`, and Storage into one read path.
- `LocalCorpusRetriever` accepts a read path only from trusted internal composition;
  it never creates authorization itself.
- **[decision]** No consumer may construct or inject an arbitrary `SourceReadBoundary`
  to obtain read permission. The only code that builds the allowing read path is the
  service composition root, and it binds the verified bearer token and selected
  workspace. Tests may inject clearly-isolated fakes that never touch real Storage.

### 2. How identity flows through the read path

The read path is, in order:

1. Verify the bearer token (`verify_access_token`) and bind its expiry.
2. Resolve active workspace membership (`resolve_workspace_context`).
3. Resolve the pinned snapshot version from the catalog (published only).
4. Check `source_grants` for an explicit `read` grant.
5. Re-verify token, membership, and grant **before** opening the Storage object.
6. Read the Storage object with a hard byte cap.
7. Recompute SHA-256 and compare against the catalog digest (constant-time).
8. Re-verify token, membership, and grant **before** delivering the bytes.

Any ambiguity, malformed claim, or verification failure fails closed. A grant is
**not** implied by membership, by object existence in Storage, or by a database
connection. `DenyAllSourceReadBoundary` remains the default in untrusted contexts.

### 3. MCP stdio stays deny-all for protected sources

The local MCP stdio server has no trusted user identity. It must not infer identity
from a process name, environment variable, or request-supplied `workspace_id` /
`source_id`, and must not synthesize an identity for the snapshot verifier.
Protected-source reads over stdio remain denied by default. The first allowing path
is the HTTP service with real authentication. An MCP stdio delegation design is a
later, separate identity decision — it is never a bypass of the guard.

### 4. Local-first integration

Integration starts against an isolated local Supabase, never the production project.
The sequence exercised locally is: test-user identity → workspace membership →
`source_grants` → pinned `source_snapshots` version → Storage object for that
version → SHA-256 recheck against the catalog digest → return only the authorized
content to the retrieval path. The operation must fail if the version changed, the
grant was revoked, or verification failed, even when the bytes are physically
present in Storage.

### 5. Acceptance criteria

The feature is not complete when one read succeeds. All of the following must be
proven by automated tests:

1. No read without a trusted identity.
2. No read without active membership and a per-source `read` grant.
3. No read of a non-published / non-approved version.
4. No read when SHA-256 differs from the catalog digest.
5. Authorization is re-verified before the Storage object is opened.
6. Rejected source content never leaks into retrieval results.
7. `DenyAllSourceReadBoundary` stays the default in untrusted contexts.

## Implementation status

- **[verified]** Contracts (`source_access.py`), the verifier core
  (`snapshot_verifier.py`), token verification (`auth.py`), workspace membership
  (`tenant_context.py`, `supabase_repository.py`), context issuance
  (`source_context.py`), the deny-all async boundary (`source_read_boundary.py`),
  the version-pinned contract coordinator (`source_read_contract.py`), and the
  full fail-closed snapshot read path (`secure_snapshot_read.py`) exist and are
  unit-tested.
- **[verified]** The database schema already holds `source_snapshots` (immutable,
  published-only visibility) and `source_grants` (explicit `read` grants), with RLS
  so membership alone never grants source access.
- **[not yet]** Supabase-backed `SnapshotCatalog` and `SnapshotSourceAccessPolicy`
  adapters that use the user's verified token.
- **[not yet]** A composition hook and HTTP endpoint in `app.py` that wire
  `SecureSnapshotReadPath` to those adapters.
- **[not yet]** Local integration tests that prove the acceptance criteria end to
  end, and the retrieval wiring that guarantees rejected content never reaches
  results.
