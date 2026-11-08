from __future__ import annotations

import unittest
from unittest.mock import patch
from uuid import UUID

from evidence_service.deletion_worker import process_pending


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.sql = sql

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows):
        self.cursor_obj = FakeCursor(rows)

    def cursor(self):
        return self.cursor_obj


REQUEST_ID = UUID("11111111-1111-1111-1111-111111111111")


class DeletionWorkerTests(unittest.TestCase):
    def test_process_pending_drains_storage_then_accounts(self):
        connection = FakeConnection([(REQUEST_ID,)])
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
        self.assertEqual(result["accounts"], [])


if __name__ == "__main__":
    unittest.main()
