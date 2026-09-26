from __future__ import annotations

from pathlib import Path

import pytest

from app import startup_checks


def test_get_pricing_db_status_accepts_existing_db(tmp_path: Path, monkeypatch) -> None:
    backend_root = tmp_path / "backend"
    backend_root.mkdir()
    db_path = backend_root / "maxops_pricing.db"
    db_path.write_bytes(b"sqlite")

    monkeypatch.setattr(startup_checks, "_backend_root", lambda: backend_root)
    monkeypatch.setattr(startup_checks.settings, "pricing_database_path", "./maxops_pricing.db")

    status = startup_checks.get_pricing_db_status()
    assert status["ready"] is True
    assert status["state"] == "ready"


def test_get_pricing_db_status_reports_unpack_message_when_artifact_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backend_root = tmp_path / "backend"
    artifact_dir = backend_root / "pricing_artifacts"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "maxops_pricing.db.xz").write_bytes(b"xz")
    (artifact_dir / "maxops_pricing.db.xz.sha256").write_text("deadbeef  maxops_pricing.db.xz\n")

    monkeypatch.setattr(startup_checks, "_backend_root", lambda: backend_root)
    monkeypatch.setattr(startup_checks.settings, "pricing_database_path", "./maxops_pricing.db")

    status = startup_checks.get_pricing_db_status()
    assert status["ready"] is False
    assert status["state"] == "artifact_available"
    assert "unpack_pricing_db.py" in str(status["message"])


def test_get_pricing_db_status_reports_missing_artifact_message(tmp_path: Path, monkeypatch) -> None:
    backend_root = tmp_path / "backend"
    backend_root.mkdir()

    monkeypatch.setattr(startup_checks, "_backend_root", lambda: backend_root)
    monkeypatch.setattr(startup_checks.settings, "pricing_database_path", "./maxops_pricing.db")

    status = startup_checks.get_pricing_db_status()
    assert status["ready"] is False
    assert status["state"] == "missing_artifact"
    assert "no bundled pricing artifact was found" in str(status["message"])
