from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

import pytest

from app.services.asg_rightsizer import (
    TARGET_INDEPENDENT_DEMAND_SCHEMA as SERVICE_DEMAND_SCHEMA,
)
from app.services.ec2_instance_catalog import EC2CatalogEntry
from rightsizers.asg.asg_rightsizer.models import ASGCapacityPolicy
from rightsizers.asg.asg_rightsizer.normalization import (
    TARGET_INDEPENDENT_DEMAND_SCHEMA as NORMALIZATION_DEMAND_SCHEMA,
)
from rightsizers.common.ec2_candidates import EC2CandidatePolicy
from tests.asg_rightsizer_helpers import catalog, metadata, run


class StaticCatalog:
    def __init__(self, entries: list[EC2CatalogEntry]) -> None:
        self.entries = {entry.instance_type: entry for entry in entries}

    def list_region(
        self, region: str, platform: str = "linux"
    ) -> dict[str, EC2CatalogEntry]:
        return dict(self.entries)

    def get(
        self, region: str, instance_type: str, platform: str = "linux"
    ) -> EC2CatalogEntry | None:
        return self.entries.get(instance_type)

    @staticmethod
    def specification_metadata() -> dict[str, str]:
        return {
            "schema_version": "ec2-instance-spec-v2",
            "generated_at": "2026-07-15T00:00:00+00:00",
            "source_region": "us-east-1",
        }


_UNSET = object()


@lru_cache(maxsize=1)
def _real_entries() -> dict[str, EC2CatalogEntry]:
    return catalog().list_region("us-east-1", "linux")


@lru_cache(maxsize=None)
def _real_entry(instance_type: str) -> EC2CatalogEntry:
    entry = _real_entries().get(instance_type)
    assert entry is not None, f"{instance_type} must exist in the packaged EC2 catalog"
    return entry


def shape(
    instance_type: str,
    *,
    monthly: float,
    vcpus: float | None | object = _UNSET,
    memory_mib: float | None | object = _UNSET,
    coremark: float | None | object = _UNSET,
    architectures: tuple[str, ...] | object = _UNSET,
    capability_source: str | object = _UNSET,
) -> EC2CatalogEntry:
    real = _real_entry(instance_type)
    attributes = {
        **real.attributes,
        "test_fixture": "REAL_CATALOG_PRICE_OVERRIDE",
        "catalog_monthly_usd": real.monthly_usd,
    }
    return replace(
        real,
        monthly_usd=monthly,
        vcpus=real.vcpus if vcpus is _UNSET else vcpus,
        memory_mib=real.memory_mib if memory_mib is _UNSET else memory_mib,
        coremark=real.coremark if coremark is _UNSET else coremark,
        architectures=(
            real.architectures if architectures is _UNSET else architectures
        ),
        capability_source=(
            real.capability_source if capability_source is _UNSET else capability_source
        ),
        attributes=attributes,
    )


def demand(
    cpu_low: float | None,
    cpu_high: float | None,
    *,
    memory_low: float | None = None,
    memory_high: float | None = None,
    high_points: int = 2,
) -> dict[str, Any]:
    end = datetime(2026, 7, 15, tzinfo=timezone.utc)
    points = []
    for index in range(100):
        high = index >= 100 - high_points
        points.append(
            {
                "timestamp": (end - timedelta(minutes=5 * (99 - index))).isoformat(),
                "cpu_used_instance_equivalents": cpu_high if high else cpu_low,
                "memory_used_instance_equivalents": (
                    memory_high if high else memory_low
                ),
            }
        )
    return {
        "normalization_version": "asg-v2-target-independent-demand",
        "period_seconds": 300,
        "start": points[0]["timestamp"],
        "end": points[-1]["timestamp"],
        "points": points,
    }


