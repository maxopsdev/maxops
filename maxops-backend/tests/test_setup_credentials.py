"""Separate credentials for privileged setup work.

Scans run as a deliberately read-only role. Setup work — creating a CUR
export, running Athena queries — needs permissions that role must not have,
so it resolves its own profile. The important properties are that setup never
silently borrows scan credentials without saying so, and that scans never pick
up the elevated profile.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.settings import AccountSettings
from app.services import aws_credentials

pytestmark = [pytest.mark.unit]


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    AccountSettings.__table__.create(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def account(db):
    row = AccountSettings(
        environment="prod",
        account="123456789012",
        environment_options=[],
        regions=[],
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture(autouse=True)
def use_test_session(db, monkeypatch):
    """Point the credential helpers at the in-memory database."""
    monkeypatch.setattr(aws_credentials, "SessionLocal", lambda: db)
    # Keep the real session open across helper calls that would close it.
    monkeypatch.setattr(db, "close", lambda: None)
    yield


def test_no_setup_profile_configured_returns_none(account, monkeypatch):
    monkeypatch.setattr(aws_credentials.settings, "setup_aws_profile", None)

    assert aws_credentials.get_setup_aws_profile_name() is None


def test_stored_profile_is_used(db, account, monkeypatch):
    monkeypatch.setattr(aws_credentials.settings, "setup_aws_profile", None)
    aws_credentials.set_setup_aws_profile(db, "admin-profile")

    assert aws_credentials.get_setup_aws_profile_name() == "admin-profile"


def test_stored_profile_beats_the_environment_default(db, account, monkeypatch):
    monkeypatch.setattr(aws_credentials.settings, "setup_aws_profile", "from-env")
    aws_credentials.set_setup_aws_profile(db, "from-settings")

    assert aws_credentials.get_setup_aws_profile_name() == "from-settings"


def test_environment_default_applies_when_nothing_is_stored(account, monkeypatch):
    monkeypatch.setattr(aws_credentials.settings, "setup_aws_profile", "from-env")

    assert aws_credentials.get_setup_aws_profile_name() == "from-env"


def test_clearing_the_profile_falls_back_to_the_environment(db, account, monkeypatch):
    aws_credentials.set_setup_aws_profile(db, "admin-profile")
    monkeypatch.setattr(aws_credentials.settings, "setup_aws_profile", "from-env")

    aws_credentials.set_setup_aws_profile(db, "")

    assert db.query(AccountSettings).first().setup_aws_profile is None
    assert aws_credentials.get_setup_aws_profile_name() == "from-env"


def test_whitespace_only_profile_is_treated_as_unset(db, account, monkeypatch):
    monkeypatch.setattr(aws_credentials.settings, "setup_aws_profile", None)
    aws_credentials.set_setup_aws_profile(db, "   ")

    assert aws_credentials.get_setup_aws_profile_name() is None


def test_setup_session_falls_back_to_runtime_when_unconfigured(account, monkeypatch):
    monkeypatch.setattr(aws_credentials.settings, "setup_aws_profile", None)
    calls = []
    monkeypatch.setattr(
        aws_credentials,
        "create_runtime_boto3_session",
        lambda region_name=None: calls.append(region_name) or "runtime-session",
    )

    session = aws_credentials.create_setup_boto3_session(region_name="eu-west-1")

    assert session == "runtime-session"
    assert calls == ["eu-west-1"]


def test_setup_session_uses_the_configured_profile(db, account, monkeypatch):
    aws_credentials.set_setup_aws_profile(db, "admin-profile")
    captured = {}

    class _Session:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(aws_credentials.boto3, "Session", _Session)

    aws_credentials.create_setup_boto3_session(region_name="us-east-2")

    assert captured["profile_name"] == "admin-profile"
    assert captured["region_name"] == "us-east-2"


def test_scan_credentials_never_pick_up_the_setup_profile(db, account, monkeypatch):
    """The whole point of a separate profile is that scans keep the read-only one."""
    aws_credentials.set_setup_aws_profile(db, "admin-profile")
    monkeypatch.setattr(aws_credentials, "get_onboarding_scan_profile_name", lambda: "scan-role")

    assert aws_credentials.get_runtime_aws_profile_name() == "scan-role"


def test_setting_the_profile_without_account_settings_is_a_clear_error(db):
    with pytest.raises(ValueError, match="onboarding"):
        aws_credentials.set_setup_aws_profile(db, "admin-profile")
