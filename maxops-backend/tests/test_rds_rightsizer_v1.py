from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.main import app
from app.adapters.aws.adapter import AWSAdapter
from app.models.inventory import RdsInventory
from app.api.routes.recommendations import _rds_classification_filter
from app.services.iam_onboarding_service import READ_ONLY_SCAN_POLICY
from app.services.rds_rightsizer import RDSRightsizer
from app.services.scan_service import CachedAWSAdapter
from rightsizers.rds.rds_rightsizer.catalogs import RdsClassEntry
from rightsizers.rds.rds_rightsizer.catalogs import compatible_orderable_options, storage_capability
from rightsizers.rds.rds_rightsizer.evaluation import evaluate_rds
from rightsizers.rds.rds_rightsizer.inventory import performance_insights_supported
from rightsizers.rds.rds_rightsizer.normalization import normalize_cloudwatch_metrics
from rightsizers.rds.rds_rightsizer.telemetry import normalize_performance_insights


GIB = 1024**3


def _class(name: str, vcpus: float, memory: float) -> RdsClassEntry:
    return RdsClassEntry(
        db_instance_class=name,
        vcpus=vcpus,
        memory_gib=memory,
        network_baseline_mbps=1000,
        network_peak_mbps=5000,
        ebs_baseline_iops=12000,
        ebs_peak_iops=20000,
        ebs_baseline_mbps=500,
        ebs_peak_mbps=1000,
        architecture="x86_64",
        burstable=False,
        cpu_baseline_ratio=None,
        local_nvme=False,
        current_generation=True,
        capability_source="test",
    )


CATALOG = {
    "db.m5.xlarge": _class("db.m5.xlarge", 4, 16),
    "db.m6i.large": _class("db.m6i.large", 2, 8),
    "db.m6i.xlarge": _class("db.m6i.xlarge", 4, 16),
}
PRICES = {"db.m5.xlarge": 200.0, "db.m6i.large": 100.0, "db.m6i.xlarge": 150.0}


def _price(_region, instance_type, _engine, _multi_az, _license_model):
    return PRICES.get(instance_type)


def _summary(value: float, *, lower: bool = False, days: float = 60, coverage: float = 1.0):
    common = {
        "status": "PRESENT",
        "sample_count": int(days * 288),
        "observed_days": days,
        "coverage_ratio": coverage,
        "invalid_sample_count": 0,
    }
    if lower:
        return {**common, "minimum": value, "p01": value, "p05": value, "average": value}
    return {**common, "average": value, "p50": value, "p95": value, "p99": value, "maximum": value}


def _metadata() -> dict:
    names = {
        "cpu_percent": (20, False),
        "freeable_memory_bytes": (12 * GIB, True),
        "swap_bytes": (0, False),
        "connections": (20, False),
        "read_iops": (400, False),
        "write_iops": (600, False),
        "total_iops": (1000, False),
        "read_throughput_bps": (20 * 1024**2, False),
        "write_throughput_bps": (30 * 1024**2, False),
        "total_throughput_bps": (50 * 1024**2, False),
        "read_latency_seconds": (0.002, False),
        "write_latency_seconds": (0.003, False),
        "disk_queue_depth": (1, False),
        "network_rx_bps": (50, False),
        "network_tx_bps": (40, False),
        "free_storage_bytes": (100 * GIB, True),
    }
    windows = {}
    for window in ("14d", "30d", "60d"):
        summaries = {name: _summary(value, lower=lower) for name, (value, lower) in names.items()}
        summaries["total_iops"]["pairing_ratio"] = 1.0
        summaries["total_throughput_bps"]["pairing_ratio"] = 1.0
        windows[window] = summaries
    return {
        "DBInstanceIdentifier": "db-prod",
        "DBInstanceClass": "db.m5.xlarge",
        "Engine": "postgres",
        "EngineVersion": "16.3",
        "LicenseModel": "postgresql-license",
        "MultiAZ": False,
        "StorageType": "gp3",
        "AllocatedStorage": 100,
        "Iops": 3000,
        "StorageThroughput": 125,
        "rightsizing_context": {
            "orderable_status": "SUCCESS",
            "orderable_options": [{"DBInstanceClass": name} for name in CATALOG],
            "valid_storage_status": "SUCCESS",
            "valid_storage_options": [
                {
                    "StorageType": "gp3",
                    "ProvisionedIops": [{"From": 3000, "To": 64000}],
                    "StorageThroughput": [{"From": 125, "To": 4000}],
                }
            ],
            "connection_limit": {
                "status": "PRESENT",
                "resolved_value": 1000,
                "raw_values": [{"value": "1000", "source": "user"}],
            },
            "target_connection_limits": {name: 1000 for name in CATALOG},
        },
        "rightsizing_metrics": {
            "rds_v1": {
                "generated_at": "2026-07-16T00:00:00+00:00",
                "period_seconds": 300,
                "collection": {"status": "SUCCESS", "reason_code": None},
                "windows": windows,
                "selected_decision_values": {name: value for name, (value, _lower) in names.items()},
                "performance_insights": {"status": "NOT_NEEDED", "required": False},
            }
        },
    }


