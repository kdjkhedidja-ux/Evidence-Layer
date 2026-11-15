from __future__ import annotations

import unittest
from uuid import UUID

from evidence_service.restore_reconciliation import verify_restored_deletion

REQUEST_ID = UUID("91000000-0000-4000-8000-000000000001")
SUBJECT_ID = UUID("92000000-0000-4000-8000-000000000001")


class FakeCursor:
    def __init__(self, queue):
        self.queue = list(queue)
        self.sql = None
        self.params = None
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = params
        self.executions.append(sql)

    def fetchone(self):
        return self.queue.pop(0)

    def fetchall(self):
        return self.queue.pop(0)


class FakeConnection:
    def __init__(self, queue):
        self.cursor_obj = FakeCursor(queue)

    def cursor(self):
        return self.cursor_obj


class RestoreReconciliationTests(unittest.TestCase):
    def test_clean_restore(self):
        connection = FakeConnection(
            [
                (True,),
                [(REQUEST_ID, SUBJECT_ID)],
                (0,),
                (0, 0, 0, 0, 0, 0),
            ]
        )
        result = verify_restored_deletion(connection)
        self.assertEqual(result["status"], "clean")
        self.assertEqual(result["requests_checked"], 1)
        self.assertEqual(result["failures"], [])
        self.assertTrue(
            any("status = 'purged'" in sql for sql in connection.cursor_obj.executions)
        )

    def test_resurrected_data_and_auth_identity(self):
        connection = FakeConnection(
            [
                (True,),
                [(REQUEST_ID, SUBJECT_ID)],
                (1,),
                (1, 0, 0, 0, 0, 0),
            ]
        )
        result = verify_restored_deletion(connection)
        self.assertEqual(result["status"], "resurrected")
        failure = result["failures"][0]
        self.assertEqual(failure["request_id"], str(REQUEST_ID))
        self.assertEqual(failure["resurrected"]["workspaces"], 1)
        self.assertEqual(failure["resurrected"]["auth_identity"], 1)
        self.assertTrue(failure["auth_checked"])

    def test_auth_schema_absent_is_reported_not_failed(self):
        connection = FakeConnection(
            [
                (False,),
                [(REQUEST_ID, SUBJECT_ID)],
                (0, 0, 0, 0, 0, 0),
            ]
        )
        result = verify_restored_deletion(connection)
        self.assertEqual(result["status"], "clean")
        self.assertFalse(result["auth_schema_present"])
        self.assertEqual(result["failures"], [])

    def test_no_purged_requests(self):
        connection = FakeConnection([(True,), []])
        result = verify_restored_deletion(connection)
        self.assertEqual(result["status"], "clean")
        self.assertEqual(result["requests_checked"], 0)


if __name__ == "__main__":
    unittest.main()
