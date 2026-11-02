begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(18);

insert into auth.users (id, aud, role, email)
values
    ('10000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'owner@example.test'),
    ('20000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'member@example.test'),
    ('30000000-0000-4000-8000-000000000003', 'authenticated', 'authenticated', 'other@example.test');

insert into public.workspaces (id, name)
values
    ('a1000000-0000-4000-8000-000000000001', 'Gate 4 workspace A'),
    ('b3000000-0000-4000-8000-000000000003', 'Gate 4 workspace B');

insert into public.workspace_memberships (workspace_id, user_id, role, status)
values
    ('a1000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', 'owner', 'active'),
    ('a1000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000002', 'member', 'active'),
    ('b3000000-0000-4000-8000-000000000003', '30000000-0000-4000-8000-000000000003', 'owner', 'active');

insert into public.evidence_records (id, workspace_id, content)
values
    ('e1000000-0000-4000-8000-000000000001', 'a1000000-0000-4000-8000-000000000001', 'Gate 4 A one'),
    ('e2000000-0000-4000-8000-000000000002', 'a1000000-0000-4000-8000-000000000001', 'Gate 4 A two'),
    ('e3000000-0000-4000-8000-000000000003', 'b3000000-0000-4000-8000-000000000003', 'Gate 4 B one');

insert into public.source_snapshots (
    workspace_id, source_id, version_id, object_key, sha256, size_bytes, state, created_by
)
values
    (
        'a1000000-0000-4000-8000-000000000001',
        'c1000000-0000-4000-8000-000000000001',
        'd1000000-0000-4000-8000-000000000001',
        'workspace/a1000000-0000-4000-8000-000000000001/source/c1000000-0000-4000-8000-000000000001/version/d1000000-0000-4000-8000-000000000001',
        repeat('a', 64), 1, 'staged', '10000000-0000-4000-8000-000000000001'
    ),
    (
        'a1000000-0000-4000-8000-000000000001',
        'c2000000-0000-4000-8000-000000000002',
        'd2000000-0000-4000-8000-000000000002',
        'workspace/a1000000-0000-4000-8000-000000000001/source/c2000000-0000-4000-8000-000000000002/version/d2000000-0000-4000-8000-000000000002',
        repeat('b', 64), 1, 'staged', '10000000-0000-4000-8000-000000000001'
    ),
    (
        'b3000000-0000-4000-8000-000000000003',
        'c3000000-0000-4000-8000-000000000003',
        'd3000000-0000-4000-8000-000000000003',
        'workspace/b3000000-0000-4000-8000-000000000003/source/c3000000-0000-4000-8000-000000000003/version/d3000000-0000-4000-8000-000000000003',
        repeat('c', 64), 1, 'staged', '30000000-0000-4000-8000-000000000003'
    );

create temporary table gate4_observations (
    name text primary key,
    value text not null
);
grant insert, select on gate4_observations to authenticated;

set local role authenticated;
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
select set_config(
    'request.jwt.claims',
    '{"sub":"10000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}',
    true
);

insert into public.evidence_records (workspace_id, content, source_id, source_version_id)
values (
    'a1000000-0000-4000-8000-000000000001',
    'Gate 4 valid linked evidence',
    'c1000000-0000-4000-8000-000000000001',
    'd1000000-0000-4000-8000-000000000001'
);

do $$
begin
    insert into public.evidence_records (workspace_id, content, source_id)
    values ('a1000000-0000-4000-8000-000000000001', 'source only', 'c1000000-0000-4000-8000-000000000001');
    insert into pg_temp.gate4_observations values ('source_only', 'allowed');
exception when others then
    insert into pg_temp.gate4_observations values ('source_only', sqlstate);
end $$;

do $$
begin
    insert into public.evidence_records (workspace_id, content, source_version_id)
    values ('a1000000-0000-4000-8000-000000000001', 'version only', 'd1000000-0000-4000-8000-000000000001');
    insert into pg_temp.gate4_observations values ('version_only', 'allowed');
exception when others then
    insert into pg_temp.gate4_observations values ('version_only', sqlstate);
end $$;

do $$
begin
    insert into public.evidence_records (workspace_id, content, source_id, source_version_id)
    values (
        'a1000000-0000-4000-8000-000000000001',
        'missing source version',
        'c9000000-0000-4000-8000-000000000009',
        'd9000000-0000-4000-8000-000000000009'
    );
    insert into pg_temp.gate4_observations values ('missing_source_version', 'allowed');
exception when others then
    insert into pg_temp.gate4_observations values ('missing_source_version', sqlstate);
end $$;

do $$
begin
    insert into public.evidence_records (workspace_id, content, source_id, source_version_id)
    values (
        'a1000000-0000-4000-8000-000000000001',
        'mismatched source and version',
        'c1000000-0000-4000-8000-000000000001',
        'd2000000-0000-4000-8000-000000000002'
    );
    insert into pg_temp.gate4_observations values ('mismatched_source_version', 'allowed');
exception when others then
    insert into pg_temp.gate4_observations values ('mismatched_source_version', sqlstate);
end $$;

do $$
begin
    insert into public.evidence_records (workspace_id, content, source_id, source_version_id)
    values (
        'a1000000-0000-4000-8000-000000000001',
        'cross-workspace source',
        'c3000000-0000-4000-8000-000000000003',
        'd3000000-0000-4000-8000-000000000003'
    );
    insert into pg_temp.gate4_observations values ('cross_workspace_source', 'allowed');
exception when others then
    insert into pg_temp.gate4_observations values ('cross_workspace_source', sqlstate);
end $$;

insert into public.evidence_relationships (
    workspace_id, evidence_id_a, evidence_id_b, relation_type, rationale, provenance_note
)
values (
    'a1000000-0000-4000-8000-000000000001',
    'e1000000-0000-4000-8000-000000000001',
    'e2000000-0000-4000-8000-000000000002',
    'supports', 'The records corroborate one another.', 'Compared in the test fixture.'
);

do $$
begin
    insert into public.evidence_relationships (
        workspace_id, evidence_id_a, evidence_id_b, relation_type, rationale, provenance_note
    )
    values (
        'a1000000-0000-4000-8000-000000000001',
        'e1000000-0000-4000-8000-000000000001',
        'e2000000-0000-4000-8000-000000000002',
        'supports', 'Duplicate relationship.', 'Must be rejected.'
    );
    insert into pg_temp.gate4_observations values ('duplicate_relationship', 'allowed');
exception when others then
    insert into pg_temp.gate4_observations values ('duplicate_relationship', sqlstate);
end $$;

do $$
begin
    insert into public.evidence_relationships (
        workspace_id, evidence_id_a, evidence_id_b, relation_type, rationale, provenance_note
    )
    values (
        'a1000000-0000-4000-8000-000000000001',
        'e1000000-0000-4000-8000-000000000001',
        'e3000000-0000-4000-8000-000000000003',
        'supports', 'Cross-workspace endpoint.', 'Must be rejected.'
    );
    insert into pg_temp.gate4_observations values ('cross_workspace_relationship', 'allowed');
exception when others then
    insert into pg_temp.gate4_observations values ('cross_workspace_relationship', sqlstate);
end $$;

do $$
begin
    insert into public.evidence_relationships (
        workspace_id, evidence_id_a, evidence_id_b, relation_type, rationale, provenance_note
    )
    values (
        'a1000000-0000-4000-8000-000000000001',
        'e1000000-0000-4000-8000-000000000001',
        'e1000000-0000-4000-8000-000000000001',
        'supports', 'Self-reference.', 'Must be rejected.'
    );
    insert into pg_temp.gate4_observations values ('self_relationship', 'allowed');
exception when others then
    insert into pg_temp.gate4_observations values ('self_relationship', sqlstate);
end $$;

do $$
begin
    insert into public.evidence_relationships (
        workspace_id, evidence_id_a, evidence_id_b, relation_type, rationale, provenance_note
    )
    values (
        'a1000000-0000-4000-8000-000000000001',
        'e2000000-0000-4000-8000-000000000002',
        'e1000000-0000-4000-8000-000000000001',
        'supports', 'Reversed evidence pair.', 'Must be rejected.'
    );
    insert into pg_temp.gate4_observations values ('reversed_relationship', 'allowed');
exception when others then
    insert into pg_temp.gate4_observations values ('reversed_relationship', sqlstate);
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
        'invalid', 'Unsupported relation.', 'Must be rejected.'
    );
    insert into pg_temp.gate4_observations values ('invalid_relation_type', 'allowed');
exception when others then
    insert into pg_temp.gate4_observations values ('invalid_relation_type', sqlstate);
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
        'contradicts', '   ', 'Empty rationale must be rejected.'
    );
    insert into pg_temp.gate4_observations values ('empty_rationale', 'allowed');