def _inventory(metadata: dict | None = None) -> dict:
    return {
        "inventory_id": 42,
        "resource_id": "db-prod",
        "resource_name": "Production DB",
        "account_id": "123456789012",
        "region": "us-east-1",
        "state": "available",
        "engine": "postgres",
        "db_instance_class": "db.m5.xlarge",
        "metadata": metadata if metadata is not None else _metadata(),
    }


def test_class_selection_is_deterministic_and_tiers_are_compute_only():
    result = evaluate_rds(_inventory(), CATALOG, _price)
    assert result["classification"] == "ACTIONABLE"
    assert result["evaluation_status"]["instance_class"] == "RECOMMENDED"
    recommendation = result["recommendations"][0]
    assert recommendation["kind"] == "DB_INSTANCE_CLASS_CHANGE"
    assert recommendation["tiers"]["default"] == "balanced"
    target = recommendation["tiers"]["balanced"]
    assert target["target_db_instance_class"] == "db.m6i.large"
    assert target["projected_cpu_percent"] == 40
    assert target["monthly_savings"] == 100


def test_thin_telemetry_is_conditional_with_distinct_reason_codes():
    metadata = _metadata()
    for window in metadata["rightsizing_metrics"]["rds_v1"]["windows"].values():
        for name in ("cpu_percent", "freeable_memory_bytes"):
            window[name]["observed_days"] = 3
            window[name]["coverage_ratio"] = 0.25
    result = evaluate_rds(_inventory(metadata), CATALOG, _price)
    candidate = result["recommendations"][0]["candidates"][0]
    assert result["classification"] == "CONDITIONAL"
    assert "OBSERVATION_WINDOW_TOO_SHORT" in candidate["reason_codes"]
    assert "OBSERVATION_COVERAGE_TOO_LOW" in candidate["reason_codes"]


def test_empty_cpu_and_memory_retain_current_capacity_and_have_no_tier():
    metadata = _metadata()
    metrics = metadata["rightsizing_metrics"]["rds_v1"]
    for window in metrics["windows"].values():
        for name in ("cpu_percent", "freeable_memory_bytes"):
            window[name] = {"status": "EMPTY", "sample_count": 0, "observed_days": 0, "coverage_ratio": 0}
    metrics["selected_decision_values"]["cpu_percent"] = None
    metrics["selected_decision_values"]["freeable_memory_bytes"] = None
    result = evaluate_rds(_inventory(metadata), CATALOG, _price)
    recommendation = result["recommendations"][0]
    assert recommendation["tiers"]["default"] is None
    assert [item["target_db_instance_class"] for item in recommendation["candidates"]] == ["db.m6i.xlarge"]
    candidate = recommendation["candidates"][0]
    assert candidate["projected_util"] is None
    assert "RDS_CPU_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED" in candidate["reason_codes"]
    assert "RDS_MEMORY_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED" in candidate["reason_codes"]


