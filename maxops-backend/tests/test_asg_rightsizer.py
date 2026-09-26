from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from inspect import signature

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException

from app.api.routes.recommendations import (
    get_asg_rightsize_confidence_trend,
    get_asg_rightsize_recommendation,
    list_asg_rightsize_recommendations,
)
from app.services.asg_rightsizer import ASGRightsizer
from app.services.asg_trend_cache import asg_trend_cache
from app.models.inventory import AsgInventory
from app.services.scan_service import _store_inventory_resource
from rightsizers.asg.asg_rightsizer.models import ASGCapacityPolicy
from tests.asg_rightsizer_helpers import catalog, metadata, run, session


def test_clean_recommendation_has_exact_targets_and_unchanged_max():
    result, _, _ = run()
    assert result["classification"] == "ACTIONABLE"
    assert result["current_capacity_evidence"]["inventory_generated_at"] == (
        "2026-07-15T00:00:00Z"
    )
    assert [
        (item["target_min_size"], item["target_desired_capacity"], item["target_max_size"])
        for item in result["recommendations"]
    ] == [(3, 6, 20), (3, 7, 20), (4, 9, 20)]
    assert result["tiers"]["balanced"]["target_desired_capacity"] == 7
    assert result["tiers"]["default"] == "balanced"


def test_missing_memory_is_preview_only():
    telemetry = metadata()["telemetry_summary"]
    telemetry["memory_percent"] = {
        "present": False,
        "observed_days": 0.0,
        "thin_data": True,
        "pairing_ratio": None,
        "source": None,
        "status": "unavailable",
    }
    result, _, _ = run(metadata_override={"telemetry_summary": telemetry})
    assert result["recommendations"] == []
    assert result["tiers"]["balanced"] is None
    assert result["classification"] == "PREVIEW"
    preview = result["savings_previews"][0]
    assert preview["blockers"] == ["MEMORY_METRIC_NOT_ENABLED"]
    assert preview["options"]["balanced"]["target_desired_capacity"] == 7


@pytest.mark.parametrize(
    "metric,reason",
    [
        ("cpu_percent", "ASG_CPU_METRIC_UNAVAILABLE"),
        ("in_service_instances", "ASG_IN_SERVICE_CAPACITY_METRIC_UNAVAILABLE"),
    ],
)
def test_missing_required_metric_is_insufficient(metric, reason):
    telemetry = metadata()["telemetry_summary"]
    telemetry[metric]["present"] = False
    telemetry[metric]["observed_days"] = 0.0
    result, _, _ = run(metadata_override={"telemetry_summary": telemetry})
    assert result["classification"] == "INSUFFICIENT_DATA"
    assert result["blocking_reasons"] == [reason]
    if metric == "in_service_instances":
        assert result["messages"] == [
            "Historical ASG capacity is unavailable. Enable Auto Scaling group "
            "metrics collection to provide GroupInServiceInstances for capacity assessment."
        ]
        assert "message" not in result


def test_desired_history_missing_caps_conditional():
    telemetry = metadata()["telemetry_summary"]
    telemetry["desired_capacity"]["present"] = False
    result, _, _ = run(metadata_override={"telemetry_summary": telemetry})
    assert result["classification"] == "CONDITIONAL"
    assert "ASG_DESIRED_CAPACITY_HISTORY_UNAVAILABLE" in result["tiers"]["balanced"]["reason_codes"]
    assert result["messages"] == [
        "Historical desired capacity is unavailable. Enable Auto Scaling group "
        "metrics collection to provide GroupDesiredCapacity; the current desired "
        "value remains available from the group configuration."
    ]
    assert "message" not in result


def test_zero_current_in_service_is_insufficient_even_with_healthy_history():
    result, _, _ = run(metadata_override={"in_service_instance_ids": []})
    assert result["classification"] == "INSUFFICIENT_DATA"
    assert result["blocking_reasons"] == ["ASG_CURRENT_IN_SERVICE_CAPACITY_ZERO"]
    assert result["recommendations"] == []
    assert result["savings_previews"] == []


@pytest.mark.parametrize("ratio", [0.8999, 0.9, 0.9001])
def test_pairing_boundary_is_inclusive(ratio):
    telemetry = metadata()["telemetry_summary"]
    telemetry["cpu_percent"]["pairing_ratio"] = ratio
    result, _, _ = run(metadata_override={"telemetry_summary": telemetry})
    assert result["classification"] == (
        "INSUFFICIENT_DATA" if ratio < 0.9 else "ACTIONABLE"
    )


def test_decimal_minimum_savings_equality_is_included():
    unit = catalog().get("us-east-1", "m6i.large", "linux").monthly_usd
    result, _, _ = run(min_monthly_savings=3 * unit)
    assert result["tiers"]["balanced"] is not None


def test_policy_validation():
    with pytest.raises(ValueError):
        replace(ASGCapacityPolicy(), minimum_pairing_ratio=1.1)


