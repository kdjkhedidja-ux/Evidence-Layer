begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(14);

select ok(
    (select relrowsecurity from pg_class where oid = 'public.evidence_relationships'::regclass),
    'relationship RLS is enabled'
);
select ok(
    (select relforcerowsecurity from pg_class where oid = 'public.evidence_relationships'::regclass),
    'relationship RLS is forced'
);
select is(
    (select count(*)::integer from pg_policies
     where schemaname = 'public' and tablename = 'evidence_relationships'),
    6, 'six explicit relationship policies exist'
);
select ok(
    not has_table_privilege('anon', 'public.evidence_relationships', 'SELECT')
    and not has_table_privilege('service_role', 'public.evidence_relationships', 'SELECT'),
    'anonymous and service roles have no relationship-table access'
);
select ok(
    has_table_privilege('authenticated', 'public.evidence_relationships', 'SELECT')
    and has_table_privilege('authenticated', 'public.evidence_relationships', 'INSERT')
    and has_table_privilege('authenticated', 'public.evidence_relationships', 'DELETE')
    and has_column_privilege('authenticated', 'public.evidence_relationships', 'rationale', 'UPDATE')
    and not has_column_privilege('authenticated', 'public.evidence_relationships', 'workspace_id', 'UPDATE')
    and not has_column_privilege('authenticated', 'public.evidence_relationships', 'created_by', 'UPDATE'),
    'authenticated can read and mutate relationship content but not its identity'
);
select is(
    (select count(*)::integer from pg_constraint
     where conrelid = 'public.evidence_relationships'::regclass
       and conname in (
           'evidence_relationships_evidence_a_fkey',
           'evidence_relationships_evidence_b_fkey'
       )
       and contype = 'f'
       and confrelid = 'public.evidence_records'::regclass),
    2, 'both relationship ends are constrained to evidence records'
);
select is(
    (select count(*)::integer from pg_constraint
     where conrelid = 'public.evidence_relationships'::regclass
       and conname = 'evidence_relationships_pair_type_unique'
       and contype = 'u'),
    1, 'a workspace cannot duplicate the same relationship type for a pair'
);

insert into auth.users (id, aud, role, email)
values
    ('10000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'owner@example.test'),
    ('20000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'member@example.test'),
    ('30000000-0000-4000-8000-000000000003', 'authenticated', 'authenticated', 'other@example.test');

insert into public.workspaces (id, name)
values
    ('a1000000-0000-4000-8000-000000000001', 'Workspace A'),
    ('b3000000-0000-4000-8000-000000000003', 'Workspace B');

insert into public.workspace_memberships (workspace_id, user_id, role, status)
values
    ('a1000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', 'owner', 'active'),
    ('a1000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000002', 'member', 'active'),
    ('b3000000-0000-4000-8000-000000000003', '30000000-0000-4000-8000-000000000003', 'owner', 'active');

insert into public.evidence_records (id, workspace_id, content)
values
    ('e1000000-0000-4000-8000-000000000001', 'a1000000-0000-4000-8000-000000000001', 'A first'),
    ('e2000000-0000-4000-8000-000000000002', 'a1000000-0000-4000-8000-000000000001', 'A second'),
    ('e3000000-0000-4000-8000-000000000003', 'b3000000-0000-4000-8000-000000000003', 'B first');

create temporary table relationship_rls_observations (
    name text primary key,
    value text not null
);
grant insert, select on relationship_rls_observations to authenticated;

set local role authenticated;
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
select set_config(
    'request.jwt.claims',
    '{"sub":"10000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}',
    true
);

insert into public.evidence_relationships (
    workspace_id, evidence_id_a, evidence_id_b, relation_type, rationale, provenance_note
)
values (
    'a1000000-0000-4000-8000-000000000001',
    'e1000000-0000-4000-8000-000000000001',
    'e2000000-0000-4000-8000-000000000002',
    'contradicts',
    'The records state different dates.',
    'Owner compared both original records.'
);

insert into relationship_rls_observations
values ('owner_sees_own', (
    select count(*)::text from public.evidence_relationships
    where workspace_id = 'a1000000-0000-4000-8000-000000000001'
));

select set_config('request.jwt.claim.sub', '20000000-0000-4000-8000-000000000002', true);
select set_config(
    'request.jwt.claims',
    '{"sub":"20000000-0000-4000-8000-000000000002","role":"authenticated","aud":"authenticated"}',
    true
);

insert into relationship_rls_observations
values
    ('member_reads_a', (
        select count(*)::text from public.evidence_relationships
        where workspace_id = 'a1000000-0000-4000-8000-000000000001'
    )),
    ('member_reads_b', (
        select count(*)::text from public.evidence_relationships
        where workspace_id = 'b3000000-0000-4000-8000-000000000003'
    ));

do $$
declare
    affected integer;
begin
    update public.evidence_relationships
    set rationale = 'unauthorized'
    where workspace_id = 'a1000000-0000-4000-8000-000000000001';
    get diagnostics affected = row_count;
    insert into pg_temp.relationship_rls_observations values ('member_update_rows', affected::text);

    delete from public.evidence_relationships
    where workspace_id = 'a1000000-0000-4000-8000-000000000001';
    get diagnostics affected = row_count;
    insert into pg_temp.relationship_rls_observations values ('member_delete_rows', affected::text);
end $$;

do $$
begin
    insert into public.evidence_relationships (
        workspace_id, evidence_id_a, evidence_id_b, relation_type, rationale, provenance_note
    )
    values (
        'a1000000-0000-4000-8000-000000000001',
        'e1000000-0000-4000-8000-000000000001',
        'e2000000-0000-4000-8000-000000000002',
        'supports',
        'unauthorized insert',
        'unauthorized'
    );
    insert into pg_temp.relationship_rls_observations values ('member_insert', 'not denied');
exception when insufficient_privilege then
    insert into pg_temp.relationship_rls_observations values ('member_insert', '42501');
end $$;

select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
select set_config(
    'request.jwt.claims',
    '{"sub":"10000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}',
    true
);

update public.evidence_relationships
set rationale = 'Rechecked; the differing dates remain explicit.'
where workspace_id = 'a1000000-0000-4000-8000-000000000001';

select is((select value from relationship_rls_observations where name = 'owner_sees_own'), '1', 'owner can create and read a relationship');
select is((select value from relationship_rls_observations where name = 'member_reads_a'), '1', 'active member can read its workspace relationship');
select is((select value from relationship_rls_observations where name = 'member_reads_b'), '0', 'member cannot read a different workspace');
select is((select value from relationship_rls_observations where name = 'member_insert'), '42501', 'member cannot insert a relationship');
select is((select value from relationship_rls_observations where name = 'member_update_rows'), '0', 'member cannot update a relationship');
select is((select value from relationship_rls_observations where name = 'member_delete_rows'), '0', 'member cannot delete a relationship');
select is(
    (select updated_by::text from public.evidence_relationships
     where workspace_id = 'a1000000-0000-4000-8000-000000000001'),
    '10000000-0000-4000-8000-000000000001',
    'update trigger records the verified owner identity'
);

select * from finish();
rollback;
