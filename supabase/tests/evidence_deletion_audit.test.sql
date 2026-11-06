begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(13);

select ok(
    (select relrowsecurity and relforcerowsecurity
     from pg_catalog.pg_class
     where oid = 'public.evidence_deletion_audit'::regclass),
    'deletion audit RLS is enabled and forced'
);
select is(
    (select count(*)::integer
     from pg_catalog.pg_policies
     where schemaname = 'public' and tablename = 'evidence_deletion_audit'),
    8,
    'audit has explicit deny and worker-insert policies'
);
select ok(
    not pg_catalog.has_table_privilege('authenticated', 'public.evidence_deletion_audit', 'SELECT')
    and not pg_catalog.has_table_privilege('authenticated', 'public.evidence_deletion_audit', 'INSERT')
    and not pg_catalog.has_table_privilege('authenticated', 'public.evidence_deletion_audit', 'UPDATE')
    and not pg_catalog.has_table_privilege('authenticated', 'public.evidence_deletion_audit', 'DELETE'),
    'authenticated clients have no audit table DML privileges'
);

insert into auth.users (id, aud, role, email)
values
    ('10000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'audit-owner-a@example.test'),
    ('30000000-0000-4000-8000-000000000003', 'authenticated', 'authenticated', 'audit-owner-b@example.test');

insert into public.workspaces (id, name)
values
    ('a1000000-0000-4000-8000-000000000001', 'Audit workspace A'),
    ('b3000000-0000-4000-8000-000000000003', 'Audit workspace B');

insert into public.workspace_memberships (workspace_id, user_id, role, status)
values
    ('a1000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', 'owner', 'active'),
    ('b3000000-0000-4000-8000-000000000003', '30000000-0000-4000-8000-000000000003', 'owner', 'active');

insert into public.evidence_records (id, workspace_id, content)
values
    ('e1000000-0000-4000-8000-000000000001', 'a1000000-0000-4000-8000-000000000001', 'audit evidence A1'),
    ('e2000000-0000-4000-8000-000000000002', 'a1000000-0000-4000-8000-000000000001', 'audit evidence A2'),
    ('e5000000-0000-4000-8000-000000000005', 'a1000000-0000-4000-8000-000000000001', 'audit evidence A3'),
    ('e3000000-0000-4000-8000-000000000003', 'b3000000-0000-4000-8000-000000000003', 'audit evidence B1'),
    ('e4000000-0000-4000-8000-000000000004', 'b3000000-0000-4000-8000-000000000003', 'audit evidence B2');

select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    '{"sub":"10000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}', true);
insert into public.evidence_relationships (
    id, workspace_id, evidence_id_a, evidence_id_b, relation_type,
    rationale, provenance_note, created_by, updated_by
) values
    ('f1000000-0000-4000-8000-000000000001', 'a1000000-0000-4000-8000-000000000001',
     'e1000000-0000-4000-8000-000000000001', 'e2000000-0000-4000-8000-000000000002',
     'supports', 'Audit fixture relationship', 'Audit fixture provenance',
     '10000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001'),
    ('f3000000-0000-4000-8000-000000000003', 'a1000000-0000-4000-8000-000000000001',
     'e2000000-0000-4000-8000-000000000002', 'e5000000-0000-4000-8000-000000000005',
     'supports', 'Audit fixture relationship direct', 'Audit fixture provenance direct',
     '10000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001');

select set_config('request.jwt.claim.sub', '30000000-0000-4000-8000-000000000003', true);
select set_config('request.jwt.claims',
    '{"sub":"30000000-0000-4000-8000-000000000003","role":"authenticated","aud":"authenticated"}', true);
insert into public.evidence_relationships (
    id, workspace_id, evidence_id_a, evidence_id_b, relation_type,
    rationale, provenance_note, created_by, updated_by
) values
    ('f2000000-0000-4000-8000-000000000002', 'b3000000-0000-4000-8000-000000000003',
     'e3000000-0000-4000-8000-000000000003', 'e4000000-0000-4000-8000-000000000004',
     'contradicts', 'Audit fixture relationship B', 'Audit fixture provenance B',
     '30000000-0000-4000-8000-000000000003', '30000000-0000-4000-8000-000000000003');

create temporary table audit_observations (name text primary key, value text not null);
grant insert, select on audit_observations to authenticated;

set local role authenticated;
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
select set_config(
    'request.jwt.claims',
    '{"sub":"10000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}',
    true
);

delete from public.evidence_records
where id = 'e1000000-0000-4000-8000-000000000001'
  and workspace_id = 'a1000000-0000-4000-8000-000000000001';
insert into audit_observations values ('evidence_delete', 'pending');

delete from public.evidence_relationships
where id = 'f2000000-0000-4000-8000-000000000002'
  and workspace_id = 'b3000000-0000-4000-8000-000000000003';
insert into audit_observations values ('cross_workspace_delete', 'pending');

delete from public.evidence_relationships
where id = 'f3000000-0000-4000-8000-000000000003'
  and workspace_id = 'a1000000-0000-4000-8000-000000000001';
insert into audit_observations values ('relationship_delete', 'pending');

do $attempts$
begin
    begin
        update public.evidence_deletion_audit set result = 'success';
        insert into audit_observations values ('audit_update', 'allowed');
    exception when others then
        insert into audit_observations values ('audit_update', sqlstate);
    end;
    begin
        delete from public.evidence_deletion_audit;
        insert into audit_observations values ('audit_delete', 'allowed');
    exception when others then
        insert into audit_observations values ('audit_delete', sqlstate);
    end;
end
$attempts$;

set local role postgres;
select is((select count(*)::integer from public.evidence_deletion_audit
    where resource_type = 'evidence'), 1,
    'evidence DELETE creates one audit event, excluding cascaded relationship rows');
select is((select count(*)::integer from public.evidence_deletion_audit
    where resource_id = 'f2000000-0000-4000-8000-000000000002'), 0,
    'cross-workspace DELETE creates no success audit event');
select is((select count(*)::integer from public.evidence_deletion_audit
    where resource_type = 'relationship'), 1,
    'relationship DELETE creates one additional audit event, without the cascade');
select is((select count(*)::integer from public.evidence_deletion_audit), 2,
    'audit contains exactly the successful evidence and relationship events');
select is((select actor_user_id::text from public.evidence_deletion_audit where resource_type = 'evidence'),
    '10000000-0000-4000-8000-000000000001', 'evidence audit records verified actor');
select is((select workspace_id::text from public.evidence_deletion_audit where resource_type = 'evidence'),
    'a1000000-0000-4000-8000-000000000001', 'evidence audit records workspace');
select is((select resource_id::text from public.evidence_deletion_audit where resource_type = 'evidence'),
    'e1000000-0000-4000-8000-000000000001', 'evidence audit records resource');
select is((select metadata ->> 'relationship_type' from public.evidence_deletion_audit where resource_type = 'relationship' order by occurred_at limit 1),
    'supports', 'relationship audit metadata contains only compact type');
select is((select value from audit_observations where name = 'audit_update'), '42501',
    'authenticated cannot update audit rows');
select is((select value from audit_observations where name = 'audit_delete'), '42501',
    'authenticated cannot delete audit rows');

select * from finish();
rollback;
