from __future__ import annotations

import unittest
from unittest.mock import patch
from uuid import UUID

from evidence_service.deletion_worker import (
    acquire_single_instance_lock,
    claim_next_request,
    process_pending,
    reconcile_stale_manifest_ack,
    record_failure,
    release_claim,
)


class FakeCursor:
    def __init__(self, rows, rowcount=0, row=None):
        self.rows = rows
        self.rowcount = rowcount
        self.row = row if row is not None else (0,)
        self.sql = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, rows=(), repaired=0, row=None):
        self.cursor_obj = FakeCursor(rows, rowcount=repaired, row=row)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


REQUEST_ID = UUID("11111111-1111-1111-1111-111111111111")
SECOND_REQUEST_ID = UUID("22222222-2222-4222-8222-222222222222")


class DeletionWorkerTests(unittest.TestCase):
    def test_reconcile_stale_manifest_ack_commits(self):
        connection = FakeConnection([], repaired=1)
        self.assertEqual(reconcile_stale_manifest_ack(connection), 1)
        self.assertEqual(connection.commits, 1)
        self.assertIn("journal_ack_pending", connection.cursor_obj.sql)
        self.assertIn("manifest_frozen", connection.cursor_obj.sql)

    def test_single_instance_lock_acquires_when_available(self):
        connection = FakeConnection([], repaired=1, row=(True,))
        self.assertTrue(acquire_single_instance_lock(connection, timeout_seconds=0))
        self.assertIn("pg_try_advisory_lock", connection.cursor_obj.sql)

    def test_single_instance_lock_times_out(self):
        connection = FakeConnection([], repaired=0, row=(False,))
        self.assertFalse(acquire_single_instance_lock(connection, timeout_seconds=0))

    def test_claim_next_request_commits_and_returns_payload(self):
        payload = {"request_id": str(REQUEST_ID), "attempts": 1}
        connection = FakeConnection(row=(payload,))
        result = claim_next_request(connection, "worker-a")
        self.assertEqual(result, payload)
        self.assertEqual(connection.commits, 1)
        self.assertIn("claim_deletion_request", connection.cursor_obj.sql)
        self.assertEqual(connection.cursor_obj.params, ("worker-a", 300, 5))

    def test_claim_next_request_returns_none_when_empty(self):
        connection = FakeConnection(row=(None,))
        self.assertIsNone(claim_next_request(connection, "worker-a"))

    def test_record_failure_commits_and_returns_payload(self):
        payload = {"status": "purge_failed", "terminal": False}
        connection = FakeConnection(row=(payload,))
        result = record_failure(connection, REQUEST_ID, "worker-a", "worker_error")
        self.assertEqual(result, payload)
        self.assertEqual(connection.commits, 1)
        self.assertIn("record_deletion_failure", connection.cursor_obj.sql)
        self.assertEqual(
            connection.cursor_obj.params,
            (str(REQUEST_ID), "worker-a", "worker_error", 5),
        )

    def test_release_claim_commits(self):
        connection = FakeConnection()
        release_claim(connection, REQUEST_ID, "worker-a", "purged")
        self.assertEqual(connection.commits, 1)
        self.assertIn("release_deletion_claim", connection.cursor_obj.sql)
        self.assertEqual(
            connection.cursor_obj.params,
            (str(REQUEST_ID), "worker-a", "purged"),
        )

    def test_process_pending_releases_purged_claim(self):
        connection = FakeConnection()
        with patch(
            "evidence_service.deletion_worker.run_export_cleanup",
            return_value=0,
        ), patch(
            "evidence_service.deletion_worker.run_storage_cleanup",
            return_value={"processed": 1, "completed": 1, "failed": 0},
        ), patch(
            "evidence_service.deletion_worker.claim_next_request",
            side_effect=[
                {"request_id": str(REQUEST_ID), "attempts": 1},
                None,
            ],
        ), patch(
            "evidence_service.deletion_worker.run_account_deletion",
            return_value={"status": "purged", "subject_id": "s"},
        ), patch(
            "evidence_service.deletion_worker.release_claim",
        ) as release, patch(
            "evidence_service.deletion_worker.record_failure",
        ) as failure:
            result = process_pending(connection, owner="worker-a")
        release.assert_called_once_with(
            connection, REQUEST_ID, "worker-a", "purged"
        )
        failure.assert_not_called()
        self.assertEqual(result["repaired_stale_ack"], 0)
        self.assertEqual(result["exports_cleaned"], 0)
        self.assertEqual(result["storage"]["completed"], 1)
        self.assertEqual(len(result["accounts"]), 1)

    def test_process_pending_records_retryable_failure(self):
        connection = FakeConnection()
        with patch(
            "evidence_service.deletion_worker.run_export_cleanup",
            return_value=0,
        ), patch(
            "evidence_service.deletion_worker.run_storage_cleanup",
            return_value={"processed": 0, "completed": 0, "failed": 0},
        ), patch(
            "evidence_service.deletion_worker.claim_next_request",
            side_effect=[{"request_id": str(REQUEST_ID), "attempts": 1}, None],
        ), patch(
            "evidence_service.deletion_worker.run_account_deletion",
            return_value={
                "status": "purge_pending",
                "subject_id": "s",
                "failure": "storage_cleanup_failed",
            },
        ), patch(
            "evidence_service.deletion_worker.record_failure",
            return_value={"status": "purge_failed", "terminal": False},
        ) as failure, patch(
            "evidence_service.deletion_worker.release_claim",
        ) as release:
            result = process_pending(connection, owner="worker-a")
        failure.assert_called_once_with(
            connection, REQUEST_ID, "worker-a", "worker_error", max_attempts=5
        )
        release.assert_not_called()
        self.assertEqual(result["accounts"][0]["failure_record"]["terminal"], False)

    def test_process_pending_preserves_verification_failure_category(self):
        connection = FakeConnection()
        with patch(
            "evidence_service.deletion_worker.run_export_cleanup",
            return_value=0,
        ), patch(
            "evidence_service.deletion_worker.run_storage_cleanup",
            return_value={"processed": 0, "completed": 0, "failed": 0},
        ), patch(
            "evidence_service.deletion_worker.claim_next_request",
            side_effect=[{"request_id": str(REQUEST_ID), "attempts": 1}, None],
        ), patch(
            "evidence_service.deletion_worker.run_account_deletion",
            return_value={
                "status": "purge_failed",
                "subject_id": "s",
                "failure_category": "verification_failed",
            },
        ), patch(
            "evidence_service.deletion_worker.record_failure",
            return_value={"status": "purge_failed", "terminal": True},
        ) as failure:
            process_pending(connection, owner="worker-a")
        failure.assert_called_once_with(
            connection,
            REQUEST_ID,
            "worker-a",
            "verification_failed",
            max_attempts=5,
        )

    def test_process_pending_records_worker_error_on_exception(self):
        connection = FakeConnection()
        with patch(
            "evidence_service.deletion_worker.run_export_cleanup",
            return_value=0,
        ), patch(
            "evidence_service.deletion_worker.run_storage_cleanup",
            return_value={"processed": 0, "completed": 0, "failed": 0},
        ), patch(
            "evidence_service.deletion_worker.claim_next_request",
            side_effect=[{"request_id": str(REQUEST_ID), "attempts": 1}, None],
        ), patch(
            "evidence_service.deletion_worker.run_account_deletion",
            side_effect=RuntimeError("boom"),
        ), patch(
            "evidence_service.deletion_worker.record_failure",
            return_value={"status": "purge_failed", "terminal": False},
        ) as failure:
            result = process_pending(connection, owner="worker-a")
        self.assertEqual(connection.rollbacks, 1)
        failure.assert_called_once_with(
            connection, REQUEST_ID, "worker-a", "worker_error", max_attempts=5
        )
        self.assertEqual(result["accounts"][0]["status"], "worker_error")

    def test_process_pending_claims_at_most_limit(self):
        connection = FakeConnection()
        with patch(
            "evidence_service.deletion_worker.run_export_cleanup",
            return_value=0,
        ), patch(
            "evidence_service.deletion_worker.run_storage_cleanup",
            return_value={"processed": 0, "completed": 0, "failed": 0},
        ), patch(
            "evidence_service.deletion_worker.claim_next_request",
            side_effect=[
                {"request_id": str(REQUEST_ID), "attempts": 1},
                {"request_id": str(SECOND_REQUEST_ID), "attempts": 1},
            ],
        ) as claim, patch(
            "evidence_service.deletion_worker.run_account_deletion",
            return_value={"status": "purged", "subject_id": "s"},
        ), patch(
            "evidence_service.deletion_worker.release_claim",
        ):
            result = process_pending(connection, owner="worker-a", limit=1)
        self.assertEqual(claim.call_count, 1)
        self.assertEqual(len(result["accounts"]), 1)

    def test_process_pending_with_no_requests(self):
        connection = FakeConnection()
        with patch(
            "evidence_service.deletion_worker.run_export_cleanup",
            return_value=0,
        ), patch(
            "evidence_service.deletion_worker.run_storage_cleanup",
            return_value={"processed": 0, "completed": 0, "failed": 0},
        ), patch(
            "evidence_service.deletion_worker.claim_next_request",
            return_value=None,
        ), patch(
            "evidence_service.deletion_worker.run_account_deletion"
        ) as account:
            result = process_pending(connection, owner="worker-a")
        account.assert_not_called()
        self.assertEqual(result["repaired_stale_ack"], 0)
        self.assertEqual(result["exports_cleaned"], 0)
        self.assertEqual(result["accounts"], [])


if __name__ == "__main__":
    unittest.main()
