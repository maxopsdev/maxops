from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.api.routes.checks as checks_route
from app.config import settings as app_settings
from app.database import Base
from app.models.settings import AccountSettings
from app.schemas.check import CheckActionRequest

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


def test_execute_check_action_403s_when_actions_disabled(monkeypatch):
    # Patch the config singleton itself: the gate reads it through
    # settings_service, not through the route module.
    monkeypatch.setattr(app_settings, "actions_enabled", False)
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        _make_account_settings(db)
        payload = CheckActionRequest(
            action="stop",
            account_id="123456789012",
            region="us-east-1",
            resource_id="i-PLACEHOLDER-INSTANCE",
        )

        with pytest.raises(HTTPException) as exc_info:
            checks_route.execute_check_action(check_id=CHECK_ID, payload=payload, db=db)

        assert exc_info.value.status_code == 403

        from app.models.action_execution import ActionExecution

        recorded = db.query(ActionExecution).filter(ActionExecution.check_id == CHECK_ID).first()
        assert recorded is not None
        assert recorded.status == "failed"
