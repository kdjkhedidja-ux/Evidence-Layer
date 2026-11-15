# Source-object cleanup — 2026-11-15

Status: **implemented; migration pending production apply.**

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

## Residual

- The deletion worker remains one replica; source cleanup shares that worker.
- A dead cleanup row requires operator inspection.
- The 100-workspace pilot remains.