def v2_run(
    entries: list[EC2CatalogEntry],
    demand_payload: dict[str, Any],
    *,
    metadata_override: dict[str, Any] | None = None,
    current_instance_type: str = "m6i.large",
    current_min: int = 6,
    current_desired: int = 10,
    current_max: int = 20,
    min_monthly_savings: float = 0.0,
    candidate_limit: int | None = None,
    capacity_policy: ASGCapacityPolicy | None = None,
    candidate_policy: EC2CandidatePolicy | None = None,
    deployment_enabled: bool = True,
    coremark_coverage_override: float | None = 1.0,
) -> dict[str, Any]:
    overrides = dict(metadata_override or {})
    overrides["rightsizing_demand"] = demand_payload
    kwargs: dict[str, Any] = {
        "deployment_enabled": deployment_enabled,
        "coremark_coverage_override": coremark_coverage_override,
    }
    if capacity_policy is not None:
        kwargs["capacity_policy"] = capacity_policy
    if candidate_policy is not None:
        kwargs["candidate_policy"] = candidate_policy
    result, db, row = run(
        metadata_override=overrides,
        current_min=current_min,
        current_desired=current_desired,
        current_max=current_max,
        current_instance_type=current_instance_type,
        min_monthly_savings=min_monthly_savings,
        service_kwargs=kwargs,
        catalog_override=StaticCatalog(entries),
    )
    if candidate_limit is not None:
        from app.services.asg_rightsizer import ASGRightsizer

        result = ASGRightsizer(
            db,
            catalog=StaticCatalog(entries),
            **kwargs,
        ).get_recommendation(
            row.inventory_id,
            min_monthly_savings=min_monthly_savings,
            candidate_limit=candidate_limit,
        )
        assert result is not None
    return result


def _configuration(result: dict[str, Any], instance_type: str, tier: str):
    for option in result["recommendations"]:
        if (
            option["target_instance_type"] == instance_type
            and tier in option["satisfied_tiers"]
        ):
            return option
    raise AssertionError(f"missing {tier} option for {instance_type}")


def _missing_memory() -> dict[str, Any]:
    telemetry = deepcopy(metadata()["telemetry_summary"])
    telemetry["memory_percent"].update(
        present=False,
        observed_days=0.0,
        thin_data=True,
        pairing_ratio=None,
        source=None,
        status="unavailable",
    )
    return telemetry


def test_v2_demand_schema_constant_is_shared_by_service_and_normalization():
    assert SERVICE_DEMAND_SCHEMA == NORMALIZATION_DEMAND_SCHEMA
    assert SERVICE_DEMAND_SCHEMA == "asg-v2-target-independent-demand"


@pytest.mark.parametrize(
    "coremark,expected_basis",
    [
        (2.0, "coremark"),
        (None, "vcpu_fallback"),
    ],
)
def test_exact_tier_boundary_does_not_overcount_required_instances(
    coremark, expected_basis
):
    current = shape(
        "m6i.large",
        monthly=80,
        vcpus=2.0,
        memory_mib=2.0,
        coremark=coremark,
    )
    result = v2_run(
        [current],
        demand(10.5, 10.5, memory_low=10.5, memory_high=10.5),
        current_min=20,
        current_desired=20,
        current_max=30,
    )
    balanced = result["tiers"]["balanced"]
    assert balanced["cpu_capacity_basis"] == expected_basis
    assert balanced["target_desired_capacity"] == 15
    assert balanced["evidence"]["desired_record"] == {
        "timestamp": balanced["evidence"]["desired_record"]["timestamp"],
        "required": 15,
        "binding_dimension": "cpu_and_memory",
        "cpu_used_capacity": 21.0,
        "memory_used_mib": 21.0,
    }


def test_current_type_v2_matches_v1_tier_counts_and_evidence():
    current = shape("m6i.large", monthly=80)
    result = v2_run(
        [current],
        demand(2.0, 4.5, memory_low=2.0, memory_high=4.5),
    )

    expected = {
        "conservative": (4, 9, 0.55),
        "balanced": (3, 7, 0.70),
        "aggressive": (3, 6, 0.85),
    }
    for tier, (minimum, desired, ratio) in expected.items():
        option = result["tiers"][tier]
        assert option["target_instance_type"] == "m6i.large"
        assert (option["target_min_size"], option["target_desired_capacity"]) == (
            minimum,
            desired,
        )
        assert option["evidence"]["tier_ratio"] == ratio
        assert option["optimization_kind"] == "CAPACITY_ONLY"
    assert result["classification"] == "ACTIONABLE"


