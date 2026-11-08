-- accept_account_deletion_v2 uses auth.uid(); its owner role needs USAGE on
-- the auth schema. The original function avoided this by reading the legacy
-- GUC, which PostgREST no longer sets.
grant usage on schema auth to account_lifecycle_transition_writer;
