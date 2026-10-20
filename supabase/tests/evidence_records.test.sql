begin;

-- Supabase CLI uses a temporary login role for hosted tests. Run fixtures as
-- postgres, then switch explicitly to anon/authenticated for RLS assertions.
set local role postgres;
set local search_path = public, extensions;

create extension if not exists pgtap with schema extensions;

select plan(24);

select ok(
    (select relrowsecurity from pg_class where oid = 'public.evidence_records'::regclass),
    'RLS is enabled on evidence records'
);
select ok(
    (select relforcerowsecurity from pg_class where oid = 'public.evidence_records'::regclass),
    'RLS is forced on evidence records'
);
select is(
    (select count(*)::integer from pg_policies
     where schemaname = 'public' and tablename = 'evidence_records'
       and policyname = 'evidence_records_select_active_members'),
    1, 'a SELECT policy exists'
);
select is(
    (select count(*)::integer from pg_policies
     where schemaname = 'public' and tablename = 'evidence_records'
       and policyname = 'evidence_records_insert_active_members'),
    1, 'an INSERT policy exists'
);
select is(
    (select count(*)::integer from pg_policies
     where schemaname = 'public' and tablename = 'evidence_records'
       and policyname = 'evidence_records_update_active_members'),
    1, 'an UPDATE policy exists'
);
select is(
    (select count(*)::integer from pg_policies
     where schemaname = 'public' and tablename = 'evidence_records'
       and policyname = 'evidence_records_delete_active_members'),
    1, 'a DELETE policy exists'
);
select ok(
    not has_table_privilege('anon', 'public.evidence_records', 'SELECT'),
    'anon has no evidence-table SELECT grant'
);
select ok(
    has_table_privilege('authenticated', 'public.evidence_records', 'SELECT')
    and has_table_privilege('authenticated', 'public.evidence_records', 'DELETE')
    and has_column_privilege('authenticated', 'public.evidence_records', 'workspace_id', 'INSERT')
    and has_column_privilege('authenticated', 'public.evidence_records', 'content', 'UPDATE')
    and not has_column_privilege('authenticated', 'public.evidence_records', 'workspace_id', 'UPDATE'),
    'authenticated has evidence CRUD grants but cannot update ownership'
);
select ok(
    not has_table_privilege('service_role', 'public.evidence_records', 'SELECT')
    and not has_table_privilege('service_role', 'public.evidence_records', 'INSERT')
    and not has_table_privilege('service_role', 'public.evidence_records', 'UPDATE')
    and not has_table_privilege('service_role', 'public.evidence_records', 'DELETE'),
    'the service_role has no evidence-table grants'
);