def test_combined_option_uses_total_configuration_economics():
    current = shape("m6i.large", monthly=80)
    target = shape(
        "m6i.xlarge",
        monthly=150,
    )
    result = v2_run(
        [current, target],
        demand(1.0, 4.2, memory_low=1.0, memory_high=4.0),
    )

    option = next(
        item
        for item in result["recommendations"]
        if item["target_instance_type"] == "m6i.xlarge"
    )
    assert option["optimization_kind"] == "COMBINED"
    assert option["target_unit_monthly_price"] == 150
    assert option["target_desired_capacity"] < 10
    assert option["monthly_savings"] == round(800 - option["target_monthly_cost"], 2)
    assert option["monthly_savings"] > 0
    assert option["classification"] == "CONDITIONAL"


def test_instance_only_and_changed_type_contract():
    current = shape("m6i.large", monthly=80)
    target = shape("m7i.large", monthly=70)
    result = v2_run(
        [current, target],
        demand(9.0, 9.0, memory_low=0.0, memory_high=0.0),
    )

    option = next(
        item
        for item in result["recommendations"]
        if item["target_instance_type"] == "m7i.large"
        and item["target_desired_capacity"] == 10
    )
    assert option["optimization_kind"] == "INSTANCE_ONLY"
    assert option["family_changed"] is True
    assert option["classification"] == "CONDITIONAL"
    assert (
        "INSTANCE_TYPE_CHANGE_NETWORK_STORAGE_VALIDATION_REQUIRED"
        in option["reason_codes"]
    )
    assert option["risk_assessment"]["network"] is None
    assert option["risk_assessment"]["storage"] is None
    assert option["risk_assessment"]["compatibility"] == "MEDIUM"


def test_candidate_architecture_family_metal_and_capability_gates():
    current = shape("m6i.large", monthly=80)
    eligible = shape("m7i.large", monthly=60)
    missing_arch = shape("m6a.large", monthly=60, architectures=())
    arm = shape("m7g.large", monthly=60)
    burstable = shape("t3a.large", monthly=50)
    metal = shape("m6i.metal", monthly=40)
    unknown_cpu = shape("r7i.large", monthly=50, vcpus=None, coremark=None)
    result = v2_run(
        [current, eligible, missing_arch, arm, burstable, metal, unknown_cpu],
        demand(2.0, 4.5, memory_low=2.0, memory_high=4.5),
    )

    returned = {item["target_instance_type"] for item in result["recommendations"]}
    assert "m7i.large" in returned
    assert (
        not {"m6a.large", "m7g.large", "t3a.large", "m6i.metal", "r7i.large"} & returned
    )
    assert result["rejection_summary"]["ARCHITECTURE_INCOMPATIBLE"] >= 2
    assert result["rejection_summary"]["FAMILY_NOT_ELIGIBLE"] >= 1
    assert result["rejection_summary"]["CPU_CAPABILITY_UNKNOWN"] >= 1


def test_target_without_authoritative_spec_is_skipped_and_tallied():
    current = shape("m6i.large", monthly=80)
    missing = shape("cr1.8xlarge", monthly=20)
    result = v2_run(
        [current, missing],
        demand(2.0, 4.5, memory_low=2.0, memory_high=4.5),
    )
    assert all(
        item["target_instance_type"] != "cr1.8xlarge"
        for item in result["recommendations"]
    )
    assert result["rejection_summary"]["TARGET_INSTANCE_PRICING_OR_SPEC_MISSING"] == 1


def test_same_gated_family_is_eligible_when_architecture_overlaps():
    current = shape("t3.large", monthly=80)
    target = shape("t3a.large", monthly=60)
    result = v2_run(
        [current, target],
        demand(2.0, 4.5, memory_low=2.0, memory_high=4.5),
        current_instance_type="t3.large",
    )
    assert any(
        item["target_instance_type"] == "t3a.large"
        for item in result["recommendations"]
    )


def test_vcpu_fallback_is_consistent_and_performance_is_unavailable():
    current = shape("m6i.large", monthly=80)
    target = shape("c5ad.xlarge", monthly=120)
    result = v2_run(
        [current, target],
        demand(1.0, 4.2, memory_low=1.0, memory_high=4.0),
    )
    option = next(
        item
        for item in result["recommendations"]
        if item["target_instance_type"] == "c5ad.xlarge"
    )
    assert option["cpu_capacity_basis"] == "vcpu_fallback"
    assert option["performance_ratio"] is None
    assert option["performance_change_pct"] is None


