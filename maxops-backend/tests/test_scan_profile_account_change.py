"""Switching the scan profile to a different AWS account starts from scratch.

A role swap inside one account is routine and must stay silent. Pointing at
another account is not: every finding, inventory row and snooze describes
resources the new credentials cannot see, so it is refused until confirmed and
confirming stops running scans and clears that data.
"""
from __future__ import annotations

import pytest

from app.models.inventory import Ec2Inventory, MaxOpsInventory, ResourceTag
from app.models.settings import (
    AccountSettings,
    OnboardingExecution,
    ResourceExemption,
)

URL = "/api/v1/settings/scan-credentials"
HOME_ACCOUNT = "123456789012"
OTHER_ACCOUNT = "210987654321"

PROFILES = [
    {"profile_name": "home", "display_name": "home", "account_id": HOME_ACCOUNT,
     "arn": None, "is_default": False, "error": None},
    {"profile_name": "home-admin", "display_name": "home-admin", "account_id": HOME_ACCOUNT,
     "arn": None, "is_default": False, "error": None},
    {"profile_name": "other", "display_name": "other", "account_id": OTHER_ACCOUNT,
     "arn": None, "is_default": False, "error": None},
    {"profile_name": "unreadable", "display_name": "unreadable", "account_id": None,
     "arn": None, "is_default": False, "error": "timed out"},
]


@pytest.fixture(autouse=True)
def stub_profiles(monkeypatch):
    import app.services.iam_onboarding_service as iam

    monkeypatch.setattr(iam, "list_available_aws_profiles", lambda: PROFILES)


