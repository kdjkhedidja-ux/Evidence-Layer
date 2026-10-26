import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import sql_statement
from scripts import supabase_migration_guard as guard


class FakeProject:
    def __init__(self, history=(), backup=None):
        self.remote_history = list(history)
        self.recovery = backup or {"pitr_enabled": False, "latest_completed_backup": None, "physical_backup_data": {}}

    def target(self):
        pass

    def history(self):
        return self.remote_history

    def catalog(self):
        return {key: [] for key in ("tables", "indexes", "foreign_keys", "policies", "triggers", "functions", "extensions")}

    def backup(self):
        return self.recovery


class MigrationGuardTests(unittest.TestCase):
    def setUp(self):
        self.inventory = guard.local_inventory()
        self.sha = "a" * 40

    def test_clean_existing_project_has_eleven_reviewable_pending_migrations(self):
        with patch.dict(os.environ, {"GITHUB_SHA": self.sha}):
            plan = guard.build_plan(FakeProject(), self.inventory, require_recovery=False)
        self.assertEqual(plan["target_ref"], guard.PROJECT_REF)
        self.assertEqual(plan["pending"], [item["version"] for item in self.inventory])
        self.assertEqual(len(plan["digest"]), 64)

    def test_remote_only_migration_blocks_plan(self):
        with patch.dict(os.environ, {"GITHUB_SHA": self.sha}):
            with self.assertRaisesRegex(guard.GuardError, "not a prefix"):
                guard.build_plan(FakeProject(["20200101000000"]), self.inventory, require_recovery=False)

    def test_no_verified_recovery_point_blocks_release(self):
        with patch.dict(os.environ, {"GITHUB_SHA": self.sha}):
            with self.assertRaisesRegex(guard.GuardError, "No recent completed backup"):
                guard.build_plan(FakeProject(), self.inventory, require_recovery=True)

    def test_history_claiming_uncreated_schema_blocks_release(self):
        with patch.dict(os.environ, {"GITHUB_SHA": self.sha}):
            with self.assertRaisesRegex(guard.GuardError, "Remote extensions differ"):
                guard.build_plan(
                    FakeProject([self.inventory[0]["version"]]),
                    self.inventory,
                    require_recovery=False,
                )


class ManifestIntegrityTests(unittest.TestCase):
    """The manifest must describe exactly the reviewed bytes, and fail closed."""

    def setUp(self):
        self.original = guard.MANIFEST.read_text(encoding="utf-8")
        self.manifest = json.loads(self.original)

    def tearDown(self):
        guard.MANIFEST.write_text(self.original, encoding="utf-8")

    def test_manifest_covers_every_migration_file_and_records_provenance(self):
        on_disk = sorted(p.name for p in guard.MIGRATIONS.iterdir() if p.is_file())
        reviewed = sorted(entry["file"] for entry in self.manifest["migrations"])
        self.assertEqual(reviewed, on_disk)
        self.assertEqual(len(reviewed), 11)
        for entry in self.manifest["migrations"]:
            self.assertTrue(entry["provenance"].strip(), entry["file"])
            self.assertEqual(entry["version"], entry["file"][:14])
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")

    def test_unreviewed_new_migration_blocks_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp)
            for path in guard.MIGRATIONS.iterdir():
                if path.is_file():
                    (staged / path.name).write_bytes(path.read_bytes())
            (staged / "20261101000000_unreviewed.sql").write_text("select 1;\n", encoding="utf-8")
            with patch.object(guard, "MIGRATIONS", staged):
                with self.assertRaisesRegex(guard.GuardError, "present but unreviewed"):
                    guard.local_inventory()

    def test_renamed_migration_blocks_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp)
            for path in guard.MIGRATIONS.iterdir():
                if path.is_file():
                    (staged / path.name).write_bytes(path.read_bytes())
            oldest = sorted(p.name for p in staged.iterdir())[0]
            (staged / oldest).rename(staged / "20260101000000_renamed.sql")
            with patch.object(guard, "MIGRATIONS", staged):
                with self.assertRaises(guard.GuardError):
                    guard.local_inventory()

    def test_single_byte_change_blocks_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp)
            for path in guard.MIGRATIONS.iterdir():
                if path.is_file():
                    (staged / path.name).write_bytes(path.read_bytes())
            target = sorted(path for path in staged.iterdir() if path.is_file())[0]
            target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with patch.object(guard, "MIGRATIONS", staged):
                with self.assertRaisesRegex(guard.GuardError, "contents changed"):
                    guard.local_inventory()

    def test_manifest_entry_without_provenance_is_rejected(self):
        document = json.loads(self.original)
        document["migrations"][0]["provenance"] = "   "
        guard.MANIFEST.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(guard.GuardError, "missing provenance"):
            guard.load_manifest()

    def test_manifest_version_must_match_filename(self):
        document = json.loads(self.original)
        document["migrations"][0]["version"] = "19990101000000"
        guard.MANIFEST.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(guard.GuardError, "does not match its filename"):
            guard.load_manifest()

    def test_missing_manifest_blocks_validation(self):
        with patch.object(guard, "MANIFEST", Path("/nonexistent/manifest.json")):
            with self.assertRaisesRegex(guard.GuardError, "Missing reviewed migration manifest"):
                guard.load_manifest()