def test_changed_smaller_target_overflow_is_tallied_while_larger_survives():
    current = shape("m6i.xlarge", monthly=80)
    smaller = shape("m6i.large", monthly=45)
    larger = shape("m6i.2xlarge", monthly=150)
    result = v2_run(
        [current, smaller, larger],
        demand(2.0, 4.2, memory_low=2.0, memory_high=4.2),
        current_instance_type="m6i.xlarge",
    )
    assert result["rejection_summary"]["TARGET_REQUIRES_CAPACITY_INCREASE"] >= 1
    assert any(
        item["target_instance_type"] == "m6i.2xlarge"
        for item in result["recommendations"]
    )
    assert result["blocking_reasons"] == []


def test_current_target_overflow_without_alternative_uses_top_level_disclosure():
    current = shape("m6i.large", monthly=80)
    result = v2_run(
        [current],
        demand(8.0, 8.0, memory_low=8.0, memory_high=8.0),
    )
    assert result["recommendations"] == []
    assert result["blocking_reasons"] == ["CURRENT_CAPACITY_NOT_OVERPROVISIONED"]
    assert result["rejection_summary"]["TARGET_REQUIRES_CAPACITY_INCREASE"] >= 1


def test_v1_telemetry_schema_does_not_activate_instance_optimization(caplog):
    current = shape("m6i.large", monthly=80)
    caplog.set_level("INFO", logger="uvicorn.error")
    result, _, _ = run(
        service_kwargs={
            "deployment_enabled": True,
            "coremark_coverage_override": 1.0,
        },
        catalog_override=StaticCatalog([current]),
    )
    assert "candidate_policy" not in result
    assert all(
        item.get("target_instance_type") is None for item in result["recommendations"]
    )
    assert "ASG_INSTANCE_OPTIMIZATION_TELEMETRY_UPGRADE_REQUIRED" in caplog.text


def test_decimal_total_savings_floor_and_request_equality_are_inclusive():
    current = shape("m6i.large", monthly=1.00)
    target = shape("m7i.large", monthly=0.999)
    payload = demand(8.5, 8.5, memory_low=8.5, memory_high=8.5)
    below = v2_run([current, target], payload, min_monthly_savings=0.009)
    equal = v2_run([current, target], payload, min_monthly_savings=0.01)
    above = v2_run([current, target], payload, min_monthly_savings=0.0101)

    def exact_option(result):
        return next(
            (
                item
                for item in result["recommendations"]
                if item["target_instance_type"] == "m7i.large"
                and item["target_desired_capacity"] == 10
            ),
            None,
        )

    assert exact_option(below)["monthly_savings"] == 0.01
    assert exact_option(equal)["monthly_savings"] == 0.01
    assert exact_option(above) is None


def test_missing_memory_capacity_floor_separates_normal_and_preview_options():
    current = shape("m6i.large", monthly=80)
    retained = shape("m6i.xlarge", monthly=100)
    reduced = shape("c7i.xlarge", monthly=90)
    result = v2_run(
        [current, retained, reduced],
        demand(2.0, 7.0),
        metadata_override={"telemetry_summary": _missing_memory()},
    )

    retained_option = next(
        item
        for item in result["recommendations"]
        if item["target_instance_type"] == "m6i.xlarge"
    )
    assert retained_option["projected_memory_util"] is None
    assert retained_option["binding_dimension"] == "cpu"
    assert (
        "MEMORY_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED"
        in retained_option["reason_codes"]
    )
    assert retained_option["classification"] == "CONDITIONAL"
    preview = next(
        item
        for item in result["savings_previews"]
        if item["kind"] == "MEMORY_METRIC_MISSING_CAPACITY_REDUCTION"
    )
    assert preview["classification"] == "PREVIEW"
    assert preview["target_instance_type"] in {"m6i.large", "c7i.xlarge"}
    assert all(
        item["target_instance_type"] != "c7i.xlarge"
        for item in result["recommendations"]
    )