def test_failed_collection_is_persisted_domain_status_not_a_dead_request():
    metadata = _metadata()
    metadata["rightsizing_metrics"]["rds_v1"].update(
        collection={"status": "ACCESS_DENIED", "reason_code": "RDS_CLOUDWATCH_COLLECTION_FAILED"},
        windows={},
        selected_decision_values={},
    )
    result = evaluate_rds(_inventory(metadata), CATALOG, _price)
    assert result["classification"] == "INSUFFICIENT_DATA"
    assert result["evaluation_status"] == {
        "instance_class": "INSUFFICIENT_DATA",
        "storage_configuration": "INSUFFICIENT_DATA",
    }


def test_existing_inventory_requires_a_fresh_rds_v1_scan():
    metadata = _metadata()
    metadata.pop("rightsizing_metrics")
    result = evaluate_rds(_inventory(metadata), CATALOG, _price)
    assert result["classification"] == "INSUFFICIENT_DATA"
    assert result["evaluation_reason_codes"]["instance_class"] == ["RDS_RIGHTSIZING_SCAN_REQUIRED"]


def test_persisted_exact_class_prices_override_legacy_catalog():
    metadata = _metadata()
    metadata["rightsizing_context"]["class_prices"] = {
        "db.m5.xlarge": {"monthly": "250.00"},
        "db.m6i.large": {"monthly": "90.00"},
        "db.m6i.xlarge": {"monthly": "160.00"},
    }
    result = evaluate_rds(_inventory(metadata), CATALOG, lambda *_args: None)
    target = result["recommendations"][0]["tiers"]["balanced"]
    assert target["monthly_savings"] == 160


def test_normalization_pairs_directions_without_zero_fill_and_converts_network():
    end = datetime(2026, 7, 16, tzinfo=timezone.utc)
    first = end - timedelta(minutes=10)
    second = end - timedelta(minutes=5)
    raw = {
        "period_seconds": 300,
        "metrics": {
            "read_iops": {"timestamps": [first, second], "values": [100, 200]},
            "write_iops": {"timestamps": [second], "values": [50]},
            "network_rx_bps": {"timestamps": [second], "values": [1_000_000]},
            "cpu_percent": {"timestamps": [second], "values": [101]},
        },
    }
    normalized = normalize_cloudwatch_metrics(raw, end)
    window = normalized["windows"]["14d"]
    assert window["total_iops"]["sample_count"] == 1
    assert window["total_iops"]["p99"] == 250
    assert window["total_iops"]["pairing_ratio"] == 0.5
    assert window["network_rx_bps"]["p99"] == 8
    assert window["cpu_percent"]["status"] == "INVALID"


def test_static_storage_policy_can_only_narrow_live_rds_ranges():
    gp3 = storage_capability("postgres", "gp3", 100)
    assert gp3 is not None
    assert (gp3.min_iops, gp3.max_iops) == (3000, 64000)
    assert storage_capability("postgres", "gp3", 10) is None
    assert storage_capability("sqlserver-se", "gp3", 100) is None


def test_orderable_options_preserve_exact_storage_features_network_and_az():
    metadata = {
        "StorageType": "gp3", "MultiAZ": True, "StorageEncrypted": True,
        "Iops": 3000, "StorageThroughput": 125, "PerformanceInsightsEnabled": True,
        "NetworkType": "DUAL", "AvailabilityZone": "us-east-1a",
    }
    good = {
        "DBInstanceClass": "db.m7g.large", "StorageType": "gp3",
        "MultiAZCapable": True, "SupportsStorageEncryption": True,
        "SupportsIops": True, "SupportsStorageThroughput": True,
        "SupportsPerformanceInsights": True, "SupportedNetworkTypes": ["IPV4", "DUAL"],
        "AvailabilityZones": [{"Name": "us-east-1a"}],
    }
    wrong_storage = {**good, "DBInstanceClass": "db.m7i.large", "StorageType": "gp2"}
    wrong_az = {**good, "DBInstanceClass": "db.m6i.large", "AvailabilityZones": [{"Name": "us-east-1b"}]}
    assert compatible_orderable_options(metadata, [wrong_storage, good, wrong_az]) == [good]


