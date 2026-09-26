"""Age thresholds on the "unused for N days" checks.

These three checks accepted an age parameter, documented it, registered a
default for it and then never applied it -- each computed a `cutoff_date` that
was never read, so a volume detached sixty seconds ago was reported exactly
like one detached a year ago. These tests pin the threshold down.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.checks.base import (
    age_in_days,
    meets_min_age,
    parse_state_transition_time,
    parse_timestamp,
)
from app.checks.ebs.unattached_volumes import check_ebs_unattached_volumes
from app.checks.ec2.unused_instances import check_ec2_unused_instances
from app.checks.efs.unused_file_systems import check_efs_unused_file_systems


NOW = datetime.now(timezone.utc)


def iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


class StubAdapter:
    """Returns a fixed resource list, ignoring filters."""

    def __init__(self, resources):
        self._resources = resources

    def get_resources(self, resource_type, filters=None, region=None):
        return [dict(r, metadata=dict(r.get("metadata", {}))) for r in self._resources]


# --------------------------------------------------------------- helpers ---

def test_parse_timestamp_accepts_iso_and_datetime():
    assert parse_timestamp("2024-01-15T10:30:00+00:00").year == 2024
    assert parse_timestamp("2024-01-15T10:30:00Z").tzinfo is not None
    naive = parse_timestamp(datetime(2024, 1, 15, 10, 30))
    assert naive.tzinfo == timezone.utc  # naive input assumed UTC


@pytest.mark.parametrize("value", [None, "", "not a date", 42, {}])
def test_parse_timestamp_rejects_junk(value):
    assert parse_timestamp(value) is None


def test_parse_state_transition_reason():
    """EC2 buries the stop time in a free-text field."""
    parsed = parse_state_transition_time("User initiated (2024-01-15 10:30:00 GMT)")
    assert parsed == datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "reason", [None, "", "User initiated", "Client.UserInitiatedShutdown"]
)
def test_state_transition_without_timestamp_is_unknown(reason):
    assert parse_state_transition_time(reason) is None


def test_age_in_days():
    assert round(age_in_days(iso(10)), 1) == 10.0
    assert age_in_days(None) is None


def test_meets_min_age_boundaries():
    assert meets_min_age(iso(7.1), 7) is True
    assert meets_min_age(iso(6.9), 7) is False
    # unknown age never satisfies a positive threshold -- these findings
    # recommend deletion, so "could not tell" must not mean "old enough"
    assert meets_min_age(None, 7) is False
    # a zero/None threshold disables the gate entirely
    assert meets_min_age(None, 0) is True
    assert meets_min_age(None, None) is True


# ------------------------------------------------------------------ EBS ---

def ebs_volume(volume_id, created_days_ago):
    return {
        "resource_id": volume_id,
        "resource_type": "ebs",
        "state": "available",
        "attached": False,
        "metadata": {
            "size": 100,
            "volume_type": "gp3",
            "create_time": None if created_days_ago is None else iso(created_days_ago),
        },
    }


def test_ebs_respects_min_age_days():
    adapter = StubAdapter([ebs_volume("vol-old", 30), ebs_volume("vol-new", 1)])
    found = {v["resource_id"] for v in check_ebs_unattached_volumes(adapter, min_age_days=7)}
    assert found == {"vol-old"}


def test_ebs_zero_threshold_reports_everything():
    adapter = StubAdapter([ebs_volume("vol-old", 30), ebs_volume("vol-new", 0.01)])
    found = {v["resource_id"] for v in check_ebs_unattached_volumes(adapter, min_age_days=0)}
    assert found == {"vol-old", "vol-new"}


def test_ebs_unknown_age_is_not_reported():
    adapter = StubAdapter([ebs_volume("vol-unknown", None)])
    assert check_ebs_unattached_volumes(adapter, min_age_days=7) == []


def test_ebs_attached_volumes_are_never_reported():
    attached = ebs_volume("vol-attached", 30)
    attached["state"] = "in-use"
    attached["attached"] = True
    assert check_ebs_unattached_volumes(StubAdapter([attached]), min_age_days=7) == []


def test_ebs_reports_observed_age():
    adapter = StubAdapter([ebs_volume("vol-old", 30)])
    result = check_ebs_unattached_volumes(adapter, min_age_days=7)
    assert result[0]["metadata"]["volume_age_days"] == pytest.approx(30, abs=0.2)


# ------------------------------------------------------------------ EC2 ---

def ec2_instance(instance_id, stopped_days_ago):
    reason = (
        None
        if stopped_days_ago is None
        else f"User initiated ({(NOW - timedelta(days=stopped_days_ago)).strftime('%Y-%m-%d %H:%M:%S')} GMT)"
    )
    return {
        "resource_id": instance_id,
        "resource_type": "ec2",
        "state": "stopped",
        "instance_type": "m5.large",
        "metadata": {"instance_type": "m5.large", "state_transition_reason": reason},
    }


def test_ec2_respects_stopped_days():
    adapter = StubAdapter([ec2_instance("i-old", 60), ec2_instance("i-new", 3)])
    found = {i["resource_id"] for i in check_ec2_unused_instances(adapter, stopped_days=30)}
    assert found == {"i-old"}


def test_ec2_unknown_stop_time_is_not_reported():
    """Previously every stopped instance was returned regardless of age."""
    adapter = StubAdapter([ec2_instance("i-unknown", None)])
    assert check_ec2_unused_instances(adapter, stopped_days=30) == []


def test_ec2_zero_threshold_reports_everything():
    adapter = StubAdapter([ec2_instance("i-unknown", None), ec2_instance("i-new", 1)])
    found = {i["resource_id"] for i in check_ec2_unused_instances(adapter, stopped_days=0)}
    assert found == {"i-unknown", "i-new"}


def test_ec2_reports_observed_stopped_days():
    adapter = StubAdapter([ec2_instance("i-old", 60)])
    result = check_ec2_unused_instances(adapter, stopped_days=30)
    assert result[0]["metadata"]["stopped_days_observed"] == pytest.approx(60, abs=0.2)


# ------------------------------------------------------------------ EFS ---

def efs_filesystem(fs_id, created_days_ago, mount_targets=0):
    return {
        "resource_id": fs_id,
        "resource_type": "efs",
        "metadata": {
            "NumberOfMountTargets": mount_targets,
            "SizeInBytes": {"Value": 1024 ** 3},
            "CreationTime": None if created_days_ago is None else iso(created_days_ago),
        },
    }


def test_efs_respects_min_age_days():
    adapter = StubAdapter([efs_filesystem("fs-old", 30), efs_filesystem("fs-new", 2)])
    found = {f["resource_id"] for f in check_efs_unused_file_systems(adapter, min_age_days=7)}
    assert found == {"fs-old"}


def test_efs_unknown_age_is_not_reported():
    adapter = StubAdapter([efs_filesystem("fs-unknown", None)])
    assert check_efs_unused_file_systems(adapter, min_age_days=7) == []


def test_efs_mounted_file_systems_are_never_reported():
    adapter = StubAdapter([efs_filesystem("fs-mounted", 30, mount_targets=2)])
    assert check_efs_unused_file_systems(adapter, min_age_days=7) == []


def test_efs_reports_observed_age():
    adapter = StubAdapter([efs_filesystem("fs-old", 30)])
    result = check_efs_unused_file_systems(adapter, min_age_days=7)
    assert result[0]["metadata"]["file_system_age_days"] == pytest.approx(30, abs=0.2)