@pytest.mark.parametrize(
    "target_type,low,high,expected_configuration",
    [
        ("c5ad.xlarge", 2.0, 13.0, (3, 10)),  # desired passes, minimum fails
        ("m6i.xlarge", 2.0, 4.5, (3, 4)),  # minimum passes, desired fails
    ],
)
def test_missing_memory_requires_both_aggregate_floors(
    target_type: str,
    low: float,
    high: float,
    expected_configuration: tuple[int, int],
):
    current = shape("m6i.large", monthly=80)
    target = shape(target_type, monthly=50)
    result = v2_run(
        [current, target],
        demand(low, high),
        metadata_override={"telemetry_summary": _missing_memory()},
    )
    assert all(
        (
            item["target_instance_type"],
            item["target_min_size"],
            item["target_desired_capacity"],
        )
        != (target_type, *expected_configuration)
        for item in result["recommendations"]
    )
    preview = next(
        item
        for item in result["savings_previews"]
        if item["kind"] == "MEMORY_METRIC_MISSING_CAPACITY_REDUCTION"
    )
    assert any(
        option
        and (option["target_min_size"], option["target_desired_capacity"])
        == expected_configuration
        for tier, option in preview["options"].items()
        if tier != "default"
    )
    assert result["rejection_summary"]["MEMORY_REQUIREMENT_NOT_MET"] >= 1


def test_graviton_preview_retains_raw_capacity_and_nulls_cross_arch_cpu_evidence():
    current = shape("m6i.large", monthly=80)
    arm = shape(
        "m7g.2xlarge",
        monthly=200,
    )
    result = v2_run(
        [current, arm],
        demand(2.0, 7.0, memory_low=2.0, memory_high=7.0),
    )
    preview = next(
        item
        for item in result["savings_previews"]
        if item["kind"] == "GRAVITON_MIGRATION"
    )
    assert preview["classification"] == "OPPORTUNITY"
    option = preview["options"]["balanced"]
    assert option["target_instance_type"] == "m7g.2xlarge"
    assert option["projected_cpu_util"] is None
    assert option["performance_ratio"] is None
    assert option["performance_change_pct"] is None
    assert option["risk_assessment"]["network"] is None
    assert option["risk_assessment"]["storage"] is None


def test_preview_flags_remove_only_their_preview_kinds():
    current = shape("m6i.large", monthly=80)
    arm = shape(
        "m7g.2xlarge",
        monthly=200,
    )
    payload = demand(2.0, 7.0)
    enabled = v2_run(
        [current, arm],
        payload,
        metadata_override={"telemetry_summary": _missing_memory()},
    )
    disabled = v2_run(
        [current, arm],
        payload,
        metadata_override={"telemetry_summary": _missing_memory()},
        candidate_policy=EC2CandidatePolicy(
            memory_preview_enabled=False,
            graviton_preview_enabled=False,
        ),
    )
    assert enabled["savings_previews"]
    assert disabled["savings_previews"] == []
    for key in ("recommendations", "tiers", "rejection_summary"):
        assert disabled[key] == enabled[key]


def test_rollout_switch_precedence_and_coremark_coverage_boundary():
    current = shape("m6i.large", monthly=80)
    payload = demand(2.0, 4.5, memory_low=2.0, memory_high=4.5)
    policy_off = replace(ASGCapacityPolicy(), instance_optimization_enabled=False)

    deployment_off = v2_run([current], payload, deployment_enabled=False)
    policy_disabled = v2_run([current], payload, capacity_policy=policy_off)
    below = v2_run([current], payload, coremark_coverage_override=0.6999)
    equal = v2_run([current], payload, coremark_coverage_override=0.70)
    above = v2_run([current], payload, coremark_coverage_override=0.7001)

    assert "candidate_policy" not in deployment_off
    assert "candidate_policy" not in policy_disabled
    assert "candidate_policy" not in below
    for flag_off in (deployment_off, policy_disabled, below):
        assert flag_off["capacity_policy"]["policy_version"] == "asg-v1-balanced"
        assert "candidate_limit" not in flag_off["capacity_policy"]
        assert "instance_optimization_enabled" not in flag_off["capacity_policy"]
    assert equal["pricing_evidence"]["coremark_catalog_coverage"]["passed"] is True
    assert above["pricing_evidence"]["coremark_catalog_coverage"]["passed"] is True
    assert equal["recommendations"]
    assert above["recommendations"]
    assert equal["pricing_evidence"]["coremark_catalog_coverage"] == {
        "numerator": 7000,
        "denominator": 10000,
        "ratio": 0.70,
        "minimum_ratio": 0.70,
        "passed": True,
        "region": "us-east-1",
        "platform": "linux",
        "catalog_schema_version": "ec2-instance-spec-v2",
        "catalog_generated_at": "2026-07-15T00:00:00+00:00",
        "specification_source_region": "us-east-1",
    }


