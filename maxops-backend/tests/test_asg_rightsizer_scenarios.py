from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import re

import pytest

from tests.asg_rightsizer_helpers import catalog, metadata, requirement, run
from tests.fixtures.asg_rightsizer.capture_v1_decision_golden import (
    GOLDEN_PATH,
    canonical_bytes,
    capture as capture_v1_decisions,
)
from rightsizers.asg.asg_rightsizer.models import ASGCapacityPolicy


SCENARIO_NODE_MAP = {
    **{
        f"CAP-{number:03d}": "tests/test_asg_rightsizer.py::test_clean_recommendation_has_exact_targets_and_unchanged_max"
        for number in range(1, 11)
    },
    **{
        f"BND-{number:03d}": "tests/test_asg_rightsizer.py::test_pairing_boundary_is_inclusive"
        for number in range(1, 8)
    },
    **{
        f"AZ-{number:03d}": "tests/test_asg_rightsizer_scenarios.py::test_availability_floor_and_below_floor_guard"
        for number in range(1, 7)
    },
    **{
        f"TIER-{number:03d}": "tests/test_asg_rightsizer_scenarios.py::test_tier_collapse_deduplicates_configuration"
        for number in range(1, 10)
    },
    **{
        f"COST-{number:03d}": "tests/test_asg_rightsizer.py::test_decimal_minimum_savings_equality_is_included"
        for number in range(1, 9)
    },
    **{
        f"TEL-{number:03d}": "tests/test_asg_rightsizer.py::test_missing_required_metric_is_insufficient"
        for number in range(1, 11)
    },
    **{
        f"PRV-{number:03d}": "tests/test_asg_rightsizer.py::test_missing_memory_is_preview_only"
        for number in range(1, 9)
    },
    **{
        f"SCOPE-{number:03d}": "tests/test_asg_rightsizer_scenarios.py::test_scope_gates"
        for number in range(1, 12)
    },
    **{
        f"CLS-{number:03d}": "tests/test_asg_rightsizer_scenarios.py::test_operational_review_caps_conditional"
        for number in range(1, 11)
    },
    **{
        f"ENV-{number:03d}": "tests/test_asg_rightsizer_scenarios.py::test_response_envelope_and_null_risks"
        for number in range(1, 8)
    },
    **{
        f"MET-{number:03d}": "tests/test_asg_metric_collection.py::test_normalization_pairs_exact_timestamps_and_never_fills"
        for number in range(1, 14)
    },
    **{
        f"INV-{number:03d}": "tests/test_asg_metric_collection.py::test_asg_discovery_paginates_resolves_launch_and_isolates_group_failures"
        for number in range(1, 11)
    },
    **{
        f"TRD-{number:03d}": "tests/test_asg_rightsizer.py::test_trend_cache_isolated_and_reused"
        for number in range(1, 9)
    },
    "BASE2-001": "tests/test_asg_rightsizer_scenarios.py::test_v1_decision_projection_matches_frozen_golden",
    "BASE2-002": "tests/test_asg_rightsizer_v2.py::test_current_type_v2_matches_v1_tier_counts_and_evidence",
    "BASE2-003": "tests/test_ec2_rightsizer_v1.py::test_balanced_matches_pre_tiering_with_memory_present_and_absent",
    "BASE2-004": "tests/test_asg_rightsizer_scenarios.py::test_v1_decision_projection_matches_frozen_golden",
    **{
        f"CND2-{number:03d}": "tests/test_asg_rightsizer_v2.py::test_candidate_architecture_family_metal_and_capability_gates"
        for number in (1, 2, 3, 4, 5, 7)
    },
    "CND2-006": "tests/test_asg_rightsizer_v2.py::test_same_gated_family_is_eligible_when_architecture_overlaps",
    "CND2-008": "tests/test_asg_rightsizer_v2.py::test_target_without_authoritative_spec_is_skipped_and_tallied",
    "CND2-009": "tests/test_asg_rightsizer_v2_properties.py::test_catalog_order_does_not_change_v2_output",
    "CND2-010": "tests/test_asg_rightsizer_v2.py::test_v2_response_exposes_complete_configuration_policy_and_availability_evidence",
    "DEM2-001": "tests/test_asg_rightsizer_v2.py::test_current_type_v2_matches_v1_tier_counts_and_evidence",
    "DEM2-002": "tests/test_asg_rightsizer_v2.py::test_vcpu_fallback_is_consistent_and_performance_is_unavailable",
    "DEM2-003": "tests/test_asg_rightsizer_v2.py::test_candidate_architecture_family_metal_and_capability_gates",
    "DEM2-004": "tests/test_asg_metric_collection.py::test_usable_memory_does_not_erase_cpu_spike_without_memory_timestamp",
    "DEM2-005": "tests/test_asg_metric_collection.py::test_usable_memory_spike_survives_missing_cpu_timestamp",
    "DEM2-006": "tests/test_asg_rightsizer_v2_properties.py::test_max_then_percentile_numeric_counterexample_is_preserved",
    "DEM2-007": "tests/test_asg_rightsizer_v2.py::test_current_type_v2_matches_v1_tier_counts_and_evidence",
    "DEM2-008": "tests/test_asg_rightsizer_scenarios.py::test_required_min_above_current_is_clamped_while_desired_still_reduces",
    "DEM2-009": "tests/test_asg_rightsizer_v2.py::test_current_target_overflow_without_alternative_uses_top_level_disclosure",
    "DEM2-010": "tests/test_asg_rightsizer_v2.py::test_current_target_overflow_without_alternative_uses_top_level_disclosure",
    "DEM2-011": "tests/test_asg_rightsizer_v2.py::test_changed_smaller_target_overflow_is_tallied_while_larger_survives",
    "DEM2-012": "tests/test_asg_rightsizer_v2.py::test_v1_telemetry_schema_does_not_activate_instance_optimization",
    "DEM2-013": "tests/test_asg_rightsizer_v2.py::test_changed_smaller_target_overflow_is_tallied_while_larger_survives",
    "ECO2-001": "tests/test_asg_rightsizer_v2.py::test_current_type_v2_matches_v1_tier_counts_and_evidence",
    "ECO2-002": "tests/test_asg_rightsizer_v2.py::test_instance_only_and_changed_type_contract",
    **{
        f"ECO2-{number:03d}": "tests/test_asg_rightsizer_v2.py::test_combined_option_uses_total_configuration_economics"
        for number in (3, 4, 5, 8)
    },
    **{
        f"ECO2-{number:03d}": "tests/test_asg_rightsizer_v2.py::test_decimal_total_savings_floor_and_request_equality_are_inclusive"
        for number in (6, 7)
    },
    "MEM2-001": "tests/test_asg_rightsizer_v2.py::test_missing_memory_capacity_floor_separates_normal_and_preview_options",
    "MEM2-002": "tests/test_asg_rightsizer_v2.py::test_missing_memory_capacity_floor_separates_normal_and_preview_options",
    **{
        f"MEM2-{number:03d}": "tests/test_asg_rightsizer_v2.py::test_missing_memory_requires_both_aggregate_floors"
        for number in (3, 4, 5)
    },
    **{
        f"MEM2-{number:03d}": "tests/test_asg_rightsizer_v2.py::test_missing_memory_capacity_floor_separates_normal_and_preview_options"
        for number in (6, 7, 8)
    },
    "PRW2-001": "tests/test_asg_rightsizer_v2.py::test_missing_memory_capacity_floor_separates_normal_and_preview_options",
    **{
        f"PRW2-{number:03d}": "tests/test_asg_rightsizer_v2.py::test_graviton_preview_retains_raw_capacity_and_nulls_cross_arch_cpu_evidence"
        for number in (2, 3, 4)
    },
    **{
        f"PRW2-{number:03d}": "tests/test_asg_rightsizer_v2.py::test_preview_flags_remove_only_their_preview_kinds"
        for number in (5, 6)
    },
    **{
        f"TIR2-{number:03d}": "tests/test_asg_rightsizer_v2.py::test_candidate_limit_is_validated_and_balanced_reference_is_returned"
        for number in (1, 2, 3, 5, 6, 7, 8)
    },
    "TIR2-004": "tests/test_asg_rightsizer_v2_properties.py::test_catalog_order_does_not_change_v2_output",
    "ENV2-001": "tests/test_asg_rightsizer_v2.py::test_current_type_v2_matches_v1_tier_counts_and_evidence",
    **{
        f"ENV2-{number:03d}": "tests/test_asg_rightsizer_v2.py::test_instance_only_and_changed_type_contract"
        for number in (2, 3)
    },
    **{
        f"ENV2-{number:03d}": "tests/test_asg_rightsizer_v2.py::test_v2_response_exposes_complete_configuration_policy_and_availability_evidence"
        for number in (4, 6, 7, 8, 10)
    },
    "ENV2-005": "tests/test_asg_rightsizer_v2.py::test_vcpu_fallback_is_consistent_and_performance_is_unavailable",
    "ENV2-009": "tests/test_asg_rightsizer_v2.py::test_graviton_preview_retains_raw_capacity_and_nulls_cross_arch_cpu_evidence",
    **{
        f"ROL2-{number:03d}": "tests/test_asg_rightsizer_v2.py::test_rollout_switch_precedence_and_coremark_coverage_boundary"
        for number in (1, 2, 4, 6)
    },
    "ROL2-003": "tests/test_asg_rightsizer_v2.py::test_v1_telemetry_schema_does_not_activate_instance_optimization",
    "ROL2-005": "tests/test_asg_rightsizer_v2.py::test_empty_coremark_catalog_fails_closed_without_division_by_zero",
}


