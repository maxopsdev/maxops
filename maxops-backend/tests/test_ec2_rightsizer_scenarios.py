"""Offline, service-boundary scenarios from ec2_rightsizer_test_plan.md.

This file deliberately constructs no AWS adapter.  Run it by its exact path;
repository-wide discovery can include integration tests that provision resources.
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.inventory import EbsInventory, Ec2Inventory
from app.services.ec2_instance_catalog import EC2CatalogEntry, EC2InstanceCatalog
from app.services.ec2_rightsizer import (
    EC2CandidatePolicy,
    EC2ComputePolicy,
    EC2Rightsizer,
    EC2ScopePolicy,
    _recommendation_tiers,
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
from rightsizers.ec2.ec2_rightsizer.constraints import (
    EBSConstraintInput,
    NetworkConstraintInput,
)
from rightsizers.ec2.ec2_rightsizer.selection import classify_recommendation
from rightsizers.ec2.ec2_rightsizer.warnings import (
    EBSWarningInput,
    NetworkWarningInput,
)


def catalog_entry(
    instance_type: str,
    monthly_usd: float,
    *,
    vcpus: float = 8,
    memory_mib: float = 16_384,
    coremark: float | None = 100,
    architectures: tuple[str, ...] = ("x86_64",),
    network_baseline_mbps: float | None = 100,
    network_reliable_max_mbps: float | None = 1_000,
    network_capacity_kind: str = "BASELINE",
    network_class: str = "LOW",
    eni_limit: int | None = 10,
    efa_supported: bool | None = False,
    ebs_supported: bool | None = True,
    ebs_attachment_limit: int | None = 20,
    ebs_attachment_limit_type: str = "dedicated",
    instance_store_device_count: int | None = 0,
    ebs_baseline_iops: float | None = 10_000,
    ebs_max_iops: float | None = 20_000,
    ebs_baseline_throughput_mibps: float | None = 500,
    ebs_max_throughput_mibps: float | None = 1_000,
    gpu_device_count: int | None = None,
    gpu_fractional: bool = False,
    gpu_model: str | None = None,
    gpu_memory_mib_per_device: int | None = None,
) -> EC2CatalogEntry:
    return EC2CatalogEntry(
        instance_type=instance_type,
        region="us-east-1",
        platform="linux",
        monthly_usd=monthly_usd,
        vcpus=vcpus,
        memory_mib=memory_mib,
        coremark=coremark,
        architectures=architectures,
        network_performance="Up to 1 Gigabit",
        network_class=network_class,
        network_baseline_mbps=network_baseline_mbps,
        network_reliable_max_mbps=network_reliable_max_mbps,
        network_capacity_kind=network_capacity_kind,
        eni_limit=eni_limit,
        efa_supported=efa_supported,
        ebs_supported=ebs_supported,
        ebs_attachment_limit=ebs_attachment_limit,
        ebs_attachment_limit_type=ebs_attachment_limit_type,
        instance_store_device_count=instance_store_device_count,
        ebs_baseline_iops=ebs_baseline_iops,
        ebs_max_iops=ebs_max_iops,
        ebs_baseline_throughput_mibps=ebs_baseline_throughput_mibps,
        ebs_max_throughput_mibps=ebs_max_throughput_mibps,
        capability_source="describe_instance_types",
        attributes={},
        gpu_device_count=gpu_device_count,
        gpu_fractional=gpu_fractional,
        gpu_model=gpu_model,
        gpu_memory_mib_per_device=gpu_memory_mib_per_device,
    )


def run_recommendation_scenario(
    *,
    current: EC2CatalogEntry,
    candidates: tuple[EC2CatalogEntry, ...],
    normalized_metrics: dict[str, dict[str, float]] | None = None,
    signals: dict[str, bool] | None = None,
    metadata: dict[str, object] | None = None,
    attached_volumes: tuple[EbsInventory, ...] = (),
    warning_policy: PerformanceWarningPolicy | None = None,
    compute_policy: EC2ComputePolicy | None = None,
    scope_policy: EC2ScopePolicy | None = None,
    candidate_policy: EC2CandidatePolicy | None = None,
    min_monthly_savings: float = 0.0,
    candidate_limit: int = 10,
    include_current_in_catalog: bool = True,
) -> dict[str, object]:
    """Execute one recommendation using only an in-memory DB and catalog."""

    class Catalog:
        def list_region(self, region, platform):
            assert (region, platform) == ("us-east-1", "linux")
            entries = candidates
            if include_current_in_catalog:
                entries = (current, *entries)
            return {entry.instance_type: entry for entry in entries}

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    normalized = (
        normalized_metrics
        if normalized_metrics is not None
        else {
            "cpu_percent": {"p99": 20, "sample_count": 2_016},
            "memory_percent": {"p99": 20, "sample_count": 2_016},
            "network_in_mbps": {"p99": 5, "maximum": 10, "sample_count": 2_016},
            "network_out_mbps": {"p99": 2, "maximum": 4, "sample_count": 2_016},
            "ebs_combined_iops": {"p99": 100, "sample_count": 2_016},
            "ebs_combined_mibps": {"p99": 10, "sample_count": 2_016},
        }
    )
    collected_signals = (
        signals
        if signals is not None
        else {
            "bw_in_allowance_exceeded": False,
            "bw_out_allowance_exceeded": False,
            "pps_allowance_exceeded": False,
            "conntrack_allowance_exceeded": False,
            "instance_ebs_iops_exceeded": False,
            "instance_ebs_throughput_exceeded": False,
        }
    )
    payload = {
        "instance_store_present": False,
        "attached_eni_count": 1,
        "attached_volume_count": len(attached_volumes),
        "attached_volume_ids": [volume.resource_id for volume in attached_volumes],
        "rightsizing_metrics": {
            "60d": {
                "period_seconds": 300,
                "normalized": normalized,
                "signals": collected_signals,
            }
        },
        **(metadata or {}),
    }
    session.add(
        Ec2Inventory(
            inventory_id=1,
            resource_id="i-offline",
            resource_type="ec2",
            resource_name="offline",
            region="us-east-1",
            state="running",
            instance_type=current.instance_type,
            metadata_json=payload,
        )
    )
    session.add_all(attached_volumes)
    session.commit()
    result = EC2Rightsizer(
        session,
        catalog=Catalog(),
        warning_policy=warning_policy,
        compute_policy=compute_policy,
        scope_policy=scope_policy,
        candidate_policy=candidate_policy,
    ).get_recommendation(
        1,
        min_monthly_savings=min_monthly_savings,
        candidate_limit=candidate_limit,
    )
    assert result is not None
    return result


def neutral_metrics(**overrides):
    metrics = {
        "cpu_percent": {"p99": 20, "sample_count": 2_016},
        "memory_percent": {"p99": 20, "sample_count": 2_016},
        "network_in_mbps": {"p99": 5, "maximum": 10, "sample_count": 2_016},
        "network_out_mbps": {"p99": 2, "maximum": 4, "sample_count": 2_016},
        "ebs_combined_iops": {"p99": 100, "sample_count": 2_016},
        "ebs_combined_mibps": {"p99": 10, "sample_count": 2_016},
    }
    for name, value in overrides.items():
        if value is None:
            metrics.pop(name, None)
        elif isinstance(value, dict):
            metrics[name] = value
        else:
            metrics[name] = {"p99": value, "sample_count": 2_016}
    return metrics


def neutral_signals(**overrides):
    signals = {
        "bw_in_allowance_exceeded": False,
        "bw_out_allowance_exceeded": False,
        "pps_allowance_exceeded": False,
        "conntrack_allowance_exceeded": False,
        "instance_ebs_iops_exceeded": False,
        "instance_ebs_throughput_exceeded": False,
    }
    signals.update(overrides)
    return signals


def volume(number: int, *, iops: int | None = None, throughput: int | None = None):
    return EbsInventory(
        inventory_id=10_000 + number,
        resource_id=f"vol-{number}",
        resource_type="ebs",
        region="us-east-1",
        state="in-use",
        volume_type="gp3",
        attached="true",
        iops=iops,
        throughput=throughput,
    )


def recommendation(result, target="m6i.xlarge"):
    return next(
        item
        for item in result["recommendations"]
        if item["target_instance_type"] == target
    )


def decision_projection(result):
    recommendations = result.get("recommendations", [])
    return {
        "targets": [item["target_instance_type"] for item in recommendations],
        "savings": [item["monthly_savings"] for item in recommendations],
        "classifications": {
            item["target_instance_type"]: item["classification"]
            for item in recommendations
        },
        "reason_codes": {
            item["target_instance_type"]: item["reason_codes"]
            for item in recommendations
        },
        "warnings": {
            item["target_instance_type"]: [
                detail["code"] for detail in item["warning_details"]
            ]
            for item in recommendations
        },
        "tiers": {
            name: option["target_instance_type"] if option else None
            for name, option in result.get("tiers", {}).items()
            if name != "default"
        },
        "tier_default": result.get("tiers", {}).get("default"),
        "rejection_summary": result.get("rejection_summary", {}),
        "previews": {
            item["kind"]: item["target_instance_type"]
            for item in result.get("savings_previews", [])
        },
    }


def gpu_metrics(required_devices, vram_mib, sample_count=2_016):
    """Build the persisted GPU summaries used by offline rightsizer scenarios."""
    return neutral_metrics(
        gpu_required_devices={"maximum": required_devices, "sample_count": sample_count},
        gpu_vram_used_mib={"maximum": vram_mib, "sample_count": sample_count},
    )


@pytest.mark.parametrize(
    "ratio,risk,warning",
    [
        (0.399, RiskLevel.LOW, None),
        (0.40, RiskLevel.MEDIUM, "NETWORK_USAGE_REVIEW_REQUIRED"),
        (0.70, RiskLevel.MEDIUM, "NETWORK_USAGE_REVIEW_REQUIRED"),
        (0.701, RiskLevel.HIGH, "HIGH_NETWORK_USAGE_REVIEW_REQUIRED"),
        (1.0, RiskLevel.HIGH, "NETWORK_SUSTAINED_ABOVE_BASELINE"),
    ],
)
def test_network_warning_boundaries(ratio, risk, warning):
    evaluation = evaluate_network(
        NetworkConstraintInput(
            observed_in_p99_mbps=ratio * 100,
            target_reliable_max_mbps=1_000,
        ),
        NetworkWarningInput(
            ratio * 100,
            0,
            100,
            CapacityKind.BASELINE,
            allowance_metrics_collected=True,
            target_peak_mbps=1_000,
        ),
        PerformanceWarningPolicy(),
    )
    assert evaluation.risk_level == risk
    if warning:
        assert warning in evaluation.warnings
    else:
        assert not evaluation.warnings


def test_gpu_g5_recommends_same_model_target_with_enough_devices():
    result = run_recommendation_scenario(
        current=catalog_entry(
            "g5.48xlarge", 100, gpu_device_count=8, gpu_model="A10G",
            gpu_memory_mib_per_device=24_576,
        ),
        candidates=(
            catalog_entry(
                "g5.12xlarge", 60, gpu_device_count=4, gpu_model="A10G",
                gpu_memory_mib_per_device=24_576,
            ),
            catalog_entry(
                "g5.2xlarge", 20, gpu_device_count=1, gpu_model="A10G",
                gpu_memory_mib_per_device=24_576,
            ),
        ),
        normalized_metrics=gpu_metrics(2, 18 * 1024),
        metadata={"gpu_metric_status": "usable", "gpu_device_count_observed": 8},
    )
    assert [item["target_instance_type"] for item in result["recommendations"]] == [
        "g5.12xlarge"
    ]
    candidate = recommendation(result, "g5.12xlarge")
    assert "GPU_DEVICE_DEMAND_USES_WINDOW_MAXIMUM" in candidate["reason_codes"]
    assert result["rejection_summary"]["GPU_DEVICE_COUNT_REQUIREMENT_NOT_MET"] == 1


def test_gpu_vram_headroom_rejects_target_that_cannot_hold_peak():
    result = run_recommendation_scenario(
        current=catalog_entry(
            "g5.48xlarge", 100, gpu_device_count=8, gpu_model="A10G",
            gpu_memory_mib_per_device=24_576,
        ),
        candidates=(catalog_entry(
            "g5.12xlarge", 60, gpu_device_count=4, gpu_model="A10G",
            gpu_memory_mib_per_device=24_576,
        ),),
        normalized_metrics=gpu_metrics(2, 23 * 1024),
        metadata={"gpu_metric_status": "usable", "gpu_device_count_observed": 8},
    )
    assert result["recommendations"] == []
    assert result["rejection_summary"]["GPU_VRAM_REQUIREMENT_NOT_MET"] == 1


@pytest.mark.parametrize("vram_mib, expected", [(30 * 1024, True), (32 * 1024, False)])
def test_gpu_p4de_to_p4d_same_model_vram_gate(vram_mib, expected):
    result = run_recommendation_scenario(
        current=catalog_entry(
            "p4de.24xlarge", 100, gpu_device_count=8, gpu_model="A100",
            gpu_memory_mib_per_device=81_920,
        ),
        candidates=(catalog_entry(
            "p4d.24xlarge", 60, gpu_device_count=8, gpu_model="A100",
            gpu_memory_mib_per_device=40_960,
        ),),
        normalized_metrics=gpu_metrics(8, vram_mib),
        metadata={"gpu_metric_status": "usable", "gpu_device_count_observed": 8},
    )
    assert bool(result["recommendations"]) is expected


def test_gpu_cross_model_candidate_is_rejected_with_model_diagnostic():
    result = run_recommendation_scenario(
        current=catalog_entry(
            "p4d.24xlarge", 100, gpu_device_count=8, gpu_model="A100",
            gpu_memory_mib_per_device=40_960,
        ),
        candidates=(catalog_entry(
            "p5.24xlarge", 60, gpu_device_count=8, gpu_model="H100",
            gpu_memory_mib_per_device=81_920,
        ),),
        normalized_metrics=gpu_metrics(2, 18 * 1024),
        metadata={"gpu_metric_status": "usable", "gpu_device_count_observed": 8},
    )
    assert result["recommendations"] == []
    assert result["rejection_summary"]["GPU_MODEL_MISMATCH"] == 1


def test_gpu_unused_full_window_allows_conditional_capability_removal_only_at_zero():
    current = catalog_entry(
        "g5.48xlarge", 100, gpu_device_count=8, gpu_model="A10G",
        gpu_memory_mib_per_device=24_576,
    )
    target = catalog_entry("m6i.4xlarge", 60)
    unused = run_recommendation_scenario(
        current=current,
        candidates=(target,),
        normalized_metrics=gpu_metrics(0, 0),
        metadata={"gpu_metric_status": "usable", "gpu_device_count_observed": 8},
    )
    candidate = recommendation(unused, "m6i.4xlarge")
    assert candidate["classification"] == "CONDITIONAL"
    assert "GPU_UNUSED_FULL_WINDOW" in candidate["reason_codes"]
    busy = run_recommendation_scenario(
        current=current,
        candidates=(target,),
        normalized_metrics=gpu_metrics(1, 1024),
        metadata={"gpu_metric_status": "usable", "gpu_device_count_observed": 8},
    )
    assert busy["recommendations"] == []


def test_gpu_unused_source_keeps_burstable_and_other_accelerator_families_gated():
    result = run_recommendation_scenario(
        current=catalog_entry(
            "g5.2xlarge", 100, gpu_device_count=1, gpu_model="A10G",
            gpu_memory_mib_per_device=24_576,
        ),
        candidates=(
            catalog_entry("t3.2xlarge", 20),
            catalog_entry("inf2.xlarge", 30),
            catalog_entry("m6i.2xlarge", 60),
        ),
        normalized_metrics=gpu_metrics(0, 0),
        metadata={"gpu_metric_status": "usable", "gpu_device_count_observed": 1},
    )
    assert decision_projection(result)["targets"] == ["m6i.2xlarge"]
    assert result["rejection_summary"]["FAMILY_NOT_ELIGIBLE"] == 2


def test_gpu_source_with_device_demand_records_family_rejection_for_t3_target():
    result = run_recommendation_scenario(
        current=catalog_entry(
            "g5.2xlarge", 100, gpu_device_count=1, gpu_model="A10G",
            gpu_memory_mib_per_device=24_576,
        ),
        candidates=(catalog_entry("t3.2xlarge", 20),),
        normalized_metrics=gpu_metrics(2, 18 * 1024),
        metadata={"gpu_metric_status": "usable", "gpu_device_count_observed": 1},
    )
    assert result["recommendations"] == []
    assert result["rejection_summary"]["FAMILY_NOT_ELIGIBLE"] == 1


def test_gpu_telemetry_unavailable_prevents_unsafe_cpu_only_downsize():
    result = run_recommendation_scenario(
        current=catalog_entry(
            "g5.48xlarge", 100, gpu_device_count=8, gpu_model="A10G",
            gpu_memory_mib_per_device=24_576,
        ),
        candidates=(catalog_entry(
            "g5.12xlarge", 60, gpu_device_count=4, gpu_model="A10G",
            gpu_memory_mib_per_device=24_576,
        ),),
        normalized_metrics=neutral_metrics(cpu_percent=1),
        metadata={"gpu_metric_status": "unavailable", "gpu_device_count_observed": 0},
    )
    assert result["classification"] == "DEFERRED"
    assert result["deferred_reason_codes"] == [
        "ACCELERATOR_TELEMETRY_UNAVAILABLE"
    ]
    assert result["recommendations"] == []


def test_gpu_telemetry_incomplete_defers_instead_of_inferencing_idle_devices():
    result = run_recommendation_scenario(
        current=catalog_entry(
            "g5.48xlarge", 100, gpu_device_count=8, gpu_model="A10G",
            gpu_memory_mib_per_device=24_576,
        ),
        candidates=(),
        normalized_metrics=gpu_metrics(0, 0),
        metadata={"gpu_metric_status": "usable", "gpu_device_count_observed": 6},
    )
    assert result["classification"] == "DEFERRED"
    assert result["deferred_reason_codes"] == [
        "ACCELERATOR_TELEMETRY_INCOMPLETE"
    ]


def test_thin_gpu_window_caps_candidate_at_conditional():
    result = run_recommendation_scenario(
        current=catalog_entry(
            "g5.48xlarge", 100, gpu_device_count=8, gpu_model="A10G",
            gpu_memory_mib_per_device=24_576,
        ),
        candidates=(catalog_entry(
            "g5.12xlarge", 60, gpu_device_count=4, gpu_model="A10G",
            gpu_memory_mib_per_device=24_576,
        ),),
        normalized_metrics=gpu_metrics(2, 18 * 1024, sample_count=12),
        metadata={"gpu_metric_status": "usable", "gpu_device_count_observed": 8},
    )
    candidate = recommendation(result, "g5.12xlarge")
    assert candidate["classification"] == "CONDITIONAL"
    assert "GPU_OBSERVATION_WINDOW_TOO_SHORT" in candidate["reason_codes"]


def test_fractional_gpu_slice_is_evaluated_with_slice_vram_capacity():
    result = run_recommendation_scenario(
        current=catalog_entry(
            "g6f.xlarge", 100, gpu_device_count=1, gpu_fractional=True,
            gpu_model="L4", gpu_memory_mib_per_device=12_288,
        ),
        candidates=(catalog_entry(
            "g6f.large", 60, gpu_device_count=1, gpu_fractional=True,
            gpu_model="L4", gpu_memory_mib_per_device=12_288,
        ),),
        normalized_metrics=gpu_metrics(1, 6 * 1024),
        metadata={"gpu_metric_status": "usable", "gpu_device_count_observed": 1},
    )
    assert recommendation(result, "g6f.large")["gpu_evidence"]["required_devices"] == 1


def test_non_gpu_projection_is_unchanged_by_gpu_contract():
    result = run_recommendation_scenario(
        current=catalog_entry("m5.xlarge", 100),
        candidates=(catalog_entry("c6i.xlarge", 60),),
    )
    assert decision_projection(result)["targets"] == ["c6i.xlarge"]
    assert recommendation(result, "c6i.xlarge")["classification"] == "ACTIONABLE"


@pytest.mark.parametrize(
    "ratio,risk,warning",
    [
        (0.399, RiskLevel.LOW, None),
        (0.40, RiskLevel.MEDIUM, "EBS_USAGE_REVIEW_REQUIRED"),
        (0.70, RiskLevel.MEDIUM, "EBS_USAGE_REVIEW_REQUIRED"),
        (0.701, RiskLevel.HIGH, "HIGH_EBS_USAGE_REVIEW_REQUIRED"),
    ],
)
def test_ebs_warning_boundaries(ratio, risk, warning):
    evaluation = evaluate_ebs(
        EBSConstraintInput(
            observed_combined_iops_p99=ratio * 10_000,
            target_max_iops=20_000,
        ),
        EBSWarningInput(
            ratio * 10_000,
            0,
            10_000,
            500,
            CapacityKind.BASELINE,
            exceeded_metrics_collected=True,
        ),
        PerformanceWarningPolicy(),
    )
    assert evaluation.risk_level == risk
    if warning:
        assert warning in evaluation.warnings
    else:
        assert not evaluation.warnings


def test_service_selection_ranking_architecture_and_family_gates():
    current = catalog_entry("m5.xlarge", 100)
    cross_family = catalog_entry("c6i.xlarge", 60)
    tie = catalog_entry("r6i.xlarge", 60)
    arm = catalog_entry("m7g.xlarge", 50, architectures=("arm64",))
    gated = catalog_entry("g5.xlarge", 40)
    result = run_recommendation_scenario(
        current=current, candidates=(cross_family, tie, arm, gated)
    )
    projection = decision_projection(result)
    assert projection["targets"] == ["c6i.xlarge", "r6i.xlarge"]
    assert result["recommendations"][0]["family_changed"] is True
    assert projection["rejection_summary"]["ARCHITECTURE_INCOMPATIBLE"] == 1
    assert projection["rejection_summary"]["FAMILY_NOT_ELIGIBLE"] == 1


def test_network_peak_eni_and_efa_hard_boundaries():
    below = evaluate_network(
        NetworkConstraintInput(
            attached_eni_count=2,
            target_eni_limit=2,
            observed_in_p99_mbps=999,
            target_reliable_max_mbps=1_000,
            requires_efa=True,
            target_supports_efa=True,
        ),
        NetworkWarningInput(999, 0, 500, target_peak_mbps=1_000),
        PerformanceWarningPolicy(),
    )
    assert below.hard_constraint_status == HardConstraintStatus.PASS
    at_peak = evaluate_network(
        NetworkConstraintInput(
            attached_eni_count=3,
            target_eni_limit=2,
            observed_out_p99_mbps=1_000,
            target_reliable_max_mbps=1_000,
            requires_efa=True,
            target_supports_efa=None,
        ),
        NetworkWarningInput(0, 1_000, 500, target_peak_mbps=1_000),
        PerformanceWarningPolicy(),
    )
    assert at_peak.hard_constraint_status == HardConstraintStatus.FAIL
    assert "NETWORK_RELIABLE_MAX_EXCEEDED" in at_peak.warnings
    assert "TARGET_ENI_LIMIT_TOO_LOW" in at_peak.warnings


def test_ebs_reliable_maximum_equality_and_provisioned_capacity():
    equality = evaluate_ebs(
        EBSConstraintInput(
            attached_volume_count=2,
            target_attachment_limit=2,
            observed_combined_iops_p99=20_000,
            target_max_iops=20_000,
            required_provisioned_throughput_mibps=1_000,
            target_max_throughput_mibps=1_000,
        ),
        EBSWarningInput(20_000, 0, 30_000, 500, CapacityKind.BASELINE),
        PerformanceWarningPolicy(),
    )
    assert equality.hard_constraint_status == HardConstraintStatus.PASS
    exceeded = evaluate_ebs(
        EBSConstraintInput(
            attached_volume_count=3,
            target_attachment_limit=2,
            observed_combined_iops_p99=20_001,
            target_max_iops=20_000,
            required_provisioned_throughput_mibps=1_001,
            target_max_throughput_mibps=1_000,
        ),
        EBSWarningInput(20_001, 0, 30_000, 500, CapacityKind.BASELINE),
        PerformanceWarningPolicy(),
    )
    assert exceeded.hard_constraint_status == HardConstraintStatus.FAIL
    assert {
        "TARGET_EBS_ATTACHMENT_LIMIT_TOO_LOW",
        "EBS_MAX_IOPS_EXCEEDED",
        "PROVISIONED_THROUGHPUT_UNSUPPORTED",
    } <= set(exceeded.warnings)


@pytest.mark.parametrize(
    "network,storage,expected",
    [
        (
            ResourceEvaluation(HardConstraintStatus.PASS, RiskLevel.LOW),
            ResourceEvaluation(HardConstraintStatus.NOT_APPLICABLE, RiskLevel.LOW),
            RecommendationClassification.ACTIONABLE,
        ),
        (
            ResourceEvaluation(HardConstraintStatus.PASS, RiskLevel.MEDIUM),
            ResourceEvaluation(HardConstraintStatus.PASS, RiskLevel.LOW),
            RecommendationClassification.CONDITIONAL,
        ),
        (
            ResourceEvaluation(HardConstraintStatus.UNKNOWN, RiskLevel.LOW),
            ResourceEvaluation(HardConstraintStatus.PASS, RiskLevel.LOW),
            RecommendationClassification.CONDITIONAL,
        ),
        (
            ResourceEvaluation(HardConstraintStatus.FAIL, RiskLevel.HIGH),
            ResourceEvaluation(HardConstraintStatus.PASS, RiskLevel.LOW),
            RecommendationClassification.REJECTED,
        ),
    ],
)
def test_classification_matrix(network, storage, expected):
    assert (
        classify_recommendation(
            cpu_passes=True,
            memory_passes=True,
            network=network,
            storage=storage,
        )
        == expected
    )


def test_tier_boundaries_are_inclusive_and_reference_returned_candidates():
    def candidate(name, util, savings):
        return {
            "target_instance_type": name,
            "projected_cpu_util": util,
            "projected_memory_util": util,
            "projected_util": util,
            "performance_ratio": 1,
            "performance_change_pct": 0,
            "binding_dimension": "cpu",
            "monthly_savings": savings,
            "yearly_savings": savings * 12,
            "classification": "ACTIONABLE",
            "risk_assessment": {},
        }

    returned = [
        candidate("c6i.large", 0.55, 10),
        candidate("m6i.large", 0.70, 20),
        candidate("r6i.large", 0.85, 30),
    ]
    tiers = _recommendation_tiers(returned, EC2ComputePolicy())
    assert tiers["conservative"]["target_instance_type"] == "c6i.large"
    assert tiers["balanced"]["target_instance_type"] == "m6i.large"
    assert tiers["aggressive"]["target_instance_type"] == "r6i.large"
    assert {
        tiers[name]["target_instance_type"]
        for name in ("conservative", "balanced", "aggressive")
    } <= {item["target_instance_type"] for item in returned}


def test_thin_telemetry_is_disclosure_only():
    current = catalog_entry("m5.xlarge", 100)
    target = catalog_entry("m6i.xlarge", 60)
    full = run_recommendation_scenario(current=current, candidates=(target,))
    thin_metrics = {
        "cpu_percent": {"p99": 20, "sample_count": 12},
        "memory_percent": {"p99": 20, "sample_count": 12},
        "network_in_mbps": {"p99": 5, "sample_count": 12},
        "network_out_mbps": {"p99": 2, "sample_count": 12},
    }
    thin = run_recommendation_scenario(
        current=current, candidates=(target,), normalized_metrics=thin_metrics
    )
    assert decision_projection(full) == decision_projection(thin)
    assert thin["telemetry_summary"]["cpu_percent"]["thin_data"] is True
    assert "INSUFFICIENT_DATA" not in str(thin)


def test_burst_only_ebs_warning_companion_boundary():
    evaluation = evaluate_ebs(
        EBSConstraintInput(),
        EBSWarningInput(None, None, None, None, CapacityKind.BURST_OR_UP_TO),
        PerformanceWarningPolicy(),
    )
    assert evaluation.risk_level == RiskLevel.HIGH
    assert "EBS_BURST_CAPACITY_REQUIRES_REVIEW" in evaluation.warnings


def test_base_pricing_savings_and_catalog_boundaries():
    current = catalog_entry("m5.xlarge", 100)

    clean = run_recommendation_scenario(
        current=current, candidates=(catalog_entry("m6i.xlarge", 60),)
    )
    clean_candidate = recommendation(clean)
    assert clean_candidate["classification"] == "ACTIONABLE"  # REC-001
    assert (clean_candidate["monthly_savings"], clean_candidate["yearly_savings"]) == (
        40,
        480,
    )

    equal = run_recommendation_scenario(
        current=current, candidates=(catalog_entry("m6i.xlarge", 100),)
    )
    expensive = run_recommendation_scenario(
        current=current, candidates=(catalog_entry("m6i.xlarge", 101),)
    )
    assert equal["recommendations"] == []  # REC-002
    assert expensive["recommendations"] == []  # REC-003

    threshold = run_recommendation_scenario(
        current=catalog_entry("m5.xlarge", 10),
        candidates=(
            catalog_entry("c6i.xlarge", 9.01),
            catalog_entry("m6i.xlarge", 9.00),
            catalog_entry("r6i.xlarge", 8.99),
        ),
        min_monthly_savings=1.00,
    )
    assert decision_projection(threshold)["targets"] == [
        "r6i.xlarge",
        "m6i.xlarge",
    ]  # REC-004

    missing_current = run_recommendation_scenario(
        current=current,
        candidates=(catalog_entry("m6i.xlarge", 60),),
        include_current_in_catalog=False,
    )
    assert missing_current["recommendations"] == []  # REC-012
    assert missing_current["blocking_reasons"] == [
        "CURRENT_INSTANCE_PRICING_OR_SPEC_MISSING"
    ]


def test_architecture_family_policy_limit_and_rejection_boundaries():
    current = catalog_entry("m5.xlarge", 100)
    empty_arch = run_recommendation_scenario(
        current=current,
        candidates=(catalog_entry("m6i.xlarge", 60, architectures=()),),
    )
    assert empty_arch["rejection_summary"] == {
        "ARCHITECTURE_INCOMPATIBLE": 1
    }  # REC-007

    same_gated_class = run_recommendation_scenario(
        current=catalog_entry("t3.xlarge", 100),
        candidates=(catalog_entry("t3a.xlarge", 60),),
    )
    assert decision_projection(same_gated_class)["targets"] == ["t3a.xlarge"]  # REC-009

    many = run_recommendation_scenario(
        current=current,
        candidates=(
            catalog_entry("c6i.xlarge", 40),
            catalog_entry("m6i.xlarge", 60),
            catalog_entry("r6i.xlarge", 50),
        ),
        candidate_limit=2,
    )
    assert decision_projection(many)["targets"] == [
        "c6i.xlarge",
        "r6i.xlarge",
    ]  # REC-010

    hard_rejections = run_recommendation_scenario(
        current=current,
        candidates=(
            catalog_entry("m7g.xlarge", 10, architectures=("arm64",)),
            catalog_entry("g5.xlarge", 20),
            catalog_entry("c6i.large", 30, network_reliable_max_mbps=1),
        ),
    )
    assert hard_rejections["recommendations"] == []  # REC-014
    assert hard_rejections["rejection_summary"] == {
        "ARCHITECTURE_INCOMPATIBLE": 1,
        "FAMILY_NOT_ELIGIBLE": 1,
        "NETWORK_RELIABLE_MAX_EXCEEDED": 1,
    }

    gated = catalog_entry("g5.xlarge", 60)
    default_gate = run_recommendation_scenario(current=current, candidates=(gated,))
    open_gate = run_recommendation_scenario(
        current=current,
        candidates=(gated,),
        candidate_policy=EC2CandidatePolicy(gated_family_classes=()),
    )
    assert default_gate["recommendations"] == []
    assert decision_projection(open_gate)["targets"] == ["g5.xlarge"]  # REC-017


def test_compute_projection_memory_and_performance_contracts():
    current = catalog_entry("m5.xlarge", 100, coremark=100, memory_mib=16_000)
    coremark_target = catalog_entry("m6i.xlarge", 60, coremark=50, memory_mib=16_000)
    result = run_recommendation_scenario(
        current=current,
        candidates=(coremark_target,),
        normalized_metrics=neutral_metrics(cpu_percent=20, memory_percent=20),
    )
    candidate = recommendation(result)
    assert candidate["projected_cpu_util"] == pytest.approx(0.4)  # CMP-001
    assert candidate["projected_memory_util"] == pytest.approx(0.2)  # CMP-003
    assert candidate["compute_evidence"]["estimated_memory_used_mib"] == 3_200

    vcpu_result = run_recommendation_scenario(
        current=catalog_entry("m5.xlarge", 100, coremark=None, vcpus=8),
        candidates=(catalog_entry("m6i.xlarge", 60, coremark=None, vcpus=4),),
        normalized_metrics=neutral_metrics(cpu_percent=20),
    )
    assert recommendation(vcpu_result)["projected_cpu_util"] == pytest.approx(
        0.4
    )  # CMP-002

    memory_missing = run_recommendation_scenario(
        current=current,
        candidates=(
            catalog_entry("m6i.xlarge", 60, coremark=100, memory_mib=16_000),
            catalog_entry("r6i.large", 50, coremark=100, memory_mib=8_000),
        ),
        normalized_metrics=neutral_metrics(memory_percent=None),
    )
    retained = recommendation(memory_missing)
    assert retained["projected_memory_util"] is None  # CMP-004
    assert (
        "MEMORY_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED"
        in retained["reason_codes"]
    )
    assert (
        memory_missing["rejection_summary"]["MEMORY_REQUIREMENT_NOT_MET"] == 1
    )  # CMP-005

    cpu_missing = run_recommendation_scenario(
        current=current,
        candidates=(
            catalog_entry("m6i.xlarge", 60, coremark=100, memory_mib=16_000),
            catalog_entry("r6i.large", 50, coremark=90, memory_mib=16_000),
        ),
        normalized_metrics=neutral_metrics(cpu_percent=None),
    )
    retained = recommendation(cpu_missing)
    assert retained["projected_cpu_util"] is None  # CMP-006
    assert retained["binding_dimension"] != "cpu"
    assert cpu_missing["rejection_summary"]["CPU_REQUIREMENT_NOT_MET"] == 1  # CMP-007

    slower = run_recommendation_scenario(
        current=current,
        candidates=(catalog_entry("m6i.xlarge", 60, coremark=80),),
    )
    slower_candidate = recommendation(slower)
    assert slower_candidate["performance_ratio"] == pytest.approx(0.8)  # CMP-011
    assert slower_candidate["performance_change_pct"] == pytest.approx(-20)

    unavailable = run_recommendation_scenario(
        current=catalog_entry("m5.xlarge", 100, coremark=0),
        candidates=(catalog_entry("m6i.xlarge", 60, coremark=None),),
    )
    unavailable_candidate = recommendation(unavailable)
    assert unavailable_candidate["performance_ratio"] is None  # CMP-012
    assert unavailable_candidate["performance_change_pct"] is None


@pytest.mark.parametrize(
    "ratio,classification,risk,warning",
    [
        pytest.param(0.399, "ACTIONABLE", "LOW", None, id="NET-001"),
        pytest.param(
            0.40,
            "CONDITIONAL",
            "MEDIUM",
            "NETWORK_USAGE_REVIEW_REQUIRED",
            id="NET-002",
        ),
        pytest.param(
            0.70,
            "CONDITIONAL",
            "MEDIUM",
            "NETWORK_USAGE_REVIEW_REQUIRED",
            id="NET-003",
        ),
        pytest.param(
            0.701,
            "CONDITIONAL",
            "HIGH",
            "HIGH_NETWORK_USAGE_REVIEW_REQUIRED",
            id="NET-004",
        ),
        pytest.param(
            1.0,
            "CONDITIONAL",
            "HIGH",
            "NETWORK_SUSTAINED_ABOVE_BASELINE",
            id="NET-005",
        ),
        pytest.param(
            5.0,
            "CONDITIONAL",
            "HIGH",
            "NETWORK_SUSTAINED_ABOVE_BASELINE",
            id="NET-006",
        ),
    ],
)
def test_network_service_warning_and_baseline_boundaries(
    ratio, classification, risk, warning
):
    result = run_recommendation_scenario(
        current=catalog_entry("m5.xlarge", 100),
        candidates=(catalog_entry("m6i.xlarge", 60),),
        normalized_metrics=neutral_metrics(
            network_in_mbps=ratio * 100, network_out_mbps=1
        ),
    )
    candidate = recommendation(result)
    evaluation = candidate["network_evaluation"]
    assert candidate["classification"] == classification
    assert evaluation["risk_level"] == risk
    assert evaluation["evidence"]["network_in_utilization_ratio"] == pytest.approx(
        ratio
    )
    if warning is None:
        assert "NETWORK_USAGE_REVIEW_REQUIRED" not in evaluation["warnings"]
    else:
        assert warning in evaluation["warnings"]


@pytest.mark.parametrize(
    "demand,returned",
    [
        pytest.param(999.9, True, id="NET-007"),
        pytest.param(1_000, False, id="NET-008"),
        pytest.param(1_001, False, id="NET-009"),
    ],
)
def test_network_reliable_peak_boundaries(demand, returned):
    result = run_recommendation_scenario(
        current=catalog_entry("m5.xlarge", 100),
        candidates=(catalog_entry("m6i.xlarge", 60),),
        normalized_metrics=neutral_metrics(network_in_mbps=demand, network_out_mbps=1),
    )
    if returned:
        assert decision_projection(result)["targets"] == ["m6i.xlarge"]
    else:
        assert result["recommendations"] == []
        assert result["rejection_summary"]["NETWORK_RELIABLE_MAX_EXCEEDED"] == 1


def test_network_assumed_unknown_and_directional_paths():
    assumed_target = catalog_entry(
        "c6i.large",
        60,
        vcpus=2,
        network_baseline_mbps=None,
        network_reliable_max_mbps=1_000,
        network_capacity_kind="BURST_OR_UP_TO",
    )
    assumed = run_recommendation_scenario(
        current=catalog_entry("m5.xlarge", 100),
        candidates=(
            assumed_target,
            catalog_entry("c6i.4xlarge", 110, vcpus=16),
            catalog_entry("c6a.24xlarge", 120, vcpus=96),
            catalog_entry("c6i.metal", 130, vcpus=128),
        ),
        normalized_metrics=neutral_metrics(network_in_mbps=20),
    )
    candidate = recommendation(assumed, "c6i.large")
    evidence = candidate["network_evaluation"]["evidence"]
    assert evidence["baseline_mbps"] == pytest.approx(125)  # NET-010
    assert evidence["capacity_kind"] == "ASSUMED_BASELINE"
    assert evidence["assumed_baseline"] is True
    assert "NETWORK_BASELINE_ASSUMED" in candidate["reason_codes"]
    assert candidate["classification"] == "CONDITIONAL"

    burst = run_recommendation_scenario(
        current=catalog_entry("m5.xlarge", 100),
        candidates=(
            catalog_entry(
                "m6i.xlarge",
                60,
                network_baseline_mbps=None,
                network_capacity_kind="BURST_OR_UP_TO",
            ),
        ),
        warning_policy=PerformanceWarningPolicy(network_assumed_baseline_enabled=False),
    )
    burst_candidate = recommendation(burst)
    assert burst_candidate["classification"] == "CONDITIONAL"  # NET-011
    assert "NETWORK_BURST_CAPACITY_REQUIRES_REVIEW" in burst_candidate["reason_codes"]

    unknown = run_recommendation_scenario(
        current=catalog_entry("m5.xlarge", 100, network_class="UNKNOWN"),
        candidates=(
            catalog_entry(
                "m6i.xlarge",
                60,
                network_baseline_mbps=None,
                network_reliable_max_mbps=None,
                network_capacity_kind="UNKNOWN",
                network_class="UNKNOWN",
            ),
        ),
    )
    unknown_candidate = recommendation(unknown)
    assert unknown_candidate["classification"] == "CONDITIONAL"  # NET-012
    assert "NETWORK_CAPABILITY_UNKNOWN" in unknown_candidate["reason_codes"]

    for inbound, outbound in ((80, 10), (10, 80)):
        directional = run_recommendation_scenario(
            current=catalog_entry("m5.xlarge", 100),
            candidates=(catalog_entry("m6i.xlarge", 60),),
            normalized_metrics=neutral_metrics(
                network_in_mbps=inbound, network_out_mbps=outbound
            ),
        )
        network = recommendation(directional)["network_evaluation"]
        assert network["risk_level"] == "HIGH"  # NET-013
        assert network["evidence"]["network_in_utilization_ratio"] == pytest.approx(
            inbound / 100
        )
        assert network["evidence"]["network_out_utilization_ratio"] == pytest.approx(
            outbound / 100
        )


def test_network_weighting_allowance_and_operational_signal_paths():
    current = catalog_entry("m5.xlarge", 100)
    target = catalog_entry("m6i.xlarge", 60)
    weighted = run_recommendation_scenario(
        current=current,
        candidates=(target,),
        metadata={"network_bandwidth_weighting": "vpc-1"},
        normalized_metrics=neutral_metrics(network_in_mbps=50),
    )
    weighted_candidate = recommendation(weighted)
    assert "NON_DEFAULT_NETWORK_BANDWIDTH_WEIGHTING_REQUIRES_REVIEW" in (
        weighted_candidate["reason_codes"]
    )  # NET-014
    assert "NETWORK_USAGE_REVIEW_REQUIRED" not in weighted_candidate["reason_codes"]

    blocked = run_recommendation_scenario(
        current=current,
        candidates=(target,),
        signals=neutral_signals(bw_in_allowance_exceeded=True),
    )
    assert blocked["recommendations"] == []  # NET-015
    assert (
        blocked["rejection_summary"][
            "NETWORK_ALLOWANCE_EVENTS_WITHOUT_CAPACITY_INCREASE"
        ]
        == 1
    )

    increased = run_recommendation_scenario(
        current=current,
        candidates=(catalog_entry("m6i.xlarge", 60, network_baseline_mbps=200),),
        signals=neutral_signals(bw_in_allowance_exceeded=True),
    )
    assert recommendation(increased)["classification"] == "CONDITIONAL"  # NET-016
    assert "NETWORK_ALLOWANCE_EXCEEDED" in recommendation(increased)["reason_codes"]

    for signal, warning in (
        ("pps_allowance_exceeded", "PPS_ALLOWANCE_EXCEEDED"),
        ("conntrack_allowance_exceeded", "CONNTRACK_ALLOWANCE_EXCEEDED"),
    ):
        signaled = run_recommendation_scenario(
            current=current,
            candidates=(catalog_entry("m6i.xlarge", 60, network_baseline_mbps=200),),
            signals=neutral_signals(**{signal: True}),
        )
        candidate = recommendation(signaled)
        assert candidate["network_evaluation"]["risk_level"] == "HIGH"  # NET-017
        assert warning in candidate["reason_codes"]

    missing_disabled = run_recommendation_scenario(
        current=current, candidates=(target,), signals={}
    )
    missing_enabled = run_recommendation_scenario(
        current=current,
        candidates=(target,),
        signals={},
        warning_policy=PerformanceWarningPolicy(
            require_network_allowance_metrics_for_actionable=True
        ),
    )
    assert recommendation(missing_disabled)["classification"] == "ACTIONABLE"  # NET-018
    assert recommendation(missing_enabled)["classification"] == "CONDITIONAL"
    assert (
        "NETWORK_ALLOWANCE_METRICS_REQUIRED"
        in recommendation(missing_enabled)["reason_codes"]
    )

    weighted_blocked = run_recommendation_scenario(
        current=current,
        candidates=(target,),
        metadata={"network_bandwidth_weighting": "vpc-1"},
        signals=neutral_signals(bw_out_allowance_exceeded=True),
    )
    assert weighted_blocked["recommendations"] == []  # NET-021
    assert (
        weighted_blocked["rejection_summary"][
            "NETWORK_ALLOWANCE_EVENTS_WITHOUT_CAPACITY_INCREASE"
        ]
        == 1
    )


def test_network_eni_and_efa_service_boundaries():
    current = catalog_entry("m5.xlarge", 100)
    target = catalog_entry("m6i.xlarge", 60, eni_limit=2)
    equal = run_recommendation_scenario(
        current=current, candidates=(target,), metadata={"attached_eni_count": 2}
    )
    exceeded = run_recommendation_scenario(
        current=current, candidates=(target,), metadata={"attached_eni_count": 3}
    )
    assert decision_projection(equal)["targets"] == ["m6i.xlarge"]  # NET-019
    assert exceeded["rejection_summary"]["TARGET_ENI_LIMIT_TOO_LOW"] == 1

    supported = run_recommendation_scenario(
        current=current,
        candidates=(catalog_entry("m6i.xlarge", 60, efa_supported=True),),
        metadata={"requires_efa": True},
    )
    unsupported = run_recommendation_scenario(
        current=current,
        candidates=(catalog_entry("m6i.xlarge", 60, efa_supported=False),),
        metadata={"requires_efa": True},
    )
    unknown = run_recommendation_scenario(
        current=current,
        candidates=(catalog_entry("m6i.xlarge", 60, efa_supported=None),),
        metadata={"requires_efa": True},
    )
    assert recommendation(supported)["classification"] == "ACTIONABLE"  # NET-020
    assert unsupported["rejection_summary"]["EFA_NOT_SUPPORTED"] == 1
    assert recommendation(unknown)["classification"] == "CONDITIONAL"


def test_ebs_support_attachment_and_not_applicable_paths():
    current = catalog_entry("m5.xlarge", 100)
    target = catalog_entry("m6i.xlarge", 60)
    no_volumes = run_recommendation_scenario(current=current, candidates=(target,))
    storage = recommendation(no_volumes)["storage_evaluation"]
    assert storage["hard_constraint_status"] == "NOT_APPLICABLE"  # EBS-001
    assert storage["warnings"] == ()

    unsupported = run_recommendation_scenario(
        current=current,
        candidates=(catalog_entry("m6i.xlarge", 60, ebs_supported=False),),
        attached_volumes=(volume(1),),
    )
    assert unsupported["recommendations"] == []  # EBS-002
    assert unsupported["rejection_summary"]["TARGET_DOES_NOT_SUPPORT_EBS"] == 1

    equal = run_recommendation_scenario(
        current=current,
        candidates=(catalog_entry("m6i.xlarge", 60, ebs_attachment_limit=2),),
        attached_volumes=(volume(2), volume(3)),
    )
    exceeded = run_recommendation_scenario(
        current=current,
        candidates=(catalog_entry("m6i.xlarge", 60, ebs_attachment_limit=1),),
        attached_volumes=(volume(4), volume(5)),
    )
    assert decision_projection(equal)["targets"] == ["m6i.xlarge"]  # EBS-003
    assert exceeded["rejection_summary"]["TARGET_EBS_ATTACHMENT_LIMIT_TOO_LOW"] == 1


@pytest.mark.parametrize(
    "ratio,classification,risk,warning",
    [
        pytest.param(0.399, "ACTIONABLE", "LOW", None, id="EBS-004"),
        pytest.param(
            0.40,
            "CONDITIONAL",
            "MEDIUM",
            "EBS_USAGE_REVIEW_REQUIRED",
            id="EBS-005-at-medium",
        ),
        pytest.param(
            0.70,
            "CONDITIONAL",
            "MEDIUM",
            "EBS_USAGE_REVIEW_REQUIRED",
            id="EBS-005-at-high",
        ),
        pytest.param(
            0.701,
            "CONDITIONAL",
            "HIGH",
            "HIGH_EBS_USAGE_REVIEW_REQUIRED",
            id="EBS-006",
        ),
    ],
)
def test_ebs_service_warning_boundaries(ratio, classification, risk, warning):
    result = run_recommendation_scenario(
        current=catalog_entry("m5.xlarge", 100),
        candidates=(catalog_entry("m6i.xlarge", 60),),
        normalized_metrics=neutral_metrics(
            ebs_combined_iops=ratio * 10_000, ebs_combined_mibps=1
        ),
        attached_volumes=(volume(10),),
    )
    candidate = recommendation(result)
    storage = candidate["storage_evaluation"]
    assert candidate["classification"] == classification
    assert storage["risk_level"] == risk
    if warning is None:
        assert "EBS_USAGE_REVIEW_REQUIRED" not in storage["warnings"]
    else:
        assert warning in storage["warnings"]


def test_ebs_maximum_and_provisioned_capacity_boundaries():
    current = catalog_entry("m5.xlarge", 100)
    target = catalog_entry("m6i.xlarge", 60)
    exact_observed = run_recommendation_scenario(
        current=current,
        candidates=(target,),
        normalized_metrics=neutral_metrics(
            ebs_combined_iops=20_000, ebs_combined_mibps=1_000
        ),
        attached_volumes=(volume(20),),
    )
    above_observed = run_recommendation_scenario(
        current=current,
        candidates=(target,),
        normalized_metrics=neutral_metrics(
            ebs_combined_iops=20_001, ebs_combined_mibps=1_001
        ),
        attached_volumes=(volume(21),),
    )
    assert decision_projection(exact_observed)["targets"] == ["m6i.xlarge"]  # EBS-007
    assert above_observed["recommendations"] == []  # EBS-008
    assert {
        "EBS_MAX_IOPS_EXCEEDED",
        "EBS_MAX_THROUGHPUT_EXCEEDED",
    } <= set(above_observed["rejection_summary"])

    exact_provisioned = run_recommendation_scenario(
        current=current,
        candidates=(target,),
        attached_volumes=(volume(22, iops=20_000, throughput=1_000),),
    )
    above_provisioned = run_recommendation_scenario(
        current=current,
        candidates=(target,),
        attached_volumes=(volume(23, iops=20_001, throughput=1_001),),
    )
    assert decision_projection(exact_provisioned)["targets"] == [
        "m6i.xlarge"
    ]  # EBS-009
    assert {
        "PROVISIONED_IOPS_UNSUPPORTED",
        "PROVISIONED_THROUGHPUT_UNSUPPORTED",
    } <= set(above_provisioned["rejection_summary"])


def test_ebs_unknown_signal_and_collection_policy_paths():
    current = catalog_entry("m5.xlarge", 100)
    unknown = run_recommendation_scenario(
        current=current,
        candidates=(
            catalog_entry(
                "m6i.xlarge",
                60,
                ebs_baseline_iops=None,
                ebs_baseline_throughput_mibps=None,
            ),
        ),
        attached_volumes=(volume(30),),
    )
    unknown_candidate = recommendation(unknown)
    assert unknown_candidate["classification"] == "CONDITIONAL"  # EBS-010
    assert "EBS_BASELINE_CAPABILITY_UNKNOWN" in unknown_candidate["reason_codes"]

    throttled = run_recommendation_scenario(
        current=current,
        candidates=(catalog_entry("m6i.xlarge", 60),),
        signals=neutral_signals(instance_ebs_iops_exceeded=True),
        attached_volumes=(volume(31),),
    )
    assert throttled["recommendations"] == []  # EBS-011
    assert (
        throttled["rejection_summary"]["EBS_EXCEEDED_EVENTS_WITHOUT_CAPACITY_INCREASE"]
        == 1
    )

    for signal, warning in (
        ("high_ebs_queue", "EBS_QUEUE_REVIEW_REQUIRED"),
        ("high_ebs_latency", "EBS_LATENCY_REVIEW_REQUIRED"),
        ("ebs_burst_balance_depleted", "EBS_BURST_BALANCE_DEPLETED"),
    ):
        signaled = run_recommendation_scenario(
            current=current,
            candidates=(catalog_entry("m6i.xlarge", 60),),
            signals=neutral_signals(**{signal: True}),
            attached_volumes=(volume(40 + len(warning)),),
        )
        candidate = recommendation(signaled)
        assert candidate["storage_evaluation"]["risk_level"] == "HIGH"  # EBS-012
        assert warning in candidate["reason_codes"]

    incomplete = run_recommendation_scenario(
        current=current,
        candidates=(catalog_entry("m6i.xlarge", 60),),
        normalized_metrics=neutral_metrics(
            ebs_combined_iops=200, ebs_combined_mibps=None
        ),
        signals=neutral_signals(ebs_directional_metrics_incomplete=True),
        attached_volumes=(volume(50),),
    )
    incomplete_candidate = recommendation(incomplete)
    assert (
        incomplete_candidate["storage_evaluation"]["evidence"][
            "observed_combined_iops_p99"
        ]
        == 200
    )  # EBS-013
    assert "EBS_DIRECTIONAL_METRICS_INCOMPLETE" in incomplete_candidate["reason_codes"]

    missing_disabled = run_recommendation_scenario(
        current=current,
        candidates=(catalog_entry("m6i.xlarge", 60),),
        signals={
            "bw_in_allowance_exceeded": False,
            "bw_out_allowance_exceeded": False,
            "pps_allowance_exceeded": False,
            "conntrack_allowance_exceeded": False,
        },
        attached_volumes=(volume(60),),
    )
    missing_enabled = run_recommendation_scenario(
        current=current,
        candidates=(catalog_entry("m6i.xlarge", 60),),
        signals={
            "bw_in_allowance_exceeded": False,
            "bw_out_allowance_exceeded": False,
            "pps_allowance_exceeded": False,
            "conntrack_allowance_exceeded": False,
        },
        warning_policy=PerformanceWarningPolicy(
            require_ebs_exceeded_metrics_for_actionable=True
        ),
        attached_volumes=(volume(61),),
    )
    assert recommendation(missing_disabled)["classification"] == "ACTIONABLE"  # EBS-014
    assert recommendation(missing_enabled)["classification"] == "CONDITIONAL"
    assert (
        "EBS_EXCEEDED_METRICS_MISSING"
        in recommendation(missing_enabled)["reason_codes"]
    )


@pytest.mark.parametrize(
    "metadata,reason",
    [
        pytest.param({"management_context": "asg"}, "MANAGED_BY_ASG", id="SCOPE-001"),
        pytest.param({"management_context": "ecs"}, "MANAGED_BY_ECS", id="SCOPE-002"),
        pytest.param(
            {"management_context": "kubernetes"},
            "MANAGED_BY_KUBERNETES",
            id="SCOPE-003",
        ),
        pytest.param({"tenancy": "dedicated"}, "UNSUPPORTED_TENANCY", id="SCOPE-004"),
        pytest.param({"tenancy": "host"}, "UNSUPPORTED_TENANCY", id="SCOPE-004-host"),
        pytest.param({"lifecycle": "spot"}, "UNSUPPORTED_LIFECYCLE", id="SCOPE-005"),
    ],
)
def test_scope_metadata_defers_before_candidate_generation(metadata, reason):
    result = run_recommendation_scenario(
        current=catalog_entry("m5.xlarge", 100),
        candidates=(catalog_entry("m6i.xlarge", 60),),
        metadata=metadata,
    )
    assert result["classification"] == "DEFERRED"
    assert result["deferred_reason_codes"] == [reason]
    assert result["recommendations"] == []
    assert "rejection_summary" not in result


def test_scope_instance_store_default_and_override_paths():
    present = run_recommendation_scenario(
        current=catalog_entry("m5.xlarge", 100),
        candidates=(catalog_entry("m6i.xlarge", 60),),
        metadata={"instance_store_present": True},
    )
    unresolved = run_recommendation_scenario(
        current=catalog_entry("m5.xlarge", 100, instance_store_device_count=None),
        candidates=(catalog_entry("m6i.xlarge", 60),),
        metadata={"instance_store_present": None},
    )
    for result in (present, unresolved):
        assert result["classification"] == "DEFERRED"  # SCOPE-006
        assert result["deferred_reason_codes"] == ["INSTANCE_STORE_USAGE_UNKNOWN"]
        assert result["recommendations"] == []

    absent = run_recommendation_scenario(
        current=catalog_entry("m5.xlarge", 100),
        candidates=(catalog_entry("m6i.xlarge", 60),),
        metadata={"instance_store_present": False},
    )
    assert decision_projection(absent)["targets"] == ["m6i.xlarge"]  # SCOPE-007

    overridden = run_recommendation_scenario(
        current=catalog_entry("m5.xlarge", 100, instance_store_device_count=None),
        candidates=(catalog_entry("m6i.xlarge", 60),),
        metadata={"instance_store_present": None},
        scope_policy=EC2ScopePolicy(allow_unknown_instance_store_usage=True),
    )
    candidate = recommendation(overridden)
    assert candidate["classification"] == "CONDITIONAL"  # SCOPE-008
    assert candidate["risk_assessment"]["compatibility"] == "HIGH"
    assert "INSTANCE_STORE_USAGE_UNKNOWN" in candidate["reason_codes"]


@pytest.mark.parametrize(
    "tier,ratio,expected",
    [
        pytest.param("conservative", 0.5499, True, id="TIER-001-below"),
        pytest.param("conservative", 0.55, True, id="TIER-001-equal"),
        pytest.param("conservative", 0.5501, False, id="TIER-001-above"),
        pytest.param("balanced", 0.6999, True, id="TIER-002-below"),
        pytest.param("balanced", 0.70, True, id="TIER-002-equal"),
        pytest.param("balanced", 0.7001, False, id="TIER-002-above"),
        pytest.param("aggressive", 0.8499, True, id="TIER-003-below"),
        pytest.param("aggressive", 0.85, True, id="TIER-003-equal"),
    ],
)
def test_each_tier_boundary_is_inclusive(tier, ratio, expected):
    candidate = {
        "target_instance_type": "m6i.large",
        "projected_cpu_util": ratio,
        "projected_memory_util": ratio,
        "projected_util": ratio,
        "performance_ratio": 1,
        "performance_change_pct": 0,
        "binding_dimension": "cpu",
        "monthly_savings": 10,
        "yearly_savings": 120,
        "classification": "ACTIONABLE",
        "risk_assessment": {},
    }
    tiers = _recommendation_tiers([candidate], EC2ComputePolicy())
    assert (tiers[tier] is not None) is expected


def test_aggressive_generation_boundary_and_tier_tie_breaking():
    current = catalog_entry("m5.xlarge", 100, coremark=100, memory_mib=16_000)
    target = catalog_entry("m6i.xlarge", 60, coremark=100, memory_mib=16_000)
    for cpu_percent, returned in ((84.99, True), (85.0, True), (85.01, False)):
        result = run_recommendation_scenario(
            current=current,
            candidates=(target,),
            normalized_metrics=neutral_metrics(
                cpu_percent=cpu_percent, memory_percent=5
            ),
        )
        assert bool(result["recommendations"]) is returned  # TIER-003
        if not returned:
            assert result["rejection_summary"]["CPU_REQUIREMENT_NOT_MET"] == 1

    tied = []
    for name in ("r6i.large", "c6i.large"):
        tied.append(
            {
                "target_instance_type": name,
                "projected_cpu_util": 0.5,
                "projected_memory_util": 0.5,
                "projected_util": 0.5,
                "performance_ratio": 1,
                "performance_change_pct": 0,
                "binding_dimension": "cpu",
                "monthly_savings": 10,
                "yearly_savings": 120,
                "classification": "ACTIONABLE",
                "risk_assessment": {"overall": "LOW"},
            }
        )
    tiers = _recommendation_tiers(tied, EC2ComputePolicy())
    assert tiers["conservative"]["target_instance_type"] == "c6i.large"  # TIER-009


def test_tier_option_data_matches_the_returned_candidate():
    result = run_recommendation_scenario(
        current=catalog_entry("m5.xlarge", 100),
        candidates=(
            catalog_entry("c6i.xlarge", 50),
            catalog_entry("m6i.xlarge", 60),
        ),
    )
    by_target = {
        item["target_instance_type"]: item for item in result["recommendations"]
    }
    for tier_name in ("conservative", "balanced", "aggressive"):
        option = result["tiers"][tier_name]
        if option is None:
            continue
        candidate = by_target[option["target_instance_type"]]
        for field in (
            "projected_cpu_util",
            "projected_memory_util",
            "projected_util",
            "monthly_savings",
            "yearly_savings",
            "classification",
            "risk_assessment",
            "performance_ratio",
            "performance_change_pct",
            "satisfied_tiers",
        ):
            assert option[field] == candidate[field]  # TIER-012


def test_complete_classification_contract():
    low_pass = ResourceEvaluation(HardConstraintStatus.PASS, RiskLevel.LOW)
    medium = ResourceEvaluation(HardConstraintStatus.PASS, RiskLevel.MEDIUM)
    high = ResourceEvaluation(HardConstraintStatus.PASS, RiskLevel.HIGH)
    unknown = ResourceEvaluation(HardConstraintStatus.UNKNOWN, RiskLevel.LOW)
    blocking = ResourceEvaluation(
        HardConstraintStatus.PASS, RiskLevel.LOW, ("REVIEW_REQUIRED",)
    )
    not_applicable = ResourceEvaluation(
        HardConstraintStatus.NOT_APPLICABLE, RiskLevel.LOW
    )

    assert (
        classify_recommendation(
            cpu_passes=True,
            memory_passes=True,
            network=low_pass,
            storage=not_applicable,
        ).value
        == "ACTIONABLE"
    )  # CLS-001
    assert (
        classify_recommendation(
            cpu_passes=True, memory_passes=True, network=medium, storage=low_pass
        ).value
        == "CONDITIONAL"
    )  # CLS-002
    assert (
        classify_recommendation(
            cpu_passes=True, memory_passes=True, network=low_pass, storage=medium
        ).value
        == "CONDITIONAL"
    )  # CLS-003
    for network, storage in ((high, low_pass), (low_pass, high)):
        assert (
            classify_recommendation(
                cpu_passes=True,
                memory_passes=True,
                network=network,
                storage=storage,
            ).value
            == "CONDITIONAL"
        )  # CLS-004
    assert (
        classify_recommendation(
            cpu_passes=True, memory_passes=True, network=unknown, storage=low_pass
        ).value
        == "CONDITIONAL"
    )  # CLS-005
    assert (
        classify_recommendation(
            cpu_passes=True, memory_passes=True, network=blocking, storage=low_pass
        ).value
        == "CONDITIONAL"
    )  # CLS-006

    cpu_missing = run_recommendation_scenario(
        current=catalog_entry("m5.xlarge", 100),
        candidates=(catalog_entry("m6i.xlarge", 60),),
        normalized_metrics=neutral_metrics(cpu_percent=None),
    )
    candidate = recommendation(cpu_missing)
    assert candidate["classification"] == "ACTIONABLE"  # CLS-007
    assert candidate["risk_assessment"]["telemetry"] == "HIGH"


def test_generated_recommendation_invariants():
    current = catalog_entry("m5.xlarge", 100)
    for cpu_percent in (10, 30, 50):
        result = run_recommendation_scenario(
            current=current,
            candidates=(
                catalog_entry("c6i.xlarge", 70),
                catalog_entry("m6i.xlarge", 80),
                catalog_entry("r6i.xlarge", 90),
                catalog_entry("m7g.xlarge", 50, architectures=("arm64",)),
                catalog_entry("g5.xlarge", 40),
            ),
            normalized_metrics=neutral_metrics(cpu_percent=cpu_percent),
            min_monthly_savings=5,
        )
        recommendations = result["recommendations"]
        assert all(item["monthly_savings"] >= 5 for item in recommendations)
        assert all(item["monthly_savings"] > 0 for item in recommendations)
        assert recommendations == sorted(
            recommendations,
            key=lambda item: (
                -item["monthly_savings"],
                item["target_instance_type"],
            ),
        )
        assert not {"m7g.xlarge", "g5.xlarge"} & {
            item["target_instance_type"] for item in recommendations
        }
        returned = {item["target_instance_type"] for item in recommendations}
        assert all(
            option is None or option["target_instance_type"] in returned
            for name, option in result["tiers"].items()
            if name != "default"
        )
        for item in recommendations:
            satisfied = set(item["satisfied_tiers"])
            if "conservative" in satisfied:
                assert {"balanced", "aggressive"} <= satisfied
            if "balanced" in satisfied:
                assert "aggressive" in satisfied

    outcomes = []
    for demand in (20, 40, 80, 1_000):
        result = run_recommendation_scenario(
            current=current,
            candidates=(catalog_entry("m6i.xlarge", 60),),
            normalized_metrics=neutral_metrics(network_in_mbps=demand),
        )
        outcomes.append(
            "REJECTED"
            if not result["recommendations"]
            else recommendation(result)["classification"]
        )
    order = {"ACTIONABLE": 2, "CONDITIONAL": 1, "REJECTED": 0}
    assert [order[item] for item in outcomes] == sorted(
        (order[item] for item in outcomes), reverse=True
    )

    lower_capacity = run_recommendation_scenario(
        current=current,
        candidates=(catalog_entry("m6i.xlarge", 60, network_reliable_max_mbps=700),),
        normalized_metrics=neutral_metrics(network_in_mbps=800),
    )
    higher_capacity = run_recommendation_scenario(
        current=current,
        candidates=(catalog_entry("m6i.xlarge", 60, network_reliable_max_mbps=900),),
        normalized_metrics=neutral_metrics(network_in_mbps=800),
    )
    assert lower_capacity["recommendations"] == []
    assert decision_projection(higher_capacity)["targets"] == ["m6i.xlarge"]


def test_mock_ui_has_three_catalog_backed_instances_per_behavior_group():
    backend_root = Path(__file__).parents[1]
    html = (backend_root / "rightsizers/ec2/ec2_rightsizer_ui_mock.html").read_text(encoding="utf-8")
    group_source = re.search(
        r"const INSTANCE_GROUPS = \[(.*?)\n\];\n\nfunction cloneMock",
        html,
        re.DOTALL,
    )
    assert group_source is not None
    source = group_source.group(1)
    assert source.count("templateId:") == 6
    assert source.count("variants: [") == 6
    assert source.count("resourceId:") == 12
    assert "18 instances evaluated · 6 behavior groups" in html

    instance_types = set(re.findall(r'(?:currentType|targetType): "([^"]+)"', html))
    assert len(instance_types) >= 18
    database_path = backend_root / "maxops_pricing.db"
    assert database_path.is_file(), (
        f"Packaged pricing database is missing at {database_path}. "
        "From maxops-backend, run "
        "'./.venv/bin/python staging_pricing/unpack_pricing_db.py' before this test."
    )
    with sqlite3.connect(database_path) as connection:
        placeholders = ",".join("?" for _ in instance_types)
        catalog_types = {
            row[0]
            for row in connection.execute(
                f"SELECT instance_type FROM ec2_instance_specs "
                f"WHERE instance_type IN ({placeholders})",
                sorted(instance_types),
            )
        }
    assert catalog_types == instance_types

    catalog_source = re.search(
        r"const MOCK_CATALOG = \{(.*?)\n\};\n\nconst INSTANCE_GROUPS",
        html,
        re.DOTALL,
    )
    assert catalog_source is not None
    ui_catalog = {
        instance_type: json.loads(values)
        for instance_type, values in re.findall(
            r'^\s+"([^"]+)": (\[[^\n]+\]),$',
            catalog_source.group(1),
            re.MULTILINE,
        )
    }
    assert set(ui_catalog) == instance_types

    packaged_catalog = EC2InstanceCatalog(database_path)
    for instance_type, values in ui_catalog.items():
        entry = packaged_catalog.get("us-east-1", instance_type, "linux")
        assert entry is not None
        expected = [
            entry.vcpus,
            entry.memory_mib / 1024,
            entry.architectures[0],
            round(entry.monthly_usd, 2),
            entry.network_baseline_mbps,
            entry.network_reliable_max_mbps,
            entry.ebs_baseline_iops,
            entry.ebs_baseline_throughput_mibps,
            entry.instance_store_device_count,
        ]
        assert values == expected, instance_type


def _scenario_map(ids, node):
    return {scenario_id: node for scenario_id in ids}


SCENARIO_IMPLEMENTATION = {
    **_scenario_map(
        ("REC-001", "REC-002", "REC-003", "REC-004", "REC-012"),
        "test_ec2_rightsizer_scenarios.py::test_base_pricing_savings_and_catalog_boundaries",
    ),
    **_scenario_map(
        ("REC-005", "REC-006", "REC-008", "REC-011"),
        "test_ec2_rightsizer_scenarios.py::test_service_selection_ranking_architecture_and_family_gates",
    ),
    **_scenario_map(
        ("REC-007", "REC-009", "REC-010", "REC-014", "REC-017"),
        "test_ec2_rightsizer_scenarios.py::test_architecture_family_policy_limit_and_rejection_boundaries",
    ),
    "REC-013": "test_ec2_rightsizer_v1.py::test_candidate_limit_reserves_balanced_and_nested_aggressive_target",
    "REC-015": "test_ec2_rightsizer_v1.py::test_limit_pressure_cannot_displace_sole_balanced_qualifier",
    "REC-016": "test_ec2_rightsizer_v1.py::test_decimal_safe_one_cent_savings_floor_and_minimum_equality",
    **_scenario_map(
        (
            "CMP-001",
            "CMP-002",
            "CMP-003",
            "CMP-004",
            "CMP-005",
            "CMP-006",
            "CMP-007",
            "CMP-011",
            "CMP-012",
        ),
        "test_ec2_rightsizer_scenarios.py::test_compute_projection_memory_and_performance_contracts",
    ),
    "CMP-008": "test_ec2_rightsizer_v1.py::test_missing_cpu_and_memory_keeps_floor_candidate_without_tiers",
    "CMP-009": "test_ec2_rightsizer_v1.py::test_missing_cpu_retains_current_capacity_without_affecting_tier_projection",
    "CMP-010": "test_ec2_rightsizer_v1.py::test_positive_performance_change_is_carried_by_candidate_and_tier_option",
    **_scenario_map(
        tuple(f"NET-{number:03d}" for number in range(1, 7)),
        "test_ec2_rightsizer_scenarios.py::test_network_service_warning_and_baseline_boundaries",
    ),
    **_scenario_map(
        tuple(f"NET-{number:03d}" for number in range(7, 10)),
        "test_ec2_rightsizer_scenarios.py::test_network_reliable_peak_boundaries",
    ),
    **_scenario_map(
        tuple(f"NET-{number:03d}" for number in range(10, 14)),
        "test_ec2_rightsizer_scenarios.py::test_network_assumed_unknown_and_directional_paths",
    ),
    **_scenario_map(
        ("NET-014", "NET-015", "NET-016", "NET-017", "NET-018", "NET-021"),
        "test_ec2_rightsizer_scenarios.py::test_network_weighting_allowance_and_operational_signal_paths",
    ),
    **_scenario_map(
        ("NET-019", "NET-020"),
        "test_ec2_rightsizer_scenarios.py::test_network_eni_and_efa_service_boundaries",
    ),
    **_scenario_map(
        ("EBS-001", "EBS-002", "EBS-003"),
        "test_ec2_rightsizer_scenarios.py::test_ebs_support_attachment_and_not_applicable_paths",
    ),
    **_scenario_map(
        ("EBS-004", "EBS-005", "EBS-006"),
        "test_ec2_rightsizer_scenarios.py::test_ebs_service_warning_boundaries",
    ),
    **_scenario_map(
        ("EBS-007", "EBS-008", "EBS-009"),
        "test_ec2_rightsizer_scenarios.py::test_ebs_maximum_and_provisioned_capacity_boundaries",
    ),
    **_scenario_map(
        tuple(f"EBS-{number:03d}" for number in range(10, 15)),
        "test_ec2_rightsizer_scenarios.py::test_ebs_unknown_signal_and_collection_policy_paths",
    ),
    **_scenario_map(
        tuple(f"SCOPE-{number:03d}" for number in range(1, 6)),
        "test_ec2_rightsizer_scenarios.py::test_scope_metadata_defers_before_candidate_generation",
    ),
    **_scenario_map(
        ("SCOPE-006", "SCOPE-007", "SCOPE-008"),
        "test_ec2_rightsizer_scenarios.py::test_scope_instance_store_default_and_override_paths",
    ),
    **_scenario_map(
        ("TIER-001", "TIER-002"),
        "test_ec2_rightsizer_scenarios.py::test_each_tier_boundary_is_inclusive",
    ),
    "TIER-003": "test_ec2_rightsizer_scenarios.py::test_aggressive_generation_boundary_and_tier_tie_breaking",
    **_scenario_map(
        ("TIER-004", "TIER-008"),
        "test_ec2_rightsizer_v1.py::test_tiered_recommendations_select_three_distinct_targets_and_risks",
    ),
    **_scenario_map(
        ("TIER-005", "TIER-006"),
        "test_ec2_rightsizer_v1.py::test_tiers_collapse_shared_targets_and_allow_missing_conservative",
    ),
    "TIER-007": "test_ec2_rightsizer_v1.py::test_missing_cpu_and_memory_keeps_floor_candidate_without_tiers",
    "TIER-009": "test_ec2_rightsizer_scenarios.py::test_aggressive_generation_boundary_and_tier_tie_breaking",
    "TIER-010": "test_ec2_rightsizer_v1.py::test_balanced_matches_pre_tiering_with_memory_present_and_absent",
    "TIER-011": "test_ec2_rightsizer_v1.py::test_projected_cpu_falls_back_to_vcpu_and_binding_uses_highest_ratio",
    "TIER-012": "test_ec2_rightsizer_scenarios.py::test_tier_option_data_matches_the_returned_candidate",
    **_scenario_map(
        tuple(f"CLS-{number:03d}" for number in range(1, 8)),
        "test_ec2_rightsizer_scenarios.py::test_complete_classification_contract",
    ),
    "TEL-001": "test_ec2_rightsizer_scenarios.py::test_thin_telemetry_is_disclosure_only",
    "MOD-001": "test_ec2_rightsizer_scenarios.py::test_burst_only_ebs_warning_companion_boundary",
    **_scenario_map(
        ("PRV-001", "PRV-002"),
        "test_ec2_rightsizer_v1.py::test_memory_preview_keeps_rejection_and_checks_other_hard_constraints",
    ),
    "PRV-003": "test_ec2_rightsizer_v1.py::test_memory_preview_is_not_emitted_when_memory_metric_is_present",
    "PRV-004": "test_ec2_rightsizer_v1.py::test_graviton_preview_retains_raw_capacity_and_hides_coremark_evidence",
    **_scenario_map(
        ("PRV-005", "PRV-006"),
        "test_ec2_rightsizer_v1.py::test_graviton_preview_rejects_capacity_reduction_family_gate_and_hard_failures",
    ),
    **_scenario_map(
        ("PRV-007", "PRV-008"),
        "test_ec2_rightsizer_v1.py::test_previews_choose_one_per_kind_and_never_change_recommendations",
    ),
}


def test_scenario_traceability_manifest_matches_authoritative_plan():
    documented = set(
        re.findall(
            r"^\| `([A-Z]+-[0-9]+)` \|",
            (
                Path(__file__).parents[1]
                / "rightsizers/ec2/ec2_rightsizer_test_plan.md"
            ).read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
    expected = {
        *(f"REC-{number:03d}" for number in range(1, 18)),
        *(f"CMP-{number:03d}" for number in range(1, 13)),
        *(f"NET-{number:03d}" for number in range(1, 22)),
        *(f"EBS-{number:03d}" for number in range(1, 15)),
        *(f"SCOPE-{number:03d}" for number in range(1, 9)),
        *(f"TIER-{number:03d}" for number in range(1, 13)),
        *(f"CLS-{number:03d}" for number in range(1, 8)),
        "TEL-001",
        "MOD-001",
        *(f"PRV-{number:03d}" for number in range(1, 9)),
    }
    assert documented == expected
    assert set(SCENARIO_IMPLEMENTATION) == documented

    function_names_by_file = {}
    for node_id in set(SCENARIO_IMPLEMENTATION.values()):
        filename, function_name = node_id.split("::", 1)
        if filename not in function_names_by_file:
            tree = ast.parse((Path(__file__).parent / filename).read_text(encoding="utf-8"))
            function_names_by_file[filename] = {
                node.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
        assert function_name in function_names_by_file[filename], node_id
