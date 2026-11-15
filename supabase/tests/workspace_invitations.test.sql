begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(12);

insert into auth.users (id, aud, role, email)
values
    ('83000000-0000-4000-8000-000000000021', 'authenticated', 'authenticated', 'invite-owner@example.test'),
    ('83000000-0000-4000-8000-000000000022', 'authenticated', 'authenticated', 'invite-member@example.test');

insert into public.workspaces (id, name)
values ('a4000000-0000-4000-8000-000000000021', 'Invitation workspace');

insert into public.workspace_memberships (workspace_id, user_id, role, status)
values ('a4000000-0000-4000-8000-000000000021', '83000000-0000-4000-8000-000000000021', 'owner', 'active');

set local role authenticated;
select set_config('request.jwt.claim.sub', '83000000-0000-4000-8000-000000000021', true);
select set_config('request.jwt.claims',
    '{"sub":"83000000-0000-4000-8000-000000000021","role":"authenticated","aud":"authenticated"}', true);

select (public.create_workspace_invitation(
    'a4000000-0000-4000-8000-000000000021',
    'invite-member@example.test',
    repeat('a', 64),
    clock_timestamp() + interval '7 days'
) ->> 'invitation_id') as first_invitation_id
\gset

select is(:'first_invitation_id' is not null, true, 'owner creates an invitation');
select is(
    (select count(*)::integer from public.list_workspace_invitations(
        'a4000000-0000-4000-8000-000000000021'
    )),
    1,
    'owner lists the pending invitation'
);

set local role authenticated;
select set_config('request.jwt.claim.sub', '83000000-0000-4000-8000-000000000022', true);
select set_config('request.jwt.claims',
    '{"sub":"83000000-0000-4000-8000-000000000022","role":"authenticated","aud":"authenticated"}', true);

select is(
    (public.accept_workspace_invitation(repeat('a', 64)) ->> 'workspace_id')::uuid,
    'a4000000-0000-4000-8000-000000000021'::uuid,
    'invitee accepts the invitation'
);

set local role postgres;
select is(
    (select status from public.workspace_memberships
      where workspace_id = 'a4000000-0000-4000-8000-000000000021'
        and user_id = '83000000-0000-4000-8000-000000000022'),
    'active',
    'acceptance inserts an active membership'
);
select is(
    (select role from public.workspace_memberships
      where workspace_id = 'a4000000-0000-4000-8000-000000000021'
        and user_id = '83000000-0000-4000-8000-000000000022'),
    'member',
    'acceptance uses the invitation member role'
);
select set_config('request.jwt.claim.sub', '83000000-0000-4000-8000-000000000021', true);
select set_config('request.jwt.claims',
    '{"sub":"83000000-0000-4000-8000-000000000021","role":"authenticated","aud":"authenticated"}', true);
select ok(
    (select out_accepted_at is not null from public.list_workspace_invitations(
        'a4000000-0000-4000-8000-000000000021'
    ) where out_invitation_id = :'first_invitation_id'::uuid),
    'the accepted invitation is marked accepted'
);

set local role authenticated;
set local role authenticated;
select set_config('request.jwt.claim.sub', '83000000-0000-4000-8000-000000000022', true);
select set_config('request.jwt.claims',
    '{"sub":"83000000-0000-4000-8000-000000000022","role":"authenticated","aud":"authenticated"}', true);
select throws_ok(
    $$select * from public.accept_workspace_invitation(repeat('a', 64))$$,
    'P0002', null,
    'an accepted invitation cannot be accepted again'
);

select set_config('request.jwt.claim.sub', '83000000-0000-4000-8000-000000000022', true);
select set_config('request.jwt.claims',
    '{"sub":"83000000-0000-4000-8000-000000000022","role":"authenticated","aud":"authenticated"}', true);
select throws_ok(
    $$select * from public.create_workspace_invitation(
        'a4000000-0000-4000-8000-000000000021',
        'other@example.test',
        repeat('c', 64),
        clock_timestamp() + interval '7 days'
    )$$,
    '42501', null,
    'a non-owner cannot create invitations'
);

set local role authenticated;
select set_config('request.jwt.claim.sub', '83000000-0000-4000-8000-000000000021', true);
select set_config('request.jwt.claims',
    '{"sub":"83000000-0000-4000-8000-000000000021","role":"authenticated","aud":"authenticated"}', true);

select throws_ok(
    $$select * from public.create_workspace_invitation(
        'a4000000-0000-4000-8000-000000000021',
        'not-an-email',
        repeat('c', 64),
        clock_timestamp() + interval '7 days'
    )$$,
    '23514', null,
    'an invalid email is rejected'
);

select (public.create_workspace_invitation(
    'a4000000-0000-4000-8000-000000000021',
    'other@example.test',
    repeat('b', 64),
    clock_timestamp() + interval '7 days'
) ->> 'invitation_id') as second_invitation_id
\gset

select public.revoke_workspace_invitation(:'second_invitation_id'::uuid);

set local role postgres;
select ok(
    (select out_revoked_at is not null from public.list_workspace_invitations(
        'a4000000-0000-4000-8000-000000000021'
    ) where out_invitation_id = :'second_invitation_id'::uuid),
    'owner revokes the second invitation'
);

set local role authenticated;
select throws_ok(
    $$select * from public.accept_workspace_invitation(repeat('b', 64))$$,
    'P0002', null,
    'a revoked invitation cannot be accepted'
);

select throws_ok(
    $$select * from public.accept_workspace_invitation('not-a-hash')$$,
    '22023', null,
    'a malformed token hash is rejected'
);

select * from finish();
rollback;
