-- Server-side task ledger: retrieval metrics are persisted without task/evidence text.
create table public.task_run_ledger (
    run_id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references public.workspaces(id) on delete cascade,
    actor_user_id uuid not null references auth.users(id) on delete cascade,
    run_type text not null default 'task_context'
        check (run_type in ('task_context', 'agent_execution')),
    status text not null
        check (status in ('ok', 'no_results', 'budget_exhausted', 'failed')),
    created_at timestamptz not null default now(),
    retrieval_latency_ms integer not null check (retrieval_latency_ms >= 0),
    retrieval_tokens integer not null check (retrieval_tokens >= 0),
    retrieval_chars integer not null check (retrieval_chars >= 0),
    selected_count integer not null check (selected_count >= 0),
    chunk_count integer not null check (chunk_count >= 0),
    relationship_count integer not null check (relationship_count >= 0),
    scoring_provider text not null,
    fallback_reason text,
    use_jev boolean not null default true,
    agent_provider text,
    agent_model text,
    agent_input_tokens integer check (agent_input_tokens is null or agent_input_tokens >= 0),
    agent_output_tokens integer check (agent_output_tokens is null or agent_output_tokens >= 0),
    agent_total_tokens integer check (agent_total_tokens is null or agent_total_tokens >= 0),
    agent_cost_usd numeric(14, 8) check (agent_cost_usd is null or agent_cost_usd >= 0),
    agent_latency_ms integer check (agent_latency_ms is null or agent_latency_ms >= 0),
    quality_status text,
    quality_score numeric(6, 5) check (quality_score is null or quality_score between 0 and 1)
);

create index task_run_ledger_workspace_created_idx
    on public.task_run_ledger (workspace_id, created_at desc, run_id desc);

alter table public.task_run_ledger enable row level security;
alter table public.task_run_ledger force row level security;

revoke all on table public.task_run_ledger from public, anon;
grant select, insert on table public.task_run_ledger to authenticated;
grant all on table public.task_run_ledger to service_role;

create policy task_run_ledger_select_active_member
    on public.task_run_ledger
    for select
    to authenticated
    using (
        exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = task_run_ledger.workspace_id
              and membership.user_id = auth.uid()
              and membership.status = 'active'
        )
    );

create policy task_run_ledger_insert_active_member
    on public.task_run_ledger
    for insert
    to authenticated
    with check (
        actor_user_id = auth.uid()
        and exists (
            select 1
            from public.workspace_memberships as membership
            where membership.workspace_id = task_run_ledger.workspace_id
              and membership.user_id = auth.uid()
              and membership.status = 'active'
        )
    );

comment on table public.task_run_ledger is
    'Content-free server ledger for task-context retrieval and future agent execution metrics.';
