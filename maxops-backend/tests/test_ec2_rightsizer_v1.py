from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.adapters.aws.adapter import _detect_ec2_management_context
from app.models.inventory import EbsInventory, Ec2Inventory
from app.services.ec2_instance_catalog import (
    EC2CatalogEntry,
    EC2InstanceCatalog,
    instance_family,
)
from app.services.ec2_rightsizer import (
    EC2CandidatePolicy,
    EC2ComputePolicy,
    EC2Rightsizer,
    EC2ScopePolicy,
    _binding_dimension,
    _family_max_vcpus,
    _limit_candidates_preserving_balanced,
    _normalized_metric,
    _projected_candidate_utilization,
    _recommendation_tiers,
    _signal,
    _telemetry_summary,
    assumed_network_baseline_mbps,
)
from app.services.scan_service import (
    _enrich_ec2_instance_store_capability,
    _enrich_ec2_rightsizing_metrics,
)
from staging_pricing.enrich_ec2_instance_specs import enrich_database
from rightsizers.ec2.ec2_rightsizer.constraints import EBSConstraintInput
from rightsizers.ec2.ec2_rightsizer.constraints.ebs_hard_constraints import (
    evaluate_ebs_constraints,
)
from rightsizers.ec2.ec2_rightsizer.evaluation import evaluate_ebs, evaluate_network
from rightsizers.ec2.ec2_rightsizer.models import (
    CapacityKind,
    HardConstraintStatus,
    PerformanceWarningPolicy,
    RecommendationClassification,
    ResourceEvaluation,
    RiskLevel,
)
from rightsizers.ec2.ec2_rightsizer.normalization.ebs import (
    bytes_to_mibps,
    operations_to_iops,
)
from rightsizers.ec2.ec2_rightsizer.normalization.network import (
    bytes_to_mbps,
    packets_to_pps,
)
from rightsizers.ec2.ec2_rightsizer.selection import classify_recommendation
from rightsizers.ec2.ec2_rightsizer.warnings import EBSWarningInput, NetworkWarningInput
from rightsizers.ec2.ec2_rightsizer.constraints import NetworkConstraintInput


@pytest.mark.parametrize("byte_sum,period", [(300_000_000, 300), (60_000_000, 60)])
def test_network_normalization_is_period_exact(byte_sum, period):
    assert bytes_to_mbps(byte_sum, period) == 8.0


def test_network_directions_and_missing_values_remain_separate():
    assert bytes_to_mbps(300_000_000, 300) == 8.0
    assert bytes_to_mbps(600_000_000, 300) == 16.0
    assert bytes_to_mbps(None, 300) is None
    assert packets_to_pps(3_000, 300) == 10.0


@pytest.mark.parametrize("period", [60, 300])
def test_ebs_normalization_is_period_exact(period):
    assert operations_to_iops(100 * period, period) == 100.0
    assert bytes_to_mibps(8 * 1_048_576 * period, period) == 8.0
    assert operations_to_iops(None, period) is None


def test_hard_ebs_attachment_and_known_max_failures_reject():
    status, reasons, _ = evaluate_ebs_constraints(
        EBSConstraintInput(
            attached_volume_count=5,
            target_attachment_limit=4,
            observed_combined_iops_p99=10_001,
            target_max_iops=10_000,
        )
    )
    assert status == HardConstraintStatus.FAIL
    assert "TARGET_EBS_ATTACHMENT_LIMIT_TOO_LOW" in reasons
    assert "EBS_MAX_IOPS_EXCEEDED" in reasons


def test_high_usage_below_max_warns_but_does_not_hard_fail():
    policy = PerformanceWarningPolicy()
    evaluation = evaluate_ebs(
        EBSConstraintInput(observed_combined_iops_p99=8_000, target_max_iops=10_000),
        EBSWarningInput(8_000, 10, 10_000, 100, CapacityKind.BASELINE),
        policy,
    )
    assert evaluation.hard_constraint_status == HardConstraintStatus.PASS
    assert evaluation.risk_level == RiskLevel.HIGH
    assert "HIGH_EBS_USAGE_REVIEW_REQUIRED" in evaluation.warnings


def test_unknown_and_burst_network_baselines_are_conditional_and_visible():
    network = evaluate_network(
        NetworkConstraintInput(),
        NetworkWarningInput(
            10, 20, None, CapacityKind.BURST_OR_UP_TO, allowance_metrics_collected=True
        ),
        PerformanceWarningPolicy(),
    )
    storage = ResourceEvaluation(HardConstraintStatus.PASS, RiskLevel.LOW)
    assert "NETWORK_BURST_CAPACITY_REQUIRES_REVIEW" in network.warnings
    assert (
        classify_recommendation(
            cpu_passes=True,
            memory_passes=True,
            network=network,
            storage=storage,
        )
        == RecommendationClassification.CONDITIONAL
    )


def test_candidate_without_blocking_warnings_is_actionable():
    evaluation = ResourceEvaluation(HardConstraintStatus.PASS, RiskLevel.LOW)
    assert (
        classify_recommendation(
            cpu_passes=True,
            memory_passes=True,
            network=evaluation,
            storage=evaluation,
        )
        == RecommendationClassification.ACTIONABLE
    )


def test_warning_threshold_change_does_not_change_normalization():
    normalized = bytes_to_mbps(300_000_000, 300)
    low_threshold = evaluate_network(
        NetworkConstraintInput(),
        NetworkWarningInput(
            8, 1, 10, CapacityKind.BASELINE, allowance_metrics_collected=True
        ),
        PerformanceWarningPolicy(network_high_ratio=0.70),
    )
    high_threshold = evaluate_network(
        NetworkConstraintInput(),
        NetworkWarningInput(
            8, 1, 10, CapacityKind.BASELINE, allowance_metrics_collected=True
        ),
        PerformanceWarningPolicy(network_high_ratio=0.90),
    )
    assert normalized == 8.0
    assert low_threshold.risk_level == RiskLevel.HIGH
    assert high_threshold.risk_level == RiskLevel.MEDIUM


def test_network_burst_zone_and_reliable_peak_equality_are_distinct():
    policy = PerformanceWarningPolicy()
    burst = evaluate_network(
        NetworkConstraintInput(
            observed_in_p99_mbps=900,
            observed_out_p99_mbps=10,
            target_reliable_max_mbps=1_000,
        ),
        NetworkWarningInput(
            900,
            10,
            500,
            CapacityKind.BASELINE,
            allowance_metrics_collected=True,
            target_peak_mbps=1_000,
        ),
        policy,
    )
    assert burst.hard_constraint_status == HardConstraintStatus.PASS
    assert burst.risk_level == RiskLevel.HIGH
    assert "NETWORK_SUSTAINED_ABOVE_BASELINE" in burst.warnings

    at_peak = evaluate_network(
        NetworkConstraintInput(
            observed_in_p99_mbps=1_000,
            observed_out_p99_mbps=10,
            target_reliable_max_mbps=1_000,
        ),
        NetworkWarningInput(
            1_000,
            10,
            500,
            CapacityKind.BASELINE,
            allowance_metrics_collected=True,
            target_peak_mbps=1_000,
        ),
        policy,
    )
    assert at_peak.hard_constraint_status == HardConstraintStatus.FAIL
    assert at_peak.risk_level == RiskLevel.HIGH
    assert "NETWORK_RELIABLE_MAX_EXCEEDED" in at_peak.warnings
    assert "NETWORK_SUSTAINED_ABOVE_BASELINE" not in at_peak.warnings


def test_non_default_weighting_suppresses_numeric_band_warning():
    evaluation = evaluate_network(
        NetworkConstraintInput(),
        NetworkWarningInput(
            900,
            10,
            500,
            CapacityKind.BASELINE,
            allowance_metrics_collected=True,
            target_peak_mbps=1_000,
            bandwidth_weighting_default=False,
        ),
        PerformanceWarningPolicy(),
    )
    assert evaluation.risk_level == RiskLevel.HIGH
    assert (
        "NON_DEFAULT_NETWORK_BANDWIDTH_WEIGHTING_REQUIRES_REVIEW" in evaluation.warnings
    )
    assert "NETWORK_SUSTAINED_ABOVE_BASELINE" not in evaluation.warnings


