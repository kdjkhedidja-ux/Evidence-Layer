from __future__ import annotations

from uuid import UUID

import pytest

from evidence_service import purge_worker


def test_worker_requires_private_database_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVIDENCE_PURGE_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="EVIDENCE_PURGE_DATABASE_URL"):
        purge_worker.run_account_deletion_slice(
            UUID("72000000-0000-4000-8000-000000000001")
        )
