import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import production_smoke_auth


def test_requires_all_configuration(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    for name in (
        "SUPABASE_URL",
        "NEXT_PUBLIC_SUPABASE_URL",
        "SUPABASE_PUBLISHABLE_KEY",
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
        "SMOKE_USER_EMAIL",
        "SMOKE_USER_PASSWORD",
        "SMOKE_ACCESS_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "argv", ["production_smoke_auth.py"])
    assert production_smoke_auth.main() == 2
    assert "SMOKE_USER_PASSWORD" in capsys.readouterr().err


def test_grant_failure_does_not_echo_credentials(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "public-key")
    monkeypatch.setenv("SMOKE_USER_EMAIL", "test@example.test")
    monkeypatch.setenv("SMOKE_USER_PASSWORD", "do-not-print-me")
    monkeypatch.setattr(
        production_smoke_auth, "_password_grant",
        lambda *args: (400, {"error_code": "invalid_credentials"}),
    )
    monkeypatch.setattr(sys, "argv", ["production_smoke_auth.py"])
    assert production_smoke_auth.main() == 1
    stderr = capsys.readouterr().err
    assert "invalid_credentials" in stderr
    assert "do-not-print-me" not in stderr