def test_markdown_scenario_manifest_is_complete_and_references_named_tests():
    backend = Path(__file__).resolve().parents[1]
    plan = (
        backend / "rightsizers/asg/asg_rightsizer_test_plan.md"
    ).read_text(encoding="utf-8")
    documented = set(
        re.findall(
            r"\b(?:CAP|BND|AZ|TIER|COST|TEL|PRV|SCOPE|CLS|ENV|MET|INV|TRD|BASE2|CND2|DEM2|ECO2|MEM2|PRW2|TIR2|ENV2|ROL2)-\d{3}\b",
            plan,
        )
    )
    assert set(SCENARIO_NODE_MAP) == documented
    allowed = {
        "tests/test_asg_rightsizer.py",
        "tests/test_asg_rightsizer_scenarios.py",
        "tests/test_asg_metric_collection.py",
        "tests/test_asg_memory_metric_discovery.py",
        "tests/test_asg_rightsizer_v2.py",
        "tests/test_asg_rightsizer_v2_properties.py",
        "tests/test_ec2_rightsizer_v1.py",
    }
    for node in SCENARIO_NODE_MAP.values():
        file_name, function_name = node.split("::", 1)
        assert file_name in allowed
        source = (backend / file_name).read_text(encoding="utf-8")
        assert re.search(rf"^def {re.escape(function_name)}\b", source, re.MULTILINE)


