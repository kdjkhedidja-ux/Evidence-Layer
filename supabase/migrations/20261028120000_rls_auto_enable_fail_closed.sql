-- Make the RLS bootstrap event trigger fail closed.
--
-- 20261008000000_rls_auto_enable_prerequisite.sql restored the catalog
-- definition verbatim from the existing project. That definition catches every
-- error raised by "alter table ... enable row level security", writes a LOG
-- line, and keeps going. A table created in public could therefore end up
-- without row level security while the statement that created it reported
-- success, and nothing but a log line recorded the difference.
--
-- This migration replaces only the error handling. The event trigger now
-- re-raises with the table identity and the original SQLSTATE/SQLERRM, and
-- checks pg_class.relrowsecurity after the ALTER. Raising inside an event
-- trigger aborts the DDL statement and the surrounding transaction, so a
-- public table can no longer be created unprotected: the migration that
-- creates it fails instead. The trade-off is deliberate and must be accepted
-- before applying: a legitimate public table whose RLS cannot be enabled now
-- blocks the whole migration rather than silently shipping without RLS.
--
-- Forward-only. The function signature, owner, SECURITY DEFINER, fixed
-- search_path, ACL, and event trigger definition are unchanged, so the
-- contract asserted by 20261008000000 and by supabase/tests/rls_auto_enable
-- .test.sql keeps holding. This migration refuses to overwrite a body it does
-- not recognise instead of guessing.
do $migration$
declare
    expected_source text := $expected_function$
DECLARE
  cmd record;
  rls_enabled boolean;
BEGIN
  FOR cmd IN
    SELECT *
    FROM pg_event_trigger_ddl_commands()
    WHERE command_tag IN ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
      AND object_type IN ('table','partitioned table')
  LOOP
     IF cmd.schema_name IS NOT NULL AND cmd.schema_name IN ('public') AND cmd.schema_name NOT IN ('pg_catalog','information_schema') AND cmd.schema_name NOT LIKE 'pg_toast%' AND cmd.schema_name NOT LIKE 'pg_temp%' THEN
      BEGIN
        EXECUTE format('alter table if exists %s enable row level security', cmd.object_identity);
      EXCEPTION
        WHEN OTHERS THEN
          RAISE EXCEPTION 'rls_auto_enable: failed to enable row level security on % (SQLSTATE %: %)', cmd.object_identity, SQLSTATE, SQLERRM;
      END;
      SELECT c.relrowsecurity INTO rls_enabled
      FROM pg_catalog.pg_class AS c
      WHERE c.oid = pg_catalog.to_regclass(cmd.object_identity);
      IF rls_enabled IS NOT TRUE THEN
        RAISE EXCEPTION 'rls_auto_enable: row level security is not enabled on %', cmd.object_identity;
      END IF;
      RAISE LOG 'rls_auto_enable: enabled RLS on %', cmd.object_identity;
     ELSE
        RAISE LOG 'rls_auto_enable: skip % (either system schema or not in enforced list: %.)', cmd.object_identity, cmd.schema_name;
      END IF;
  END LOOP;
END;
$expected_function$;
    previous_source text := $previous_function$
DECLARE
  cmd record;
BEGIN
  FOR cmd IN
    SELECT *
    FROM pg_event_trigger_ddl_commands()
    WHERE command_tag IN ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
      AND object_type IN ('table','partitioned table')
  LOOP
     IF cmd.schema_name IS NOT NULL AND cmd.schema_name IN ('public') AND cmd.schema_name NOT IN ('pg_catalog','information_schema') AND cmd.schema_name NOT LIKE 'pg_toast%' AND cmd.schema_name NOT LIKE 'pg_temp%' THEN
      BEGIN
        EXECUTE format('alter table if exists %s enable row level security', cmd.object_identity);
        RAISE LOG 'rls_auto_enable: enabled RLS on %', cmd.object_identity;
      EXCEPTION
        WHEN OTHERS THEN
          RAISE LOG 'rls_auto_enable: failed to enable RLS on %', cmd.object_identity;
      END;
     ELSE
        RAISE LOG 'rls_auto_enable: skip % (either system schema or not in enforced list: %.)', cmd.object_identity, cmd.schema_name;
     END IF;
  END LOOP;
