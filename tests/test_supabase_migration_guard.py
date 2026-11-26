import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request
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
        return {key: [] for key in ("tables", "indexes", "foreign_keys", "policies", "triggers", "functions", "extensions", "roles", "role_memberships", "event_triggers")}

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
        with patch.dict(os.environ, {
            "GITHUB_SHA": self.sha,
            "SUPABASE_EXTERNAL_RECOVERY_ATTESTATION": "/nonexistent/attestation.json",
        }):
            with self.assertRaisesRegex(guard.GuardError, "No recent completed backup"):
                guard.build_plan(FakeProject(), self.inventory, require_recovery=True)

    def test_reviewed_external_recovery_attestation_satisfies_the_gate(self):
        attestation = json.loads(
            (guard.ROOT / "supabase" / "recovery-attestation.json").read_text(encoding="utf-8")
        )
        now = datetime.now(timezone.utc).isoformat()
        attestation["uploaded_at"] = now
        attestation["drill"]["completed_at"] = now
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(attestation, handle)
            path = handle.name
        try:
            with patch.dict(os.environ, {
                "GITHUB_SHA": self.sha,
                "SUPABASE_EXTERNAL_RECOVERY_ATTESTATION": path,
            }):
                plan = guard.build_plan(FakeProject(), self.inventory, require_recovery=True)
        finally:
            os.unlink(path)
        self.assertIsNotNone(plan["external_recovery"])
        self.assertEqual(plan["external_recovery"]["object_key"], attestation["object_key"])

    def test_stale_or_invalid_external_attestation_is_rejected(self):
        import json
        import tempfile
        base = {
            "provider": "r2",
            "bucket": "evidence-layer-db-backups",
            "object_key": "dumps/2026-11-07/evidence-layer.dump",
            "endpoint": "https://7b52588f0e52079bcc49b92c6318056b.r2.cloudflarestorage.com",
            "sha256": "6450cabff82f64a09a28616ae4d5a2284575bedf2bb869e9b24867732d32b73e",
            "size_bytes": 127007,
            "uploaded_at": "2026-11-07T22:04:30Z",
            "drill": {
                "status": "PASS",
                "completed_at": "2026-11-07T22:06:00Z",
                "target": "disposable PostgreSQL 17 (non-prod)",
                "evidence": "docs/PRODUCTION-RESTORE-CHECKLIST-2026-11-07.md",
            },
            "attested_by": "operations",
        }
        invalid = [
            {**base, "uploaded_at": "2020-01-01T00:00:00Z"},
            {**base, "sha256": "not-a-hash"},
            {**base, "drill": {**base["drill"], "status": "FAIL"}},
            {**base, "object_key": "dumps/2026-11-07/evidence-layer.sql"},
            {**base, "provider": "s3"},
            {
                **base,
                "provider": "b2",
                "bucket": "wrong-bucket",
                "endpoint": "https://s3.eu-central-003.backblazeb2.com",
            },
            {
                **base,
                "provider": "b2",
                "bucket": "evidence2",
                "endpoint": "https://s3.eu-central-003.backblazeb2.com.evil.test",
            },
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
                    json.dump(payload, handle)
                    path = handle.name
                try:
                    with patch.dict(os.environ, {
                        "GITHUB_SHA": self.sha,
                        "SUPABASE_EXTERNAL_RECOVERY_ATTESTATION": path,
                    }):
                        with self.assertRaisesRegex(guard.GuardError, "external recovery attestation"):
                            guard.build_plan(FakeProject(), self.inventory, require_recovery=True)
                finally:
                    os.unlink(path)

    def test_b2_external_recovery_attestation_satisfies_the_gate(self):
        now = datetime.now(timezone.utc).isoformat()
        attestation = {
            "provider": "b2",
            "bucket": "evidence2",
            "object_key": "dumps/2026-11-14/evidence-layer.dump",
            "endpoint": "https://s3.eu-central-003.backblazeb2.com",
            "sha256": "a" * 64,
            "size_bytes": 123,
            "uploaded_at": now,
            "drill": {
                "status": "PASS",
                "completed_at": now,
                "target": "disposable PostgreSQL 17 (non-prod)",
                "evidence": "docs/PRODUCTION-RESTORE-CHECKLIST-2026-11-11.md",
            },
            "attested_by": "test",
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(attestation, handle)
            path = handle.name
        try:
            with patch.dict(os.environ, {
                "GITHUB_SHA": self.sha,
                "SUPABASE_EXTERNAL_RECOVERY_ATTESTATION": path,
            }):
                plan = guard.build_plan(FakeProject(), self.inventory, require_recovery=True)
        finally:
            os.unlink(path)
        self.assertEqual(plan["external_recovery"]["provider"], "b2")
        self.assertEqual(plan["external_recovery"]["bucket"], "evidence2")

    def test_history_claiming_uncreated_schema_blocks_release(self):
        with patch.dict(os.environ, {"GITHUB_SHA": self.sha}):
            with self.assertRaisesRegex(guard.GuardError, "Remote extensions differ"):
                guard.build_plan(
                    FakeProject([self.inventory[0]["version"]]),
                    self.inventory,
                    require_recovery=False,
                )


class ReadOnlyManagementApiTests(unittest.TestCase):
    def setUp(self):
        self.credentials = patch.dict(os.environ, {"SUPABASE_ACCESS_TOKEN": "test-token"})
        self.credentials.start()
        self.addCleanup(self.credentials.stop)
        self.api = guard.Supabase()

    @staticmethod
    def response(payload):
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    def assert_from_join_references_qualified(self, query):
        references = re.findall(
            r"\b(from|join)\s+([A-Za-z_][\w$.]*)",
            query,
            re.I,
        )
        for clause, reference in references:
            self.assertIn(
                ".",
                reference,
                f"Unqualified {clause.upper()} relation {reference!r} in: {query}",
            )
        return references

    def test_from_and_join_references_must_each_be_schema_qualified(self):
        references = self.assert_from_join_references_qualified(
            "select * from public.items i join pg_catalog.pg_class c on true"
        )
        self.assertEqual(
            references,
            [("from", "public.items"), ("join", "pg_catalog.pg_class")],
        )

        unqualified_from = (
            "select pg_catalog.current_database() "
            "from schema_migrations m join pg_catalog.pg_class c on true"
        )
        unqualified_join = (
            "select pg_catalog.current_database() "
            "from public.items i join schema_migrations m on true"
        )
        for query in (unqualified_from, unqualified_join):
            with self.subTest(query=query), self.assertRaisesRegex(
                AssertionError, "Unqualified (FROM|JOIN) relation"
            ):
                self.assert_from_join_references_qualified(query)

    def test_query_uses_the_dedicated_read_only_endpoint_and_request_shape(self):
        with patch(
            "scripts.supabase_migration_guard.urllib.request.urlopen",
            return_value=self.response([{"value": 1}]),
        ) as urlopen:
            self.assertEqual(
                self.api.query("select 1", {"value": int}),
                [{"value": 1}],
            )

        actual = urlopen.call_args.args[0]
        self.assertIsInstance(actual, Request)
        self.assertTrue(actual.full_url.endswith("/database/query/read-only"))
        self.assertEqual(actual.get_method(), "POST")
        self.assertEqual(json.loads(actual.data), {"query": "select 1"})

    def test_history_relation_name_allows_null_only_as_declared(self):
        with patch(
            "scripts.supabase_migration_guard.urllib.request.urlopen",
            return_value=self.response([{"table_name": None}]),
        ):
            rows = self.api.query(
                "select pg_catalog.to_regclass('supabase_migrations.schema_migrations')"
                "::pg_catalog.text as table_name",
                {"table_name": (str, type(None))},
            )
        self.assertEqual(rows, [{"table_name": None}])

    def test_catalog_history_and_grant_queries_use_qualified_entities(self):
        queries = []

        def record_query(query, expected_columns):
            queries.append(query)
            if "to_regclass" in query:
                return [{"table_name": "schema_migrations"}]
            if "select version::pg_catalog.text" in query:
                return [{"version": guard.V_PLATFORM}]
            if "visible_catalog_relations" in query:
                return [{"visible_catalog_relations": 12}]
            if "has_" in query:
                return [{name: True for name in expected_columns}]
            return []

        with patch.object(self.api, "query", side_effect=record_query):
            self.api.history()
            self.api.catalog()
            guard.assert_grants(
                self.api,
                [item["version"] for item in guard.local_inventory()],
            )

        self.assertGreater(len(queries), 10)
        for query in queries:
            self.assert_from_join_references_qualified(query)
            self.assertNotRegex(
                query,
                r"(?<![\w.])(?:to_regclass|has_column_privilege|"
                r"has_table_privilege|has_function_privilege)\s*\(",
            )
            self.assertNotRegex(query, r"::\s*(?:regclass|text)\b")
        self.assertTrue(any("supabase_migrations.schema_migrations" in query for query in queries))
        self.assertTrue(any("pg_catalog.pg_class" in query for query in queries))
        self.assertTrue(any("pg_catalog.pg_auth_members" in query for query in queries))
        self.assertTrue(any("pg_catalog.has_table_privilege" in query for query in queries))

    def test_every_sql_query_uses_the_dedicated_read_only_endpoint(self):
        requests = []

        def respond(request, timeout):
            requests.append(request)
            sql = json.loads(request.data)["query"]
            if "to_regclass" in sql:
                return self.response([{"table_name": "schema_migrations"}])
            if "select version::pg_catalog.text" in sql:
                return self.response([{"version": "20261007170000"}])
            if "visible_catalog_relations" in sql:
                return self.response([{"visible_catalog_relations": 12}])
            if "from pg_catalog.pg_class c join" in sql:
                row = {"schema": "public", "name": "table", "rls": True, "force_rls": False}
            elif "from pg_catalog.pg_indexes" in sql:
                row = {"schema": "public", "name": "index"}
            elif "from pg_catalog.pg_constraint" in sql:
                row = {"name": "fk", "source": "public.source", "target": "public.target", "on_delete": "a"}
            elif "from pg_catalog.pg_policies" in sql:
                row = {"schema": "public", "table": "workspaces", "name": "policy"}
            elif "from pg_catalog.pg_trigger" in sql:
                row = {"schema": "public", "name": "trigger"}
            elif "from pg_catalog.pg_proc" in sql:
                row = {"schema": "public", "name": "function"}
            elif "from pg_catalog.pg_extension" in sql:
                row = {"name": "vector"}
            elif "left join pg_catalog.pg_auth_members" in sql:
                row = {
                    "name": "snapshot_test",
                    "membership_count": 0,
                    "allowed_admin_memberships": 0,
                    "allowed_set_memberships": 0,
                }
            elif "from pg_catalog.pg_roles" in sql:
                row = {
                    "name": "snapshot_test",
                    "canlogin": False,
                    "inherit": False,
                    "super": False,
                    "bypassrls": False,
                    "createrole": False,
                    "createdb": False,
                    "replication": False,
                }
            elif "from pg_catalog.pg_event_trigger" in sql:
                row = {"name": "ensure_rls"}
            elif "has_" in sql:
                names = re.findall(r"\bas\s+([a-z_]+)", sql, re.I)
                row = {name: True for name in names}
            else:
                self.fail(f"Unrecognized guard SQL query: {sql}")
            return self.response([row])

        with patch(
            "scripts.supabase_migration_guard.urllib.request.urlopen",
            side_effect=respond,
        ):
            self.api.history()
            self.api.catalog()
            guard.assert_grants(
                self.api,
                [item["version"] for item in guard.local_inventory()],
            )

        self.assertEqual(len(requests), 21)
        for request in requests:
            self.assertTrue(request.full_url.endswith("/database/query/read-only"))
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(set(json.loads(request.data)), {"query"})
        history_queries = [
            json.loads(request.data)["query"]
            for request in requests
            if "schema_migrations" in json.loads(request.data)["query"]
        ]
        self.assertTrue(any("select version::pg_catalog.text" in query for query in history_queries))
        membership_queries = [
            json.loads(request.data)["query"]
            for request in requests
            if "left join pg_catalog.pg_auth_members" in json.loads(request.data)["query"]
        ]
        self.assertEqual(len(membership_queries), 1)
        self.assertIn("membership.admin_option", membership_queries[0])
        self.assertIn("membership.set_option", membership_queries[0])
        self.assertIn("membership.inherit_option", membership_queries[0])
        self.assertIn(
            "membership.member in (current_user::pg_catalog.regrole, 'postgres'::pg_catalog.regrole)",
            membership_queries[0],
        )
        self.assertIn("and not membership.set_option", membership_queries[0])
        self.assertIn("and not membership.inherit_option", membership_queries[0])

    def test_missing_migration_history_table_fails_closed(self):
        with patch.object(self.api, "query", return_value=[{"table_name": None}]):
            with self.assertRaisesRegex(guard.GuardError, "migration history table is missing"):
                self.api.history()

    def test_unreadable_catalog_probe_blocks_empty_catalog_results(self):
        with patch.object(
            self.api,
            "query",
            return_value=[{"visible_catalog_relations": 0}],
        ) as query:
            with self.assertRaisesRegex(guard.GuardError, "cannot verify all required"):
                self.api.catalog()
        self.assertEqual(query.call_count, 1)

    def test_empty_rows_do_not_bypass_catalog_or_grant_checks(self):
        versions = [item["version"] for item in guard.local_inventory()]
        empty_catalog = {
            key: []
            for key in (
                "tables",
                "indexes",
                "foreign_keys",
                "policies",
                "triggers",
                "functions",
                "extensions",
                "roles",
                "role_memberships",
                "event_triggers",
            )
        }
        with self.assertRaises(guard.GuardError):
            guard.assert_catalog(empty_catalog, versions)

        with patch.object(self.api, "query", return_value=[]):
            with self.assertRaises(guard.GuardError):
                guard.assert_grants(self.api, [guard.V_IDENTITY])

    def test_query_auth_and_transport_failures_do_not_fall_back_to_write_endpoint(self):
        failures = (
            HTTPError("https://api.supabase.com/test", 401, "unauthorized", None, None),
            URLError("unavailable"),
            TimeoutError("request timed out"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch(
                    "scripts.supabase_migration_guard.urllib.request.urlopen",
                    side_effect=failure,
                ) as urlopen:
                    with self.assertRaises(guard.GuardError):
                        self.api.query("select 1", {"value": int})
                self.assertEqual(urlopen.call_count, 1)
                self.assertTrue(
                    urlopen.call_args.args[0].full_url.endswith("/database/query/read-only")
                )

    def test_invalid_or_unexpected_query_responses_fail_closed(self):
        responses = (
            (io.BytesIO(b"not-json"), {"value": int}),
            (self.response({"rows": []}), {"value": int}),
            (self.response([{}]), {"value": bool}),
            (self.response([{"value": None}]), {"value": bool}),
            (self.response([{"value": "true"}]), {"value": bool}),
            (self.response([{"value": 1}]), {"value": bool}),
            (
                self.response([{"value": True}]),
                {"value": bool, "required": str},
            ),
            (self.response([{"value": True, "extra": 1}]), {"value": bool}),
            (self.response([True]), {"value": bool}),
        )
        for response, expected_columns in responses:
            with self.subTest(response=type(response).__name__, columns=expected_columns):
                with patch(
                    "scripts.supabase_migration_guard.urllib.request.urlopen",
                    return_value=response,
                ):
                    with self.assertRaises(guard.GuardError):
                        self.api.query("select 1", expected_columns)

    def test_role_membership_audit_rejects_unexpected_edges_and_incomplete_rows(self):
        guard.assert_role_memberships(
            [{"name": "snapshot_verifier", "membership_count": 0, "allowed_admin_memberships": 0, "allowed_set_memberships": 0}],
            {"snapshot_verifier"},
        )
        guard.assert_role_memberships(
            [{"name": "snapshot_verifier", "membership_count": 1, "allowed_admin_memberships": 1, "allowed_set_memberships": 0}],
            {"snapshot_verifier"},
        )
        guard.assert_role_memberships(
            [{"name": "account_deletion_worker", "membership_count": 2, "allowed_admin_memberships": 1, "allowed_set_memberships": 1}],
            {"account_deletion_worker"},
        )
        invalid_rows = (
            [],
            [{"name": "snapshot_verifier", "membership_count": 1, "allowed_admin_memberships": 0, "allowed_set_memberships": 0}],
            [{"name": "snapshot_verifier", "membership_count": 2, "allowed_admin_memberships": 1, "allowed_set_memberships": 0}],
            [{"name": "snapshot_verifier", "membership_count": 0, "allowed_admin_memberships": True, "allowed_set_memberships": 0}],
            [{"name": "snapshot_verifier", "membership_count": 1, "allowed_admin_memberships": 0, "allowed_set_memberships": 1}],
            [{"name": "account_deletion_worker", "membership_count": 1, "allowed_admin_memberships": 1, "allowed_set_memberships": 0}],
            [{"name": "account_deletion_worker", "membership_count": 3, "allowed_admin_memberships": 1, "allowed_set_memberships": 1}],
            [
                {"name": "snapshot_verifier", "membership_count": 0, "allowed_admin_memberships": 0, "allowed_set_memberships": 0},
                {"name": "snapshot_verifier", "membership_count": 0, "allowed_admin_memberships": 0, "allowed_set_memberships": 0},
            ],
        )
        for rows in invalid_rows:
            with self.subTest(rows=rows), self.assertRaises(guard.GuardError):
                guard.assert_role_memberships(rows, {"snapshot_verifier"})
        with self.assertRaises(guard.GuardError):
            guard.assert_role_memberships(
                [{"name": "account_deletion_worker", "membership_count": 2, "allowed_admin_memberships": 1, "allowed_set_memberships": 1}],
                {"snapshot_verifier"},
            )

    def test_policy_inventory_rejects_missing_or_misplaced_policy(self):
        expected = {"storage.source_snapshots_objects_select_granted_published"}
        valid = [{
            "schema": "storage",
            "table": "objects",
            "name": "source_snapshots_objects_select_granted_published",
        }]
        guard.assert_policy_inventory(valid, expected)

        misplaced = [{
            "schema": "storage",
            "table": "buckets",
            "name": "source_snapshots_objects_select_granted_published",
        }]
        with self.assertRaisesRegex(guard.GuardError, "Remote policies differ"):
            guard.assert_policy_inventory(misplaced, expected)
        with self.assertRaises(guard.GuardError):
            guard.assert_policy_inventory([], expected)

    def test_target_rejects_empty_project_ref_before_any_request(self):
        with patch.dict(os.environ, {"SUPABASE_PROJECT_REF": ""}):
            with patch("scripts.supabase_migration_guard.urllib.request.urlopen") as urlopen:
                with self.assertRaisesRegex(guard.GuardError, "Configured project ref"):
                    self.api.target()
                urlopen.assert_not_called()

    def test_plan_stops_after_read_only_query_auth_transport_or_response_failure(self):
        project = {
            "ref": guard.PROJECT_REF,
            "name": guard.PROJECT_NAME,
            "status": "ACTIVE_HEALTHY",
        }
        failures = (
            HTTPError("https://api.supabase.com/test", 401, "unauthorized", None, None),
            URLError("unavailable"),
            TimeoutError("request timed out"),
            io.BytesIO(b"not-json"),
            self.response({"unexpected": "shape"}),
        )
        with patch.dict(
            os.environ,
            {
                "SUPABASE_PROJECT_REF": guard.PROJECT_REF,
                "GITHUB_SHA": "a" * 40,
            },
        ):
            for failure in failures:
                with self.subTest(failure=type(failure).__name__):
                    with patch(
                        "scripts.supabase_migration_guard.urllib.request.urlopen",
                        side_effect=[self.response(project), failure],
                    ) as urlopen:
                        with self.assertRaises(guard.GuardError):
                            guard.build_plan(
                                self.api,
                                guard.local_inventory(),
                                require_recovery=True,
                            )
                    self.assertEqual(urlopen.call_count, 2)
                    self.assertEqual(
                        urlopen.call_args_list[-1].args[0].full_url.split("/projects/")[1].split("/")[1:],
                        ["database", "query", "read-only"],
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
        self.assertEqual(len(reviewed), len(on_disk))
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
        self.assertIn("58 files", message)


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
        self.assertEqual(len(self.records), 937)
        self.assertEqual(len({record["file"] for record in self.records}), 58)

    def test_no_reviewed_statement_is_hard_denied(self):
        denied = [r for r in self.records if r["tier"] == sql_statement.TIER_HARD_DENY]
        self.assertEqual(denied, [])

    def test_committed_ledger_approves_the_reviewed_inventory(self):
        guard.assert_ledger(self.records)  # raises GuardError on any drift

    def test_every_ledger_entry_resolves_to_a_written_rationale(self):
        ledger = guard.load_ledger()
        self.assertEqual(len(ledger["entries"]), 807)
        for entry in ledger["entries"]:
            self.assertTrue(ledger["rationales"][entry["rationale_ref"]].strip())
            self.assertTrue(entry["file"].endswith(".sql"))

    def test_ledger_entries_are_ordered_like_the_migrations(self):
        ledger = guard.load_ledger()
        lines = [(entry["file"], int(entry["line"])) for entry in ledger["entries"]]
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
        self.assertIn("807 registry-approved", buffer.getvalue())

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


class CatalogCompletenessTests(unittest.TestCase):
    """Every object a reviewed migration creates must be named in the catalog model.

    These tests re-derive the object inventory from the migration SQL itself, so
    a migration that adds a table, policy, trigger, function, role, index,
    extension, or event trigger without extending ``expected_catalog`` fails
    here instead of passing a silently incomplete post-apply verification.
    """

    @staticmethod
    def _body():
        versions = [entry["version"] for entry in guard.local_inventory()]
        expected = guard.expected_catalog(versions)
        text = {}
        for entry in guard.local_inventory():
            text[entry["file"]] = (guard.MIGRATIONS / entry["file"]).read_text(encoding="utf-8")
        return expected, text

    @staticmethod
    def _join(body):
        return "\n".join(body.values())

    def test_every_created_table_is_catalogued(self):
        expected, body = self._body()
        pattern = re.compile(
            r"\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?([a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)\s*\(",
            re.I,
        )
        found = set()
        for match in pattern.finditer(self._join(body)):
            schema = match.group(1).rstrip(".") if match.group(1) else "public"
            if schema == "pg_temp":
                continue
            found.add(f"{schema}.{match.group(2)}")
        self.assertEqual(found, set(expected["tables"]))

    def test_every_created_policy_is_catalogued(self):
        expected, body = self._body()
        pattern = re.compile(
            r"\bcreate\s+policy\s+([a-z_][a-z0-9_]*)\s+on\s+([a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)",
            re.I,
        )
        found = set()
        for match in pattern.finditer(self._join(body)):
            schema = match.group(2).rstrip(".") if match.group(2) else "public"
            if schema == "pg_temp":
                continue
            table = match.group(3)
            found.add(f"{schema}.{table}.{match.group(1)}")
        drop_pattern = re.compile(
            r"\bdrop\s+policy\s+(?:if\s+exists\s+)?([a-z_][a-z0-9_]*)\s+on\s+([a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)",
            re.I,
        )
        body_text = self._join(body)
        last_create = {}
        for match in pattern.finditer(body_text):
            schema = match.group(2).rstrip(".") if match.group(2) else "public"
            last_create[f"{schema}.{match.group(3)}.{match.group(1)}"] = match.start()
        dropped = set()
        for match in drop_pattern.finditer(body_text):
            schema = match.group(2).rstrip(".") if match.group(2) else "public"
            key = f"{schema}.{match.group(3)}.{match.group(1)}"
            if last_create.get(key, -1) < match.start():
                dropped.add(key)
        found.difference_update(dropped)
        self.assertEqual(
            found,
            {
                guard.expected_policy_identity(policy)
                for policy in expected["policies"]
            },
        )

    def test_every_created_trigger_is_catalogued(self):
        expected, body = self._body()
        pattern = re.compile(
            r"\bcreate\s+trigger\s+([a-z_][a-z0-9_]*)[^;]*?\bon\s+([a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)",
            re.I,
        )
        found = set()
        for match in pattern.finditer(self._join(body)):
            schema = match.group(2).rstrip(".") if match.group(2) else "public"
            if schema == "pg_temp":
                continue
            found.add(f"{schema}.{match.group(1)}")
        self.assertEqual(found, expected["triggers"])

    def test_every_created_function_is_catalogued(self):
        expected, body = self._body()
        pattern = re.compile(
            r"\bcreate\s+(?:or\s+replace\s+)?function\s+([a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)\s*\(",
            re.I,
        )
        found = set()
        for match in pattern.finditer(self._join(body)):
            schema = match.group(1).rstrip(".") if match.group(1) else "public"
            found.add(f"{schema}.{match.group(2)}")
        self.assertEqual(found, expected["functions"])

    def test_every_created_role_is_catalogued(self):
        expected, body = self._body()
        joined = self._join(body)
        create_role = re.compile(r"\bcreate\s+role\s+([a-z_][a-z0-9_]*)\s+(?=nologin\b)", re.I)
        array_role = re.compile(r"foreach\s+role_name\s+in\s+array\s+array\[([^\]]*)\]", re.I)
        found = {match.group(1) for match in create_role.finditer(joined)}
        for match in array_role.finditer(joined):
            for quoted in re.findall(r"'([a-z_][a-z0-9_]*)'", match.group(1)):
                if quoted.startswith("snapshot_"):
                    found.add(quoted)
        self.assertEqual(found, expected["roles"])

    def test_every_created_index_is_catalogued(self):
        expected, body = self._body()
        joined = self._join(body)
        create_index = re.compile(
            r"\bcreate\s+(?:unique\s+)?index\s+([a-z_][a-z0-9_]*)\s+on\s+([a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)",
            re.I,
        )
        pkey = re.compile(
            r"\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?([a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)\s*\([^;]*?\bprimary\s+key",
            re.I,
        )
        unique_constraint = re.compile(
            r"\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?([a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)\s*\([^;]*?constraint\s+([a-z_][a-z0-9_]*)\s+unique\b",
            re.I,
        )
        table_block = re.compile(
            r"\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?([a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)\s*\((.*?)\);",
            re.I | re.S,
        )
        column_unique = re.compile(
            r"\b([a-z_][a-z0-9_]*)\s+(?:uuid|text|bigint|integer|timestamptz|boolean|jsonb|bytea|smallint|numeric)[^,;]*?\bunique\b",
            re.I,
        )
        alter_unique = re.compile(
            r"\balter\s+table\s+(?:if\s+exists\s+)?([a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)\s+add\s+constraint\s+([a-z_][a-z0-9_]*)\s+unique\b",
            re.I,
        )
        add_unique = re.compile(
            r"\badd\s+constraint\s+([a-z_][a-z0-9_]*)\s+unique\b",
            re.I,
        )
        found = set()
        for match in create_index.finditer(joined):
            schema = match.group(2).rstrip(".") if match.group(2) else "public"
            if schema == "pg_temp":
                continue
            found.add(f"{schema}.{match.group(1)}")
        for match in pkey.finditer(joined):
            schema = match.group(1).rstrip(".") if match.group(1) else "public"
            if schema == "pg_temp":
                continue
            found.add(f"{schema}.{match.group(2)}_pkey")
        for match in unique_constraint.finditer(joined):
            schema = match.group(1).rstrip(".") if match.group(1) else "public"
            if schema == "pg_temp":
                continue
            found.add(f"{schema}.{match.group(3)}")
        for match in add_unique.finditer(joined):
            found.add(f"public.{match.group(1)}")
        for match in alter_unique.finditer(joined):
            schema = match.group(1).rstrip(".") if match.group(1) else "public"
            if schema == "pg_temp":
                continue
            found.add(f"{schema}.{match.group(3)}")
        for match in table_block.finditer(joined):
            schema = match.group(1).rstrip(".") if match.group(1) else "public"
            if schema == "pg_temp":
                continue
            for column in column_unique.finditer(match.group(3)):
                found.add(f"{schema}.{match.group(2)}_{column.group(1)}_key")
        # A later migration may intentionally drop a constraint/index created
        # earlier. Only names that are never re-added leave the final catalog.
        drop_constraint = re.compile(r"\bdrop\s+constraint\s+(?:if\s+exists\s+)?([a-z_][a-z0-9_]*)", re.I)
        add_constraint = re.compile(r"\badd\s+constraint\s+([a-z_][a-z0-9_]*)", re.I)
        dropped = {match.group(1) for match in drop_constraint.finditer(joined)}
        readded = {match.group(1) for match in add_constraint.finditer(joined)}
        for name in dropped - readded:
            found.discard(f"public.{name}")
        # PostgreSQL truncated the unnamed three-column UNIQUE index name at 63
        # bytes on the queue table; record the catalog name explicitly.
        if "unique (workspace_id, source_id, version_id)" in joined:
            found.add(
                "private.snapshot_verification_jobs_workspace_id_source_id_version_i_key"
            )
        self.assertEqual(found, expected["indexes"])

    def test_every_extension_and_event_trigger_is_catalogued(self):
        expected, body = self._body()
        joined = self._join(body)
        extension = re.compile(r"\bcreate\s+extension\s+(?:if\s+not\s+exists\s+)?([a-z_][a-z0-9_]*)", re.I)
        event_trigger = re.compile(r"\bcreate\s+event\s+trigger\s+([a-z_][a-z0-9_]*)", re.I)
        self.assertEqual({match.group(1) for match in extension.finditer(joined)}, expected["extensions"])
        self.assertEqual({match.group(1) for match in event_trigger.finditer(joined)}, expected["event_triggers"])


if __name__ == "__main__":
    unittest.main()
