"""Offline source-contract checks for the fail-closed RLS bootstrap trigger.

These checks read migration and pgTAP text only. They do not execute SQL and do
not prove PostgreSQL behaviour; the behavioural probe that does is documented in
supabase/README.md and needs a disposable stack.
"""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase/migrations"
PREREQUISITE = (MIGRATIONS / "20261008000000_rls_auto_enable_prerequisite.sql").read_text()
CORRECTIVE = (MIGRATIONS / "20261028120000_rls_auto_enable_fail_closed.sql").read_text()
SQL_TEST = (ROOT / "supabase/tests/rls_auto_enable.test.sql").read_text()


def _body(variable, tag):
    pattern = r"%s text := \$%s\$(.*?)\$%s\$;" % (variable, tag, tag)
    body = re.search(pattern, CORRECTIVE, re.S)
    assert body is not None, f"{variable} literal not found in the corrective migration"
    return body.group(1)


def test_corrective_migration_is_the_newest_forward_only_step():
    versions = sorted(path.name.split("_")[0] for path in MIGRATIONS.glob("*.sql"))
    assert versions[-1] == "20261028120000"
    assert len(versions) == len(set(versions))


def test_previous_body_matches_the_prerequisite_migration_byte_for_byte():
    start = PREREQUISITE.index("$function$") + len("$function$")
    end = PREREQUISITE.rindex("$function$")
    previous = _body("previous_source", "previous_function")
    assert previous == PREREQUISITE[start:end]
    assert "RAISE LOG 'rls_auto_enable: failed to enable RLS on %'" in previous


def test_expected_body_raises_instead_of_logging_and_continuing():
    expected = _body("expected_source", "expected_function")
    assert "RAISE LOG 'rls_auto_enable: failed to enable RLS on %'" not in expected
    assert re.search(r"EXCEPTION\s+WHEN OTHERS THEN\s+RAISE EXCEPTION", expected)
    assert "SQLSTATE" in expected
    assert "SQLERRM" in expected
    assert "IF rls_enabled IS NOT TRUE THEN" in expected
    assert "pg_catalog.to_regclass" in expected


def test_expected_body_keeps_the_reviewed_scope():
    expected = _body("expected_source", "expected_function")
    assert "object_type IN ('table','partitioned table')" in expected
    assert "cmd.schema_name IN ('public')" in expected
    assert "enable row level security" in expected
    assert "pg_event_trigger_ddl_commands()" in expected


def test_migration_refuses_unknown_bodies_and_reasserts_the_contract():
    assert "neither the reviewed fail-open definition nor the reviewed fail-closed definition" in CORRECTIVE
    assert "CREATE OR REPLACE FUNCTION public.rls_auto_enable() RETURNS event_trigger" in CORRECTIVE
    assert "SECURITY DEFINER SET search_path TO pg_catalog" in CORRECTIVE
    assert "REVOKE EXECUTE ON FUNCTION public.rls_auto_enable() FROM PUBLIC, anon, authenticated" in CORRECTIVE
    assert "GRANT EXECUTE ON FUNCTION public.rls_auto_enable() TO service_role" in CORRECTIVE
    assert "p.proconfig = array['search_path=pg_catalog']::text[]" in CORRECTIVE
    assert "does not match the reviewed fail-closed definition" in CORRECTIVE
    assert "e.evttags = array['CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO']::text[]" in CORRECTIVE
    assert "if function_oid is null or trigger_oid is null then" in CORRECTIVE


def test_pgtap_suite_asserts_the_fail_closed_shape():
    assert "select plan(18);" in SQL_TEST
    assert "failed to enable row level security on" in SQL_TEST
    assert "row level security is not enabled on" in SQL_TEST
    assert "the historical log-and-continue handler is gone" in SQL_TEST
    assert "the exception handler raises instead of logging" in SQL_TEST