exception when others then
    insert into pg_temp.gate4_observations values ('empty_rationale', sqlstate);
end $$;

select set_config('request.jwt.claim.sub', '30000000-0000-4000-8000-000000000003', true);
select set_config(
    'request.jwt.claims',
    '{"sub":"30000000-0000-4000-8000-000000000003","role":"authenticated","aud":"authenticated"}',
    true
);

insert into gate4_observations
values ('nonmember_reads_workspace_a', (
    select count(*)::text from public.evidence_relationships
    where workspace_id = 'a1000000-0000-4000-8000-000000000001'
));

do $$
begin
    insert into public.evidence_relationships (
        workspace_id, evidence_id_a, evidence_id_b, relation_type, rationale, provenance_note
    )
    values (
        'a1000000-0000-4000-8000-000000000001',
        'e1000000-0000-4000-8000-000000000001',
        'e2000000-0000-4000-8000-000000000002',
        'contextualizes', 'Nonmember write.', 'Must be rejected.'
    );
    insert into pg_temp.gate4_observations values ('nonmember_insert', 'allowed');
exception when others then
    insert into pg_temp.gate4_observations values ('nonmember_insert', sqlstate);
end $$;

select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
select set_config(
    'request.jwt.claims',
    '{"sub":"10000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}',
    true
);

