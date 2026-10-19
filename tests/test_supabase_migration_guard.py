import os
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

    def test_clean_existing_project_has_four_reviewable_pending_migrations(self):
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


if __name__ == "__main__":
    unittest.main()