class ValidateMessageTests(unittest.TestCase):
    """`validate` must not imply that the SQL has been judged safe."""

    def test_validate_reports_conformance_only(self):
        buffer = io.StringIO()
        with patch.object(sys, "argv", ["guard", "validate"]), contextlib.redirect_stdout(buffer):
            self.assertEqual(guard.main(), 0)
        message = buffer.getvalue().lower()
        self.assertIn("manifest conforms", message)
        self.assertIn("static sql analysis", message)
        self.assertIn("separate", message)
        self.assertNotIn("no destructive statements detected", message)
        self.assertIn("11 files", message)


class StatementLexerTests(unittest.TestCase):
    """The lexer must never guess: it either splits correctly or raises."""

    def test_line_comment_hides_a_statement_separator(self):
        sql = "select 1; -- select 2;\nselect 3;"
        self.assertEqual(len(sql_statement.split_statements(sql)), 2)

    def test_nested_block_comment_is_consumed_whole(self):
        sql = "/* outer /* inner ; */ still ; */ select 1;"
        statements = sql_statement.split_statements(sql)
        self.assertEqual(len(statements), 1)
        self.assertEqual(statements[0].normalised, "select 1")

    def test_semicolon_inside_a_string_literal_does_not_split(self):
        sql = "comment on table public.t is 'a; b'; select 1;"
        self.assertEqual(len(sql_statement.split_statements(sql)), 2)

    def test_doubled_quote_inside_a_string_literal(self):
        sql = "comment on table public.t is 'it''s; fine';"
        self.assertEqual(len(sql_statement.split_statements(sql)), 1)

    def test_quoted_identifier_may_hold_a_semicolon_and_a_doubled_quote(self):
        sql = 'create table "we;ird""name" (id int);'
        statements = sql_statement.split_statements(sql)
        self.assertEqual(len(statements), 1)
        self.assertIn('"we;ird""name"', statements[0].normalised)

    def test_dollar_quoted_body_is_opaque_for_every_tag(self):
        for tag in ("$$", "$body$", "$migration$"):
            with self.subTest(tag=tag):
                sql = f"do {tag} begin; perform 1; end {tag}; select 1;"
                self.assertEqual(len(sql_statement.split_statements(sql)), 2)

    def test_dollar_body_nested_in_a_differently_tagged_body(self):
        sql = "do $outer$ begin perform $$ ; $$; end $outer$;"
        self.assertEqual(len(sql_statement.split_statements(sql)), 1)

    def test_multiple_statements_on_one_line_are_split(self):
        self.assertEqual(len(sql_statement.split_statements("select 1; select 2; select 3;")), 3)

    def test_trailing_statement_without_a_semicolon_is_kept(self):
        self.assertEqual(len(sql_statement.split_statements("select 1")), 1)

    def test_comments_only_produce_no_statements(self):
        self.assertEqual(sql_statement.split_statements("-- nothing\n/* nothing */;;\n"), [])

    def test_unterminated_constructs_raise_instead_of_splitting(self):
        cases = {
            "string literal": "select 'unterminated",
            "quoted identifier": 'select "unterminated',
            "block comment": "select 1 /* unterminated",
            "dollar body": "do $body$ begin end",
        }
        for label, sql in cases.items():
            with self.subTest(label):
                with self.assertRaises(sql_statement.SqlLexError):
                    sql_statement.split_statements(sql)

    def test_statement_identity_ignores_case_and_whitespace(self):
        left = sql_statement.split_statements("GRANT   SELECT  ON public.t TO anon;")[0]
        right = sql_statement.split_statements("grant select on public.t to anon;")[0]
        self.assertEqual(left.statement_id, right.statement_id)

    def test_statement_identity_changes_with_the_touched_object(self):
        left = sql_statement.split_statements("grant select on public.t to anon;")[0]
        right = sql_statement.split_statements("grant select on public.u to anon;")[0]
        self.assertNotEqual(left.statement_id, right.statement_id)

    def test_statement_identity_changes_with_the_grantee(self):
        left = sql_statement.split_statements("grant select on public.t to anon;")[0]
        right = sql_statement.split_statements("grant select on public.t to authenticated;")[0]
        self.assertNotEqual(left.statement_id, right.statement_id)


