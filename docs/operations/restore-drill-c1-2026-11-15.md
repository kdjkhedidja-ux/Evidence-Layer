# C1 isolated restore drill — 2026-11-15

Status: **PASS.** Reproducible isolated restore completed with matching source
and restored counts and a clean deletion reconciliation.

## Method

`scripts/restore_drill.py`:

1. creates a read-only logical dump through the verified Supabase pooler;
2. starts a disposable PostgreSQL 17 container;
3. creates production non-system roles plus `auth`/`extensions`/`storage`
   stubs;
4. restores `pre-data`, `data`, Auth FK stubs, and `post-data`;
5. runs `evidence_service.restore_reconciliation`;
6. writes content-free JSON evidence and removes the container and local dump.

Command:

```text
python scripts/restore_drill.py --evidence /tmp/restore-drill-c1.json
```

## Evidence

| Item | Value |
|---|---|
| Dump | 893,541 bytes |
| Source / restored workspaces | 125 / 125 |
| Source / restored evidence | 57 / 57 |
| Source / restored deletion requests | 51 / 51 |
| Source / restored audit rows | 562 / 562 |
| Source / restored memberships | 126 / 126 |
| Restore warning | expected `CREATE SCHEMA public` precondition only |
| Verifier | `status=clean`, `requests_checked=42`, `failures=[]` |

## C1 decision

**C1 is closed** for the isolated synthetic restore scope. The drill is
reproducible and does not write to production.

Residual: this is a database restore, not a provider data-plane/Auth/Storage
restore. C7 remains separate.
