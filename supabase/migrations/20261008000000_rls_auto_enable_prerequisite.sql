-- Restore the catalog definition captured from the existing Evidence Layer
-- project before the first product table is created on a fresh database.
-- Existing objects are checked and left untouched; a mismatch aborts the
-- migration instead of replacing security-sensitive code.
-- Security follow-up: this historical function catches RLS enablement errors,
-- logs them, and continues. Review that fail-open behavior separately.
do $migration$
declare
    expected_source text := $function$
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
$function$;
    function_oid oid;
    trigger_oid oid;
    actual_source text;
    actual_acl text[];
begin
    if current_user <> 'postgres' then
        raise exception 'rls_auto_enable migration requires postgres as owner (got %)', current_user;
    end if;

    select p.oid into function_oid
    from pg_catalog.pg_proc as p
    join pg_catalog.pg_namespace as n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.proname = 'rls_auto_enable'
      and p.pronargs = 0;

    select e.oid into trigger_oid
    from pg_catalog.pg_event_trigger as e
    where e.evtname = 'ensure_rls';

    if (function_oid is null) <> (trigger_oid is null) then
        raise exception 'rls_auto_enable and ensure_rls must both exist or both be absent';
    end if;

    if function_oid is null then
        execute format(
            'CREATE FUNCTION public.rls_auto_enable() RETURNS event_trigger '
            || 'LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog AS %L',
            expected_source
        );
        execute 'REVOKE EXECUTE ON FUNCTION public.rls_auto_enable() FROM PUBLIC, anon, authenticated';
        execute 'GRANT EXECUTE ON FUNCTION public.rls_auto_enable() TO service_role';
        execute 'CREATE EVENT TRIGGER ensure_rls ON ddl_command_end '
            || 'WHEN TAG IN (''CREATE TABLE'', ''CREATE TABLE AS'', ''SELECT INTO'') '
            || 'EXECUTE FUNCTION public.rls_auto_enable()';

        select p.oid into function_oid
        from pg_catalog.pg_proc as p
        join pg_catalog.pg_namespace as n on n.oid = p.pronamespace
        where n.nspname = 'public' and p.proname = 'rls_auto_enable'
          and p.pronargs = 0;
        select e.oid into trigger_oid
        from pg_catalog.pg_event_trigger as e
        where e.evtname = 'ensure_rls';
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
        raise exception 'existing public.rls_auto_enable() differs from the verified definition or privileges';
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