def test_gp3_storage_option_is_balanced_decimal_priced_and_never_shrinks_storage():
    metadata = _metadata()
    metadata.update(Iops=12000, StorageThroughput=500)
    metadata["rightsizing_context"]["storage_prices"] = {
        "gp3": {
            "storage_gib_month": "0.10", "iops_month": "0.02",
            "throughput_mibps_month": "0.10", "included_iops": 3000,
            "included_throughput_mibps": 125,
            "currency": "USD", "rate_codes": ["storage", "iops", "throughput"],
        }
    }
    result = evaluate_rds(_inventory(metadata), CATALOG, _price)
    storage = next(item for item in result["recommendations"] if item["kind"] == "STORAGE_CONFIGURATION_CHANGE")
    assert storage["classification"] == "ACTIONABLE"
    assert storage["current_storage"]["allocated_storage_gib"] == 100
    assert storage["target_storage"] == {
        "storage_type": "gp3", "allocated_storage_gib": 100,
        "iops": 3000, "throughput_mibps": 125,
    }
    assert storage["monthly_savings"] == 217.5


def test_storage_health_defaults_block_recommendations():
    for metric, unhealthy, expected in (
        ("write_latency_seconds", 0.050, "STORAGE_LATENCY_REQUIRES_REVIEW"),
        ("disk_queue_depth", 30, "STORAGE_QUEUE_REQUIRES_REVIEW"),
    ):
        metadata = _metadata()
        metadata.update(Iops=12000, StorageThroughput=500)
        metadata["rightsizing_context"]["storage_prices"] = {
            "gp3": {
                "storage_gib_month": "0.10",
                "iops_month": "0.02",
                "throughput_mibps_month": "0.10",
                "included_iops": 3000,
                "included_throughput_mibps": 125,
                "currency": "USD",
                "rate_codes": ["production-shaped"],
            }
        }
        metadata["rightsizing_metrics"]["rds_v1"]["selected_decision_values"][metric] = unhealthy
        result = evaluate_rds(_inventory(metadata), CATALOG, _price)
        assert not any(
            item["kind"] == "STORAGE_CONFIGURATION_CHANGE"
            for item in result["recommendations"]
        )
        assert result["evaluation_reason_codes"]["storage_configuration"] == [expected]


def test_unknown_future_engine_uses_latency_catch_all():
    metadata = _metadata()
    metadata.update(Engine="futuredb", Iops=12000, StorageThroughput=500)
    metadata["rightsizing_context"]["storage_prices"] = {
        "gp3": {
            "storage_gib_month": "0.10", "iops_month": "0.02",
            "throughput_mibps_month": "0.10", "included_iops": 3000,
            "included_throughput_mibps": 125,
        }
    }
    metadata["rightsizing_metrics"]["rds_v1"]["selected_decision_values"][
        "write_latency_seconds"
    ] = 0.050
    inventory = _inventory(metadata)
    inventory["engine"] = "futuredb"
    result = evaluate_rds(inventory, CATALOG, _price)
    assert result["evaluation_reason_codes"]["storage_configuration"] == [
        "STORAGE_LATENCY_REQUIRES_REVIEW"
    ]


def test_missing_runtime_iops_range_fails_closed():
    metadata = _metadata()
    metadata.update(Iops=12000, StorageThroughput=500)
    metadata["rightsizing_context"]["valid_storage_options"][0]["ProvisionedIops"] = []
    metadata["rightsizing_context"]["storage_prices"] = {
        "gp3": {
            "storage_gib_month": "0.10", "iops_month": "0.02",
            "throughput_mibps_month": "0.10", "included_iops": 3000,
            "included_throughput_mibps": 125,
        }
    }
    result = evaluate_rds(_inventory(metadata), CATALOG, _price)
    assert result["evaluation_status"]["storage_configuration"] == "NO_RECOMMENDATION"
    assert result["evaluation_reason_codes"]["storage_configuration"] == [
        "STORAGE_RUNTIME_CONSTRAINT_VIOLATED"
    ]


