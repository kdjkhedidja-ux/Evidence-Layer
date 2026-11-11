# Evidence Layer service

This local FastAPI service exposes a public `GET /healthz`, plus a small
Supabase Auth identity and workspace-membership boundary:

- `GET /v1/me` returns the verified Supabase user ID, verified issuer, and the
  caller's active workspace memberships and roles.
- `PUT /v1/me/personal-workspace` idempotently provisions the authenticated
  user's single personal workspace and owner membership.
- `GET /v1/workspaces/{workspace_id}` selects one workspace only after the
  caller's active membership has been checked.
- `GET /v1/workspaces/{workspace_id}/evidence` (plus `POST`, and
  `GET`/`PATCH`/`DELETE` on `{evidence_id}`) manages workspace-scoped evidence
  records under the caller's own RLS policies. Active members can read;
  active owners can create, update, and delete. Records may carry an optional
  provenance link (`source_id` + `source_version_id`) to an exact published
  source snapshot version; the service accepts a link only after the caller's
  own token resolves that version through the RLS-protected catalog.
- `GET /v1/workspaces/{workspace_id}/relationships` lists explicit links
  between evidence records. `POST`, `PATCH`, and `DELETE` on that collection
  are restricted to active owners; active members may read. Each link stores
  its type, rationale, provenance note, creator, and last updater, and the
  database constrains both endpoints to evidence in the same workspace.