def _pricing_db(path):
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE street_pricing_ec2 (
                id INTEGER PRIMARY KEY, region_code TEXT, region_name TEXT,
                instance_type TEXT, platform TEXT, platform_normalized TEXT,
                hourly_usd REAL, monthly_usd REAL, currency TEXT,
                pretty_name TEXT, family TEXT, attributes_json TEXT, pricing_json TEXT
            )
            """
        )
        for index, (
            instance_type,
            monthly,
            vcpu,
            memory,
            coremark,
            ebs_iops,
        ) in enumerate(
            (
                ("m5.large", 70, 2, 8, 30_000, 18_750),
                ("m5.xlarge", 140, 4, 16, 60_000, 18_750),
            ),
            1,
        ):
            attrs = {
                "vCPU": vcpu,
                "memory": memory,
                "coremark_iterations_second": coremark,
                "arch": ["x86_64"],
                "network_performance": "Up to 10 Gigabit",
                "vpc": {"max_enis": 3},
                "ebs_optimized": True,
                "ebs_baseline_iops": 10_000,
                "ebs_iops": ebs_iops,
                "ebs_baseline_throughput": 500,
                "ebs_throughput": 1_000,
            }
            connection.execute(
                "INSERT INTO street_pricing_ec2 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    index,
                    "us-east-1",
                    "test",
                    instance_type,
                    "Linux",
                    "linux",
                    monthly / 730,
                    monthly,
                    "USD",
                    instance_type,
                    "m5",
                    json.dumps(attrs),
                    "{}",
                ),
            )


def _insert_pricing_type(
    path,
    instance_type,
    monthly,
    vcpus,
    memory_gib,
    coremark,
    architectures=("x86_64",),
):
    with sqlite3.connect(path) as connection:
        next_id = connection.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 FROM street_pricing_ec2"
        ).fetchone()[0]
        attrs = {
            "vCPU": vcpus,
            "memory": memory_gib,
            "coremark_iterations_second": coremark,
            "arch": list(architectures),
            "network_performance": "Up to 10 Gigabit",
            "vpc": {"max_enis": 3},
            "ebs_optimized": True,
            "ebs_baseline_iops": 10_000,
            "ebs_iops": 18_750,
            "ebs_baseline_throughput": 500,
            "ebs_throughput": 1_000,
        }
        connection.execute(
            "INSERT INTO street_pricing_ec2 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                next_id,
                "us-east-1",
                "test",
                instance_type,
                "Linux",
                "linux",
                monthly / 730,
                monthly,
                "USD",
                instance_type,
                instance_family(instance_type),
                json.dumps(attrs),
                "{}",
            ),
        )


def _add_structured_specs(
    path, *, target_attachment_limit=10, target_network_mbps=1_000
):
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE ec2_instance_specs (
                region_code TEXT, instance_type TEXT, spec_json TEXT,
                schema_version INTEGER, generated_at TEXT
            )
            """
        )
        for instance_type, network_mbps, attachment_limit in (
            ("m5.large", target_network_mbps, target_attachment_limit),
            ("m5.xlarge", 2_000, 10),
        ):
            spec = {
                "InstanceType": instance_type,
                "InstanceStorageSupported": False,
                "NetworkInfo": {
                    "NetworkPerformance": f"{network_mbps / 1000:g} Gigabit",
                    "MaximumNetworkInterfaces": 4,
                    "EfaSupported": False,
                    "NetworkCards": [
                        {
                            "BaselineBandwidthInGbps": network_mbps / 1000,
                            "PeakBandwidthInGbps": network_mbps / 1000,
                        }
                    ],
                },
                "EbsInfo": {
                    "MaximumEbsAttachments": attachment_limit,
                    "AttachmentLimitType": "dedicated",
                    "EbsOptimizedInfo": {
                        "BaselineIops": 10_000,
                        "MaximumIops": 18_750,
                        "BaselineThroughputInMBps": 500,
                        "MaximumThroughputInMBps": 1_000,
                    },
                },
            }
            connection.execute(
                "INSERT INTO ec2_instance_specs VALUES (?, ?, ?, 1, ?)",
                (
                    "us-east-1",
                    instance_type,
                    json.dumps(spec),
                    "2026-07-12T00:00:00Z",
                ),
            )


def test_service_uses_inventory_scan_metrics_and_pricing_sqlite(tmp_path):
    pricing_path = tmp_path / "pricing.db"
    _pricing_db(pricing_path)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    normalized = {
        "cpu_percent": {"p99": 10},
        "memory_percent": {"p99": 20},
        "network_in_mbps": {"p99": 8},
        "network_out_mbps": {"p99": 2},
        "ebs_combined_iops": {"p99": 100},
        "ebs_combined_mibps": {"p99": 10},
    }
    signals = {
        "bw_in_allowance_exceeded": False,
        "bw_out_allowance_exceeded": False,
        "pps_allowance_exceeded": False,
        "conntrack_allowance_exceeded": False,
        "instance_ebs_iops_exceeded": False,
        "instance_ebs_throughput_exceeded": False,
    }
    session.add(
        Ec2Inventory(
            inventory_id=1,
            resource_id="i-1",
            resource_type="ec2",
            resource_name="web",
            region="us-east-1",
            state="running",
            instance_type="m5.xlarge",
            metadata_json={
                "instance_store_present": False,
                "attached_eni_count": 1,
                "attached_volume_count": 1,
                "attached_volume_ids": ["vol-1"],
                "rightsizing_metrics": {
                    "14d": {"normalized": normalized, "signals": signals},
                    "30d": {"normalized": normalized, "signals": signals},
                },
            },
        )
    )
    session.add(
        EbsInventory(
            inventory_id=1,
            resource_id="vol-1",
            resource_type="ebs",
            region="us-east-1",
            state="in-use",
            attached="true",
            volume_type="gp3",
            iops=100,
            throughput=10,
        )
    )
    session.commit()
    result = EC2Rightsizer(
        session, catalog=EC2InstanceCatalog(pricing_path)
    ).get_recommendation(1)
    assert result["pricing_source"] == "street_pricing_sqlite"
    assert result["availability_validated"] is False
    assert result["capability_catalog"]["numeric_network_path_available"] is False
    assert result["capability_catalog"]["ebs_attachment_path_available"] is False
    assert result["recommendations"][0]["target_instance_type"] == "m5.large"
    assert result["recommendations"][0]["classification"] == "CONDITIONAL"
    assert result["recommendations"][0]["monthly_savings"] == 70.0
    assert (
        "NETWORK_BURST_CAPACITY_REQUIRES_REVIEW"
        in result["recommendations"][0]["reason_codes"]
    )
    assert len(result["recommendations"][0]["warning_details"]) == len(
        result["recommendations"][0]["reason_codes"]
    )
    assert (
        result["recommendations"][0]["storage_evaluation"]["evidence"][
            "required_provisioned_iops"
        ]
        == 100.0
    )