def test_memory_reduction_without_proven_target_connection_limit_is_conditional():
    metadata = _metadata()
    metadata["rightsizing_context"].pop("target_connection_limits")
    result = evaluate_rds(_inventory(metadata), CATALOG, _price)
    candidate = next(
        item
        for item in result["recommendations"][0]["candidates"]
        if item["target_db_instance_class"] == "db.m6i.large"
    )
    assert candidate["classification"] == "CONDITIONAL"
    assert "CONNECTION_HEADROOM_REQUIRES_REVIEW" in candidate["reason_codes"]


def test_scope_statuses_and_reason_codes_are_stable():
    cases = (
        ({"Engine": "aurora-postgresql"}, "available", "AURORA_REQUIRES_CLUSTER_RIGHTSIZER"),
        ({"DBClusterIdentifier": "cluster-1"}, "available", "MULTI_AZ_CLUSTER_REQUIRES_CLUSTER_RIGHTSIZER"),
        ({"ProcessorFeatures": [{"Name": "coreCount", "Value": "2"}]}, "available", "CUSTOM_PROCESSOR_CONFIGURATION_UNSUPPORTED"),
        ({}, "stopped", "RESOURCE_NOT_AVAILABLE"),
        ({}, "storage-optimization", "STORAGE_OPTIMIZATION_IN_PROGRESS"),
    )
    for changes, state, expected in cases:
        metadata = _metadata()
        metadata.update(changes)
        inventory = _inventory(metadata)
        inventory["state"] = state
        result = evaluate_rds(inventory, CATALOG, _price)
        assert result["classification"] == "DEFERRED"
        assert result["deferred_reason_codes"] == [expected]


def test_db2_uses_unsupported_pi_guidance_without_enablement_prompt():
    metadata = _metadata()
    metadata["Engine"] = "db2-se"
    metrics = metadata["rightsizing_metrics"]["rds_v1"]
    metrics["performance_insights"] = {"status": "UNSUPPORTED", "required": True}
    inventory = _inventory(metadata)
    inventory["engine"] = "db2-se"
    result = evaluate_rds(inventory, CATALOG, _price)
    assert performance_insights_supported(metadata) is False
    assert result["database_load_attribution"]["status"] == "UNSUPPORTED"
    assert result["database_load_attribution"]["enablement_prompt"] is None
    candidate = result["recommendations"][0]["candidates"][0]
    assert "PERFORMANCE_INSIGHTS_UNSUPPORTED" in candidate["reason_codes"]


def test_multi_volume_storage_is_not_applicable_without_suppressing_class_result():
    metadata = _metadata()
    metadata["AdditionalStorageVolumes"] = [{"StorageType": "gp3"}]
    result = evaluate_rds(_inventory(metadata), CATALOG, _price)
    assert result["evaluation_status"]["instance_class"] == "RECOMMENDED"
    assert result["evaluation_status"]["storage_configuration"] == "NOT_APPLICABLE"
    assert result["evaluation_reason_codes"]["storage_configuration"] == [
        "MULTI_VOLUME_STORAGE_OPTIMIZATION_UNSUPPORTED"
    ]


def test_pi_normalization_keeps_only_total_and_wait_type_attribution():
    raw = {
        "period_seconds": 300,
        "metric_list": [
            {"Key": {"Metric": "db.load.avg"}, "DataPoints": [{"Timestamp": "2026-07-16T00:00:00Z", "Value": 2.0}]},
            {"Key": {"Dimensions": {"db.wait_event_type.name": "CPU"}}, "DataPoints": [{"Timestamp": "2026-07-16T00:00:00Z", "Value": 1.5}]},
            {"Key": {"Dimensions": {"db.wait_event_type.name": "IO:DataFileRead"}}, "DataPoints": [{"Timestamp": "2026-07-16T00:00:00Z", "Value": 0.5}]},
        ],
    }
    normalized = normalize_performance_insights(raw)
    assert normalized["status"] == "AVAILABLE"
    assert normalized["summaries"]["total_load"]["p99"] == 2
    assert {item["name"] for item in normalized["wait_type_summary"]} == {"CPU", "IO:DataFileRead"}
    assert "sql" not in str(normalized).lower()


