# Production migration plan — 2026-11-07

Status: **DRAFT, NOT APPLIED**. The live database is behind the reviewed code. This plan lists what must happen before the account deletion panel can be enabled.

## Current gap

Anonymous probes against `https://slxfsjnsrvfkdtxayiud.supabase.co` return PostgREST `PGRST205` for:

- `public.account_lifecycle`
- `public.data_deletion_requests`
- `public.data_deletion_manifest`
- `public.data_deletion_work_set`
- `public.evidence_deletion_audit`

Core tables exist and deny anonymous SELECT (`42501`), so access fails closed.

## Migrations to apply, in order

1. `20261102120000_account_lifecycle.sql`
2. `20261105100000_evidence_deletion_audit.sql`
3. `20261105110000_deletion_request_manifest.sql`
4. `20261105120000_account_deletion_worker_slice.sql`
5. `20261105130000_account_application_purge_slice.sql`
6. `20261105140000_account_evidence_ownership_purge.sql`
7. `20261105150000_deletion_restore_reconciliation.sql`

Any newer migration reviewed after this date must be appended in version order.

## Preconditions

- [ ] Human approval from the data owner and security reviewer.
- [ ] Confirmed recent backup or PITR recovery point for the production project.
- [ ] A recorded restore destination and owner for rollback.
- [ ] Reviewed commit SHA and green `scripts/supabase_migration_guard.py validate` / `classify`.
- [ ] Production environment inventory: no `service_role` in the web app, worker credentials absent from the browser.
- [ ] A staging apply of the same commit against a disposable database, with the pgTAP suite green.

## Apply

Use the repository release process for the approved commit. Do not apply individual statements by hand. Capture:

- the project ref and commit SHA;
- the migration versions and checksums;
- operator identity and timestamps;
- apply output and any notices.

## Post-apply verification

```sh
URL=https://slxfsjnsrvfkdtxayiud.supabase.co
KEY=<publishable key>
for t in account_lifecycle data_deletion_requests data_deletion_manifest data_deletion_work_set evidence_deletion_audit; do
  curl -s -o /dev/null -w "$t %{http_code}\n" -H "apikey: $KEY" "$URL/rest/v1/$t?select=*&limit=1"
done
```

Expected: HTTP 200 with an empty array or 401/403, never `PGRST205`.

Then, with a staging user:

1. Confirm protected routes still work for an `available` account.
2. Create a deletion request, accept it, and confirm data routes return 403.
3. Confirm the audit row exists and contains no user content.
4. Confirm a second accept is idempotent.
5. Verify the account cannot be reopened without a controlled transition.

## Enable the UI

Only after verification, set `NEXT_PUBLIC_DELETION_REQUESTS_ENABLED=true` in the web build environment and redeploy. Until then the account panel does not render the deletion section.

## Rollback

- Restore from the verified recovery point if the migration must be reverted; do not hand-drop tables in production.
- Because the migrations are additive, leaving them in place with the UI disabled is the safer partial rollback.
- Record the incident, the restore timestamp, and the deletion state of any test account.

## Out of scope

Storage object purge, published snapshot deletion, terminal `purged`, worker activation, and provider PITR proof remain separate releases.