class FakeTrendAdapter:
    def __init__(self):
        self.calls = 0

    def get_asg_confidence_trend(self, *args, **kwargs):
        self.calls += 1
        return {
            "daily": [{"timestamp": "2026-07-01T00:00:00+00:00", "maximum": 40}],
            "buckets": {"30d": {"maximum": 40, "p99": 39, "p95": 35}},
            "memory": {"daily": [], "buckets": {}, "source": kwargs.get("memory_source")},
        }


def test_trend_cache_isolated_and_reused():
    asg_trend_cache.clear()
    _, db, _ = run()
    adapter = FakeTrendAdapter()
    service = ASGRightsizer(db, catalog=catalog())
    end = datetime(2026, 7, 15, tzinfo=timezone.utc)
    first = service.get_confidence_trend(1, adapter=adapter, end_date=end)
    second = service.get_confidence_trend(1, adapter=adapter, end_date=end)
    assert first["cached"] is False
    assert second["cached"] is True
    assert adapter.calls == 1


def test_trend_cache_isolated_when_persisted_memory_source_changes():
    asg_trend_cache.clear()
    _, db, row = run()
    adapter = FakeTrendAdapter()
    service = ASGRightsizer(db, catalog=catalog())
    end = datetime(2026, 7, 15, tzinfo=timezone.utc)
    service.get_confidence_trend(1, adapter=adapter, end_date=end)
    changed = dict(row.metadata_json)
    changed["memory_metric_source"] = {
        "kind": "group",
        "namespace": "Custom",
        "metric_name": "MemoryUtilization",
        "dimensions": [{"Name": "AutoScalingGroupName", "Value": "asg-test"}],
    }
    row.metadata_json = changed
    db.commit()
    service.get_confidence_trend(1, adapter=adapter, end_date=end)
    assert adapter.calls == 2


def test_list_filters_classification():
    _, db, _ = run()
    service = ASGRightsizer(db, catalog=catalog())
    assert len(service.list_recommendations(classification="ACTIONABLE")) == 1
    assert service.list_recommendations(classification="DEFERRED") == []


def test_same_asg_name_is_scoped_by_account_and_region():
    db = session()
    generated = datetime(2026, 7, 15, tzinfo=timezone.utc)
    base = {
        "resource_id": "workers",
        "resource_name": "workers",
        "resource_type": "asg",
        "state": "active",
        "min_size": 1,
        "desired_capacity": 2,
        "max_size": 4,
        "instance_type": "m6i.large",
        "metadata": {"min_size": 1, "desired_capacity": 2, "max_size": 4},
    }
    _store_inventory_resource(
        db, "asg", {**base, "region": "us-east-1"}, "111", generated
    )
    _store_inventory_resource(
        db, "asg", {**base, "region": "us-west-2"}, "111", generated
    )
    _store_inventory_resource(
        db, "asg", {**base, "region": "us-east-1"}, "222", generated
    )
    _store_inventory_resource(
        db,
        "asg",
        {**base, "region": "us-east-1", "desired_capacity": 3},
        "111",
        generated,
    )
    assert db.query(AsgInventory).count() == 3
    east = (
        db.query(AsgInventory)
        .filter_by(account_id="111", region="us-east-1", resource_id="workers")
        .one()
    )
    assert east.desired_capacity == 3


def test_asg_routes_filter_and_return_404():
    _, db, _ = run()
    result = list_asg_rightsize_recommendations(
        account_id="123456789012",
        region="us-east-1",
        state="active",
        classification="ACTIONABLE",
        min_monthly_savings=0.0,
        db=db,
    )
    assert [item["resource_id"] for item in result] == ["asg-test"]

    lowercase = list_asg_rightsize_recommendations(
        account_id="123456789012",
        region="us-east-1",
        state="active",
        classification="actionable",
        min_monthly_savings=0.0,
        db=db,
    )
    assert [item["resource_id"] for item in lowercase] == ["asg-test"]

    with pytest.raises(HTTPException) as invalid:
        list_asg_rightsize_recommendations(
            account_id=None,
            region=None,
            state="active",
            classification="BANANAS",
            min_monthly_savings=0.0,
            db=db,
        )
    assert invalid.value.status_code == 422

    with pytest.raises(HTTPException) as exc_info:
        get_asg_rightsize_recommendation(999, min_monthly_savings=0.0, db=db)
    assert exc_info.value.status_code == 404


def test_asg_route_candidate_limit_defaults_to_service_policy():
    for route in (
        list_asg_rightsize_recommendations,
        get_asg_rightsize_recommendation,
    ):
        query = signature(route).parameters["candidate_limit"].default
        assert query.default is None


def test_asg_trend_route_maps_aws_failure_to_502(monkeypatch):
    _, db, _ = run()

    def fail(*args, **kwargs):
        raise ClientError(
            {"Error": {"Code": "Throttling", "Message": "slow down"}},
            "GetMetricData",
        )

    monkeypatch.setattr(ASGRightsizer, "get_confidence_trend", fail)
    with pytest.raises(HTTPException) as exc_info:
        get_asg_rightsize_confidence_trend(1, db=db)
    assert exc_info.value.status_code == 502