def test_onboarding_policy_contains_rds_and_optional_pi_reads():
    actions = {
        action
        for statement in READ_ONLY_SCAN_POLICY["Statement"]
        for action in statement.get("Action", [])
    }
    assert {
        "rds:DescribeDBInstances",
        "rds:DescribeOrderableDBInstanceOptions",
        "rds:DescribeValidDBInstanceModifications",
        "rds:DescribePendingMaintenanceActions",
        "rds:DescribeEvents",
        "pi:GetResourceMetrics",
    } <= actions


class _Catalog:
    def list(self):
        return CATALOG

    def monthly_price(self, _region, instance_type, _engine, *, multi_az, license_model):
        return PRICES.get(instance_type)


def _db_with_rows(*rows: RdsInventory):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add_all(rows)
    db.commit()
    return db


def test_service_fleet_ordering_none_filter_and_unknown_detail():
    no_saving = _metadata()
    no_saving["rightsizing_context"]["class_prices"] = {
        "db.m5.xlarge": {"monthly": "100"},
        "db.m6i.large": {"monthly": "110"},
        "db.m6i.xlarge": {"monthly": "120"},
    }
    db = _db_with_rows(
        RdsInventory(
            inventory_id=1, resource_id="db-action", resource_name="Action",
            resource_type="rds", region="us-east-1", state="available",
            engine="postgres", db_instance_class="db.m5.xlarge", metadata_json=_metadata(),
        ),
        RdsInventory(
            inventory_id=2, resource_id="db-none", resource_name="No saving",
            resource_type="rds", region="us-east-1", state="available",
            engine="postgres", db_instance_class="db.m5.xlarge", metadata_json=no_saving,
        ),
    )
    service = RDSRightsizer(db, catalog=_Catalog())
    rows = service.list_recommendations()
    assert [row["classification"] for row in rows] == ["ACTIONABLE", None]
    assert [row["inventory_id"] for row in service.list_recommendations(classification="NONE")] == [2]
    assert service.get_recommendation(999) is None


def test_rds_api_contract_paths_and_classification_validation():
    paths = app.openapi()["paths"]
    assert "/api/v1/recommendations/rds/rightsize" in paths
    assert "/api/v1/recommendations/rds/rightsize/{inventory_id}" in paths
    assert "/api/v1/recommendations/rds/rightsize/{inventory_id}/trend" in paths
    assert _rds_classification_filter("none") == "NONE"
    try:
        _rds_classification_filter("REJECTED")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
    else:
        raise AssertionError("invalid RDS classification should return 422")