def test_v1_decision_projection_matches_frozen_golden():
    assert GOLDEN_PATH.read_bytes() == canonical_bytes(capture_v1_decisions())


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("mixed_instances_policy_present", True, "MIXED_INSTANCES_POLICY_UNSUPPORTED"),
        ("weighted_capacity_present", True, "WEIGHTED_CAPACITY_UNSUPPORTED"),
        ("warm_pool_present", True, "WARM_POOL_REQUIRES_SEPARATE_OPTIMIZATION"),
        ("scheduled_actions_present", True, "SCHEDULED_SCALING_REQUIRES_SEPARATE_OPTIMIZATION"),
        ("predictive_scaling_present", True, "PREDICTIVE_SCALING_REQUIRES_SEPARATE_OPTIMIZATION"),
        ("instance_refresh_in_progress", True, "INSTANCE_REFRESH_IN_PROGRESS"),
        ("scale_in_protection_present", True, "SCALE_IN_PROTECTION_ACTIVE"),
    ],
)
def test_scope_gates(field, value, reason):
    result, _, _ = run(metadata_override={field: value})
    assert result["classification"] == "DEFERRED"
    assert result["deferred_reason_codes"] == [reason]
    assert result["current_monthly_cost"] is None


def test_heterogeneous_and_spot_scope_gates():
    result, _, _ = run(metadata_override={"effective_instance_types": ["m6i.large", "m6i.xlarge"]})
    assert result["deferred_reason_codes"] == ["HETEROGENEOUS_INSTANCE_TYPES_UNSUPPORTED"]
    result, _, _ = run(metadata_override={"instance_lifecycles": ["spot"]})
    assert result["deferred_reason_codes"] == ["UNSUPPORTED_INSTANCE_LIFECYCLE"]


def test_critical_suspended_process_is_deferred():
    result, _, _ = run(metadata_override={"suspended_processes": ["Launch"]})
    assert result["deferred_reason_codes"] == ["SCALING_PROCESS_SUSPENDED"]


