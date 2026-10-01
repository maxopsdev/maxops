"""One resource flagged by several checks is one opportunity, not several.

Every ASG check shares a flat 30%-of-cost heuristic, so an ASG caught by three
of them reported the same figure three times. The dashboard summed per check,
which multiplied a single opportunity by the number of checks that noticed it.
"""
from __future__ import annotations

import pytest

from app.api.routes.checks import _savings_by_resource
from app.models.settings import (
    AccountSettings,
    OnboardingCheckResult,
    OnboardingExecution,
)

URL = "/api/v1/checks/latest-results"


def _resource(resource_id, yearly=None, monthly=None):
    metadata = {}
    if yearly is not None:
        metadata["potential_savings_yearly"] = yearly
    if monthly is not None:
        metadata["potential_savings_monthly"] = monthly
    return {"resource_id": resource_id, "metadata": metadata}


def test_savings_are_keyed_by_resource():
    rows = [_resource("asg-a", yearly=1200), _resource("asg-b", yearly=600)]
    assert _savings_by_resource(rows) == {"asg-a": 1200.0, "asg-b": 600.0}


def test_a_monthly_only_resource_is_annualised():
    assert _savings_by_resource([_resource("asg-a", monthly=100)]) == {"asg-a": 1200.0}


def test_a_repeated_resource_within_one_check_takes_the_largest():
    """A duplicated row is a duplicate, not extra saving."""
    rows = [_resource("asg-a", yearly=500), _resource("asg-a", yearly=900)]
    assert _savings_by_resource(rows) == {"asg-a": 900.0}


def test_resources_without_savings_are_left_out():
    rows = [_resource("asg-a", yearly=0), _resource("asg-b"), _resource("asg-c", yearly=300)]
    assert _savings_by_resource(rows) == {"asg-c": 300.0}


def test_resources_without_an_id_are_skipped():
    assert _savings_by_resource([{"metadata": {"potential_savings_yearly": 100}}]) == {}


def test_a_missing_metadata_block_does_not_raise():
    assert _savings_by_resource([{"resource_id": "asg-a"}]) == {}
    assert _savings_by_resource([{"resource_id": "asg-a", "metadata": None}]) == {}


@pytest.fixture
def two_checks_one_asg(db_session):
    """The reported scenario: one ASG, flagged by two checks, same heuristic."""
    account = AccountSettings(
        environment="production",
        account="123456789012",
        environment_options=["production"],
        regions=["us-east-1"],
    )
    db_session.add(account)
    db_session.commit()

    execution = OnboardingExecution(settings_id=account.id, status="completed")
    db_session.add(execution)
    db_session.commit()

    for check_id in ("asg_low_cpu_overprovisioned", "asg_idle_capacity_high"):
        db_session.add(
            OnboardingCheckResult(
                execution_id=execution.id,
                check_id=check_id,
                name=check_id,
                resource_type="asg",
                status="completed",
                resources_found=1,
                metadata_json={"resources": [_resource("my-asg", yearly=3600)]},
            )
        )
    db_session.commit()
    return execution


def test_each_check_reports_its_own_total_unchanged(client, two_checks_one_asg):
    """Per-check figures stay as they were; only the dashboard total dedupes."""
    body = client.get(URL).json()
    assert body["asg_low_cpu_overprovisioned"]["potential_savings_yearly"] == 3600
    assert body["asg_idle_capacity_high"]["potential_savings_yearly"] == 3600


def test_the_payload_exposes_the_shared_resource(client, two_checks_one_asg):
    """Without this the client cannot tell the two totals describe one ASG."""
    body = client.get(URL).json()
    assert body["asg_low_cpu_overprovisioned"]["savings_by_resource"] == {"my-asg": 3600}
    assert body["asg_idle_capacity_high"]["savings_by_resource"] == {"my-asg": 3600}


def test_deduplicating_the_payload_yields_one_opportunity(client, two_checks_one_asg):
    """What the dashboard now computes: 3600, not 7200."""
    body = client.get(URL).json()
    best: dict = {}
    for entry in body.values():
        for resource_id, yearly in (entry.get("savings_by_resource") or {}).items():
            best[resource_id] = max(best.get(resource_id, 0), yearly)
    assert sum(best.values()) == 3600
    assert sum(e["potential_savings_yearly"] for e in body.values()) == 7200