END;
$previous_function$;
    function_oid oid;
    trigger_oid oid;
    actual_source text;
    actual_acl text[];
begin
    if current_user <> 'postgres' then
        raise exception 'rls_auto_enable fail-closed migration requires postgres as owner (got %)', current_user;
    end if;

    select p.oid into function_oid
    from pg_catalog.pg_proc as p
    join pg_catalog.pg_namespace as n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.proname = 'rls_auto_enable'
      and p.pronargs = 0;

    select e.oid into trigger_oid
    from pg_catalog.pg_event_trigger as e
    where e.evtname = 'ensure_rls';

    if function_oid is null or trigger_oid is null then
        raise exception 'public.rls_auto_enable() and the ensure_rls event trigger must already exist; apply 20261008000000_rls_auto_enable_prerequisite.sql first';
    end if;

    select p.prosrc into actual_source
    from pg_catalog.pg_proc as p
    where p.oid = function_oid;

    if actual_source is distinct from expected_source then
        if actual_source is distinct from previous_source then
            raise exception 'public.rls_auto_enable() body is neither the reviewed fail-open definition nor the reviewed fail-closed definition; refusing to overwrite it without operator review';
        end if;

        execute format(
            'CREATE OR REPLACE FUNCTION public.rls_auto_enable() RETURNS event_trigger '
            || 'LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog AS %L',
            expected_source
        );
        execute 'REVOKE EXECUTE ON FUNCTION public.rls_auto_enable() FROM PUBLIC, anon, authenticated';
        execute 'GRANT EXECUTE ON FUNCTION public.rls_auto_enable() TO service_role';
    end if;

    select p.prosrc,
           array(select acl::text from unnest(p.proacl) as acl order by acl::text)
    into actual_source, actual_acl
    from pg_catalog.pg_proc as p
    join pg_catalog.pg_namespace as n on n.oid = p.pronamespace
    join pg_catalog.pg_language as l on l.oid = p.prolang
    where p.oid = function_oid
      and n.nspname = 'public'
      and l.lanname = 'plpgsql'
      and p.proname = 'rls_auto_enable'
      and p.pronargs = 0
      and p.prokind = 'f'
      and p.prorettype = 'event_trigger'::regtype
      and p.prosecdef
      and p.provolatile = 'v'
      and p.proparallel = 'u'
      and not p.proleakproof
      and not p.proisstrict
      and p.provariadic = 0
      and p.prosupport = 0
      and p.procost = 100
      and p.prorows = 0
      and p.proconfig = array['search_path=pg_catalog']::text[]
      and pg_catalog.pg_get_userbyid(p.proowner) = 'postgres';

    if actual_source is distinct from expected_source
       or actual_acl is distinct from array[
           'postgres=X/postgres', 'service_role=X/postgres'
       ]::text[]
       or pg_catalog.has_function_privilege('anon', function_oid, 'EXECUTE')
       or pg_catalog.has_function_privilege('authenticated', function_oid, 'EXECUTE')
       or not pg_catalog.has_function_privilege('service_role', function_oid, 'EXECUTE')
    then
        raise exception 'public.rls_auto_enable() does not match the reviewed fail-closed definition or privileges after this migration';
    end if;

    if not exists (
        select 1 from pg_catalog.pg_event_trigger as e
        where e.oid = trigger_oid
          and e.evtname = 'ensure_rls'
          and e.evtevent = 'ddl_command_end'
          and e.evtenabled = 'O'
          and e.evttags = array['CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO']::text[]
          and e.evtfoid = function_oid
          and pg_catalog.pg_get_userbyid(e.evtowner) = 'postgres'
    ) then
        raise exception 'existing ensure_rls event trigger differs from the verified definition';
    end if;
end;
$migration$;