def test_structured_pricing_specs_activate_numeric_network_and_attachment_paths(
    tmp_path,
):
    pricing_path = tmp_path / "pricing.db"
    _pricing_db(pricing_path)
    _add_structured_specs(pricing_path)
    catalog = EC2InstanceCatalog(pricing_path)
    target = catalog.get("us-east-1", "m5.large")
    assert target.network_baseline_mbps == 1_000
    assert target.network_reliable_max_mbps == 1_000
    assert target.ebs_attachment_limit == 10
    assert target.capability_source == "describe_instance_types"

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    normalized = {
        "cpu_percent": {"p99": 10},
        "memory_percent": {"p99": 20},
        "network_in_mbps": {"p99": 100},
        "network_out_mbps": {"p99": 50},
        "ebs_combined_iops": {"p99": 100},
        "ebs_combined_mibps": {"p99": 10},
    }
    signals = {
        "bw_in_allowance_exceeded": False,
        "bw_out_allowance_exceeded": False,
        "pps_allowance_exceeded": False,
        "conntrack_allowance_exceeded": False,
        "instance_ebs_iops_exceeded": False,
        "instance_ebs_throughput_exceeded": False,
    }
    session.add(
        Ec2Inventory(
            inventory_id=1,
            resource_id="i-1",
            resource_type="ec2",
            region="us-east-1",
            state="running",
            instance_type="m5.xlarge",
            metadata_json={
                "instance_store_present": False,
                "attached_eni_count": 1,
                "attached_volume_count": 1,
                "memory_metric_source": {
                    "namespace": "Custom/Host",
                    "metric_name": "MemoryUtilization",
                    "dimensions": [{"Name": "InstanceId", "Value": "i-1"}],
                },
                "rightsizing_metrics": {
                    "14d": {"normalized": normalized, "signals": signals},
                    "30d": {"normalized": normalized, "signals": signals},
                },
            },
        )
    )
    session.commit()
    result = EC2Rightsizer(session, catalog=catalog).get_recommendation(1)
    candidate = result["recommendations"][0]
    assert result["capability_catalog"]["numeric_network_path_available"] is True
    assert result["capability_catalog"]["ebs_attachment_path_available"] is True
    assert candidate["classification"] == "ACTIONABLE"
    assert result["compute_policy"]["policy_version"] == "v1-balanced"
    assert result["compute_policy"]["memory_target_ratio"] == 0.70
    assert (
        result["current_capacity_evidence"]["network"]["network_in_utilization_ratio"]
        == 0.05
    )
    assert result["current_capacity_evidence"]["memory"] == {
        "observed_p99_percent": 20.0,
        "estimated_used_mib": 3276.8,
        "current_memory_mib": 16384.0,
        "source": {
            "namespace": "Custom/Host",
            "metric_name": "MemoryUtilization",
            "dimensions": [{"Name": "InstanceId", "Value": "i-1"}],
        },
        "fallback_used": False,
    }
    assert candidate["compute_evidence"]["required_memory_mib"] == pytest.approx(
        4681.142857
    )
    assert candidate["compute_evidence"]["projected_target_memory_percent"] == 40.0
    assert candidate["compute_evidence"]["memory_fallback_used"] is False
    assert candidate["risk_assessment"]["memory"] == "LOW"
    assert (
        candidate["network_evaluation"]["evidence"]["network_utilization_ratio"] == 0.1
    )
    assert candidate["network_evaluation"]["evidence"]["target_baseline_mbps"] == 1_000
    assert candidate["network_evaluation"]["evidence"]["observed_in_p99_mbps"] == 100
    assert candidate["network_evaluation"]["evidence"]["observed_out_p99_mbps"] == 50
    assert candidate["constraint_coverage"] == {
        "numeric_network_baseline": True,
        "reliable_network_maximum": True,
        "ebs_attachment_limit": True,
        "capability_source": "describe_instance_types",
    }


def test_missing_memory_retains_current_capacity_and_rejects_memory_reduction(
    tmp_path,
):
    pricing_path = tmp_path / "pricing.db"
    _pricing_db(pricing_path)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(
        Ec2Inventory(
            inventory_id=1,
            resource_id="i-no-memory",
            resource_type="ec2",
            region="us-east-1",
            state="running",
            instance_type="m5.xlarge",
            metadata_json={
                "instance_store_present": False,
                "attached_volume_count": 0,
                "rightsizing_metrics": {
                    "60d": {"normalized": {"cpu_percent": {"p99": 10}}}
                },
            },
        )
    )
    session.commit()

    result = EC2Rightsizer(
        session, catalog=EC2InstanceCatalog(pricing_path)
    ).get_recommendation(1)

    assert result["recommendations"] == []
    assert result["rejection_summary"] == {"MEMORY_REQUIREMENT_NOT_MET": 1}
    assert result["current_capacity_evidence"]["memory"]["fallback_used"] is True


def test_non_default_network_weighting_forces_conditional_review(tmp_path):
    pricing_path = tmp_path / "pricing.db"
    _pricing_db(pricing_path)
    _add_structured_specs(pricing_path)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    normalized = {
        "cpu_percent": {"p99": 10},
        "memory_percent": {"p99": 20},
        "network_in_mbps": {"p99": 100},
        "network_out_mbps": {"p99": 50},
    }
    signals = {
        "bw_in_allowance_exceeded": False,
        "bw_out_allowance_exceeded": False,
        "pps_allowance_exceeded": False,
        "conntrack_allowance_exceeded": False,
    }
    session.add(
        Ec2Inventory(
            inventory_id=1,
            resource_id="i-weighted",
            resource_type="ec2",
            region="us-east-1",
            state="running",
            instance_type="m5.xlarge",
            metadata_json={
                "instance_store_present": False,
                "attached_eni_count": 1,
                "attached_volume_count": 0,
                "network_bandwidth_weighting": "ebs-1",
                "rightsizing_metrics": {
                    "60d": {"normalized": normalized, "signals": signals}
                },
            },
        )
    )
    session.commit()

    result = EC2Rightsizer(
        session, catalog=EC2InstanceCatalog(pricing_path)
    ).get_recommendation(1)
    candidate = result["recommendations"][0]

    assert candidate["classification"] == "CONDITIONAL"
    assert candidate["network_evaluation"]["evidence"]["bandwidth_weighting"] == "ebs-1"
    assert (
        "NON_DEFAULT_NETWORK_BANDWIDTH_WEIGHTING_REQUIRES_REVIEW"
        in candidate["reason_codes"]
    )


def test_structured_attachment_limit_rejects_candidate(tmp_path):
    pricing_path = tmp_path / "pricing.db"
    _pricing_db(pricing_path)
    _add_structured_specs(pricing_path, target_attachment_limit=1)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    normalized = {
        "cpu_percent": {"p99": 10},
        "memory_percent": {"p99": 20},
        "network_in_mbps": {"p99": 1},
        "network_out_mbps": {"p99": 1},
        "ebs_combined_iops": {"p99": 1},
        "ebs_combined_mibps": {"p99": 1},
    }
    session.add(
        Ec2Inventory(
            inventory_id=1,
            resource_id="i-1",
            resource_type="ec2",
            region="us-east-1",
            state="running",
            instance_type="m5.xlarge",
            metadata_json={
                "instance_store_present": False,
                "attached_eni_count": 1,
                "attached_volume_count": 2,
                "rightsizing_metrics": {
                    "14d": {"normalized": normalized},
                    "30d": {"normalized": normalized},
                },
            },
        )
    )
    session.commit()
    result = EC2Rightsizer(
        session, catalog=EC2InstanceCatalog(pricing_path)
    ).get_recommendation(1)
    assert result["recommendations"] == []
    assert result["rejection_summary"]["TARGET_EBS_ATTACHMENT_LIMIT_TOO_LOW"] == 1


