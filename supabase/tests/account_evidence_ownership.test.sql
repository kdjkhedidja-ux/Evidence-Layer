begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(12);

insert into auth.users (id, aud, role, email)
values
    ('a1000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'p4b-owner@example.test'),
    ('a1000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'p4b-shared@example.test');
insert into public.workspaces (id, name)
values
    ('a2000000-0000-4000-8000-000000000001', 'P4b personal candidate'),
    ('a2000000-0000-4000-8000-000000000002', 'P4b shared workspace');
insert into public.workspace_memberships (workspace_id, user_id, role, status)
values
    ('a2000000-0000-4000-8000-000000000001', 'a1000000-0000-4000-8000-000000000001', 'owner', 'active'),
    ('a2000000-0000-4000-8000-000000000002', 'a1000000-0000-4000-8000-000000000001', 'owner', 'active'),
    ('a2000000-0000-4000-8000-000000000002', 'a1000000-0000-4000-8000-000000000002', 'member', 'active');
insert into public.evidence_records (id, workspace_id, content)
values
    ('a3000000-0000-4000-8000-000000000001', 'a2000000-0000-4000-8000-000000000001', 'sole owner evidence'),
    ('a3000000-0000-4000-8000-000000000003', 'a2000000-0000-4000-8000-000000000001', 'sole owner evidence two'),
    ('a3000000-0000-4000-8000-000000000002', 'a2000000-0000-4000-8000-000000000002', 'shared evidence');
select set_config('request.jwt.claim.sub', 'a1000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    '{"sub":"a1000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}', true);

insert into public.evidence_relationships (
    id, workspace_id, evidence_id_a, evidence_id_b, relation_type,
    rationale, provenance_note, created_by, updated_by
) values (
    'a4000000-0000-4000-8000-000000000001',
    'a2000000-0000-4000-8000-000000000001',
    'a3000000-0000-4000-8000-000000000001',
    'a3000000-0000-4000-8000-000000000003',
    'supports', 'sole owner relationship', 'sole owner provenance',
    'a1000000-0000-4000-8000-000000000001',
    'a1000000-0000-4000-8000-000000000001'
);

set local role authenticated;
select set_config('request.jwt.claim.sub', 'a1000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims',
    '{"sub":"a1000000-0000-4000-8000-000000000001","role":"authenticated","aud":"authenticated"}', true);
insert into public.data_deletion_requests (
    request_id, requester_user_id, scope_type, scope_id, idempotency_key
) values (
    'a5000000-0000-4000-8000-000000000001',
    'a1000000-0000-4000-8000-000000000001', 'account',
    'a1000000-0000-4000-8000-000000000001', 'p4b-key'
);
set local role account_lifecycle_transition_writer;
update public.data_deletion_requests set status = 'accepted', accepted_at = clock_timestamp()
where request_id = 'a5000000-0000-4000-8000-000000000001';

update public.account_lifecycle
set state = 'revocation_pending', request_id = 'a5000000-0000-4000-8000-000000000001'
where subject_id = (select requester_user_id from public.data_deletion_requests where request_id = 'a5000000-0000-4000-8000-000000000001');

set local role account_deletion_worker;
select ok((private.run_account_deletion_slice(
    'a5000000-0000-4000-8000-000000000001'
) ->> 'status') = 'purge_pending', 'sole-owner evidence slice verifies');

set local role postgres;
select is((select count(*)::integer from public.data_deletion_work_set
    where request_id = 'a5000000-0000-4000-8000-000000000001'
      and target_type = 'evidence_record'), 2, 'only sole-owner evidence enters work set');
select is((select count(*)::integer from public.data_deletion_work_set
    where request_id = 'a5000000-0000-4000-8000-000000000001'
      and target_type = 'relationship'), 1, 'sole-owner relationship enters work set');
select is((select count(*)::integer from public.evidence_records
    where id = 'a3000000-0000-4000-8000-000000000001'), 0, 'sole-owner evidence is deleted');
select is((select count(*)::integer from public.evidence_relationships
    where id = 'a4000000-0000-4000-8000-000000000001'), 0, 'sole-owner relationship is deleted');
select is((select count(*)::integer from public.evidence_records
    where id = 'a3000000-0000-4000-8000-000000000002'), 1, 'shared workspace evidence remains');
select is((select count(*)::integer from public.workspace_memberships
    where user_id = 'a1000000-0000-4000-8000-000000000001'), 2,
    'owner memberships remain for ownership resolution');
select ok(not exists (
    select 1 from public.data_deletion_work_set
    where request_id = 'a5000000-0000-4000-8000-000000000001'
      and target_type = 'evidence_record'
      and target_id not in ('a3000000-0000-4000-8000-000000000001', 'a3000000-0000-4000-8000-000000000003')
), 'work set contains only exact sole-owner targets');
select is((select status from public.data_deletion_requests
    where request_id = 'a5000000-0000-4000-8000-000000000001'), 'purge_pending',
    'ownership slice remains non-terminal');
select is((select count(*)::integer from public.evidence_deletion_audit
    where request_id = 'a5000000-0000-4000-8000-000000000001'
      and action in ('workset_frozen', 'purge_effect', 'purge_verified')), 3,
    'ownership work set and effects are audited');
select ok((select state = 'purge_pending' from public.account_lifecycle
    where subject_id = 'a1000000-0000-4000-8000-000000000001'),
    'account remains denied after ownership slice');
select ok(not exists (
    select 1 from pg_catalog.pg_roles
    where rolname = 'account_deletion_worker' and rolcanlogin
), 'worker remains non-login');

revoke account_lifecycle_transition_writer from postgres;
select * from finish();
rollback;
