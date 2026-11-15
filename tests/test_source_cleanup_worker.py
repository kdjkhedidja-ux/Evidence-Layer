from __future__ import annotations

import unittest
from uuid import UUID

import httpx

from evidence_service.source_cleanup_worker import (
    delete_stored_object,
    run_source_cleanup,
)

QUEUE_ID = UUID("71000000-0000-4000-8000-000000000001")
OBJECT_KEY = "workspace/a/source/c/version/d"


class FakeCursor:
    def __init__(self, queue):
        self.queue = list(queue)
        self.sql = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self.queue.pop(0)


class FakeConnection:
    def __init__(self, queue):
        self.cursor_obj = FakeCursor(queue)
        self.commits = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1


def storage_transport(status: int) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert "source-snapshots" in request.url.path
        return httpx.Response(status)

    return httpx.MockTransport(handler)


class SourceCleanupTests(unittest.TestCase):
    def test_delete_stored_object_treats_204_and_404_as_success(self):
        for status in (204, 404):
            self.assertTrue(
                delete_stored_object(
                    supabase_url="https://project-ref.supabase.co",
                    service_role_key="service-role",
                    object_key=OBJECT_KEY,
                    transport=storage_transport(status),
                )
            )
        self.assertFalse(
            delete_stored_object(
                supabase_url="https://project-ref.supabase.co",
                service_role_key="service-role",
                object_key=OBJECT_KEY,
                transport=storage_transport(500),
            )
        )

    def test_cleanup_deletes_and_completes_the_claimed_object(self):
        connection = FakeConnection([(1,), (QUEUE_ID, OBJECT_KEY, 1), None])
        result = run_source_cleanup(
            connection,
            supabase_url="https://project-ref.supabase.co",
            service_role_key="service-role",
            transport=storage_transport(204),
        )
        self.assertEqual(result["enqueued"], 1)
        self.assertEqual(result["claimed"], 1)
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(connection.commits, 4)

    def test_cleanup_records_failure_without_deleting_the_queue_row(self):
        connection = FakeConnection([(0,), (QUEUE_ID, OBJECT_KEY, 1), None])
        result = run_source_cleanup(
            connection,
            supabase_url="https://project-ref.supabase.co",
            service_role_key="service-role",
            transport=storage_transport(500),
        )
        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["failed"], 1)

    def test_cleanup_is_unavailable_without_service_credentials(self):
        connection = FakeConnection([])
        result = run_source_cleanup(
            connection,
            supabase_url="",
            service_role_key="",
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(connection.commits, 0)


if __name__ == "__main__":
    unittest.main()
