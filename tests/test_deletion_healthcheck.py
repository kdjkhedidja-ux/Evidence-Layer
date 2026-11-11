from __future__ import annotations

import unittest

from evidence_service.deletion_healthcheck import read_deletion_health


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.sql = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.sql = sql

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row):
        self.cursor_obj = FakeCursor(row)

    def cursor(self):
        return self.cursor_obj


class DeletionHealthTests(unittest.TestCase):
    def test_healthy_queue(self):
        health = read_deletion_health(FakeConnection((0, 0, 0, 0, 0, 0, 0)))
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["alerts"], [])

    def test_failed_and_old_pending_are_alerts(self):
        health = read_deletion_health(FakeConnection((1, 2, 601, 0, 0, 0, 0)))
        self.assertEqual(health["status"], "alert")
        self.assertIn("purge_failed", health["alerts"])
        self.assertIn("purge_pending_age", health["alerts"])

    def test_ack_and_storage_queue_are_alerts(self):
        health = read_deletion_health(FakeConnection((0, 0, 0, 0, 0, 1, 2)))
        self.assertEqual(health["status"], "alert")
        self.assertIn("journal_ack_pending", health["alerts"])
        self.assertIn("storage_queue_age", health["alerts"])


if __name__ == "__main__":
    unittest.main()
