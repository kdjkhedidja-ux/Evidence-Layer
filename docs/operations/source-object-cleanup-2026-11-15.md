# Source-object cleanup — 2026-11-15

Status: **implemented and production-verified.**

## Policy

- Only stale `staged` versions older than 14 days are eligible.
- A version with an active verification job is skipped.
- The catalog row is never deleted; `abandoned_at` hides it and excludes it from
  quotas.
- The private Storage object is deleted by the deletion worker using the
  service-role credential.
- Published versions are never eligible.

## Mechanics

- `private.enqueue_stale_staged_sources(age_days, limit)` marks candidates
  abandoned and inserts a cleanup queue row.
- `private.claim_source_cleanup(...)` leases one object.
- The deletion worker deletes `source-snapshots/<object_key>` and records
  `object_deleted_at`.
- Failures retry hourly up to five attempts, then become dead.
- Abandoned rows are hidden by a restrictive RLS policy.

## Production verification

- Migration 51 applied and verified (51 migrations, pending=0).
- Deletion worker deployment: `749e665b-0438-4d5f-9870-046da335bfcd`.
- Evidence Service deployment with the abandoned filter:
  `7834fb46-1724-45fe-9821-c4ba7cdcd9f6`.
- A synthetic 30-day-old staged source was marked abandoned, its queue row
  reached `deleted`, the Storage object returned HTTP 400 on read, the source
  index returned `[]`, and quota usage returned zero.

## Residual

- The deletion worker remains one replica; source cleanup shares that worker.
- A dead cleanup row requires operator inspection.
- The 100-workspace pilot remains.
