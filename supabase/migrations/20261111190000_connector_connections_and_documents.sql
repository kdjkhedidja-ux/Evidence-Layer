-- First connector ingestion slice: encrypted Google Drive connections and
-- normalized read-only documents. Tokens are encrypted by the service before
-- insertion; the database only stores ciphertext.

create table public.connector_connections (
    connection_id uuid primary key default pg_catalog.gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    provider text not null check (provider in (
        'google_drive', 'github', 'dropbox', 'confluence', 'notion'
    )),
    account_email text,
    scopes text[] not null default '{}',
    access_token_ciphertext text not null,
    refresh_token_ciphertext text,
    access_token_expires_at timestamptz,
    status text not null default 'connected' check (
        status in ('connected', 'error', 'disconnected')
    ),
    last_sync_at timestamptz,
    last_error_category text,
    created_at timestamptz not null default pg_catalog.clock_timestamp(),
    updated_at timestamptz not null default pg_catalog.clock_timestamp(),
    constraint connector_connections_user_provider_key unique (user_id, provider)
);

create index connector_connections_user_idx
    on public.connector_connections (user_id, provider);

create table public.connector_documents (
    document_id uuid primary key default pg_catalog.gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    connection_id uuid not null
        references public.connector_connections (connection_id) on delete cascade,
    provider text not null,
    external_id text not null,
    name text not null,
    mime_type text,
    revision text not null,
    source_url text,
    content_text text,
    content_base64 text,
    content_sha256 text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes bigint not null check (size_bytes >= 0),
    provider_updated_at timestamptz,
    synced_at timestamptz not null default pg_catalog.clock_timestamp(),
    constraint connector_documents_content_present check (
        content_text is not null or content_base64 is not null
    ),
    constraint connector_documents_revision_key
        unique (connection_id, external_id, revision)
);

create index connector_documents_connection_idx
    on public.connector_documents (connection_id, provider_updated_at desc);
create index connector_documents_user_idx
    on public.connector_documents (user_id, provider);

alter table public.connector_connections enable row level security;
alter table public.connector_connections force row level security;
alter table public.connector_documents enable row level security;
alter table public.connector_documents force row level security;

revoke all on table public.connector_connections
    from public, anon, authenticated, service_role;
revoke all on table public.connector_documents
    from public, anon, authenticated, service_role;

grant select, insert, update, delete on table public.connector_connections
    to authenticated;
grant select, insert, update, delete on table public.connector_documents
    to authenticated;

create policy connector_connections_owner_select
    on public.connector_connections
    for select
    to authenticated
    using (user_id = (select auth.uid()));

create policy connector_connections_owner_insert
    on public.connector_connections
    for insert
    to authenticated
    with check (user_id = (select auth.uid()));

create policy connector_connections_owner_update
    on public.connector_connections
    for update
    to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));

create policy connector_connections_owner_delete
    on public.connector_connections
    for delete
    to authenticated
    using (user_id = (select auth.uid()));

create policy connector_documents_owner_select
    on public.connector_documents
    for select
    to authenticated
    using (user_id = (select auth.uid()));

create policy connector_documents_owner_insert
    on public.connector_documents
    for insert
    to authenticated
    with check (user_id = (select auth.uid()));

create policy connector_documents_owner_update
    on public.connector_documents
    for update
    to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));

create policy connector_documents_owner_delete
    on public.connector_documents
    for delete
    to authenticated
    using (user_id = (select auth.uid()));
