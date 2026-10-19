begin;

create extension if not exists pgtap with schema extensions;

select plan(14);

select is(pg_get_userbyid(p.proowner), 'postgres', 'function owner is postgres')
from pg_proc as p where p.oid = 'public.rls_auto_enable()'::regprocedure;

select ok(p.prosecdef, 'function is security definer')
from pg_proc as p where p.oid = 'public.rls_auto_enable()'::regprocedure;

select is(p.proconfig, array['search_path=pg_catalog']::text[], 'function search path is fixed')
from pg_proc as p where p.oid = 'public.rls_auto_enable()'::regprocedure;

select is(p.prorettype::regtype::text, 'event_trigger', 'function returns event_trigger')
from pg_proc as p where p.oid = 'public.rls_auto_enable()'::regprocedure;

select is(p.proacl::text, '{postgres=X/postgres,service_role=X/postgres}', 'function ACL has only owner and service role')
from pg_proc as p where p.oid = 'public.rls_auto_enable()'::regprocedure;

select ok(not has_function_privilege('anon', 'public.rls_auto_enable()', 'EXECUTE'), 'anon cannot execute');
select ok(not has_function_privilege('authenticated', 'public.rls_auto_enable()', 'EXECUTE'), 'authenticated cannot execute');
select ok(has_function_privilege('service_role', 'public.rls_auto_enable()', 'EXECUTE'), 'service_role can execute');

select is(e.evtevent, 'ddl_command_end', 'event trigger runs after DDL')
from pg_event_trigger as e where e.evtname = 'ensure_rls';

select is(pg_get_userbyid(e.evtowner), 'postgres', 'event trigger owner is postgres')
from pg_event_trigger as e where e.evtname = 'ensure_rls';

select is(e.evttags, array['CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO']::text[], 'event trigger watches the verified tags')
from pg_event_trigger as e where e.evtname = 'ensure_rls';

select ok(e.evtenabled = 'O' and e.evtfoid = 'public.rls_auto_enable()'::regprocedure, 'event trigger is enabled and points to the function')
from pg_event_trigger as e where e.evtname = 'ensure_rls';

create table public.rls_auto_enable_probe (id integer);
select ok((select relrowsecurity from pg_class where oid = 'public.rls_auto_enable_probe'::regclass), 'new public table has RLS enabled');

create schema rls_auto_enable_test;
create table rls_auto_enable_test.probe (id integer);
select ok(not (select relrowsecurity from pg_class where oid = 'rls_auto_enable_test.probe'::regclass), 'table outside public is unaffected');

select * from finish();

rollback;