@pytest.fixture
def seeded(db_session):
    """An onboarded account with scan output of every kind sitting in the DB."""
    account = AccountSettings(
        environment="production",
        account=HOME_ACCOUNT,
        environment_options=["production"],
        regions=["us-east-1"],
        onboarding_data={"iam_role": {"scan_profile_name": "home"}},
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    db_session.add_all([
        MaxOpsInventory(
            inventory_id=1, resource_type="ec2", resource_id="i-1", region="us-east-1"
        ),
        Ec2Inventory(inventory_id=1, resource_id="i-1", region="us-east-1"),
        ResourceTag(
            resource_type="ec2", inventory_id=1, resource_id="i-1",
            tag_key="Name", tag_key_normalized="name",
        ),
        ResourceExemption(check_id="ec2_idle", resource_id="i-1", resource_type="ec2"),
        OnboardingExecution(settings_id=account.id, status="running", total_checks=5),
    ])
    db_session.commit()
    return account


def _counts(db):
    return {
        "inventory": db.query(MaxOpsInventory).count(),
        "ec2": db.query(Ec2Inventory).count(),
        "tags": db.query(ResourceTag).count(),
        "exemptions": db.query(ResourceExemption).count(),
    }


def test_same_account_role_swap_needs_no_confirmation(client, db_session, seeded):
    response = client.put(URL, json={"profile": "home-admin"})
    assert response.status_code == 200
    assert response.json()["profile"] == "home-admin"


def test_same_account_role_swap_keeps_all_scanned_data(client, db_session, seeded):
    before = _counts(db_session)
    client.put(URL, json={"profile": "home-admin"})
    assert _counts(db_session) == before


def test_same_account_role_swap_leaves_a_running_scan_alone(client, db_session, seeded):
    client.put(URL, json={"profile": "home-admin"})
    execution = db_session.query(OnboardingExecution).first()
    db_session.refresh(execution)
    assert execution.status == "running"


def test_a_different_account_is_refused_until_confirmed(client, db_session, seeded):
    response = client.put(URL, json={"profile": "other"})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "account_change_requires_confirmation"
    assert detail["current_account_id"] == HOME_ACCOUNT
    assert detail["new_account_id"] == OTHER_ACCOUNT
    assert detail["running_scans"] == 1


def test_a_refused_change_leaves_everything_untouched(client, db_session, seeded):
    before = _counts(db_session)
    client.put(URL, json={"profile": "other"})

    assert _counts(db_session) == before
    account = db_session.query(AccountSettings).first()
    db_session.refresh(account)
    assert account.account == HOME_ACCOUNT
    assert account.onboarding_data["iam_role"]["scan_profile_name"] == "home"


def test_confirming_clears_every_kind_of_scanned_data(client, db_session, seeded):
    response = client.put(URL, json={"profile": "other", "confirm_account_change": True})
    assert response.status_code == 200
    assert _counts(db_session) == {"inventory": 0, "ec2": 0, "tags": 0, "exemptions": 0}


def test_confirming_stops_the_running_scan(client, db_session, seeded):
    body = client.put(
        URL, json={"profile": "other", "confirm_account_change": True}
    ).json()
    assert body["stopped_scans"] == 1


def test_confirming_moves_the_account_number_across(client, db_session, seeded):
    client.put(URL, json={"profile": "other", "confirm_account_change": True})
    account = db_session.query(AccountSettings).first()
    db_session.refresh(account)
    assert account.account == OTHER_ACCOUNT
    assert account.onboarding_data["iam_role"]["scan_profile_name"] == "other"


def test_confirming_reports_what_it_did(client, db_session, seeded):
    body = client.put(
        URL, json={"profile": "other", "confirm_account_change": True}
    ).json()
    assert body["account_changed"] is True
    assert body["cleared_rows"] >= 4


def test_an_unreadable_account_is_not_treated_as_a_change(client, db_session, seeded):
    """A profile whose STS call timed out must not trigger a destructive reset."""
    before = _counts(db_session)
    response = client.put(URL, json={"profile": "unreadable"})

    assert response.status_code == 200
    assert _counts(db_session) == before


def test_resetting_to_the_default_chain_is_not_an_account_change(client, db_session, seeded):
    before = _counts(db_session)
    response = client.put(URL, json={"profile": None})

    assert response.status_code == 200
    assert _counts(db_session) == before


def test_user_configuration_is_cleared_too(client, db_session, seeded):
    """A reset is a fresh start: tuned checks belong to the account being left."""
    from app.models.settings import CheckSetting

    db_session.add(
        CheckSetting(
            account_settings_id=seeded.id,
            check_id="ec2_idle",
            resource_type="ec2",
            enabled=False,
        )
    )
    db_session.commit()

    client.put(URL, json={"profile": "other", "confirm_account_change": True})

    assert db_session.query(CheckSetting).count() == 0


def test_a_scan_in_flight_sees_the_cancellation_and_stops(db_session, seeded):
    """The signal is cooperative, so the running scan must actually observe it.

    The cancelling request commits on a different session, so the scan's own
    copy of the row is stale until re-read -- that re-read is the whole
    mechanism and is what this asserts.
    """
    from app.services.scan_service import (
        CANCELED_SCAN_STATUS,
        _scan_was_canceled,
        cancel_running_scans,
    )

    execution = db_session.query(OnboardingExecution).first()
    assert _scan_was_canceled(db_session, execution) is False

    cancel_running_scans(db_session)

    assert _scan_was_canceled(db_session, execution) is True
    assert execution.status == CANCELED_SCAN_STATUS
    assert execution.completed_at is not None


def test_cancelling_reports_how_many_scans_it_stopped(db_session, seeded):
    from app.services.scan_service import cancel_running_scans, count_running_scans

    db_session.add(OnboardingExecution(settings_id=seeded.id, status="completed"))
    db_session.commit()

    assert count_running_scans(db_session) == 1
    assert cancel_running_scans(db_session) == 1
    assert count_running_scans(db_session) == 0


def test_cancelling_leaves_finished_scans_alone(db_session, seeded):
    from app.services.scan_service import cancel_running_scans

    done = OnboardingExecution(settings_id=seeded.id, status="completed")
    db_session.add(done)
    db_session.commit()

    cancel_running_scans(db_session)
    db_session.refresh(done)
    assert done.status == "completed"


def test_no_running_scan_is_a_no_op(db_session, seeded):
    from app.services.scan_service import cancel_running_scans

    cancel_running_scans(db_session)
    assert cancel_running_scans(db_session) == 0


def test_the_scan_sees_a_cancellation_committed_by_another_session(db_session, seeded):
    """The real shape: the scan runs on its own session, as a background task does.

    Without expiring the row first, the scan's identity map keeps returning
    "running" and the loop never stops. One session cannot show that.
    """
    from sqlalchemy.orm import sessionmaker

    from app.services.scan_service import _scan_was_canceled, cancel_running_scans

    scan_session = sessionmaker(bind=db_session.get_bind())()
    try:
        execution = scan_session.query(OnboardingExecution).first()
        assert execution.status == "running"

        cancel_running_scans(db_session)

        assert _scan_was_canceled(scan_session, execution) is True
    finally:
        scan_session.close()


def test_confirming_empties_every_table_but_the_account_row(client, db_session, seeded):
    """"Start from scratch" means the whole MaxOps database, not just inventory."""
    from app.database import Base
    from app.models.action_execution import ActionExecution
    from app.models.cur import CurSetupJob
    from app.models.pricing import PricingCache
    from app.models.settings import ActionSetting, CheckFilter

    db_session.add_all([
        ActionExecution(check_id="ec2_idle", resource_id="i-1", action="stop"),
        CheckFilter(check_id="ec2_idle", filters_json=[]),
        ActionSetting(account_settings_id=seeded.id, action_key="stop", enabled=True),
        PricingCache(
            resource_type="ec2", region="us-east-1",
            parameters_hash="abc", parameters_json={},
        ),
        CurSetupJob(job_type="export"),
    ])
    db_session.commit()

    client.put(URL, json={"profile": "other", "confirm_account_change": True})

    leftovers = {
        table.name: len(db_session.execute(table.select()).fetchall())
        for table in Base.metadata.sorted_tables
        # policies are deliberately reseeded to defaults, not left empty
        if table.name not in {"account_settings", "policies"}
    }
    assert {name: n for name, n in leftovers.items() if n} == {}


def test_the_account_settings_row_survives_the_reset(client, db_session, seeded):
    """It carries the profile and account just switched to; losing it breaks setup."""
    client.put(URL, json={"profile": "other", "confirm_account_change": True})

    rows = db_session.query(AccountSettings).all()
    assert len(rows) == 1
    assert rows[0].account == OTHER_ACCOUNT
    assert rows[0].onboarding_data["iam_role"]["scan_profile_name"] == "other"


def test_default_policies_come_back_after_the_reset(client, db_session, seeded):
    """A reset lands on a fresh install, not an account with no checks to run."""
    from app.models.policy import Policy

    client.put(URL, json={"profile": "other", "confirm_account_change": True})

    assert db_session.query(Policy).count() > 0


def test_the_wipe_follows_the_metadata_so_new_tables_are_covered(db_session):
    """A hand-written model list is what let four tables drift out of the wipe."""
    from app.database import Base
    from app.services.settings_service import PRESERVED_TABLES, clear_account_inventory_state

    cleared = clear_account_inventory_state(db_session)
    expected = {t.name for t in Base.metadata.sorted_tables} - PRESERVED_TABLES
    assert set(cleared) == expected
