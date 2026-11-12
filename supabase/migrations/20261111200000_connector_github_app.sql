-- GitHub App connections use a server-side installation token minted on demand;
-- no user access token is persisted. Store the installation reference instead.
alter table public.connector_connections
    alter column access_token_ciphertext drop not null;

alter table public.connector_connections
    add column provider_config jsonb not null default '{}'::jsonb;
