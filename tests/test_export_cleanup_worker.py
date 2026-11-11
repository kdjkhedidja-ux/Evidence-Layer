from __future__ import annotations

import unittest

from evidence_service.export_cleanup_worker import run_export_cleanup


class FakeCursor:
    def __init__(self):
        self.sql = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.sql = sql

    def fetchone(self):
        return (3,)


class FakeConnection:
    def __init__(self):
        self.cursor_obj = FakeCursor()
        self.commits = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1


class ExportCleanupTests(unittest.TestCase):
    def test_cleanup_calls_security_definer_function_and_commits(self):
        connection = FakeConnection()
        self.assertEqual(run_export_cleanup(connection=connection), 3)
        self.assertIn("private.cleanup_expired_exports", connection.cursor_obj.sql)
        self.assertEqual(connection.commits, 1)


if __name__ == "__main__":
    unittest.main()
