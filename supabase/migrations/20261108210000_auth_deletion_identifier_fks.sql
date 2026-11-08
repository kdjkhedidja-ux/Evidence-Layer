-- The request, manifest, and work-set records are retained as historical
-- deletion proof after the Auth identity is deleted. Their identifier columns
-- keep the UUID values but no longer act as live foreign keys; the remaining
-- request_id foreign keys preserve the request/child relationship.
alter table public.data_deletion_requests
    drop constraint if exists data_deletion_requests_requester_user_id_fkey;
alter table public.data_deletion_requests
    drop constraint if exists data_deletion_requests_scope_id_fkey;
alter table public.data_deletion_manifest
    drop constraint if exists data_deletion_manifest_target_id_fkey;
alter table public.data_deletion_work_set
    drop constraint if exists data_deletion_work_set_principal_id_fkey;
