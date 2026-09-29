"""Changing or resetting the AWS profile that scans run as.

Onboarding writes onboarding_data.iam_role.scan_profile_name once and nothing
could change it afterwards -- a wrong choice there meant re-running onboarding.
Note this is NOT account_settings.setup_aws_profile, which covers privileged
setup work only and already had its own control.
"""
from __future__ import annotations

import pytest

from app.models.settings import AccountSettings
from app.services.aws_credentials import set_scan_aws_profile


def _account(db, **kwargs):
    row = AccountSettings(
        environment="production",
        account="123456789012",
        environment_options=["production"],
        regions=["us-east-1"],
        **kwargs,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_sets_the_scan_profile_when_none_exists(db_session):
    _account(db_session, onboarding_data={})
    assert set_scan_aws_profile(db_session, "prod") == "prod"
    row = db_session.query(AccountSettings).first()
    assert row.onboarding_data["iam_role"]["scan_profile_name"] == "prod"


def test_replaces_an_existing_scan_profile(db_session):
    _account(db_session, onboarding_data={"iam_role": {"scan_profile_name": "old"}})
    set_scan_aws_profile(db_session, "new")
    row = db_session.query(AccountSettings).first()
    assert row.onboarding_data["iam_role"]["scan_profile_name"] == "new"


def test_change_survives_a_fresh_read(db_session):
    """The JSON column is not change-tracked in place, so it must be reassigned."""
    _account(db_session, onboarding_data={"iam_role": {"scan_profile_name": "old"}})
    set_scan_aws_profile(db_session, "new")
    db_session.expire_all()
    row = db_session.query(AccountSettings).first()
    assert row.onboarding_data["iam_role"]["scan_profile_name"] == "new"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_blank_values_reset_to_the_default_chain(db_session, value):
    _account(db_session, onboarding_data={"iam_role": {"scan_profile_name": "old"}})
    assert set_scan_aws_profile(db_session, value) is None
    row = db_session.query(AccountSettings).first()
    assert row.onboarding_data["iam_role"]["scan_profile_name"] is None


def test_profile_names_are_trimmed(db_session):
    _account(db_session, onboarding_data={})
    assert set_scan_aws_profile(db_session, "  prod  ") == "prod"


def test_other_iam_role_fields_are_preserved(db_session):
    """Onboarding stores the role ARN and account here too; do not clobber them."""
    _account(
        db_session,
        onboarding_data={
            "iam_role": {
                "scan_profile_name": "old",
                "role_arn": "arn:aws:iam::123456789012:role/MaxOpsReadOnlyRole",
                "aws_account_id": "123456789012",
            },
            "pricing": {"ready": True},
        },
    )
    set_scan_aws_profile(db_session, "new")
    data = db_session.query(AccountSettings).first().onboarding_data
    assert data["iam_role"]["role_arn"].endswith("/MaxOpsReadOnlyRole")
    assert data["iam_role"]["aws_account_id"] == "123456789012"
    assert data["pricing"] == {"ready": True}


def test_tolerates_a_non_dict_onboarding_payload(db_session):
    _account(db_session, onboarding_data=None)
    assert set_scan_aws_profile(db_session, "prod") == "prod"


def test_tolerates_a_non_dict_iam_role_payload(db_session):
    _account(db_session, onboarding_data={"iam_role": "broken"})
    assert set_scan_aws_profile(db_session, "prod") == "prod"


def test_refuses_before_onboarding_has_created_a_row(db_session):
    with pytest.raises(ValueError, match="complete onboarding first"):
        set_scan_aws_profile(db_session, "prod")


def test_the_scan_reader_sees_the_change(db_session, monkeypatch):
    """The value written must be the one the adapter reads at scan time."""
    import app.services.aws_credentials as creds

    _account(db_session, onboarding_data={"iam_role": {"scan_profile_name": "old"}})
    set_scan_aws_profile(db_session, "new")
    monkeypatch.setattr(creds, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    assert creds.get_onboarding_scan_profile_name() == "new"


def test_setup_profile_is_left_alone(db_session):
    """The two profiles are independent; changing one must not touch the other."""
    _account(db_session, onboarding_data={}, setup_aws_profile="privileged")
    set_scan_aws_profile(db_session, "prod")
    assert db_session.query(AccountSettings).first().setup_aws_profile == "privileged"


# `prod` deliberately shares the settings account: these tests cover changing
# the profile, not changing account, which has its own confirmation flow in
# test_scan_profile_account_change.py.
PROFILES = [
    {"profile_name": None, "display_name": "default", "account_id": "123456789012",
     "arn": None, "is_default": True, "error": None},
    {"profile_name": "prod", "display_name": "prod", "account_id": "123456789012",
     "arn": None, "is_default": False, "error": None},
    {"profile_name": "elsewhere", "display_name": "elsewhere", "account_id": "210987654321",
     "arn": None, "is_default": False, "error": None},
]


@pytest.fixture
def stub_profiles(monkeypatch):
    import app.services.iam_onboarding_service as iam

    monkeypatch.setattr(iam, "list_available_aws_profiles", lambda: PROFILES)
    return PROFILES


def test_get_reports_the_current_profile_and_alternatives(client, db_session, stub_profiles):
    _account(db_session, onboarding_data={"iam_role": {"scan_profile_name": "prod"}})
    body = client.get("/api/v1/settings/scan-credentials").json()
    assert body["profile"] == "prod"
    assert body["using_default_chain"] is False
    assert [p["profile_name"] for p in body["available_profiles"]] == [
        None,
        "prod",
        "elsewhere",
    ]


def test_get_flags_a_profile_pointing_at_another_account(client, db_session, stub_profiles):
    """settings.account is 123456789012; the `elsewhere` profile resolves away."""
    _account(db_session, onboarding_data={"iam_role": {"scan_profile_name": "elsewhere"}})
    body = client.get("/api/v1/settings/scan-credentials").json()
    assert body["resolved_account_id"] == "210987654321"
    assert body["settings_account_id"] == "123456789012"
    assert body["account_mismatch"] is True


def test_get_reports_no_mismatch_when_the_accounts_agree(client, db_session, stub_profiles):
    _account(db_session, onboarding_data={"iam_role": {"scan_profile_name": None}})
    body = client.get("/api/v1/settings/scan-credentials").json()
    assert body["using_default_chain"] is True
    assert body["account_mismatch"] is False


def test_put_changes_the_profile_and_returns_the_new_state(client, db_session, stub_profiles):
    _account(db_session, onboarding_data={"iam_role": {"scan_profile_name": None}})
    body = client.put("/api/v1/settings/scan-credentials", json={"profile": "prod"}).json()
    assert body["profile"] == "prod"
    assert db_session.query(AccountSettings).first().onboarding_data["iam_role"][
        "scan_profile_name"
    ] == "prod"


def test_put_with_null_resets_to_the_default_chain(client, db_session, stub_profiles):
    _account(db_session, onboarding_data={"iam_role": {"scan_profile_name": "prod"}})
    body = client.put("/api/v1/settings/scan-credentials", json={"profile": None}).json()
    assert body["profile"] is None
    assert body["using_default_chain"] is True


def test_put_before_onboarding_is_a_400(client, db_session, stub_profiles):
    response = client.put("/api/v1/settings/scan-credentials", json={"profile": "prod"})
    assert response.status_code == 400
    assert "onboarding" in response.json()["detail"]


def test_get_still_works_when_profile_discovery_fails(client, db_session, monkeypatch):
    """A broken AWS config must not take the settings page down with it."""
    import app.services.iam_onboarding_service as iam

    def boom():
        raise RuntimeError("no AWS config")

    monkeypatch.setattr(iam, "list_available_aws_profiles", boom)
    _account(db_session, onboarding_data={"iam_role": {"scan_profile_name": "prod"}})
    body = client.get("/api/v1/settings/scan-credentials").json()
    assert body["profile"] == "prod"
    assert body["available_profiles"] == []


class _RecordingSession:
    """Captures the profile each boto3 Session is built with."""

    created: list = []

    def __init__(self, **kwargs):
        type(self).created.append(kwargs.get("profile_name"))

    def client(self, *args, **kwargs):
        class _Stub:
            def get_caller_identity(self):
                return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/t"}

        return _Stub()


def test_the_next_adapter_uses_the_new_profile_without_a_restart(
    client, db_session, monkeypatch, stub_profiles
):
    """The guarantee: changing the profile takes effect on the very next scan.

    Nothing caches an AWSAdapter beyond a single request or scan, and its
    __init__ re-reads the stored profile, so no restart or re-onboarding is
    needed for the backend to switch identity.
    """
    import app.services.aws_credentials as creds
    from app.adapters.aws import adapter as adapter_module

    _account(db_session, onboarding_data={"iam_role": {"scan_profile_name": "old"}})
    monkeypatch.setattr(creds, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(adapter_module.boto3, "Session", _RecordingSession)
    _RecordingSession.created = []

    adapter_module.AWSAdapter()
    assert _RecordingSession.created[-1] == "old"

    response = client.put("/api/v1/settings/scan-credentials", json={"profile": "prod"})
    assert response.status_code == 200

    adapter_module.AWSAdapter()
    assert _RecordingSession.created[-1] == "prod"


def test_a_reset_takes_effect_on_the_next_adapter_too(
    client, db_session, monkeypatch, stub_profiles
):
    import app.services.aws_credentials as creds
    from app.adapters.aws import adapter as adapter_module

    _account(db_session, onboarding_data={"iam_role": {"scan_profile_name": "prod"}})
    monkeypatch.setattr(creds, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(adapter_module.boto3, "Session", _RecordingSession)
    _RecordingSession.created = []

    client.put("/api/v1/settings/scan-credentials", json={"profile": None})

    adapter_module.AWSAdapter()
    assert _RecordingSession.created[-1] != "prod"
