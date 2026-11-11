from __future__ import annotations

import unittest
from unittest.mock import patch
from uuid import UUID

from evidence_service.deletion_worker import (
    acquire_single_instance_lock,
    process_pending,
    reconcile_stale_manifest_ack,
)


class FakeCursor:
    def __init__(self, rows, rowcount=0):
        self.rows = rows
        self.rowcount = rowcount
        self.row = (0,)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.sql = sql

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, rows, repaired=0):
        self.cursor_obj = FakeCursor(rows, rowcount=repaired)
        self.commits = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1


REQUEST_ID = UUID("11111111-1111-1111-1111-111111111111")


class DeletionWorkerTests(unittest.TestCase):
    def test_reconcile_stale_manifest_ack_commits(self):
        connection = FakeConnection([], repaired=1)
        self.assertEqual(reconcile_stale_manifest_ack(connection), 1)
        self.assertEqual(connection.commits, 1)
        self.assertIn("journal_ack_pending", connection.cursor_obj.sql)
        self.assertIn("manifest_frozen", connection.cursor_obj.sql)

    def test_single_instance_lock_acquires_when_available(self):
        connection = FakeConnection([], repaired=1)
        connection.cursor_obj.row = (True,)
        self.assertTrue(acquire_single_instance_lock(connection, timeout_seconds=0))
        self.assertIn("pg_try_advisory_lock", connection.cursor_obj.sql)

    def test_single_instance_lock_times_out(self):
        connection = FakeConnection([], repaired=0)
        connection.cursor_obj.row = (False,)
        self.assertFalse(acquire_single_instance_lock(connection, timeout_seconds=0))

    def test_process_pending_drains_storage_then_accounts(self):
        connection = FakeConnection([(REQUEST_ID,)], repaired=1)
        with patch(
            "evidence_service.deletion_worker.run_storage_cleanup",
            return_value={"processed": 1, "completed": 1, "failed": 0},
        ) as storage, patch(
            "evidence_service.deletion_worker.run_account_deletion",
            return_value={"status": "purged", "subject_id": "s"},
        ) as account:
            result = process_pending(connection)
        storage.assert_called_once_with(limit=500, connection=connection)
        account.assert_called_once_with(REQUEST_ID, connection=connection)
        self.assertEqual(result["repaired_stale_ack"], 1)
        self.assertEqual(result["exports_cleaned"], 0)
        self.assertEqual(result["storage"]["completed"], 1)
        self.assertEqual(len(result["accounts"]), 1)

    def test_process_pending_with_no_requests(self):
        connection = FakeConnection([])
        with patch(
            "evidence_service.deletion_worker.run_storage_cleanup",
            return_value={"processed": 0, "completed": 0, "failed": 0},
        ), patch(
            "evidence_service.deletion_worker.run_account_deletion"
        ) as account:
            result = process_pending(connection)
        account.assert_not_called()
        self.assertEqual(result["repaired_stale_ack"], 0)
        self.assertEqual(result["exports_cleaned"], 0)
        self.assertEqual(result["accounts"], [])


if __name__ == "__main__":
    unittest.main()