class StatementClassifierTests(unittest.TestCase):
    def classify(self, text):
        statements = sql_statement.split_statements(text)
        self.assertEqual(len(statements), 1, text)
        return sql_statement.classify(statements[0])

    def test_hard_denied_shapes(self):
        cases = {
            "session role switch": "set role postgres;",
            "local role switch": "set local role postgres;",
            "schema drop": "drop schema private cascade;",
            "public grant": "grant select on table public.t to public;",
            "rls disabled": "alter table public.t disable row level security;",
            "rls force removed": "alter table public.t no force row level security;",
            "superuser attribute": "create role rogue with superuser;",
            "bypassrls attribute": "create role rogue with bypassrls;",
        }
        for label, sql in cases.items():
            with self.subTest(label):
                tier, _ = self.classify(sql)
                self.assertEqual(tier, sql_statement.TIER_HARD_DENY, sql)

    def test_security_definer_without_a_pinned_search_path_is_hard_denied(self):
        sql = ("create or replace function public.f() returns integer "
               "language sql security definer as $$ select 1 $$;")
        tier, reason = self.classify(sql)
        self.assertEqual(tier, sql_statement.TIER_HARD_DENY)
        self.assertIn("search_path", reason)

    def test_security_definer_with_a_pinned_search_path_is_registry(self):
        sql = ("create or replace function public.f() returns integer "
               "language sql security definer set search_path = '' as $$ select 1 $$;")
        tier, _ = self.classify(sql)
        self.assertEqual(tier, sql_statement.TIER_REGISTRY)

    def test_registry_shapes(self):
        cases = [
            "grant select on table public.t to authenticated;",
            "revoke all on table public.t from anon;",
            "do $$ begin perform 1; end $$;",
            "drop policy if exists p on public.t;",
            "alter table public.t enable row level security;",
            "create policy p on public.t for select to authenticated using (true);",
            "select private.assert_snapshot_role_safe('x');",
            "insert into private.baseline (a) select 1;",
        ]
        for sql in cases:
            with self.subTest(sql):
                tier, _ = self.classify(sql)
                self.assertEqual(tier, sql_statement.TIER_REGISTRY, sql)

    def test_allowed_shapes(self):
        cases = [
            "create table public.t (id integer);",
            "create temp table pg_temp.t (like public.t);",
            "create index t_idx on public.t (id);",
            "create unique index t_uniq on public.t (id);",
            "comment on table public.t is 'x';",
            "set search_path = '';",
            "reset search_path;",
            "create extension if not exists pgcrypto with schema extensions;",
        ]
        for sql in cases:
            with self.subTest(sql):
                tier, _ = self.classify(sql)
                self.assertEqual(tier, sql_statement.TIER_ALLOWED, sql)

    def test_unknown_statement_is_ambiguous_rather_than_allowed(self):
        for sql in ("vacuum full public.t;", "cluster public.t;", "reindex table public.t;"):
            with self.subTest(sql):
                with self.assertRaises(sql_statement.AmbiguousStatement):
                    self.classify(sql)

    def test_empty_statement_is_ambiguous(self):
        with self.assertRaises(sql_statement.AmbiguousStatement):
            sql_statement.classify(sql_statement.Statement(tokens=(), normalised="", line=1))

    def test_do_body_is_opaque_so_dynamic_sql_is_registry_gated_not_silent(self):
        """Documented limitation: the ledger approves the DO block as a unit."""
        sql = "do $$ begin alter table public.t disable row level security; end $$;"
        tier, reason = self.classify(sql)
        self.assertEqual(tier, sql_statement.TIER_REGISTRY)
        self.assertEqual(reason, "do statement")


