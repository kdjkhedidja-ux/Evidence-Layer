from __future__ import annotations

import unittest
from unittest.mock import patch

from evidence_service.storage_cleanup_worker import (
    delete_storage_object,
    run_storage_cleanup,
)


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executions: list[tuple[str, tuple]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params=()):
        self.executions.append((sql, params))

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows):
        self.cursor_obj = FakeCursor(rows)
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self.payload = payload or {}

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, status_code: int = 200, payload=None):
        self.status_code = status_code
        self.payload = payload
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def delete(self, url: str, headers: dict):
        self.calls.append((url, headers))
        return FakeResponse(self.status_code, self.payload)

    def close(self):
        self.closed = True


class StorageCleanupWorkerTests(unittest.TestCase):
    def test_success_marks_completed_and_commits(self):
        connection = FakeConnection([("queue-1", "workspace/w/source/s/version/v")])
        client = FakeClient(200)
        result = run_storage_cleanup(connection=connection, client=client)
        self.assertEqual(result, {"processed": 1, "completed": 1, "failed": 0})
        self.assertTrue(connection.committed)
        self.assertTrue(any("completed_at" in sql for sql, _ in connection.cursor_obj.executions))
        self.assertEqual(len(client.calls), 1)
        self.assertIn("source-snapshots", client.calls[0][0])

    def test_missing_object_is_treated_as_deleted(self):
        connection = FakeConnection([("queue-1", "workspace/w/source/s/version/v")])
        result = run_storage_cleanup(connection=connection, client=FakeClient(404))
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["failed"], 0)

    def test_storage_missing_object_400_is_treated_as_deleted(self):
        connection = FakeConnection([("queue-1", "workspace/w/source/s/version/v")])
        client = FakeClient(
            400,
            {
                "statusCode": "404",
                "error": "not_found",
                "message": "Object not found",
                "code": "NoSuchKey",
            },
        )
        result = run_storage_cleanup(connection=connection, client=client)
        self.assertEqual(result, {"processed": 1, "completed": 1, "failed": 0})

    def test_failure_records_attempt_and_last_error(self):
        connection = FakeConnection([("queue-1", "workspace/w/source/s/version/v")])
        result = run_storage_cleanup(connection=connection, client=FakeClient(500))
        self.assertEqual(result, {"processed": 1, "completed": 0, "failed": 1})
        self.assertTrue(
            any("attempts = attempts + 1" in sql for sql, _ in connection.cursor_obj.executions)
        )

    def test_dry_run_does_not_call_storage_and_rolls_back(self):
        connection = FakeConnection([("queue-1", "workspace/w/source/s/version/v")])
        client = FakeClient(200)
        result = run_storage_cleanup(connection=connection, client=client, dry_run=True)
        self.assertEqual(result, {"processed": 1, "completed": 0, "failed": 0})
        self.assertEqual(client.calls, [])
        self.assertTrue(connection.rolled_back)

    def test_limit_validation(self):
        with self.assertRaises(ValueError):
            run_storage_cleanup(limit=0, connection=FakeConnection([]), client=FakeClient())
        with self.assertRaises(ValueError):
            run_storage_cleanup(limit=501, connection=FakeConnection([]), client=FakeClient())

    def test_delete_object_builds_signed_storage_request(self):
        client = FakeClient(204)
        status = delete_storage_object(
            client, "https://example.supabase.co/", "service-key", "workspace/a/b"
        )
        self.assertEqual(status, 204)
        url, headers = client.calls[0]
        self.assertEqual(
            url,
            "https://example.supabase.co/storage/v1/object/source-snapshots/workspace/a/b",
        )
        self.assertEqual(headers["apikey"], "service-key")
        self.assertEqual(headers["authorization"], "Bearer service-key")


if __name__ == "__main__":
    unittest.main()