def test_coverage_gate_precedes_scope_envelope_selection():
    current = shape("m6i.large", monthly=80)
    payload = demand(2.0, 4.5, memory_low=2.0, memory_high=4.5)
    below = v2_run(
        [current],
        payload,
        metadata_override={"warm_pool_present": True},
        coremark_coverage_override=0.6999,
    )
    passing = v2_run(
        [current],
        payload,
        metadata_override={"warm_pool_present": True},
        coremark_coverage_override=0.70,
    )

    assert below["classification"] == passing["classification"] == "DEFERRED"
    assert "candidate_policy" not in below
    assert below["capacity_policy"]["policy_version"] == "asg-v1-balanced"
    assert passing["candidate_policy"]
    assert passing["capacity_policy"]["policy_version"] == "asg-v2-combined"


def test_empty_coremark_catalog_fails_closed_without_division_by_zero():
    current = shape("m6i.large", monthly=80)
    result = v2_run(
        [current],
        demand(2.0, 4.5, memory_low=2.0, memory_high=4.5),
        coremark_coverage_override=None,
    )
    assert result["recommendations"]

    # An empty authoritative catalog cannot activate V2 and degrades through the
    # existing V1 missing-current-catalog contract without raising.
    result = v2_run(
        [],
        demand(2.0, 4.5, memory_low=2.0, memory_high=4.5),
        coremark_coverage_override=None,
    )
    assert result["recommendations"] == []
    assert result["blocking_reasons"] == ["CURRENT_INSTANCE_PRICING_OR_SPEC_MISSING"]


def test_candidate_limit_is_validated_and_balanced_reference_is_returned():
    current = shape("m6i.large", monthly=80)
    targets = [
        shape(
            instance_type,
            monthly=price,
        )
        for instance_type, price in (
            ("c7i.large", 55),
            ("m7i.large", 56),
            ("r7i.large", 57),
        )
    ]
    payload = demand(2.0, 4.5, memory_low=2.0, memory_high=4.5)
    result = v2_run([current, *targets], payload, candidate_limit=1)
    assert len(result["recommendations"]) == 1
    assert result["tiers"]["balanced"] in result["recommendations"]

    with pytest.raises(ValueError, match="candidate_limit must be positive"):
        v2_run([current], payload, candidate_limit=0)


def test_v2_response_exposes_complete_configuration_policy_and_availability_evidence():
    current = shape("m6i.large", monthly=80)
    target = shape("m7i.large", monthly=60)
    result = v2_run(
        [current, target],
        demand(2.0, 4.5, memory_low=2.0, memory_high=4.5),
    )
    option = next(
        item
        for item in result["recommendations"]
        if item["target_instance_type"] == "m7i.large"
    )
    assert result["availability_validated"] is False
    assert result["availability_note"]
    assert set(result) >= {"capacity_policy", "scope_policy", "candidate_policy"}
    assert result["capacity_policy"]["policy_version"] == "asg-v2-combined"
    assert result["capacity_policy"]["candidate_limit"] == 10
    assert result["capacity_policy"]["instance_optimization_enabled"] is True
    assert set(option) >= {
        "target_instance_type",
        "optimization_kind",
        "target_min_size",
        "target_desired_capacity",
        "target_max_size",
        "current_unit_monthly_price",
        "target_unit_monthly_price",
        "target_monthly_cost",
        "monthly_savings",
        "cpu_capacity_basis",
        "performance_ratio",
    }
    assert option["target_max_size"] == 20
    assert option["max_size_changed"] is False
    assert option["performance_ratio"] == pytest.approx(
        target.coremark / current.coremark
    )