def test_scale_to_zero_is_deferred():
    result, _, _ = run(current_min=0)
    assert result["deferred_reason_codes"] == ["SCALE_TO_ZERO_UNSUPPORTED"]


def test_availability_floor_and_below_floor_guard():
    result, _, _ = run()
    assert result["tiers"]["balanced"]["target_min_size"] == 3
    result, _, _ = run(current_min=2)
    assert result["recommendations"] == []
    assert result["blocking_reasons"] == ["CURRENT_CAPACITY_BELOW_AVAILABILITY_FLOOR"]


def test_missing_zones_uses_floor_one_and_conditional():
    result, _, _ = run(metadata_override={"availability_zones": []})
    assert result["classification"] == "CONDITIONAL"
    assert "ASG_AVAILABILITY_ZONES_UNAVAILABLE_FLOOR_ONE" in result["tiers"]["balanced"]["reason_codes"]


def test_whitespace_only_zones_use_floor_one_warning_and_conditional_cap():
    result, _, _ = run(metadata_override={"availability_zones": [" "]})
    assert result["current_capacity_evidence"]["availability_zone_count"] == 0
    assert result["tiers"]["balanced"]["evidence"]["availability_floor"] == 1
    assert result["classification"] == "CONDITIONAL"
    assert (
        "ASG_AVAILABILITY_ZONES_UNAVAILABLE_FLOOR_ONE"
        in result["tiers"]["balanced"]["reason_codes"]
    )


def test_availability_floor_counts_unique_nonempty_zones():
    result, _, _ = run(
        metadata_override={
            "availability_zones": [
                "us-east-1a",
                "us-east-1a",
                "us-east-1b",
                "",
                "   ",
            ]
        }
    )
    assert result["current_capacity_evidence"]["availability_zone_count"] == 2
    assert result["tiers"]["balanced"]["evidence"]["availability_floor"] == 2


@pytest.mark.parametrize(
    "override,reason",
    [
        ({"dynamic_policy_kinds": []}, "NO_DYNAMIC_SCALING_POLICY_REQUIRES_REVIEW"),
        ({"dynamic_policy_metrics": ["ALBRequestCountPerTarget"]}, "NON_CPU_SCALING_SIGNAL_REQUIRES_REVIEW"),
        ({"capacity_rebalance": True}, "CAPACITY_REBALANCE_REQUIRES_REVIEW"),
        ({"operational_signals": {"scaling_failure_seen": True}}, "RECENT_SCALING_FAILURE_REQUIRES_REVIEW"),
        ({"operational_signals": {"capacity_shortage_seen": True}}, "RECENT_CAPACITY_SHORTAGE_REQUIRES_REVIEW"),
        ({"operational_signals": {"desired_in_service_mismatch_seen": True}}, "DESIRED_IN_SERVICE_MISMATCH_REQUIRES_REVIEW"),
    ],
)
def test_operational_review_caps_conditional(override, reason):
    base = metadata()
    if "operational_signals" in override:
        signals = base["operational_signals"]
        signals.update(override["operational_signals"])
        override = {"operational_signals": signals}
    result, _, _ = run(metadata_override=override)
    assert result["classification"] == "CONDITIONAL"
    assert reason in result["tiers"]["balanced"]["reason_codes"]


def test_memory_pairing_failure_has_distinct_preview_blocker():
    telemetry = metadata()["telemetry_summary"]
    telemetry["memory_percent"]["pairing_ratio"] = 0.8999
    telemetry["memory_percent"]["status"] = "insufficient_pairing"
    result, _, _ = run(metadata_override={"telemetry_summary": telemetry})
    assert result["savings_previews"][0]["blockers"] == [
        "ASG_MEMORY_CAPACITY_PAIRING_INSUFFICIENT"
    ]
    option = result["savings_previews"][0]["options"]["balanced"]
    assert option["projected_memory_util"] is None
    assert option["binding_dimension"] == "cpu"


def test_memory_preview_policy_can_disable_preview_output():
    telemetry = metadata()["telemetry_summary"]
    telemetry["memory_percent"].update(
        present=False,
        observed_days=0.0,
        status="unavailable",
        source=None,
    )
    result, _, _ = run(
        metadata_override={"telemetry_summary": telemetry},
        service_kwargs={
            "capacity_policy": replace(
                ASGCapacityPolicy(), memory_preview_enabled=False
            )
        },
    )
    assert result["classification"] is None
    assert result["recommendations"] == []
    assert result["savings_previews"] == []


