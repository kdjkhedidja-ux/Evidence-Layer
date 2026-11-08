-- The continuous worker connects through the service's postgres URL. The
-- security-definer purge functions remain owned by account_deletion_worker,
-- so current_user inside them is still the dedicated worker role; postgres
-- only needs EXECUTE to call them.
grant execute on function private.run_account_deletion_slice(uuid) to postgres;
grant execute on function private.prepare_individual_account_purge(uuid) to postgres;
grant execute on function private.mark_account_purged(uuid) to postgres;
