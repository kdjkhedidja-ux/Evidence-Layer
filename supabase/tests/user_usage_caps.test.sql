begin;

set local role postgres;
set local search_path = public, extensions;
create extension if not exists pgtap with schema extensions;

select plan(28);

-- Two accounts; account_lifecycle rows are created as 'available' by trigger.
insert into auth.users (id, aud, role, email)
values
    ('84000000-0000-4000-8000-000000000041', 'authenticated', 'authenticated', 'caps-a@example.test'),
    ('84000000-0000-4000-8000-000000000042', 'authenticated', 'authenticated', 'caps-b@example.test');

insert into public.workspaces (id, name)
values ('a6000000-0000-4000-8000-000000000041', 'Caps workspace');
insert into public.workspace_memberships (workspace_id, user_id, role, status)
values ('a6000000-0000-4000-8000-000000000041', '84000000-0000-4000-8000-000000000041', 'owner', 'active');

-- Synthetic gateway secret and key for user A (hash of a synthetic token).
select private.configure_personal_api_key_gateway_secret(repeat('ab', 32));
insert into public.personal_api_keys (id, user_id, workspace_id, name, key_prefix, key_hash, expires_at)
values ('b6000000-0000-4000-8000-000000000041', '84000000-0000-4000-8000-000000000041',
        'a6000000-0000-4000-8000-000000000041', 'caps key', 'evl_pk_b6000000',
        repeat('1', 64), clock_timestamp() + interval '30 days');

-- Builds the signed gateway headers exactly like the Evidence Service does.
create function pg_temp.sign_usage(
    p_kind text, p_per_minute integer, p_per_day integer, p_nonce uuid,
    p_version text default 'v1-usage'
) returns void language plpgsql as $$
declare
    ts bigint := extract(epoch from clock_timestamp())::bigint;
    msg text;
begin
    if p_version = 'v1-usage' then
        msg := concat_ws(E'\n', 'v1-usage', p_kind, 'b6000000-0000-4000-8000-000000000041',
            'a6000000-0000-4000-8000-000000000041', repeat('1', 64), ts::text, p_nonce::text,
            p_per_minute::text, p_per_day::text);
    else
        -- A read proof for the same key (resource evidence, limit 1).
        msg := concat_ws(E'\n', 'v1', 'evidence', 'b6000000-0000-4000-8000-000000000041',
            'a6000000-0000-4000-8000-000000000041', repeat('1', 64), ts::text, p_nonce::text,
            '1', '', '');
    end if;
    perform set_config('request.headers', jsonb_build_object(
        'x-evidence-api-key-hash', repeat('1', 64),
        'x-evidence-api-key-gateway-timestamp', ts::text,
        'x-evidence-api-key-gateway-nonce', p_nonce::text,
        'x-evidence-api-key-gateway-proof', encode(
            hmac(convert_to(msg, 'UTF8'), decode(repeat('ab', 32), 'hex'), 'sha256'), 'hex')
    )::text, true);
end;
$$;
grant execute on function pg_temp.sign_usage(text, integer, integer, uuid, text) to anon;

-- Web path: user A, 2 per minute.
set local role authenticated;
select set_config('request.jwt.claims',
    '{"sub":"84000000-0000-4000-8000-000000000041","role":"authenticated","aud":"authenticated"}', true);
select is((public.consume_user_usage('retrieval', 2, 500) ->> 'allowed')::boolean, true, 'first call is allowed');
select is((public.consume_user_usage('retrieval', 2, 500) ->> 'allowed')::boolean, true, 'second call is allowed');
select is(public.consume_user_usage('retrieval', 2, 500) ->> 'window', 'minute', 'third call hits the minute cap');
select ok((public.consume_user_usage('retrieval', 2, 500) ->> 'retry_after_seconds')::integer between 1 and 60,
    'minute refusal reports a retry delay within the window');
select is((public.consume_user_usage('retrieval', 0, 0) ->> 'allowed')::boolean, true, 'a cap of 0 means uncapped');

-- JEV budget is counted separately from retrieval.
select is((public.consume_user_usage('jev', 0, 1) ->> 'allowed')::boolean, true, 'first JEV call within the daily budget');
select is(public.consume_user_usage('jev', 0, 1) ->> 'window', 'day', 'second JEV call hits the daily budget');
select ok((public.consume_user_usage('jev', 0, 1) ->> 'retry_after_seconds')::integer between 1 and 86400,
    'daily refusal reports a delay until the next UTC day');

-- Isolation: user B is not affected by user A's counters.
select set_config('request.jwt.claims',
    '{"sub":"84000000-0000-4000-8000-000000000042","role":"authenticated","aud":"authenticated"}', true);
select is((public.consume_user_usage('retrieval', 2, 500) ->> 'allowed')::boolean, true, 'another user keeps their own minute budget');
select is((public.consume_user_usage('jev', 0, 1) ->> 'allowed')::boolean, true, 'another user keeps their own JEV budget');

-- Direct access is refused.
select throws_ok($$select * from private.user_usage_counters$$, '42501', null,
    'authenticated cannot read usage counters');
select throws_ok($$select private.consume_usage('84000000-0000-4000-8000-000000000041', 'retrieval', 0, 0)$$,
    '42501', null, 'authenticated cannot count for another user directly');
