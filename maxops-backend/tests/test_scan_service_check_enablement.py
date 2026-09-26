from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.checks.registry import check_registry
from app.database import Base
from app.models.settings import AccountSettings
from app.services.scan_service import _resolve_scan_inputs
from app.services.settings_service import save_check_settings

CHECK_ID = "ec2_idle_instances"


def _session_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return SessionLocal


def _make_account_settings(db) -> AccountSettings:
    settings = AccountSettings(
        environment="Development",
        account="123456789012",
        environment_options=["Development"],
        regions=["us-east-1"],
        onboarding_completed=True,
        onboarding_step="completed",
        onboarding_data={},
    )
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def test_disabled_check_is_excluded_from_scan_inputs():
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        settings = _make_account_settings(db)

        # Sanity check: with no saved settings, the check is present via the
        # check_registry fallback path.
        _, _, check_entries = _resolve_scan_inputs(db, "ec2")
        assert CHECK_ID in {check.check_id for check, _ in check_entries}
        assert len(check_entries) == len(check_registry.list_checks("ec2"))

        save_check_settings(
            db,
            settings,
            [{"check_id": CHECK_ID, "preset": "normal", "parameters": {}, "enabled": False}],
        )

        _, _, check_entries = _resolve_scan_inputs(db, "ec2")
        entry_ids = {check.check_id for check, _ in check_entries}
        assert CHECK_ID not in entry_ids
        assert len(check_entries) == len(check_registry.list_checks("ec2")) - 1