def test_short_window_boundary():
    telemetry = metadata()["telemetry_summary"]
    telemetry["cpu_percent"]["observed_days"] = 6.999
    result, _, _ = run(metadata_override={"telemetry_summary": telemetry})
    assert result["classification"] == "INSUFFICIENT_DATA"
    assert result["messages"] == [
        "At least 7 observed days are required; 6.999 are available."
    ]
    assert "message" not in result
    telemetry["cpu_percent"]["observed_days"] = 7.0
    result, _, _ = run(metadata_override={"telemetry_summary": telemetry})
    assert result["classification"] == "ACTIONABLE"


def test_short_memory_window_uses_documented_preview_blocker():
    telemetry = metadata()["telemetry_summary"]
    telemetry["memory_percent"]["observed_days"] = 6.999
    telemetry["memory_percent"]["thin_data"] = True
    result, _, _ = run(metadata_override={"telemetry_summary": telemetry})
    assert result["classification"] == "PREVIEW"
    assert result["savings_previews"][0]["blockers"] == [
        "ASG_MEMORY_TELEMETRY_WINDOW_TOO_SHORT"
    ]


def test_tier_collapse_deduplicates_configuration():
    base = metadata()
    windows = deepcopy(base["rightsizing_metrics"])
    for window in windows.values():
        window["normalized"]["required_capacity"] = {
            tier: {**requirement(5), "p50": 3, "p50_record": requirement(3)["p50_record"]}
            for tier in ("conservative", "balanced", "aggressive")
        }
    result, _, _ = run(metadata_override={"rightsizing_metrics": windows})
    assert len(result["recommendations"]) == 1
    assert result["recommendations"][0]["satisfied_tiers"] == [
        "conservative",
        "balanced",
        "aggressive",
    ]


def test_aggressive_only_never_becomes_default():
    windows = deepcopy(metadata()["rightsizing_metrics"])
    for window in windows.values():
        window["normalized"]["required_capacity"] = {
            tier: {
                **requirement(desired),
                "p50": 3,
                "p50_record": requirement(3)["p50_record"],
            }
            for tier, desired in (
                ("conservative", 10),
                ("balanced", 10),
                ("aggressive", 9),
            )
        }
    result, _, _ = run(metadata_override={"rightsizing_metrics": windows})
    assert result["tiers"]["conservative"] is None
    assert result["tiers"]["balanced"] is None
    assert result["tiers"]["aggressive"]["target_desired_capacity"] == 9
    assert result["tiers"]["default"] is None
    assert result["classification"] == "ACTIONABLE"


def test_collapsed_tiers_share_returned_configuration_reference():
    windows = deepcopy(metadata()["rightsizing_metrics"])
    for window in windows.values():
        window["normalized"]["required_capacity"] = {
            tier: {
                **requirement(desired),
                "p50": 3,
                "p50_record": requirement(3)["p50_record"],
            }
            for tier, desired in (
                ("conservative", 8),
                ("balanced", 8),
                ("aggressive", 6),
            )
        }
    result, _, _ = run(metadata_override={"rightsizing_metrics": windows})
    assert len(result["recommendations"]) == 2
    assert result["tiers"]["conservative"] is result["tiers"]["balanced"]
    assert result["tiers"]["aggressive"] is result["recommendations"][0]


def test_longer_window_wins_equal_requirement_evidence():
    result, _, _ = run()
    assert result["tiers"]["balanced"]["evidence"]["desired_selected_window_days"] == 60


def test_response_envelope_and_null_risks():
    result, _, _ = run()
    for key in (
        "state",
        "current_monthly_cost",
        "pricing_source",
        "current_capacity_evidence",
        "telemetry_summary",
        "capacity_policy",
        "scope_policy",
    ):
        assert key in result
    risk = result["tiers"]["balanced"]["risk_assessment"]
    assert risk["network"] is risk["storage"] is risk["migration"] is None
    assert risk["operations"] == "LOW"


def test_increasing_requirement_never_reduces_target():
    low, _, _ = run()
    windows = deepcopy(metadata()["rightsizing_metrics"])
    for window in windows.values():
        higher = requirement(8)
        higher["p50"] = 3
        higher["p50_record"] = requirement(3)["p50_record"]
        window["normalized"]["required_capacity"]["balanced"] = higher
    high, _, _ = run(metadata_override={"rightsizing_metrics": windows})
    assert high["tiers"]["balanced"]["target_desired_capacity"] >= low["tiers"]["balanced"]["target_desired_capacity"]


