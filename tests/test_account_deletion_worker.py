from __future__ import annotations

import os
import unittest
from unittest.mock import patch
from uuid import UUID

from evidence_service.account_deletion_worker import run_account_deletion


class FakeCursor:
    def __init__(self, status, subject_id, slice_result, prepared, marked):
        self.status = status
        self.subject_id = subject_id
        self.slice_result = slice_result
        self.prepared = prepared
        self.marked = marked
        self.executions: list[str] = []
        self.current = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params=()):
        self.executions.append(sql)
        if "select status, requester_user_id" in sql:
            self.current = (self.status, self.subject_id)
        elif "run_account_deletion_slice" in sql:
            self.current = (self.slice_result,)
        elif "prepare_individual_account_purge" in sql:
            self.current = (self.prepared,)
        elif "delete_account_lifecycle_for_purge" in sql:
            self.current = ({"deleted": True},)
        elif "mark_account_purged" in sql:
            self.current = (self.marked,)
        else:
            self.current = None

    def fetchone(self):
        return self.current


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_obj = cursor
        self.commits = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class FakeAuthClient:
    def __init__(self, status_code=204):
        self.status_code = status_code
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def delete(self, url, headers):
        self.calls.append((url, headers))
        return FakeResponse(self.status_code)

    def close(self):
        self.closed = True


REQUEST_ID = UUID("11111111-1111-1111-1111-111111111111")
SUBJECT_ID = UUID("22222222-2222-2222-2222-222222222222")
ENV = {
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "service-key",
}


class AccountDeletionWorkerTests(unittest.TestCase):
    def _run(self, status, slice_result, prepared, marked, *, storage, auth_status=204):
        cursor = FakeCursor(status, SUBJECT_ID, slice_result, prepared, marked)
        connection = FakeConnection(cursor)
        auth = FakeAuthClient(auth_status)
        with patch.dict(os.environ, ENV), patch(
            "evidence_service.account_deletion_worker.run_storage_cleanup",
            return_value=storage,
        ):
            result = run_account_deletion(
                REQUEST_ID,
                connection=connection,
                auth_client=auth,
            )
        return result, cursor, connection, auth

    def test_full_sequence_marks_purged(self):
        result, cursor, connection, auth = self._run(
            "accepted",
            {"status": "purge_pending", "verified": True},
            {"status": "prepared", "subject_id": str(SUBJECT_ID), "storage_cleanup_queued": 2},
            {"status": "purged", "subject_id": str(SUBJECT_ID)},
            storage={"processed": 2, "completed": 2, "failed": 0},
        )
        self.assertEqual(result["status"], "purged")
        self.assertEqual(len(auth.calls), 1)
        self.assertIn(str(SUBJECT_ID), auth.calls[0][0])
        self.assertTrue(any("run_account_deletion_slice" in sql for sql in cursor.executions))
        self.assertTrue(any("prepare_individual_account_purge" in sql for sql in cursor.executions))
        self.assertTrue(any("mark_account_purged" in sql for sql in cursor.executions))
        self.assertTrue(any("delete_account_lifecycle_for_purge" in sql for sql in cursor.executions))
        self.assertGreaterEqual(connection.commits, 4)

    def test_storage_failure_stops_before_auth_and_mark(self):
        result, cursor, _, auth = self._run(
            "accepted",
            {"status": "purge_pending", "verified": True},
            {"status": "prepared", "subject_id": str(SUBJECT_ID)},
            {"status": "purged"},
            storage={"processed": 1, "completed": 0, "failed": 1},
        )
        self.assertEqual(result["status"], "purge_pending")
        self.assertEqual(result["stage"], "storage_cleanup")
        self.assertEqual(auth.calls, [])
        self.assertFalse(any("mark_account_purged" in sql for sql in cursor.executions))

    def test_auth_failure_stops_before_mark(self):
        result, cursor, _, auth = self._run(
            "accepted",
            {"status": "purge_pending", "verified": True},
            {"status": "prepared", "subject_id": str(SUBJECT_ID)},
            {"status": "purged"},
            storage={"processed": 1, "completed": 1, "failed": 0},
            auth_status=500,
        )
        self.assertEqual(result["status"], "purge_pending")
        self.assertEqual(result["stage"], "auth_delete")
        self.assertEqual(len(auth.calls), 1)
        self.assertFalse(any("mark_account_purged" in sql for sql in cursor.executions))

    def test_already_purged_short_circuits(self):
        result, cursor, _, auth = self._run(
            "purged", None, None, None, storage={"processed": 0, "completed": 0, "failed": 0}
        )
        self.assertEqual(result["status"], "purged")
        self.assertTrue(result["already_purged"])
        self.assertEqual(auth.calls, [])
        self.assertEqual(cursor.executions, ["select status, requester_user_id from public.data_deletion_requests where request_id = %s"])

    def test_purge_pending_skips_scoped_slice(self):
        result, cursor, _, _ = self._run(
            "purge_pending",
            None,
            {"status": "prepared", "subject_id": str(SUBJECT_ID)},
            {"status": "purged"},
            storage={"processed": 0, "completed": 0, "failed": 0},
        )
        self.assertEqual(result["status"], "purged")
        self.assertFalse(any("run_account_deletion_slice" in sql for sql in cursor.executions))
        self.assertTrue(any("prepare_individual_account_purge" in sql for sql in cursor.executions))

    def test_unrunnable_status_raises(self):
        with self.assertRaises(RuntimeError):
            self._run(
                "approval_pending", None, None, None,
                storage={"processed": 0, "completed": 0, "failed": 0},
            )


if __name__ == "__main__":
    unittest.main()
