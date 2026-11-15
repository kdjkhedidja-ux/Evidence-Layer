# Verification worker deployment and pilot — 2026-11-15

Status: **worker deployed; end-to-end pilot PASS; publication is active for the
pilot path.**

## Deployment

| Item | Value |
|---|---|
| Railway project | `evidence-layer-github-test` |
| Service | `snapshot-verification-worker` (`1475aea2-3a39-4b57-8917-45afc683a2a7`) |
| Deployment | `5869246c-7ae3-4230-a4a8-671132d1716e` |
| Source commit | `09d2747` |
| Replicas | 1 |
| Build | Railpack with `services/snapshot-verifier/Dockerfile.worker` |
| Evidence Service redeploy | `1137f3e0-3771-4e3b-9b16-31d6271a0065` |

Runtime variables are stored only in Railway: Supabase URL and host, publishable
key, dedicated verifier email/password, worker id, poll interval, and lease
seconds. No service role key is given to the worker.

## Verifier identity

A dedicated non-human Auth user was created and registered in
`private.snapshot_verification_worker`. It is not `service_role`. Its email and
password are not in the repository; the password is held in the Railway secret
store and can be rotated independently.

## Pilot

- Synthetic owner and workspace created.
- Staging endpoint returned a staged snapshot and private object key.
- The owner uploaded the object to `source-snapshots`.
- The staging trigger enqueued a job.
- The worker claimed it on its first poll:
  - `source_snapshots.state = published`
  - `snapshot_verification_jobs.state = published`
  - `attempts = 1`
- An evidence record linked to the published `source_id` and
  `source_version_id` was retrieved by task-context with both IDs exactly
  matching.

No per-workspace allowlist step was performed; claim auto-authorized the worker.

## Residuals

- One synthetic pilot workspace with an immutable published source remains.
  It contains synthetic content and no customer data.
- An empty first-attempt pilot workspace was removed; its synthetic Auth user
  may remain and can be removed with the Admin API.
- The worker remains one replica. Scale-out requires lease validation under
  multiple replicas and per-tenant rate limits.
- Member grants, staged-object cleanup, invitations/email, and 10/100-workspace
  rollout remain.

## Operations

- Worker logs are content-free (`worker_started`, `verification_published`,
  `verification_failed`).
- Failed jobs back off and become terminal on a verification mismatch.
- Rotate the verifier password on the same schedule as other worker secrets.
- Keep the exact HTTPS host pin; do not add wildcard hosts or proxy variables.