def test_scan_enrichment_persists_exact_period_aware_metrics():
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()

    class Adapter:
        request = None

        def get_ec2_rightsizing_metrics(self, *args, **kwargs):
            self.request = (args, kwargs)
            period = kwargs["period_seconds"]
            values = {
                "cpu_percent": 10,
                "memory_percent": 20,
                "network_in_bytes": 300_000_000,
                "network_out_bytes": 600_000_000,
                "network_packets_in": 3_000,
                "network_packets_out": 6_000,
                "ebs_read_operations": 30_000,
                "ebs_write_operations": 15_000,
                "ebs_read_bytes": 300 * 1_048_576,
                "ebs_write_bytes": 150 * 1_048_576,
            }
            return {
                "period_seconds": period,
                "metrics": {
                    key: {"timestamps": [timestamp], "values": [value]}
                    for key, value in values.items()
                },
            }

    resource = {"resource_id": "i-1", "metadata": {}}
    adapter = Adapter()
    _enrich_ec2_rightsizing_metrics(adapter, resource, "us-east-1", now)
    window = resource["metadata"]["rightsizing_metrics"]["14d"]
    assert adapter.request[0][1] == now - timedelta(days=60)
    assert adapter.request[1]["period_seconds"] == 300
    assert "60d" in resource["metadata"]["rightsizing_metrics"]
    assert window["period_seconds"] == 300
    assert window["normalized"]["network_in_mbps"]["p99"] == 8.0
    assert window["normalized"]["network_out_mbps"]["p99"] == 16.0
    assert window["normalized"]["ebs_combined_iops"]["p99"] == 150.0
    assert window["normalized"]["ebs_combined_mibps"]["p99"] == 1.5


def test_scan_retains_one_sided_ebs_demand_as_an_incomplete_lower_bound():
    now = datetime.now(timezone.utc)

    class Adapter:
        def get_ec2_rightsizing_metrics(self, *args, **kwargs):
            return {
                "period_seconds": 60,
                "metrics": {
                    "ebs_read_operations": {
                        "timestamps": [now.isoformat()],
                        "values": [600],
                    },
                    "ebs_write_operations": {"timestamps": [], "values": []},
                    "ebs_read_bytes": {
                        "timestamps": [now.isoformat()],
                        "values": [60 * 1_048_576],
                    },
                    "ebs_write_bytes": {"timestamps": [], "values": []},
                },
            }

    resource = {"resource_id": "i-1", "metadata": {}}
    _enrich_ec2_rightsizing_metrics(Adapter(), resource, "us-east-1", now)
    window = resource["metadata"]["rightsizing_metrics"]["14d"]
    assert window["normalized"]["ebs_combined_iops"]["p99"] == 10.0
    assert window["normalized"]["ebs_combined_mibps"]["p99"] == 1.0
    assert window["signals"]["ebs_directional_metrics_incomplete"] is True
    assert (
        window["aggregation_evidence"]["ebs_combined_iops"]["semantics"]
        == "observed_component_sum_lower_bound"
    )


def test_pricing_enricher_persists_describe_instance_types_specs(tmp_path):
    pricing_path = tmp_path / "pricing.db"
    _pricing_db(pricing_path)

    class Client:
        requests = []

        def describe_instance_types(self, **request):
            self.requests.append(request)
            instance_types = (
                ["m5.large"]
                if "NextToken" not in request
                else ["m5.xlarge", "not-priced.large"]
            )
            return {
                "InstanceTypes": [
                    {
                        "InstanceType": instance_type,
                        "NetworkInfo": {
                            "NetworkCards": [
                                {
                                    "BaselineBandwidthInGbps": 1,
                                    "PeakBandwidthInGbps": 2,
                                }
                            ]
                        },
                        "EbsInfo": {"MaximumEbsAttachments": 10},
                    }
                    for instance_type in instance_types
                ],
                **({"NextToken": "page-2"} if "NextToken" not in request else {}),
            }

    class Session:
        def client(self, service, region_name):
            assert service == "ec2"
            assert region_name == "us-east-1"
            return Client()

    assert enrich_database(pricing_path, session=Session()) == 2
    entry = EC2InstanceCatalog(pricing_path).get("us-east-1", "m5.large")
    assert entry.network_baseline_mbps == 1_000
    assert entry.network_reliable_max_mbps == 2_000
    assert entry.ebs_attachment_limit == 10
    with sqlite3.connect(pricing_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(ec2_instance_specs)")
        }
        assert columns == {
            "instance_type",
            "spec_json",
            "source_region",
            "schema_version",
            "generated_at",
        }
        assert connection.execute(
            "SELECT DISTINCT source_region FROM ec2_instance_specs"
        ).fetchall() == [("us-east-1",)]


