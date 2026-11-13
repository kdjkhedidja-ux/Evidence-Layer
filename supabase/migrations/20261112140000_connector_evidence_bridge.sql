-- Bridge synced connector documents into the personal workspace evidence store.
-- Evidence rows remain owner-scoped and are removed with the connection.
alter table public.connector_connections
    add column workspace_id uuid references public.workspaces (id) on delete cascade;

-- Backfill existing connections to the user's personal (owner-role) workspace.
update public.connector_connections as connection
   set workspace_id = (
       select workspace.id
         from public.workspaces as workspace
         join public.workspace_memberships as membership
           on membership.workspace_id = workspace.id
        where membership.user_id = connection.user_id
          and membership.role = 'owner'
          and membership.status = 'active'
        order by workspace.created_at, workspace.id
        limit 1
   )
 where connection.workspace_id is null;

alter table public.evidence_records
    add column connector_connection_id uuid
        references public.connector_connections (connection_id) on delete cascade,
    add column connector_external_id text,
    add column connector_revision text,
    add constraint evidence_records_connector_external_key
        unique (connector_connection_id, connector_external_id);

grant insert (
    connector_connection_id, connector_external_id, connector_revision
) on table public.evidence_records to authenticated;
grant update (
    connector_connection_id, connector_external_id, connector_revision
) on table public.evidence_records to authenticated;

alter table public.connector_documents
    drop constraint connector_documents_revision_key;
alter table public.connector_documents
    add constraint connector_documents_external_id_key
        unique (connection_id, external_id);
