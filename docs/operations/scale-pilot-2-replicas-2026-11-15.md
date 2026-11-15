# Two-replica verification worker pilot — 2026-11-15

Status: **PASS.** Two worker replicas claimed and published ten new source
versions with no duplicate work and no retries.

## Setup

- Verification worker scaled to **2 replicas**.
- Ten existing synthetic pilot workspaces; one new source version staged and
  uploaded in each.
- Jobs claimed with `FOR UPDATE SKIP LOCKED` and a lease.

## Result

| Metric | Value |
|---|---|
| New versions | 10 |
| Published | 10/10 |
| Jobs with `attempts=1` | 10/10 |
| Duplicate claims | 0 |
| Failures | 0 |
| Total staging-to-published elapsed | 52.5 seconds |
| Average worker job latency | 6.89 seconds |
| Maximum worker job latency | 11.33 seconds |

The previous one-replica run took 69.1 seconds with 7.15 s average latency.
Two replicas improved throughput without changing claim correctness.

## Residual

- The worker remains at 2 replicas; further scale-out needs a 100-workspace
  load check and per-tenant rate limiting.
- Staged-object cleanup is still not automated.
- Synthetic pilot workspaces and immutable published versions remain.
