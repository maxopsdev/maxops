from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.settings_service as settings_service
from app.database import Base
from app.models.settings import AccountSettings
from app.services.settings_service import (
    is_action_enabled,
    is_check_enabled,
    save_action_settings,
    save_check_settings,
)

CHECK_ID = "ec2_idle_instances"
ACTION_KEY = "stop"


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


def test_is_check_enabled_defaults_true_with_no_saved_row():
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        _make_account_settings(db)
        assert is_check_enabled(db, CHECK_ID) is True


def test_is_check_enabled_defaults_true_with_no_account_settings():
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        assert is_check_enabled(db, CHECK_ID) is True


def test_save_check_settings_persists_enabled_flag():
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        settings = _make_account_settings(db)

        save_check_settings(
            db,
            settings,
            [{"check_id": CHECK_ID, "preset": "normal", "parameters": {}, "enabled": False}],
        )
        assert is_check_enabled(db, CHECK_ID) is False

        save_check_settings(
            db,
            settings,
            [{"check_id": CHECK_ID, "preset": "normal", "parameters": {}, "enabled": True}],
        )
        assert is_check_enabled(db, CHECK_ID) is True


def test_is_action_enabled_false_when_global_switch_off(monkeypatch):
    monkeypatch.setattr(settings_service.app_settings, "actions_enabled", False)
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        settings = _make_account_settings(db)
        # Even an explicit per-action "enabled" row must not override the global gate.
        save_action_settings(db, settings, [{"action_key": ACTION_KEY, "enabled": True}])
        assert is_action_enabled(db, ACTION_KEY) is False


def test_is_action_enabled_defaults_true_when_global_switch_on_and_no_saved_row(monkeypatch):
    monkeypatch.setattr(settings_service.app_settings, "actions_enabled", True)
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        _make_account_settings(db)
        assert is_action_enabled(db, ACTION_KEY) is True


def test_is_action_enabled_respects_per_action_saved_setting(monkeypatch):
    monkeypatch.setattr(settings_service.app_settings, "actions_enabled", True)
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        settings = _make_account_settings(db)
        save_action_settings(db, settings, [{"action_key": ACTION_KEY, "enabled": False}])
        assert is_action_enabled(db, ACTION_KEY) is False
        assert is_action_enabled(db, "terminate") is True