def test_rds_inventory_and_pi_adapters_paginate_and_use_dbi_resource_id():
    class RdsClient:
        def __init__(self):
            self.calls = []

        def describe_db_instances(self, **request):
            self.calls.append(request)
            suffix = "a" if not request else "b"
            response = {
                "DBInstances": [{
                    "DBInstanceIdentifier": f"db-{suffix}",
                    "DBInstanceArn": f"arn:aws:rds:us-west-2:123:db:db-{suffix}",
                    "DBInstanceStatus": "available", "DBInstanceClass": "db.m6i.large",
                    "Engine": "postgres", "EngineVersion": "16.3",
                    "LicenseModel": "postgresql-license", "MultiAZ": True,
                    "AllocatedStorage": 100, "StorageType": "gp3", "Iops": 3000,
                    "StorageThroughput": 125, "StorageEncrypted": True,
                    "DbiResourceId": f"dbi-{suffix}", "PerformanceInsightsEnabled": True,
                    "PendingModifiedValues": {}, "DBParameterGroups": [{"DBParameterGroupName": "prod"}],
                    "ReadReplicaDBInstanceIdentifiers": [], "AvailabilityZone": "us-west-2a",
                }]
            }
            if not request:
                response["Marker"] = "next"
            return response

        def list_tags_for_resource(self, **_request):
            return {"TagList": [{"Key": "Name", "Value": "Production DB"}]}

    class PiClient:
        def __init__(self):
            self.calls = []

        def get_resource_metrics(self, **request):
            self.calls.append(request)
            return {"MetricList": [{"Key": {"Metric": "db.load.avg"}, "DataPoints": []}], **({"NextToken": "next"} if len(self.calls) == 1 else {})}

    rds_client = RdsClient()
    pi_client = PiClient()

    class Session:
        def client(self, name, **_kwargs):
            return rds_client if name == "rds" else pi_client

    adapter = AWSAdapter.__new__(AWSAdapter)
    adapter._use_simulator = False
    adapter.session = Session()
    instances = adapter._get_rds_instances(region="us-west-2")
    assert [item["resource_id"] for item in instances] == ["db-a", "db-b"]
    assert rds_client.calls == [{}, {"Marker": "next"}]
    assert instances[0]["metadata"]["DbiResourceId"] == "dbi-a"
    assert instances[0]["metadata"]["DBParameterGroups"] == [{"DBParameterGroupName": "prod"}]

    start = datetime(2026, 7, 15, tzinfo=timezone.utc)
    adapter.get_rds_performance_insights_metrics("dbi-a", start, start + timedelta(days=1), region="us-west-2")
    assert len(pi_client.calls) == 2
    assert all(call["Identifier"] == "dbi-a" for call in pi_client.calls)
    assert all(call["MaxResults"] == 25 for call in pi_client.calls)
    assert all(call["MetricQueries"] == [
        {"Metric": "db.load.avg"},
        {"Metric": "db.load.avg", "GroupBy": {"Group": "db.wait_event_type", "Limit": 25}},
    ] for call in pi_client.calls)
    assert "NextToken" not in pi_client.calls[0] and pi_client.calls[1]["NextToken"] == "next"


def test_rds_context_collects_paginated_connection_limit_and_proves_explicit_targets():
    class RdsClient:
        def __init__(self):
            self.parameter_calls = []

        def describe_valid_db_instance_modifications(self, **_request):
            return {"ValidDBInstanceModificationsMessage": {"ValidStorageOptions": []}}

        def describe_pending_maintenance_actions(self, **_request):
            return {"PendingMaintenanceActions": []}

        def describe_events(self, **_request):
            return {"Events": []}

        def describe_db_parameters(self, **request):
            self.parameter_calls.append(request)
            if "Marker" not in request:
                return {
                    "Parameters": [{
                        "ParameterName": "max_connections",
                        "ParameterValue": "1000",
                        "Source": "user",
                    }],
                    "Marker": "next",
                }
            return {"Parameters": []}

    client = RdsClient()

    class Session:
        def client(self, name, **_kwargs):
            assert name == "rds"
            return client

    adapter = AWSAdapter.__new__(AWSAdapter)
    adapter._use_simulator = False
    adapter.session = Session()
    adapter._resolve_region = lambda region: region or "us-east-1"
    context = adapter.get_rds_rightsizing_context(
        {
            "DBInstanceIdentifier": "db-prod",
            "DBInstanceArn": "arn:aws:rds:us-east-1:123:db:db-prod",
            "DBInstanceClass": "db.m5.xlarge",
            "Engine": "postgres",
            "EngineVersion": "16.3",
            "LicenseModel": "postgresql-license",
            "DBParameterGroups": [{
                "DBParameterGroupName": "prod",
                "ParameterApplyStatus": "in-sync",
            }],
        },
        region="us-east-1",
        orderable_options=[
            {"DBInstanceClass": "db.m5.xlarge"},
            {"DBInstanceClass": "db.m6i.large"},
        ],
        orderable_status="SUCCESS",
    )
    assert client.parameter_calls == [
        {"DBParameterGroupName": "prod"},
        {"DBParameterGroupName": "prod", "Marker": "next"},
    ]
    assert context["connection_limit"]["resolved_value"] == 1000
    assert context["target_connection_limits"] == {
        "db.m5.xlarge": 1000,
        "db.m6i.large": 1000,
    }


