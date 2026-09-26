"""Startup validation helpers."""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger("uvicorn.error")


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_backend_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return (_backend_root() / path).resolve()


def resolve_pricing_db_path() -> Path:
    """Resolve the configured pricing DB path, relative to the backend root if not absolute."""
    return _resolve_backend_path(settings.pricing_database_path)


def get_pricing_db_status() -> dict[str, object]:
    """Return the current bundled pricing DB status for startup and onboarding UI."""
    pricing_db_path = resolve_pricing_db_path()
    artifact_dir = _backend_root() / "pricing_artifacts"
    artifact_path = artifact_dir / "maxops_pricing.db.xz"
    checksum_path = artifact_dir / "maxops_pricing.db.xz.sha256"

    if pricing_db_path.exists():
        return {
            "ready": True,
            "state": "ready",
            "message": "Pricing database is unpacked and ready.",
            "db_path": str(pricing_db_path),
            "artifact_path": str(artifact_path),
        }

    if artifact_path.exists() and checksum_path.exists():
        return {
            "ready": False,
            "state": "artifact_available",
            "message": (
                "Bundled pricing database is not unpacked yet. "
                "Run './.venv/bin/python3.14 staging_pricing/unpack_pricing_db.py' from maxops-backend "
                "or use the onboarding action to unpack the repository-bundled artifact."
            ),
            "db_path": str(pricing_db_path),
            "artifact_path": str(artifact_path),
        }

    return {
        "ready": False,
        "state": "missing_artifact",
        "message": (
            "Pricing database is missing and no bundled pricing artifact was found. "
            f"Expected DB at {pricing_db_path} and bundled artifacts under {artifact_dir}."
        ),
        "db_path": str(pricing_db_path),
        "artifact_path": str(artifact_path),
    }


def log_pricing_db_status() -> dict[str, object]:
    """Log a startup warning when pricing DB setup is incomplete."""
    status = get_pricing_db_status()
    if not bool(status["ready"]):
        logger.warning("Pricing DB setup incomplete: %s", status["message"])
    return status