do $$
begin
    update public.evidence_relationships
    set created_by = '20000000-0000-4000-8000-000000000002'
    where workspace_id = 'a1000000-0000-4000-8000-000000000001';
    insert into pg_temp.gate4_observations values ('relationship_identity_update', 'allowed');
exception when others then
    insert into pg_temp.gate4_observations values ('relationship_identity_update', sqlstate);
end $$;

do $$
declare
    affected integer;
begin
    delete from public.evidence_relationships
    where workspace_id = 'a1000000-0000-4000-8000-000000000001';
    get diagnostics affected = row_count;
    insert into pg_temp.gate4_observations values ('owner_delete', affected::text);
end $$;

set local role postgres;

select is(
    (select count(*)::integer from pg_policies
     where schemaname = 'public' and tablename = 'evidence_records'
       and policyname in (
           'evidence_records_insert_active_members',
           'evidence_records_update_active_members',
           'evidence_records_delete_active_members'
       )),
    0, 'all three active-member evidence write policies were removed'
);
select ok(
    to_regclass('public.evidence_records_source_link_idx') is not null
    and to_regclass('public.evidence_relationships_workspace_idx') is not null,
    'source-link and relationship workspace indexes exist'
);
select is(
    (select count(*)::integer from public.evidence_records
     where content = 'Gate 4 valid linked evidence'
       and source_id = 'c1000000-0000-4000-8000-000000000001'
       and source_version_id = 'd1000000-0000-4000-8000-000000000001'),
    1, 'an owner can create evidence with a valid source-version pair'
);
select is((select value from gate4_observations where name = 'source_only'), '23514', 'source_id without a version is rejected');
select is((select value from gate4_observations where name = 'version_only'), '23514', 'source_version_id without a source is rejected');
select is((select value from gate4_observations where name = 'missing_source_version'), '23503', 'a missing composite source reference is rejected');
select is((select value from gate4_observations where name = 'mismatched_source_version'), '23503', 'a version from another source is rejected');
select is((select value from gate4_observations where name = 'cross_workspace_source'), '23503', 'a source from another workspace is rejected');
select is((select value from gate4_observations where name = 'cross_workspace_relationship'), '23503', 'a relationship cannot reference evidence from another workspace');
select is((select value from gate4_observations where name = 'self_relationship'), '23514', 'a relationship cannot point to itself');
select is((select value from gate4_observations where name = 'reversed_relationship'), '23514', 'relationship evidence IDs must be ordered');
select is((select value from gate4_observations where name = 'invalid_relation_type'), '23514', 'unsupported relationship types are rejected');
select is((select value from gate4_observations where name = 'duplicate_relationship'), '23505', 'a workspace cannot duplicate a relationship type for a pair');
select is((select value from gate4_observations where name = 'empty_rationale'), '23514', 'relationship rationale cannot be blank');
select is((select value from gate4_observations where name = 'nonmember_reads_workspace_a'), '0', 'a nonmember cannot read another workspace relationship');
select is((select value from gate4_observations where name = 'nonmember_insert'), '42501', 'a nonmember cannot create a relationship');
select is((select value from gate4_observations where name = 'relationship_identity_update'), '42501', 'relationship identity and provenance are immutable');
select is((select value from gate4_observations where name = 'owner_delete'), '1', 'an active owner can delete a relationship');

select * from finish();
rollback;