def test_scan_pricing_cache_is_dimension_keyed_not_target_class_keyed():
    class Adapter:
        def __init__(self):
            self.calls = []

        def get_rds_rightsizing_prices(self, db_instance, target_classes, *, region=None):
            self.calls.append((db_instance, target_classes, region))
            return {
                "status": "SUCCESS",
                "class_prices": {
                    "db.m5.xlarge": {"monthly": "200"},
                    "db.m6i.large": {"monthly": "100"},
                },
                "storage_prices": {"gp3": {"storage_gib_month": "0.10"}},
            }

    delegate = Adapter()
    cached = CachedAWSAdapter(delegate)
    database = {
        "Engine": "postgres",
        "EngineVersion": "16.3",
        "LicenseModel": "postgresql-license",
        "MultiAZ": False,
    }
    first = cached.get_rds_rightsizing_prices(database, ["db.m5.xlarge"], region="us-east-1")
    second = cached.get_rds_rightsizing_prices(database, ["db.m6i.large"], region="us-east-1")
    assert len(delegate.calls) == 1
    assert delegate.calls[0][1] == []
    assert set(first["class_prices"]) == {"db.m5.xlarge"}
    assert set(second["class_prices"]) == {"db.m6i.large"}


def test_price_list_fallback_paginates_and_persists_decimal_dimensions():
    class PricingClient:
        def __init__(self):
            self.calls = []

        def get_products(self, **request):
            self.calls.append(request)
            is_class = any(item.get("Value") == "Database Instance" for item in request["Filters"])
            if is_class:
                product = {
                    "publicationDate": "2026-07-01T00:00:00Z",
                    "product": {"sku": "class-sku", "attributes": {"instanceType": "db.m6i.large"}},
                    "terms": {"OnDemand": {"term": {"priceDimensions": {"rate": {
                        "unit": "Hrs", "pricePerUnit": {"USD": "0.123456789"},
                    }}}}},
                }
                return {"PriceList": [json.dumps(product)], **({"NextToken": "more"} if "NextToken" not in request else {})}
            product = {
                "publicationDate": "2026-07-01T00:00:00Z",
                "product": {"sku": "storage-sku", "attributes": {"volumeType": "General Purpose-GP3"}},
                "terms": {"OnDemand": {"term": {"priceDimensions": {"rate": {
                    "unit": "GB-Mo", "description": "RDS General Purpose gp3 storage",
                    "pricePerUnit": {"USD": "0.115000"}, "rateCode": "storage-rate",
                }}}}},
            }
            return {"PriceList": [json.dumps(product)]}

    pricing = PricingClient()

    class Session:
        def client(self, name, **_kwargs):
            assert name == "pricing"
            return pricing

    adapter = AWSAdapter.__new__(AWSAdapter)
    adapter._use_simulator = False
    adapter.session = Session()
    result = adapter.get_rds_rightsizing_prices(
        {"Engine": "sqlserver-se", "LicenseModel": "license-included", "MultiAZ": False},
        ["db.m6i.large"],
        region="us-east-1",
    )
    assert result["status"] == "SUCCESS"
    assert result["class_prices"]["db.m6i.large"]["hourly"] == "0.123456789"
    assert result["class_prices"]["db.m6i.large"]["monthly"] == "90.123455970"
    assert result["storage_prices"]["gp3"]["storage_gib_month"] == "0.115000"
    assert result["storage_prices"]["gp3"]["included_iops"] == 3000
    assert result["rds_engine"] == "sqlserver-se"
    class_filters = pricing.calls[0]["Filters"]
    assert {"Type": "TERM_MATCH", "Field": "databaseEngine", "Value": "SQL Server"} in class_filters
    assert {"Type": "TERM_MATCH", "Field": "databaseEdition", "Value": "Standard"} in class_filters
    assert {"Type": "TERM_MATCH", "Field": "licenseModel", "Value": "License included"} in class_filters
    assert len(pricing.calls) == 3