def test_pricing_enricher_failure_preserves_previous_catalog(tmp_path):
    pricing_path = tmp_path / "pricing.db"
    _pricing_db(pricing_path)
    with sqlite3.connect(pricing_path) as connection:
        connection.execute(
            """
            CREATE TABLE ec2_instance_specs (
                instance_type TEXT PRIMARY KEY, spec_json TEXT,
                source_region TEXT, schema_version INTEGER, generated_at TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO ec2_instance_specs VALUES (?, ?, ?, 2, ?)",
            ("m5.large", "{}", "us-east-1", "2026-07-13T00:00:00Z"),
        )

    class Client:
        def describe_instance_types(self, **request):
            return {"InstanceTypes": []}

    class Session:
        def client(self, service, region_name):
            assert service == "ec2"
            assert region_name == "us-east-1"
            return Client()

    with pytest.raises(RuntimeError, match="returned no globally priced"):
        enrich_database(pricing_path, session=Session())
    with sqlite3.connect(pricing_path) as connection:
        assert connection.execute(
            "SELECT instance_type, source_region FROM ec2_instance_specs"
        ).fetchall() == [("m5.large", "us-east-1")]


def test_assumed_baseline_uses_exact_nonmetal_family_maximum(tmp_path):
    pricing_path = tmp_path / "pricing.db"
    _pricing_db(pricing_path)
    target = EC2InstanceCatalog(pricing_path).get("us-east-1", "m5.large")
    target = replace(
        target,
        network_baseline_mbps=None,
        network_reliable_max_mbps=10_000,
        network_capacity_kind="BURST_OR_UP_TO",
    )
    entries = {
        "m5.large": target,
        "m5.metal": replace(target, instance_type="m5.metal", vcpus=96),
        "m6i.32xlarge": replace(target, instance_type="m6i.32xlarge", vcpus=128),
    }
    maxima = _family_max_vcpus(entries)
    assert maxima == {"m5": 2.0, "m6i": 128}
    assert (
        assumed_network_baseline_mbps(target, maxima["m5"], PerformanceWarningPolicy())
        == 10_000
    )
    assert (
        assumed_network_baseline_mbps(
            target,
            4,
            PerformanceWarningPolicy(network_assumed_baseline_multiplier=0.5),
        )
        == 2_500
    )


def test_cross_family_candidates_and_configurable_family_gate(tmp_path):
    pricing_path = tmp_path / "pricing.db"
    _pricing_db(pricing_path)
    _insert_pricing_type(pricing_path, "c6i.large", 60, 2, 8, 40_000)
    _insert_pricing_type(pricing_path, "t3.large", 50, 2, 8, 40_000)
    _insert_pricing_type(
        pricing_path, "c7g.large", 40, 2, 8, 40_000, architectures=("arm64",)
    )
    session = _scope_session(
        {
            "instance_store_present": False,
            "attached_volume_count": 0,
            "rightsizing_metrics": {
                "60d": {
                    "period_seconds": 300,
                    "normalized": {
                        "cpu_percent": {"p99": 10, "sample_count": 10},
                        "memory_percent": {"p99": 20, "sample_count": 10},
                    },
                }
            },
        }
    )
    default_result = EC2Rightsizer(
        session, catalog=EC2InstanceCatalog(pricing_path)
    ).get_recommendation(1)
    by_type = {
        item["target_instance_type"]: item for item in default_result["recommendations"]
    }
    assert by_type["c6i.large"]["family_changed"] is True
    assert "t3.large" not in by_type
    assert default_result["rejection_summary"]["FAMILY_NOT_ELIGIBLE"] == 1
    assert default_result["rejection_summary"]["ARCHITECTURE_INCOMPATIBLE"] == 1

    ungated = EC2Rightsizer(
        session,
        catalog=EC2InstanceCatalog(pricing_path),
        candidate_policy=EC2CandidatePolicy(gated_family_classes=()),
    ).get_recommendation(1)
    assert "t3.large" in {
        item["target_instance_type"] for item in ungated["recommendations"]
    }


def test_legacy_regional_specs_are_global_with_us_east_1_preferred(tmp_path):
    pricing_path = tmp_path / "pricing.db"
    _pricing_db(pricing_path)
    with sqlite3.connect(pricing_path) as connection:
        connection.execute(
            """
            INSERT INTO street_pricing_ec2
            SELECT id + 100, 'eu-west-1', region_name, instance_type, platform,
                   platform_normalized, hourly_usd, monthly_usd, currency,
                   pretty_name, family, attributes_json, pricing_json
            FROM street_pricing_ec2
            """
        )
        connection.execute(
            """
            CREATE TABLE ec2_instance_specs (
                region_code TEXT, instance_type TEXT, spec_json TEXT,
                schema_version INTEGER, generated_at TEXT
            )
            """
        )
        for region, baseline in (("us-west-2", 9), ("us-east-1", 1)):
            connection.execute(
                "INSERT INTO ec2_instance_specs VALUES (?, ?, ?, 1, ?)",
                (
                    region,
                    "m5.large",
                    json.dumps(
                        {
                            "NetworkInfo": {
                                "NetworkCards": [
                                    {
                                        "BaselineBandwidthInGbps": baseline,
                                        "PeakBandwidthInGbps": baseline * 2,
                                    }
                                ]
                            }
                        }
                    ),
                    "2026-07-13T00:00:00Z",
                ),
            )
    entry = EC2InstanceCatalog(pricing_path).get("eu-west-1", "m5.large")
    assert entry.network_baseline_mbps == 1_000
    assert entry.network_reliable_max_mbps == 2_000


def _scope_session(metadata):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(
        Ec2Inventory(
            inventory_id=1,
            resource_id="i-scope",
            resource_type="ec2",
            account_id="123",
            region="us-east-1",
            state="running",
            instance_type="m5.xlarge",
            metadata_json=metadata,
        )
    )
    session.commit()
    return session


@pytest.mark.parametrize(
    "metadata,reason",
    [
        ({"management_context": "asg"}, "MANAGED_BY_ASG"),
        ({"management_context": "ecs"}, "MANAGED_BY_ECS"),
        ({"management_context": "kubernetes"}, "MANAGED_BY_KUBERNETES"),
        ({"tenancy": "dedicated"}, "UNSUPPORTED_TENANCY"),
        ({"tenancy": "host"}, "UNSUPPORTED_TENANCY"),
        ({"lifecycle": "spot"}, "UNSUPPORTED_LIFECYCLE"),
    ],
)
def test_scope_metadata_gates_defer_before_candidate_generation(
    tmp_path, metadata, reason
):
    result = EC2Rightsizer(
        _scope_session(metadata), catalog=EC2InstanceCatalog(tmp_path / "missing.db")
    ).get_recommendation(1)
    assert result["classification"] == "DEFERRED"
    assert result["deferred_reason_codes"] == [reason]
    assert result["telemetry_summary"]["cpu_percent"]["present"] is False


@pytest.mark.parametrize("presence", [True, None])
def test_instance_store_presence_and_unknown_defer_by_default(tmp_path, presence):
    pricing_path = tmp_path / "pricing.db"
    _pricing_db(pricing_path)
    metadata = {} if presence is None else {"instance_store_present": presence}
    result = EC2Rightsizer(
        _scope_session(metadata), catalog=EC2InstanceCatalog(pricing_path)
    ).get_recommendation(1)
    assert result["classification"] == "DEFERRED"
    assert result["deferred_reason_codes"] == ["INSTANCE_STORE_USAGE_UNKNOWN"]
    assert result["scope_policy"] == {"allow_unknown_instance_store_usage": False}


def test_instance_store_override_forces_conditional_candidate(tmp_path):
    pricing_path = tmp_path / "pricing.db"
    _pricing_db(pricing_path)
    session = _scope_session(
        {
            "instance_store_present": True,
            "attached_volume_count": 0,
            "rightsizing_metrics": {
                "60d": {
                    "period_seconds": 300,
                    "normalized": {
                        "cpu_percent": {"p99": 10, "sample_count": 10},
                        "memory_percent": {"p99": 20, "sample_count": 10},
                    },
                }
            },
        }
    )
    result = EC2Rightsizer(
        session,
        catalog=EC2InstanceCatalog(pricing_path),
        scope_policy=EC2ScopePolicy(allow_unknown_instance_store_usage=True),
    ).get_recommendation(1)
    candidate = result["recommendations"][0]
    assert candidate["classification"] == "CONDITIONAL"
    assert candidate["risk_assessment"]["compatibility"] == "HIGH"
    assert "INSTANCE_STORE_USAGE_UNKNOWN" in candidate["reason_codes"]


def test_sixty_day_metrics_and_signals_are_consumed_conservatively():
    metadata = {
        "rightsizing_metrics": {
            "14d": {
                "normalized": {"cpu_percent": {"p99": 10}},
                "signals": {"throttled": False},
            },
            "30d": {"normalized": {"cpu_percent": {"p99": 20}}},
            "60d": {
                "normalized": {"cpu_percent": {"p99": 80}},
                "signals": {"throttled": True},
            },
        }
    }
    assert _normalized_metric(metadata, "cpu_percent") == 80
    assert _signal(metadata, "throttled") is True


def test_telemetry_summary_uses_largest_present_window_without_gating():
    metadata = {
        "rightsizing_metrics": {
            "14d": {
                "period_seconds": 300,
                "normalized": {
                    "cpu_percent": {"sample_count": 2016},
                    "network_in_mbps": {"sample_count": 100},
                },
            },
            "60d": {
                "period_seconds": 300,
                "normalized": {"cpu_percent": {"sample_count": 2880}},
            },
        }
    }
    summary = _telemetry_summary(metadata)
    assert summary["cpu_percent"] == {
        "present": True,
        "observed_days": 10.0,
        "thin_data": False,
    }
    assert summary["network_in_mbps"]["thin_data"] is True
    assert summary["network_out_mbps"] == {
        "present": False,
        "observed_days": 0.0,
        "thin_data": True,
    }
    assert summary["memory_percent"] == {
        "present": False,
        "observed_days": 0.0,
        "thin_data": True,
        "source": None,
        "status": "unavailable",
    }


@pytest.mark.parametrize(
    "tags,context",
    [
        ({"aws:autoscaling:groupName": "workers"}, "asg"),
        ({"AmazonECSManaged": "true"}, "ecs"),
        ({"kubernetes.io/cluster/prod": "owned"}, "kubernetes"),
    ],
)
def test_management_context_is_detected_from_describe_instance_tags(tags, context):
    assert _detect_ec2_management_context(tags) == context


@pytest.mark.parametrize("count,expected", [(2, True), (0, False), (None, None)])
def test_scan_persists_structured_instance_store_capability(count, expected):
    class Catalog:
        def list_region(self, region, platform):
            return {
                "m5.large": SimpleNamespace(
                    capability_source="describe_instance_types",
                    instance_store_device_count=count,
                )
            }

    resource = {
        "resource_id": "i-1",
        "instance_type": "m5.large",
        "region": "us-east-1",
        "metadata": {},
    }
    _enrich_ec2_instance_store_capability(resource, Catalog(), {})
    assert resource["metadata"]["instance_store_present"] is expected


def _tier_entry(
    instance_type,
    monthly_usd,
    vcpus,
    memory_mib,
    coremark,
    network_baseline_mbps=100.0,
):
    return EC2CatalogEntry(
        instance_type=instance_type,
        region="us-east-1",
        platform="linux",
        monthly_usd=monthly_usd,
        vcpus=vcpus,
        memory_mib=memory_mib,
        coremark=coremark,
        architectures=("x86_64",),
        network_performance="Up to 1 Gigabit",
        network_class="LOW",
        network_baseline_mbps=network_baseline_mbps,
        network_reliable_max_mbps=1_000.0,
        network_capacity_kind="BASELINE",
        eni_limit=10,
        efa_supported=False,
        ebs_supported=True,
        ebs_attachment_limit=20,
        ebs_attachment_limit_type="dedicated",
        instance_store_device_count=0,
        ebs_baseline_iops=10_000.0,
        ebs_max_iops=20_000.0,
        ebs_baseline_throughput_mibps=500.0,
        ebs_max_throughput_mibps=1_000.0,
        capability_source="describe_instance_types",
        attributes={},
    )


def _tier_recommendation(
    entries,
    *,
    include_cpu=True,
    include_memory=True,
    include_network=True,
    candidate_limit=10,
    min_monthly_savings=0.0,
    candidate_policy=None,
):
    class Catalog:
        def list_region(self, region, platform):
            assert region == "us-east-1"
            assert platform == "linux"
            return entries

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    normalized = {}
    if include_network:
        normalized.update(
            {
                "network_in_mbps": {"p99": 15},
                "network_out_mbps": {"p99": 1},
            }
        )
    if include_cpu:
        normalized["cpu_percent"] = {"p99": 26}
    if include_memory:
        normalized["memory_percent"] = {"p99": 26}
    signals = {
        "bw_in_allowance_exceeded": False,
        "bw_out_allowance_exceeded": False,
        "pps_allowance_exceeded": False,
        "conntrack_allowance_exceeded": False,
    }
    session.add(
        Ec2Inventory(
            inventory_id=991,
            resource_id="i-tiered",
            resource_type="ec2",
            resource_name="tiered",
            region="us-east-1",
            state="running",
            instance_type="c5.4xlarge",
            metadata_json={
                "instance_store_present": False,
                "attached_eni_count": 1,
                "attached_volume_count": 0,
                "rightsizing_metrics": {
                    "60d": {"normalized": normalized, "signals": signals}
                },
            },
        )
    )
    session.commit()
    return EC2Rightsizer(
        session, catalog=Catalog(), candidate_policy=candidate_policy
    ).get_recommendation(
        991,
        candidate_limit=candidate_limit,
        min_monthly_savings=min_monthly_savings,
    )


def _tier_candidate(instance_type, projected_util, monthly_savings):
    return {
        "target_instance_type": instance_type,
        "projected_cpu_util": projected_util,
        "projected_memory_util": projected_util,
        "projected_util": projected_util,
        "performance_ratio": 1.0,
        "performance_change_pct": 0.0,
        "binding_dimension": "cpu",
        "monthly_savings": monthly_savings,
        "yearly_savings": monthly_savings * 12,
        "classification": "ACTIONABLE",
        "risk_assessment": {},
    }


def test_tier_policy_validation_and_inclusive_ratio_boundary():
    with pytest.raises(ValueError, match="tier ratios"):
        EC2ComputePolicy(tier_ratios=(("conservative", 0), ("balanced", 0.70)))
    with pytest.raises(ValueError, match="balanced tier ratio"):
        EC2ComputePolicy(tier_ratios=(("conservative", 0.55), ("balanced", 0.75)))

    boundary = _tier_candidate("m6i.large", 0.55, 30)
    above = _tier_candidate("c6i.large", 0.550001, 40)
    tiers = _recommendation_tiers([above, boundary], EC2ComputePolicy())
    assert tiers["conservative"]["target_instance_type"] == "m6i.large"
    assert tiers["balanced"]["target_instance_type"] == "c6i.large"
    assert boundary["satisfied_tiers"] == ["conservative"]
    assert above["satisfied_tiers"] == ["balanced", "aggressive"]


def test_tiered_recommendations_select_three_distinct_targets_and_risks():
    entries = {
        "c5.4xlarge": _tier_entry("c5.4xlarge", 200, 8, 10_000, 100),
        "c6i.xlarge": _tier_entry("c6i.xlarge", 110, 4, 5_200, 52),
        "m6i.xlarge": _tier_entry("m6i.xlarge", 80, 3, 4_000, 40),
        "r6i.large": _tier_entry(
            "r6i.large", 50, 2, 3_250, 32.5, network_baseline_mbps=10
        ),
    }
    result = _tier_recommendation(entries)
    tiers = result["tiers"]

    assert tiers["default"] == "balanced"
    assert tiers["conservative"]["target_instance_type"] == "c6i.xlarge"
    assert tiers["balanced"]["target_instance_type"] == "m6i.xlarge"
    assert tiers["aggressive"]["target_instance_type"] == "r6i.large"
    assert tiers["conservative"]["projected_util"] == pytest.approx(0.5)
    assert tiers["balanced"]["projected_util"] == pytest.approx(0.65)
    assert tiers["aggressive"]["projected_cpu_util"] == pytest.approx(0.8)
    assert tiers["aggressive"]["performance_ratio"] == pytest.approx(0.325)
    assert tiers["aggressive"]["performance_change_pct"] == pytest.approx(-67.5)
    assert tiers["aggressive"]["binding_dimension"] == "network"
    assert tiers["conservative"]["classification"] == "ACTIONABLE"
    assert tiers["aggressive"]["classification"] == "CONDITIONAL"
    assert result["recommendations"][0]["target_instance_type"] == "r6i.large"
    assert result["recommendations"][1]["target_instance_type"] == "m6i.xlarge"
    assert result["compute_policy"]["tier_ratios"] == (
        ("conservative", 0.55),
        ("balanced", 0.70),
        ("aggressive", 0.85),
    )


def test_positive_performance_change_is_carried_by_candidate_and_tier_option():
    current = _tier_entry("c5.4xlarge", 200, 4, 10_000, 100)
    faster = _tier_entry("c6i.xlarge", 150, 4, 10_000, 120)
    result = _tier_recommendation(
        {current.instance_type: current, faster.instance_type: faster}
    )

    candidate = result["recommendations"][0]
    assert candidate["performance_ratio"] == pytest.approx(1.2)
    assert candidate["performance_change_pct"] == pytest.approx(20.0)
    assert result["tiers"]["balanced"]["performance_change_pct"] == pytest.approx(20.0)


def test_tiers_collapse_shared_targets_and_allow_missing_conservative():
    current = _tier_entry("c5.4xlarge", 200, 8, 10_000, 100)
    conservative = _tier_entry("c6i.xlarge", 110, 4, 5_200, 52)
    collapsed = _tier_recommendation(
        {current.instance_type: current, conservative.instance_type: conservative}
    )
    targets = {
        collapsed["tiers"][name]["target_instance_type"]
        for name in ("conservative", "balanced", "aggressive")
    }
    assert targets == {"c6i.xlarge"}
    assert collapsed["recommendations"][0]["satisfied_tiers"] == [
        "conservative",
        "balanced",
        "aggressive",
    ]

    balanced = _tier_entry("m6i.xlarge", 80, 3, 4_000, 40)
    without_conservative = _tier_recommendation(
        {current.instance_type: current, balanced.instance_type: balanced}
    )
    assert without_conservative["tiers"]["conservative"] is None
    assert without_conservative["tiers"]["balanced"]["target_instance_type"] == (
        "m6i.xlarge"
    )
    assert without_conservative["tiers"]["aggressive"]["target_instance_type"] == (
        "m6i.xlarge"
    )


def test_balanced_matches_pre_tiering_with_memory_present_and_absent():
    current = _tier_entry("c5.4xlarge", 200, 8, 10_000, 100)
    balanced = _tier_entry("m6i.xlarge", 80, 4, 10_000, 40)
    aggressive = _tier_entry("r6i.large", 50, 2, 5_000, 32.5)
    entries = {
        current.instance_type: current,
        balanced.instance_type: balanced,
        aggressive.instance_type: aggressive,
    }

    def pre_tiering_target(memory_metric_available):
        required_coremark = current.coremark * 0.26 / 0.70
        required_memory = (
            current.memory_mib * 0.26 / 0.70
            if memory_metric_available
            else current.memory_mib
        )
        eligible = [
            target
            for target in (balanced, aggressive)
            if target.coremark >= required_coremark
            and target.memory_mib >= required_memory
        ]
        return min(eligible, key=lambda target: target.monthly_usd).instance_type

    with_memory = _tier_recommendation(entries, include_memory=True)
    without_memory = _tier_recommendation(entries, include_memory=False)
    assert with_memory["tiers"]["balanced"]["target_instance_type"] == (
        pre_tiering_target(True)
    )
    assert without_memory["tiers"]["balanced"]["target_instance_type"] == (
        pre_tiering_target(False)
    )

    candidate = without_memory["recommendations"][0]
    assert candidate["target_instance_type"] == "m6i.xlarge"
    assert candidate["projected_memory_util"] is None
    assert candidate["projected_util"] == pytest.approx(0.65)
    assert candidate["binding_dimension"] == "cpu"
    assert (
        "MEMORY_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED"
        in candidate["reason_codes"]
    )
    assert without_memory["rejection_summary"]["MEMORY_REQUIREMENT_NOT_MET"] == 1


def test_missing_cpu_retains_current_capacity_without_affecting_tier_projection():
    current = _tier_entry("c5.4xlarge", 200, 8, 10_000, 100)
    retained = _tier_entry("c6i.4xlarge", 120, 8, 10_000, 100)
    reduced = _tier_entry("m6i.2xlarge", 60, 8, 10_000, 90)
    result = _tier_recommendation(
        {
            current.instance_type: current,
            retained.instance_type: retained,
            reduced.instance_type: reduced,
        },
        include_cpu=False,
    )

    candidate = result["recommendations"][0]
    assert candidate["target_instance_type"] == "c6i.4xlarge"
    assert candidate["projected_cpu_util"] is None
    assert candidate["projected_memory_util"] == pytest.approx(0.26)
    assert candidate["projected_util"] == pytest.approx(0.26)
    assert candidate["binding_dimension"] == "memory"
    assert result["rejection_summary"]["CPU_REQUIREMENT_NOT_MET"] == 1


def test_missing_cpu_and_memory_keeps_floor_candidate_without_tiers():
    current = _tier_entry("c5.4xlarge", 200, 8, 10_000, 100)
    retained = _tier_entry("c6i.4xlarge", 120, 8, 10_000, 100)
    result = _tier_recommendation(
        {current.instance_type: current, retained.instance_type: retained},
        include_cpu=False,
        include_memory=False,
        include_network=False,
    )

    assert len(result["recommendations"]) == 1
    candidate = result["recommendations"][0]
    assert candidate["target_instance_type"] == "c6i.4xlarge"
    assert candidate["projected_cpu_util"] is None
    assert candidate["projected_memory_util"] is None
    assert candidate["projected_util"] is None
    assert candidate["binding_dimension"] is None
    assert result["tiers"] == {
        "conservative": None,
        "balanced": None,
        "aggressive": None,
        "default": None,
    }


def test_projected_cpu_falls_back_to_vcpu_and_binding_uses_highest_ratio():
    current = _tier_entry("c5.4xlarge", 200, 8, 10_000, None)
    target = _tier_entry("c6i.xlarge", 100, 4, 10_000, None)
    metadata = {
        "rightsizing_metrics": {
            "60d": {
                "normalized": {
                    "cpu_percent": {"p99": 26},
                    "memory_percent": {"p99": 26},
                }
            }
        }
    }
    cpu, memory, projected, performance = _projected_candidate_utilization(
        current, target, metadata
    )
    assert cpu == pytest.approx(0.52)
    assert memory == pytest.approx(0.26)
    assert projected == pytest.approx(0.52)
    assert performance is None

    result = _tier_recommendation(
        {current.instance_type: current, target.instance_type: target}
    )
    assert result["tiers"]["conservative"]["target_instance_type"] == ("c6i.xlarge")
    assert result["tiers"]["conservative"]["performance_ratio"] is None
    assert result["tiers"]["conservative"]["performance_change_pct"] is None

    network = ResourceEvaluation(
        HardConstraintStatus.PASS,
        RiskLevel.LOW,
        evidence={"network_in_utilization_ratio": 0.7},
    )
    storage = ResourceEvaluation(
        HardConstraintStatus.PASS,
        RiskLevel.LOW,
        evidence={"ebs_throughput_utilization_ratio": 0.8},
    )
    assert _binding_dimension(cpu, memory, network, storage) == "storage"


def test_candidate_limit_reserves_balanced_and_nested_aggressive_target():
    current = _tier_entry("c5.4xlarge", 200, 8, 10_000, 100)
    conservative = _tier_entry("c6i.xlarge", 110, 4, 5_200, 52)
    balanced = _tier_entry("m6i.xlarge", 80, 3, 4_000, 40)
    aggressive = _tier_entry("r6i.large", 50, 2, 3_250, 32.5)

    result = _tier_recommendation(
        {
            current.instance_type: current,
            conservative.instance_type: conservative,
            balanced.instance_type: balanced,
            aggressive.instance_type: aggressive,
        },
        candidate_limit=1,
    )

    assert [item["target_instance_type"] for item in result["recommendations"]] == [
        "m6i.xlarge"
    ]
    assert result["tiers"]["conservative"] is None
    assert result["tiers"]["balanced"]["target_instance_type"] == "m6i.xlarge"
    assert result["tiers"]["aggressive"]["target_instance_type"] == "m6i.xlarge"


def test_nonpositive_candidate_limit_matches_pre_extraction_behavior():
    policy = EC2ComputePolicy()
    aggressive = {
        "target_instance_type": "c7i.large",
        "monthly_savings": 30.0,
        "projected_util": 0.80,
    }
    balanced = {
        "target_instance_type": "m7i.large",
        "monthly_savings": 20.0,
        "projected_util": 0.70,
    }
    candidates_with_balanced = [aggressive, balanced]

    assert _limit_candidates_preserving_balanced(
        candidates_with_balanced, 0, policy
    ) == [balanced]
    assert _limit_candidates_preserving_balanced(
        candidates_with_balanced, -1, policy
    ) == [balanced]

    nonqualifying = {
        "target_instance_type": "r7i.large",
        "monthly_savings": 20.0,
        "projected_util": 0.90,
    }
    candidates_without_balanced = [aggressive, nonqualifying]

    assert _limit_candidates_preserving_balanced(
        candidates_without_balanced, 0, policy
    ) == []
    assert _limit_candidates_preserving_balanced(
        candidates_without_balanced, -1, policy
    ) == [aggressive]


def test_limit_pressure_cannot_displace_sole_balanced_qualifier():
    current = _tier_entry("c5.4xlarge", 200, 8, 10_000, 100)
    balanced = _tier_entry("m6i.xlarge", 80, 3, 4_000, 40)
    aggressive_a = _tier_entry("r6i.large", 50, 2, 3_250, 32.5)
    aggressive_b = _tier_entry("r6a.large", 60, 2, 3_400, 34)

    result = _tier_recommendation(
        {
            current.instance_type: current,
            balanced.instance_type: balanced,
            aggressive_a.instance_type: aggressive_a,
            aggressive_b.instance_type: aggressive_b,
        },
        candidate_limit=2,
    )

    assert len(result["recommendations"]) == 2
    assert result["tiers"]["balanced"]["target_instance_type"] == "m6i.xlarge"
    assert {item["target_instance_type"] for item in result["recommendations"]} == {
        "r6i.large",
        "m6i.xlarge",
    }


def test_decimal_safe_one_cent_savings_floor_and_minimum_equality():
    current = _tier_entry("c5.4xlarge", 10.00, 8, 10_000, 100)
    below = _tier_entry("m6i.xlarge", 9.991, 8, 10_000, 100)
    exact = _tier_entry("r6i.xlarge", 9.99, 8, 10_000, 100)
    above = _tier_entry("c6i.xlarge", 9.989, 8, 10_000, 100)

    result = _tier_recommendation(
        {
            current.instance_type: current,
            below.instance_type: below,
            exact.instance_type: exact,
            above.instance_type: above,
        }
    )
    targets = {item["target_instance_type"] for item in result["recommendations"]}
    assert "m6i.xlarge" not in targets
    assert {"r6i.xlarge", "c6i.xlarge"} <= targets

    equality = _tier_recommendation(
        {current.instance_type: current, exact.instance_type: exact},
        min_monthly_savings=0.01,
    )
    assert equality["recommendations"][0]["target_instance_type"] == "r6i.xlarge"
    assert equality["recommendations"][0]["monthly_savings"] == 0.01


def test_no_limit_pressure_preserves_legacy_order_ranks_tiers_and_rounding():
    current = _tier_entry("c5.4xlarge", 0.017, 8, 10_000, 100)
    highest_savings = _tier_entry("c6i.4xlarge", 0.002, 8, 10_000, 100)
    tie_first = _tier_entry("m6i.4xlarge", 0.004, 8, 10_000, 80)
    tie_second = _tier_entry("r6i.4xlarge", 0.005, 8, 10_000, 60)

    result = _tier_recommendation(
        {
            current.instance_type: current,
            highest_savings.instance_type: highest_savings,
            tie_first.instance_type: tie_first,
            tie_second.instance_type: tie_second,
        },
        candidate_limit=10,
    )

    recommendations = result["recommendations"]
    assert [item["target_instance_type"] for item in recommendations] == [
        "c6i.4xlarge",
        "m6i.4xlarge",
        "r6i.4xlarge",
    ]
    assert [item["rank"] for item in recommendations] == [1, 2, 3]
    assert [item["monthly_savings"] for item in recommendations] == [0.02, 0.01, 0.01]
    assert [item["yearly_savings"] for item in recommendations] == [0.18, 0.16, 0.14]
    assert result["tiers"]["conservative"]["target_instance_type"] == "c6i.4xlarge"
    assert result["tiers"]["balanced"]["target_instance_type"] == "c6i.4xlarge"
    assert result["tiers"]["aggressive"]["target_instance_type"] == "c6i.4xlarge"


def test_memory_preview_keeps_rejection_and_checks_other_hard_constraints():
    current = _tier_entry("c5.4xlarge", 200, 8, 10_000, 100)
    eligible = _tier_entry("m6i.xlarge", 80, 8, 5_000, 100)
    result = _tier_recommendation(
        {current.instance_type: current, eligible.instance_type: eligible},
        include_memory=False,
    )

    assert result["recommendations"] == []
    assert result["rejection_summary"]["MEMORY_REQUIREMENT_NOT_MET"] == 1
    assert len(result["savings_previews"]) == 1
    preview = result["savings_previews"][0]
    assert preview["kind"] == "MEMORY_METRIC_MISSING_DOWNSIZE"
    assert preview["classification"] == "PREVIEW"
    assert preview["blockers"] == ["MEMORY_METRIC_NOT_ENABLED"]

    hard_failed = replace(eligible, network_reliable_max_mbps=10)
    failed = _tier_recommendation(
        {current.instance_type: current, hard_failed.instance_type: hard_failed},
        include_memory=False,
    )
    assert failed["savings_previews"] == []
    assert failed["rejection_summary"]["MEMORY_REQUIREMENT_NOT_MET"] == 1


def test_memory_preview_is_not_emitted_when_memory_metric_is_present():
    current = _tier_entry("c5.4xlarge", 200, 8, 10_000, 100)
    too_small = _tier_entry("m6i.xlarge", 80, 8, 2_000, 100)
    result = _tier_recommendation(
        {current.instance_type: current, too_small.instance_type: too_small}
    )

    assert result["savings_previews"] == []
    assert result["rejection_summary"]["MEMORY_REQUIREMENT_NOT_MET"] == 1


def test_graviton_preview_retains_raw_capacity_and_hides_coremark_evidence():
    current = _tier_entry("c5.4xlarge", 200, 8, 10_000, 100)
    graviton = replace(
        _tier_entry("c7g.4xlarge", 120, 8, 10_000, 250),
        architectures=("arm64",),
    )
    result = _tier_recommendation(
        {current.instance_type: current, graviton.instance_type: graviton}
    )

    assert result["recommendations"] == []
    assert result["rejection_summary"]["ARCHITECTURE_INCOMPATIBLE"] == 1
    preview = result["savings_previews"][0]
    assert preview["kind"] == "GRAVITON_MIGRATION"
    assert preview["classification"] == "OPPORTUNITY"
    assert preview["blockers"] == ["ARCHITECTURE_MIGRATION_REQUIRED"]
    assert preview["evidence"]["projected_cpu_util"] is None
    assert preview["evidence"]["performance_ratio"] is None
    assert preview["evidence"]["performance_change_pct"] is None
    assert preview["evidence"]["compute_evidence"]["target_coremark"] is None


def test_graviton_preview_rejects_capacity_reduction_family_gate_and_hard_failures():
    current = _tier_entry("c5.4xlarge", 200, 8, 10_000, 100)
    reduced = replace(
        _tier_entry("c7g.xlarge", 100, 4, 8_000, 300), architectures=("arm64",)
    )
    gated = replace(
        _tier_entry("t4g.4xlarge", 90, 8, 10_000, 300), architectures=("arm64",)
    )
    hard_failed = replace(
        _tier_entry("c8g.4xlarge", 80, 8, 10_000, 300),
        architectures=("arm64",),
        network_reliable_max_mbps=10,
    )
    result = _tier_recommendation(
        {
            current.instance_type: current,
            reduced.instance_type: reduced,
            gated.instance_type: gated,
            hard_failed.instance_type: hard_failed,
        }
    )
    assert result["savings_previews"] == []
    assert result["rejection_summary"]["ARCHITECTURE_INCOMPATIBLE"] == 3


def test_previews_choose_one_per_kind_and_never_change_recommendations():
    current = _tier_entry("c5.4xlarge", 200, 8, 10_000, 100)
    ordinary = _tier_entry("c6i.4xlarge", 150, 8, 10_000, 100)
    memory_high = _tier_entry("m6i.xlarge", 60, 8, 5_000, 100)
    memory_low = _tier_entry("r6i.xlarge", 80, 8, 5_000, 100)
    graviton_high = replace(
        _tier_entry("c7g.4xlarge", 70, 8, 10_000, 250), architectures=("arm64",)
    )
    graviton_low = replace(
        _tier_entry("m7g.4xlarge", 90, 8, 10_000, 250), architectures=("arm64",)
    )
    entries = {
        item.instance_type: item
        for item in (
            current,
            ordinary,
            memory_high,
            memory_low,
            graviton_high,
            graviton_low,
        )
    }

    enabled = _tier_recommendation(entries, include_memory=False)
    disabled = _tier_recommendation(
        entries,
        include_memory=False,
        candidate_policy=EC2CandidatePolicy(
            memory_preview_enabled=False, graviton_preview_enabled=False
        ),
    )

    assert {
        item["kind"]: item["target_instance_type"]
        for item in enabled["savings_previews"]
    } == {
        "MEMORY_METRIC_MISSING_DOWNSIZE": "m6i.xlarge",
        "GRAVITON_MIGRATION": "c7g.4xlarge",
    }
    assert disabled["savings_previews"] == []
    enabled_without_previews = dict(enabled)
    disabled_without_previews = dict(disabled)
    enabled_without_previews.pop("savings_previews")
    disabled_without_previews.pop("savings_previews")
    assert enabled_without_previews == disabled_without_previews
    recommendation_targets = {
        item["target_instance_type"] for item in enabled["recommendations"]
    }
    assert recommendation_targets == {"c6i.4xlarge"}
    assert not recommendation_targets & {
        item["target_instance_type"] for item in enabled["savings_previews"]
    }
