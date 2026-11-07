import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import production_smoke


def test_check_marks_only_2xx_as_ok() -> None:
    assert production_smoke._check("me", 200, {})["ok"] is True
    assert production_smoke._check("me", 403, {})["ok"] is False
    assert production_smoke._check("me", 503, {})["ok"] is False


def test_requires_service_url_and_token(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("EVIDENCE_SERVICE_URL", raising=False)
    monkeypatch.delenv("SMOKE_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["production_smoke.py"])
    assert production_smoke.main() == 2
    assert "EVIDENCE_SERVICE_URL is required" in capsys.readouterr().err

    monkeypatch.setenv("EVIDENCE_SERVICE_URL", "https://service.example")
    assert production_smoke.main() == 2
    assert "SMOKE_ACCESS_TOKEN is required" in capsys.readouterr().err