def test_requirement_above_current_never_becomes_hidden_increase():
    windows = deepcopy(metadata()["rightsizing_metrics"])
    for window in windows.values():
        window["normalized"]["required_capacity"] = {
            tier: requirement(11)
            for tier in ("conservative", "balanced", "aggressive")
        }
    result, _, _ = run(metadata_override={"rightsizing_metrics": windows})
    assert result["recommendations"] == []
    assert result["blocking_reasons"] == ["CURRENT_CAPACITY_NOT_OVERPROVISIONED"]


def test_required_min_above_current_is_clamped_while_desired_still_reduces():
    windows = deepcopy(metadata()["rightsizing_metrics"])
    for window in windows.values():
        window["normalized"]["required_capacity"] = {
            tier: {
                **requirement(8),
                "p50": 7,
                "p50_record": requirement(7)["p50_record"],
            }
            for tier in ("conservative", "balanced", "aggressive")
        }
    result, _, _ = run(metadata_override={"rightsizing_metrics": windows})
    assert len(result["recommendations"]) == 1
    option = result["recommendations"][0]
    assert (
        option["target_min_size"],
        option["target_desired_capacity"],
        option["target_max_size"],
    ) == (6, 8, 20)
    assert option["monthly_savings"] > 0


def test_min_only_change_is_not_returned_or_monetized():
    windows = deepcopy(metadata()["rightsizing_metrics"])
    for window in windows.values():
        window["normalized"]["required_capacity"] = {
            tier: {
                **requirement(6),
                "p50": 3,
                "p50_record": requirement(3)["p50_record"],
            }
            for tier in ("conservative", "balanced", "aggressive")
        }
    result, _, _ = run(
        current_min=6,
        current_desired=6,
        metadata_override={"rightsizing_metrics": windows},
    )
    assert result["recommendations"] == []
    assert result["tiers"] == {
        "conservative": None,
        "balanced": None,
        "aggressive": None,
        "default": None,
    }


def test_deterministic_repeated_input():
    first, _, _ = run()
    second, _, _ = run()
    for key in ("classification", "recommendations", "tiers", "telemetry_summary"):
        assert first[key] == second[key]


class StaticCatalog:
    def __init__(self, entry):
        self.entry = entry

    def get(self, region, instance_type, platform):
        return self.entry


def test_missing_price_or_spec_blocks_options_and_previews():
    result, _, _ = run(catalog_override=StaticCatalog(None))
    assert result["blocking_reasons"] == [
        "CURRENT_INSTANCE_PRICING_OR_SPEC_MISSING"
    ]
    assert result["recommendations"] == []
    assert result["savings_previews"] == []


@pytest.mark.parametrize(
    "unit_price,included",
    [(0.0099, False), (0.01, True), (0.0101, True)],
)
def test_one_cent_savings_boundary_uses_decimal_from_string(unit_price, included):
    windows = deepcopy(metadata()["rightsizing_metrics"])
    for window in windows.values():
        window["normalized"]["required_capacity"] = {
            tier: requirement(999) for tier in ("conservative", "balanced", "aggressive")
        }
    real = catalog().get("us-east-1", "m6i.large", "linux")
    result, _, _ = run(
        current_min=999,
        current_desired=1000,
        current_max=1200,
        metadata_override={"rightsizing_metrics": windows},
        catalog_override=StaticCatalog(replace(real, monthly_usd=unit_price)),
    )
    assert bool(result["recommendations"]) is included
    if included:
        assert result["current_monthly_cost"] == round(1000 * unit_price, 2)
        assert result["recommendations"][0]["monthly_savings"] == round(unit_price, 2)


def test_minimum_savings_boundary_below_equal_and_above():
    unit_price = catalog().get("us-east-1", "m6i.large", "linux").monthly_usd
    raw_savings = 3 * unit_price
    below, _, _ = run(min_monthly_savings=raw_savings - 0.0001)
    equal, _, _ = run(min_monthly_savings=raw_savings)
    above, _, _ = run(min_monthly_savings=raw_savings + 0.0001)
    assert below["tiers"]["balanced"] is not None
    assert equal["tiers"]["balanced"] is not None
    assert above["tiers"]["balanced"] is None
