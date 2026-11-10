# ADR 0010: Read-only personal API keys

Date: 2026-11-10
Status: Adopted for the requested v1 implementation

## Context

The Evidence Service currently accepts only verified Supabase Auth JWTs and
forwards each user's JWT to PostgREST and Storage. RLS, account lifecycle,
workspace membership, recovery-fence, and source-grant checks depend on that
identity. An opaque API key is not a Supabase user identity and must not be
forwarded as one, exchanged for a forged user JWT, or used with `service_role`.

## Decision

V1 personal API keys are workspace-scoped, read-only credentials for evidence
records and documented relationships. They do not grant source-content reads,
mutations, account operations, organization administration, or deletion.

- A key is generated from 256 bits of cryptographic randomness and shown once.
- The stored verifier is SHA-256; the raw secret is never persisted or logged.
- Each key names one workspace, fixed `evidence:read` and
  `relationships:read` scopes, and a required expiry no more than 90 days away.
- A user may have at most 5 unexpired, unrevoked keys; each key is limited to 60
  API reads per rolling minute and each page to at most 100 rows.
- Key management requires the normal verified user JWT and RLS. Only safe
  metadata is listable; revocation is monotonic and immediate.
- API-key reads call a fixed allowlist of SQL RPCs. A dedicated `NOLOGIN`,
  `NOINHERIT`, `NOBYPASSRLS` reader role owns these `SECURITY DEFINER` functions.
  The RPCs additionally require a fresh, one-use HMAC gateway proof from the
  Evidence Service; this prevents direct public Data API calls from bypassing
  the service's Recovery Fence check. The shared gateway secret is held only in
  the Evidence Service, a private database configuration row, and the
  protected release environment; it grants no table access or write scope.
- The functions verify the gateway proof and key before establishing
  transaction-local subject and workspace context. Explicit RLS policies for
  the reader role re-check active membership, available lifecycle state, key
  scope, expiry, and revocation on every request. The role is not a table owner,
  is not granted to API/Auth roles, and receives no write access to evidence or
  relationships.
- Invalid, replayed, stale, revoked, expired, removed-membership, and
  unavailable-account requests fail closed and return no data.
- Source-content reads remain JWT-only because their verifier and Storage
  boundary independently require the signed Supabase user identity and explicit
  source grant. Supporting them would require a separate ADR.

## Security properties and consequences

- RLS remains enabled and forced; API keys do not mint or simulate Supabase
  JWTs and cannot call the existing write or destructive endpoints.
- A key's workspace binding is revalidated against current membership, so
  removing a member or revoking a key takes effect on the next request.
- Key hashes are credential-equivalent inside the private RPC path. Service
  code must redact authorization headers and request bodies and must not emit
  key material in exceptions or logs.
- This creates a narrow database authorization broker for read-only machine
  access. Its function inventory, role membership, grants, RLS policies, HMAC
  gateway proof, nonce replay defense, and behavior are pinned by the migration
  guard and pgTAP tests.
- Free/Pro billing entitlements are not implemented, so V1 does not claim
  plan-based gating. Plan restrictions remain a future billing decision.

## Out of scope

Source bytes, staged snapshots, writes, role administration, service-role
credentials, access-token signing/exchange, and unrestricted per-user API tokens.