insert into auth.users (id, aud, role, email)
values
    ('10000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'a@example.test'),
    ('20000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'b@example.test');

insert into public.workspaces (id, name)
values
    ('a1000000-0000-4000-8000-000000000001', 'A primary'),
    ('a2000000-0000-4000-8000-000000000002', 'A secondary'),
    ('b3000000-0000-4000-8000-000000000003', 'B only');

insert into public.workspace_memberships (workspace_id, user_id, role, status)
values
    ('a1000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', 'owner', 'active'),
    ('a2000000-0000-4000-8000-000000000002', '10000000-0000-4000-8000-000000000001', 'member', 'active'),
    ('b3000000-0000-4000-8000-000000000003', '10000000-0000-4000-8000-000000000001', 'member', 'inactive'),
    ('b3000000-0000-4000-8000-000000000003', '20000000-0000-4000-8000-000000000002', 'owner', 'active');

insert into public.evidence_records (id, workspace_id, content)
values
    ('e1000000-0000-4000-8000-000000000001', 'a1000000-0000-4000-8000-000000000001', 'A private evidence'),
    ('e2000000-0000-4000-8000-000000000002', 'b3000000-0000-4000-8000-000000000003', 'B private evidence');

create temporary table evidence_rls_observations (
    name text primary key,
    value text not null
);
grant insert, select on evidence_rls_observations to authenticated;

-- Over-grant DML only in this rollback-only transaction so the negative
-- assertions exercise RLS and the immutable-ownership trigger independently
-- of the narrower API grants installed by the migration.
grant insert, update, delete on public.evidence_records to authenticated;

set local role authenticated;
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
select set_config(
    'request.jwt.claims',
    '{"sub":"10000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}',
    true
);

insert into evidence_rls_observations
values
    ('a_read_own', (
        select count(*)::text from public.evidence_records
        where workspace_id = 'a1000000-0000-4000-8000-000000000001'
    )),
    ('a_read_b', (
        select count(*)::text from public.evidence_records
        where workspace_id = 'b3000000-0000-4000-8000-000000000003'
    ));

insert into public.evidence_records (workspace_id, content)
values ('a1000000-0000-4000-8000-000000000001', 'A added evidence');

insert into evidence_rls_observations
values ('a_insert_own', (
    select count(*)::text from public.evidence_records
    where workspace_id = 'a1000000-0000-4000-8000-000000000001'
));

do $$
begin
    insert into public.evidence_records (workspace_id, content)
    values ('b3000000-0000-4000-8000-000000000003', 'forged into B');
    insert into pg_temp.evidence_rls_observations values ('a_insert_b', 'not denied');
exception when insufficient_privilege then
    insert into pg_temp.evidence_rls_observations values ('a_insert_b', '42501');
end $$;

do $$
declare
    affected integer;
    error_code text;
begin
    update public.evidence_records
    set content = 'forged update'
    where id = 'e2000000-0000-4000-8000-000000000002';
    get diagnostics affected = row_count;
    insert into pg_temp.evidence_rls_observations values ('a_update_b', affected::text);

    delete from public.evidence_records
    where id = 'e2000000-0000-4000-8000-000000000002';
    get diagnostics affected = row_count;
    insert into pg_temp.evidence_rls_observations values ('a_delete_b', affected::text);

    begin
        update public.evidence_records
        set workspace_id = 'a2000000-0000-4000-8000-000000000002'
        where id = 'e1000000-0000-4000-8000-000000000001';
        insert into pg_temp.evidence_rls_observations values ('a_change_workspace', 'not denied');
    exception when insufficient_privilege then
        get stacked diagnostics error_code = returned_sqlstate;
        insert into pg_temp.evidence_rls_observations values ('a_change_workspace', error_code);
    end;

    update public.evidence_records
    set content = 'A updated evidence'
    where id = 'e1000000-0000-4000-8000-000000000001';
    get diagnostics affected = row_count;
    insert into pg_temp.evidence_rls_observations values ('a_update_own', affected::text);
end $$;

set local role postgres;

set local role authenticated;
select set_config('request.jwt.claim.sub', '20000000-0000-4000-8000-000000000002', true);
select set_config(
    'request.jwt.claims',
    '{"sub":"20000000-0000-4000-8000-000000000002","role":"authenticated","aud":"authenticated"}',
    true
);

insert into evidence_rls_observations
values
    ('b_read_own', (
        select count(*)::text from public.evidence_records
        where workspace_id = 'b3000000-0000-4000-8000-000000000003'
    )),
    ('b_read_a', (
        select count(*)::text from public.evidence_records
        where workspace_id = 'a1000000-0000-4000-8000-000000000001'
    ));

do $$
begin
    insert into public.evidence_records (workspace_id, content)
    values ('a1000000-0000-4000-8000-000000000001', 'forged into A');
    insert into pg_temp.evidence_rls_observations values ('b_insert_a', 'not denied');
exception when insufficient_privilege then
    insert into pg_temp.evidence_rls_observations values ('b_insert_a', '42501');
end $$;

do $$
declare
    affected integer;
    error_code text;
begin
    update public.evidence_records
    set content = 'forged update'
    where id = 'e1000000-0000-4000-8000-000000000001';
    get diagnostics affected = row_count;
    insert into pg_temp.evidence_rls_observations values ('b_update_a', affected::text);

    delete from public.evidence_records
    where id = 'e1000000-0000-4000-8000-000000000001';
    get diagnostics affected = row_count;
    insert into pg_temp.evidence_rls_observations values ('b_delete_a', affected::text);

    begin
        update public.evidence_records
        set workspace_id = 'a1000000-0000-4000-8000-000000000001'
        where id = 'e2000000-0000-4000-8000-000000000002';
        insert into pg_temp.evidence_rls_observations values ('b_change_workspace', 'not denied');
    exception when insufficient_privilege then
        get stacked diagnostics error_code = returned_sqlstate;
        insert into pg_temp.evidence_rls_observations values ('b_change_workspace', error_code);
    end;
end $$;

set local role postgres;

-- Keep A's fixture until B's cross-workspace UPDATE and DELETE assertions have
-- run, so a zero affected-row count proves RLS rather than a missing record.
set local role authenticated;
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
select set_config(
    'request.jwt.claims',
    '{"sub":"10000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}',
    true
);
do $$
declare
    affected integer;
begin
    delete from public.evidence_records
    where id = 'e1000000-0000-4000-8000-000000000001';
    get diagnostics affected = row_count;
    insert into pg_temp.evidence_rls_observations values ('a_delete_own', affected::text);
end $$;
set local role postgres;

select is((select value from evidence_rls_observations where name = 'a_read_own'), '1', 'A reads evidence in an active workspace');
select is((select value from evidence_rls_observations where name = 'a_read_b'), '0', 'A with inactive membership cannot read B workspace evidence');
select is((select value from evidence_rls_observations where name = 'a_insert_own'), '2', 'A can insert evidence into an active workspace');
select is((select value from evidence_rls_observations where name = 'a_insert_b'), '42501', 'A with inactive membership cannot insert into B workspace');
select is((select value from evidence_rls_observations where name = 'a_update_b'), '0', 'A with inactive membership cannot update B evidence');
select is((select value from evidence_rls_observations where name = 'a_delete_b'), '0', 'A with inactive membership cannot delete B evidence');
select is((select value from evidence_rls_observations where name = 'a_change_workspace'), '42501', 'A cannot move evidence even to another active workspace');
select is((select value from evidence_rls_observations where name = 'a_update_own'), '1', 'A can update evidence in its workspace');
select is((select value from evidence_rls_observations where name = 'a_delete_own'), '1', 'A can delete evidence in its workspace');
select is((select value from evidence_rls_observations where name = 'b_read_own'), '1', 'B reads evidence in its workspace');
select is((select value from evidence_rls_observations where name = 'b_read_a'), '0', 'B cannot read evidence from A workspace');
select is((select value from evidence_rls_observations where name = 'b_insert_a'), '42501', 'B cannot insert evidence into A workspace');
select is((select value from evidence_rls_observations where name = 'b_update_a'), '0', 'B cannot update A evidence');
select is((select value from evidence_rls_observations where name = 'b_delete_a'), '0', 'B cannot delete A evidence');
select is((select value from evidence_rls_observations where name = 'b_change_workspace'), '42501', 'B cannot move evidence into A workspace');

select * from finish();
rollback;