class ApprovalLedgerTests(unittest.TestCase):
    """Every registry-gated statement of the reviewed inventory is approved."""

    def setUp(self):
        self.records, self.findings = guard.analyse_migrations()

    def rewrite_ledger(self, mutate):
        """Return a context manager exposing a mutated copy of the ledger."""
        document = json.loads(guard.LEDGER.read_text(encoding="utf-8"))
        mutate(document)
        temporary = tempfile.TemporaryDirectory()
        path = Path(temporary.name) / "migrations.statements.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return temporary, patch.object(guard, "LEDGER", path)

    def test_reviewed_inventory_lexes_and_classifies_without_ambiguity(self):
        self.assertEqual(self.findings, [])
        self.assertEqual(len(self.records), 191)
        self.assertEqual(len({record["file"] for record in self.records}), 11)

    def test_no_reviewed_statement_is_hard_denied(self):
        denied = [r for r in self.records if r["tier"] == sql_statement.TIER_HARD_DENY]
        self.assertEqual(denied, [])

    def test_committed_ledger_approves_the_reviewed_inventory(self):
        guard.assert_ledger(self.records)  # raises GuardError on any drift

    def test_every_ledger_entry_resolves_to_a_written_rationale(self):
        ledger = guard.load_ledger()
        self.assertEqual(len(ledger["entries"]), 155)
        for entry in ledger["entries"]:
            self.assertTrue(ledger["rationales"][entry["rationale_ref"]].strip())
            self.assertTrue(entry["file"].endswith(".sql"))

    def test_ledger_entries_are_ordered_like_the_migrations(self):
        ledger = guard.load_ledger()
        lines = [(entry["file"], entry["line"]) for entry in ledger["entries"]]
        self.assertEqual(lines, sorted(lines))

    def test_unapproved_statement_change_is_rejected(self):
        tampered = [dict(record) for record in self.records]
        for record in tampered:
            if record["tier"] == sql_statement.TIER_REGISTRY:
                record["id"] = "0" * 16
                break
        with self.assertRaisesRegex(guard.GuardError, "Registry-gated statements changed"):
            guard.assert_ledger(tampered)

    def test_removed_statement_is_rejected(self):
        registry = [r for r in self.records if r["tier"] == sql_statement.TIER_REGISTRY]
        counts = {}
        for index, record in enumerate(registry):
            counts.setdefault(record["file"], []).append(index)
        crowded = next(indexes for indexes in counts.values() if len(indexes) > 1)
        tampered = [r for i, r in enumerate(registry) if i != crowded[0]]
        with self.assertRaisesRegex(guard.GuardError, "Registry-gated statements changed"):
            guard.assert_ledger(tampered)

    def test_ledger_entry_for_a_vanished_statement_is_rejected(self):
        def mutate(document):
            document["entries"].append({
                "id": "f" * 16,
                "file": "20260101000000_ghost.sql",
                "verb": "grant select",
                "rationale_ref": "privilege-grant",
            })

        temporary, patcher = self.rewrite_ledger(mutate)
        with temporary, patcher:
            with self.assertRaisesRegex(guard.GuardError, "no longer registry-gated"):
                guard.assert_ledger(self.records)

    def test_entry_referencing_an_unknown_rationale_is_rejected(self):
        def mutate(document):
            document["entries"][0]["rationale_ref"] = "made-up-reference"

        temporary, patcher = self.rewrite_ledger(mutate)
        with temporary, patcher:
            with self.assertRaisesRegex(guard.GuardError, "unknown rationale"):
                guard.assert_ledger(self.records)

    def test_blank_rationale_text_is_rejected(self):
        def mutate(document):
            document["rationales"]["privilege-grant"] = "   "

        temporary, patcher = self.rewrite_ledger(mutate)
        with temporary, patcher:
            with self.assertRaisesRegex(guard.GuardError, "Empty rationale"):
                guard.assert_ledger(self.records)

    def test_entry_without_a_rationale_reference_is_rejected(self):
        def mutate(document):
            document["entries"][0].pop("rationale_ref")

        temporary, patcher = self.rewrite_ledger(mutate)
        with temporary, patcher:
            with self.assertRaisesRegex(guard.GuardError, "missing rationale_ref"):
                guard.assert_ledger(self.records)

    def test_missing_ledger_blocks_classification(self):
        with patch.object(guard, "LEDGER", Path("/nonexistent/statements.json")):
            with self.assertRaisesRegex(guard.GuardError, "Missing statement approval ledger"):
                guard.assert_ledger(self.records)