- `GET /v1/workspaces/{workspace_id}/sources` lists the source snapshot
  versions visible to the caller (published versions of granted sources plus
  the caller's own staged rows). Object keys are never exposed.
- `GET /v1/workspaces/{workspace_id}/sources/{source_id}/versions/{version_id}/content`
  delivers verified source bytes after membership, catalog, ACL, size, and
  SHA-256 digest checks, with revalidation before opening and before delivery.

The service does not collect passwords or expose a login/signup UI. A client
signs in with Supabase Auth, then sends its access token to this service.
`/healthz` does not depend on Supabase configuration or credentials. Protected
routes return an error when the required identity configuration is absent or
invalid. Evidence retrieval, source connectors, JEV, Decisions, and answer
generation are not part of this service increment.

## Identity and tenant boundary

Protected requests require a Supabase Auth access token in the
`Authorization: Bearer <token>` header. The service verifies its signature
using the configured project's Supabase JWKS, then checks the exact issuer, the `authenticated`
audience and role, the token type header, user subject UUID, expiry and other
time claims, and that the token is not an anonymous Auth user. Verification
uses a 30-second clock leeway. JWKS are cached for at most 300 seconds; an
unknown `kid` triggers a refresh, rate-limited to one unknown-key refresh per
second. A failed refresh or unknown key fails closed.

Only asymmetric Supabase signing algorithms `ES256` and `RS256` are accepted.
Supabase's legacy `HS256` signing secret is not accepted or stored by this
service. Before connecting a project, configure its Auth signing keys to use an
asymmetric key and set the exact active algorithm in
`SUPABASE_JWT_ALGORITHMS`. The repository does not identify a linked Supabase
project or establish its current signing-key configuration.

The access token is forwarded to Supabase's Data API with the publishable API
key, so Postgres evaluates RLS as that user. The service never uses a
`service_role` or secret API key on a user request. It uses verified `(issuer,
subject)` identity; email and client-supplied IDs are not identity evidence.
The current configuration is for one Supabase Auth issuer. Supporting another
issuer requires a trusted issuer/subject-to-internal-user mapping; identities
must not be linked by email.

`workspaces` are visible only through an active membership. Owners may update
the workspace name; members may read. Users may read only their own active
membership rows. Direct client inserts/deletes on workspaces and inserts,
updates, or deletes on memberships remain denied. The narrowly scoped
`ensure_personal_workspace()` Postgres function is executable only by the
`authenticated` role; it accepts no IDs and derives the owner from
`auth.uid()`. It atomically creates exactly one personal workspace and active
owner membership on first use. Later calls require that membership to remain
active and never restore a disabled membership. This does not allow invitations,
shared-workspace membership assignment, or role changes. The Supabase `service_role` remains a
privileged administrative role that bypasses RLS and is not used by this
service's user request path.

The current conservative role boundary allows active members to read evidence
and restricts evidence and relationship mutations to active workspace owners.
The model has no finer-grained member capabilities. Invitations, role changes,
source ingestion, and relationship edit history beyond creator/last-updater
metadata are not implemented.

## Local configuration

Use `.env.example` as a template and export the variables through your local
shell or runtime environment; this service does not load `.env` files itself.
Do not commit populated values. Required variables are:

| Variable | Meaning |
| --- | --- |
| `SUPABASE_URL` | Project API base URL, without a path; its origin must match the configured Auth issuer |
| `SUPABASE_PUBLISHABLE_KEY` | `sb_publishable_...` key used to reach the Data API; secret/service-role and legacy JWT API keys are rejected |
| `SUPABASE_JWT_ISSUER` | Exact Auth issuer, usually `https://<project-ref>.supabase.co/auth/v1` |
| `SUPABASE_JWT_AUDIENCE` | Must be `authenticated` |
| `SUPABASE_JWT_ALGORITHMS` | Exact supported asymmetric algorithms enabled for Auth, `ES256` and/or `RS256` |
| `SUPABASE_JWKS_CACHE_TTL_SECONDS` | Optional key cache duration from 1 to 300 seconds; defaults to 300 |

The JWKS endpoint is derived from the issuer as
`<issuer>/.well-known/jwks.json`. No real project values are checked into this
repository. If settings are unset, incomplete, use HTTP outside loopback, or
request an unsupported algorithm, protected routes fail closed while health
continues to work.

## Local use and tests

Use Python 3.12 or newer, from the repository root:

```bash
python -m pip install -e ".[dev,service]"
PYTHONPATH=services/evidence-service python -m evidence_service
```

The entry point binds to `0.0.0.0` and reads `PORT`, defaulting to `8000`.
The equivalent Uvicorn command is:

```bash
PYTHONPATH=services/evidence-service python -m uvicorn evidence_service.app:app \
  --host 0.0.0.0 --port "${PORT:-8000}" --no-access-log
```

Run unit and service tests from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider tests/test_evidence_service_health.py tests/test_evidence_service_auth.py
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider tests/test_evidence_service_config.py tests/test_evidence_service_repository.py
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider
```

After a client signs in through Supabase Auth, call
`PUT /v1/me/personal-workspace` with its bearer access token. The operation is
idempotent; `GET /v1/me` then lists the user's active memberships. To select a
workspace, call `GET /v1/workspaces/{workspace_id}` with the same token. The
service does not accept a user ID or workspace ID when provisioning the
personal workspace.

## Source snapshot storage

Verified source content is read from the private `source-snapshots` Supabase
Storage bucket. The read path picks its Storage adapter from the configured
project origin: plain HTTP on loopback uses the disposable local-stack
adapter, and any HTTPS non-loopback project origin uses the hosted adapter.
Both adapters send only the caller's verified bearer token plus the
publishable API key, refuse redirects, compression, and oversize bodies, and
fail closed; Storage RLS decides object access. Neither adapter accepts a
`service_role` key.

The RLS integration tests execute PostgreSQL policies under Supabase's
`authenticated` and `anon` roles with request claim context. They need the
Supabase local stack and Docker; they do not use or link a remote project:

```bash
supabase start
supabase db reset --local
supabase test db --local
supabase stop --no-backup
```

The SQL tests create synthetic users/workspaces inside rollback-only test
transactions. They cover workspace RLS write-deny policies and personal
workspace idempotence, including two-user isolation; fixture data is rolled
back after each file.

## Container and deployment status

Build the service image locally with the repository root as the context:

```bash
docker build -f services/evidence-service/Dockerfile -t evidence-service:local .
```

The image runs as a non-root user. The health check calls only `/healthz`.
This change does not configure Railway, link a Supabase project, apply a remote
migration, or deploy the service. Railway remains a future deployment target;
no hosted deployment is established by this repository change.

The account-deletion worker has a separate image (`Dockerfile.worker`), a
Railway template (`railway.worker.json`), and a names-only environment template
(`.env.worker.example`). Its deployment remains operator-controlled; see
`docs/operations/DELETION-WORKER-VERIFY-2026-11-11.md`.
