import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

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
        self.assertIn("static sql safety is evaluated separately", message)
        self.assertNotIn("no destructive statements detected", message)
        self.assertIn("11 files", message)


if __name__ == "__main__":
    unittest.main()