class ClassifyCommandTests(unittest.TestCase):
    """`classify` is the control that actually blocks a bad migration."""

    def run_classify(self, migrations, manifest, ledger=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        patchers = [
            patch.object(guard, "MIGRATIONS", migrations),
            patch.object(guard, "MANIFEST", manifest),
            patch.object(sys, "argv", ["guard", "classify"]),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ]
        if ledger is not None:
            patchers.append(patch.object(guard, "LEDGER", ledger))
        with contextlib.ExitStack() as stack:
            for patcher in patchers:
                stack.enter_context(patcher if hasattr(patcher, "__enter__") else contextlib.nullcontext())
            code = guard.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def build_probe(self, body):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        migrations = root / "migrations"
        migrations.mkdir()
        name = "20260101000000_probe.sql"
        (migrations / name).write_text(body, encoding="utf-8")
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps({"migrations": [{
            "file": name,
            "version": "20260101000000",
            "sha256": hashlib.sha256(body.encode()).hexdigest(),
            "provenance": "synthetic probe fixture",
        }]}), encoding="utf-8")
        return temporary, migrations, manifest

    def test_reviewed_inventory_passes(self):
        buffer = io.StringIO()
        with patch.object(sys, "argv", ["guard", "classify"]), contextlib.redirect_stdout(buffer):
            self.assertEqual(guard.main(), 0)
        self.assertIn("Static analysis passed", buffer.getvalue())
        self.assertIn("155 registry-approved", buffer.getvalue())

    def test_hard_denied_statement_blocks(self):
        temporary, migrations, manifest = self.build_probe(
            "alter table public.probe disable row level security;\n"
        )
        with temporary:
            code, _, stderr = self.run_classify(migrations, manifest)
        self.assertEqual(code, 1)
        self.assertIn("hard-denied statement", stderr)

    def test_ambiguous_statement_blocks(self):
        temporary, migrations, manifest = self.build_probe("vacuum full public.probe;\n")
        with temporary:
            code, _, stderr = self.run_classify(migrations, manifest)
        self.assertEqual(code, 1)
        self.assertIn("unrecognised statement", stderr)

    def test_unterminated_construct_blocks(self):
        temporary, migrations, manifest = self.build_probe("do $body$ begin end\n")
        with temporary:
            code, _, stderr = self.run_classify(migrations, manifest)
        self.assertEqual(code, 1)
        self.assertIn("unterminated dollar-quoted body", stderr)

    def test_unapproved_registry_statement_blocks(self):
        temporary, migrations, manifest = self.build_probe(
            "grant select on table public.probe to authenticated;\n"
        )
        with temporary:
            code, _, stderr = self.run_classify(
                migrations, manifest, ledger=Path("/nonexistent/statements.json")
            )
        self.assertEqual(code, 1)
        self.assertIn("Missing statement approval ledger", stderr)


if __name__ == "__main__":
    unittest.main()