select throws_ok($$select public.consume_user_usage('unknown', 1, 1)$$, '22023', null, 'unknown kind is rejected');
select throws_ok($$select public.consume_personal_api_key_usage(
    'b6000000-0000-4000-8000-000000000041', 'a6000000-0000-4000-8000-000000000041', 'retrieval', 30, 500)$$,
    '42501', null, 'the signed usage RPC is not callable by a signed-in user');

-- Refused calls were not counted; reset of an expired window.
set local role postgres;
select is((select used from private.user_usage_counters
            where user_id = '84000000-0000-4000-8000-000000000041' and kind = 'retrieval' and window_kind = 'minute'),
    3, 'refused calls are not counted (2 capped + 1 uncapped)');
update private.user_usage_counters
   set window_started_at = window_started_at - interval '2 minutes'
 where user_id = '84000000-0000-4000-8000-000000000041' and kind = 'retrieval' and window_kind = 'minute';
update private.user_usage_counters
   set window_started_at = window_started_at - interval '1 day'
 where user_id = '84000000-0000-4000-8000-000000000041' and kind = 'jev' and window_kind = 'day';
set local role authenticated;
select set_config('request.jwt.claims',
    '{"sub":"84000000-0000-4000-8000-000000000041","role":"authenticated","aud":"authenticated"}', true);
select is((public.consume_user_usage('retrieval', 2, 500) ->> 'allowed')::boolean, true, 'a new minute window resets the minute cap');
select is((public.consume_user_usage('jev', 0, 1) ->> 'allowed')::boolean, true, 'a new UTC day resets the JEV budget');

-- Anonymous callers cannot use the session path.
set local role anon;
select set_config('request.jwt.claims', '{"role":"anon"}', true);
select throws_ok($$select public.consume_user_usage('retrieval', 1, 1)$$, '42501', null,
    'anon cannot call the session usage RPC');

-- Signed API-key path (called by the Evidence Service as anon).
select pg_temp.sign_usage('retrieval', 1, 500, 'c6000000-0000-4000-8000-000000000041');
select is((public.consume_personal_api_key_usage(
    'b6000000-0000-4000-8000-000000000041', 'a6000000-0000-4000-8000-000000000041', 'retrieval', 1, 500
) ->> 'window'), 'minute', 'API-key calls share the owner''s budget with the web path');

select pg_temp.sign_usage('retrieval', 0, 500, 'c6000000-0000-4000-8000-000000000042');
select is((public.consume_personal_api_key_usage(
    'b6000000-0000-4000-8000-000000000041', 'a6000000-0000-4000-8000-000000000041', 'retrieval', 0, 500
) ->> 'allowed')::boolean, true, 'a valid signed usage proof is accepted');

select throws_ok($$select public.consume_personal_api_key_usage(
    'b6000000-0000-4000-8000-000000000041', 'a6000000-0000-4000-8000-000000000041', 'retrieval', 0, 500)$$,
    '28000', null, 'a replayed nonce is refused');

select pg_temp.sign_usage('retrieval', 30, 500, 'c6000000-0000-4000-8000-000000000043');
select throws_ok($$select public.consume_personal_api_key_usage(
    'b6000000-0000-4000-8000-000000000041', 'a6000000-0000-4000-8000-000000000041', 'retrieval', 1000000, 500)$$,
    '28000', null, 'caps cannot be changed without a new proof');

select pg_temp.sign_usage('retrieval', 1, 1, 'c6000000-0000-4000-8000-000000000044', 'v1');
select throws_ok($$select public.consume_personal_api_key_usage(
    'b6000000-0000-4000-8000-000000000041', 'a6000000-0000-4000-8000-000000000041', 'retrieval', 1, 1)$$,
    '28000', null, 'a read proof cannot be replayed as a usage proof');

select pg_temp.sign_usage('jev', 0, 200, 'c6000000-0000-4000-8000-000000000045');
select throws_ok($$select public.consume_personal_api_key_usage(
    'b6000000-0000-4000-8000-000000000041', 'a6000000-0000-4000-8000-000000000042', 'jev', 0, 200)$$,
    '28000', null, 'a key cannot count usage for another workspace');

select throws_ok($$select private.consume_usage('84000000-0000-4000-8000-000000000041', 'retrieval', 0, 0)$$,
    '42501', null, 'anon cannot call the private counter');

-- Signed usage does not spend the per-key read rate limit.
set local role postgres;
select is((select rate_window_count from public.personal_api_keys where id = 'b6000000-0000-4000-8000-000000000041'),
    0, 'usage checks do not consume the per-key read rate limit');

-- A revoked key cannot count usage.
update public.personal_api_keys set revoked_at = clock_timestamp()
 where id = 'b6000000-0000-4000-8000-000000000041';
set local role anon;
select pg_temp.sign_usage('jev', 0, 200, 'c6000000-0000-4000-8000-000000000046');
select throws_ok($$select public.consume_personal_api_key_usage(
    'b6000000-0000-4000-8000-000000000041', 'a6000000-0000-4000-8000-000000000041', 'jev', 0, 200)$$,
    '28000', null, 'a revoked key is refused');

-- Account purge removes the counters with the identity (the purge path deletes
-- auth.users after its own lifecycle steps; the FK cascade covers this table).
set local role postgres;
select is((select confdeltype::text from pg_catalog.pg_constraint
            where conrelid = 'private.user_usage_counters'::regclass
              and confrelid = 'auth.users'::regclass and contype = 'f'),
    'c', 'counters cascade with the auth identity');

select * from finish();
rollback;
