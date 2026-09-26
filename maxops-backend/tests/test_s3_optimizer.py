"""Offline unit tests for the Phase-2 S3 optimizer engine."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.services.s3_optimizer import evaluate_bucket
from app.services.s3_optimizer.costs import padding_cost, request_cost, retrieval_cost
from app.services.s3_optimizer.object_counts import object_counts_by_class
from app.services.s3_optimizer.patterns import classify_pattern
from app.services.s3_optimizer.recommendation import choose_recommendation
from app.services.s3_optimizer.risks import evaluate_risks


def _series(value: float, count: int = 90):
    """Build the small CloudWatch series shape used by unit fixtures."""

    return [{"date": str(date(2026, 1, 1) + timedelta(days=index)), "value": value} for index in range(count)]


def _cloudwatch(objects: float = 500_000, stored_gb: float = 1_000) -> dict:
    """Build one known-sized bucket without contacting CloudWatch."""

    return {"metrics": {"bucket": {"StandardStorage": _series(stored_gb * 2**30), "NumberOfObjects": _series(objects)}}}


def _cloudwatch_class(storage_type: str, objects: float = 500_000, stored_gb: float = 1_000) -> dict:
    """Build CloudWatch data with one explicitly selected storage class."""

    return {"metrics": {"bucket": {storage_type: _series(stored_gb * 2**30), "NumberOfObjects": _series(objects)}}}


def _prices() -> dict:
    """Return the two-class price map needed by the worked example."""

    return {
        "storage.STANDARD.gb_month": {"price": 0.023},
        "storage.DEEP_ARCHIVE.gb_month": {"price": 0.00099},
        "request.STANDARD.data_write.per_1000": {"price": 0.005},
        "request.STANDARD.data_read.per_1000": {"price": 0.0004},
        "request.STANDARD.list.per_1000": {"price": 0.005},
        "request.STANDARD.config.per_1000": {"price": 0.005},
        "request.STANDARD.tier_unsplit.per_1000": {"price": 0.005},
        "request.DEEP_ARCHIVE.data_write.per_1000": {"price": 0.02},
        "request.DEEP_ARCHIVE.data_read.per_1000": {"price": 0.01},
        "request.DEEP_ARCHIVE.tier_unsplit.per_1000": {"price": 0.02},
        "transition.DEEP_ARCHIVE.per_1000": {"price": 0.05},
        "retrieval.DEEP_ARCHIVE.standard.gb": {"price": 0.02},
        "restore_request.DEEP_ARCHIVE.standard.per_1000": {"price": 0.10},
    }


def _signals() -> dict:
    """Return a covered, no-request signal block."""

    return {
        "resource_id": "bucket",
        "telemetry_status": "usable",
        "confidence": "high",
        "cur_days_covered": 90,
        "covered_days": [point["date"] for point in _series(0)],
        "monthly_data_read_requests": 0,
        "monthly_data_write_requests": 0,
        "monthly_list_requests": 0,
        "monthly_config_requests": 0,
        "monthly_tier1_requests": 0,
        "monthly_tier2_requests": 0,
        "requests_per_object_per_month": 0,
        "retrieval_gb_per_month_by_class": {},
        "retrieval_ratio_by_class": {},
        "window_totals": {"tier1": {}, "tier2": {}},
        "unmapped_usage_types": [],
    }


def _seed_prices() -> dict:
    """Load the checked-in us-east-1 seed for broad scenario tests."""

    path = Path(__file__).parents[1] / "pricing/s3_price_seed.json"
    return {"resolved": json.loads(path.read_text(encoding="utf-8"))["us-east-1"]}


def _history(reads, writes, retrieval=None, stored=None):
    """Build the compact whole-month history accepted by the classifier."""

    months = [f"2026-{index:02d}" for index in range(1, len(reads) + 1)]
    return {
        "months": months,
        "data_read": reads,
        "data_write": writes,
        "config": [0] * len(reads),
        "retrieval_gb": retrieval if retrieval is not None else [0] * len(reads),
        "egress_gb": [0] * len(reads),
        "stored_gb": stored if stored is not None else [100] * len(reads),
    }


def _scenario(policy, rank, tolerance, risks=None, transition=1.0):
    """Build the minimal scenario shape used by decision-table unit tests."""

    return {
        "policy": policy,
        "status": "ok",
        "ladder_group": "main",
        "ladder_rank": rank,
        "tolerated_full_retrievals_per_year": tolerance,
        "transition_cost": transition,
        "risks": [{"code": code} for code in (risks or [])],
        "recommended": False,
    }


def test_object_count_overhead_remainder_share_and_missing_are_explicit():
    """Check the three §2.4 population-count methods and missing metrics."""

    cw = {"metrics": {"bucket": {"StandardStorage": _series(100), "StandardIAStorage": _series(300), "GlacierObjectOverhead": _series(2 * 32768), "NumberOfObjects": _series(12)}}}
    counts = object_counts_by_class(cw, "bucket")
    assert counts["GLACIER"]["objects"] == 2
    assert counts["STANDARD"]["object_count_method"] == "byte_share_estimate"
    assert counts["STANDARD"]["objects"] == 2.5
    assert counts["STANDARD_IA"]["objects"] == 7.5
    assert "DEEP_ARCHIVE" not in counts


def test_pattern_registry_order_prefers_periodic_bursts_before_active_reads():
    """A burst shape is classified by its earlier registry entry."""

    signals = {"telemetry_status": "usable", "cur_days_covered": 90, "monthly_data_read_requests": 100, "monthly_data_write_requests": 0, "requests_per_object_per_month": 1}
    history = {"months": ["1", "2", "3", "4"], "data_read": [1000, 0, 0, 0], "data_write": [1, 0, 0, 0]}
    assert classify_pattern(signals, history)[0] == "periodic_bursts"


def test_request_units_and_standard_list_rate():
    """Exactly 1,000 family requests cost exactly one per-1,000 unit."""

    prices = {"request.STANDARD.list.per_1000": {"price": 0.005}, "request.STANDARD_IA.data_write.per_1000": {"price": 0.010}, "request.STANDARD.config.per_1000": {"price": 0.005}}
    signals = {"monthly_list_requests": 1000, "monthly_data_write_requests": 0, "monthly_data_read_requests": 0, "monthly_config_requests": 0}
    assert request_cost(signals, prices, "STANDARD_IA") == 0.005


def test_explicitly_unknown_tier_request_cost_does_not_fall_back_to_window_total():
    """A present None tier signal remains unknown instead of using a raw fallback."""

    signals = {
        "monthly_tier1_requests": None,
        "monthly_tier2_requests": 0,
        "window_totals": {"tier1": {"tier_unsplit": 1000}, "tier2": {}},
    }
    prices = {"request.STANDARD.tier_unsplit.per_1000": {"price": 0.005}}

    assert request_cost(signals, prices, "STANDARD") is None


def test_padding_floor_and_unknown_price():
    """Padding is positive below 128 KB, zero at the floor, and unknown without a price."""

    prices = {"storage.STANDARD_IA.gb_month": {"price": 0.0125}}
    assert padding_cost("STANDARD_IA", 1, 1, 100_000, prices)[0] > 0
    assert padding_cost("STANDARD_IA", 1, 1, 131_072, prices)[0] == 0
    assert padding_cost("STANDARD_IA", 1, 1, 100_000, {})[0] is None


def test_observed_padding_replaces_average_size_estimate():
    """A captured 128 KiB overhead amount takes precedence over an estimate."""

    prices = _seed_prices()
    prices["resolved"]["storage.STANDARD_IA.gb_month"] = {"price": 0.001}
    cost, method = padding_cost(
        "STANDARD_IA",
        1,
        1,
        1,
        prices,
        observed_overhead_bytes=2**30,
    )
    assert method == "observed_overhead"
    assert cost == pytest.approx(prices["resolved"]["storage.STANDARD_IA.gb_month"]["price"])


def test_worked_example_deep_archive_numbers():
    """The §4.8 1,000 GB / 500,000-object example keeps its golden math."""

    result = evaluate_bucket(_signals(), {"resolved": _prices()}, _cloudwatch(), {"resource_id": "bucket", "region": "us-east-1"})
    scenario = next(item for item in result["scenarios"] if item["policy"] == "DEEP_ARCHIVE")
    assert abs(scenario["monthly_costs"]["storage"] - 1.0928) < 0.001
    assert abs(scenario["savings_monthly"]["low"] - 16.08) < 0.02
    assert abs(scenario["breakeven_months"] - 1.6) < 0.1


def test_unknown_telemetry_has_no_scenarios_or_recommendation():
    """Absent CUR telemetry never produces an actionable scenario."""

    result = evaluate_bucket({"telemetry_status": "unknown", "confidence": "low", "cur_days_covered": 0, "unmapped_usage_types": []}, {"resolved": _prices()}, {"metrics": {}}, {"resource_id": "bucket"})
    assert result["telemetry_status"] == "unknown"
    assert result["scenarios"] == []
    assert result["recommendation"]["policy"] is None


def test_appendix_top_level_shape_is_stable():
    """The result assembler exposes exactly the frozen Appendix top-level keys."""

    result = evaluate_bucket(_signals(), {"resolved": _prices()}, _cloudwatch(), {"resource_id": "bucket"})
    assert set(result) == {
        "inventory_id", "resource_id", "account_id", "region", "telemetry_status", "confidence", "coverage", "current", "signals", "checks_triggered", "scenarios", "recommendation", "pattern", "unmapped_usage_types", "price_map_window", "seed_as_of",
    }


def test_worked_example_includes_storage_transition_both_savings_and_breakeven():
    """The §4.8 example keeps all five published golden values."""

    result = evaluate_bucket(_signals(), {"resolved": _prices()}, _cloudwatch(), {"resource_id": "bucket"})
    scenario = next(item for item in result["scenarios"] if item["policy"] == "DEEP_ARCHIVE")
    assert scenario["monthly_costs"]["storage"] == pytest.approx(1.09, abs=0.01)
    assert scenario["transition_cost"] == pytest.approx(25.0)
    assert scenario["savings_monthly"]["high"] == pytest.approx(21.91, abs=0.02)
    assert scenario["savings_monthly"]["low"] == pytest.approx(16.08, abs=0.02)
    assert scenario["breakeven_months"] == pytest.approx(1.6, abs=0.1)


def test_small_object_population_is_unavailable_without_override_and_dominated_with_override():
    """A 500M-object, 2 KB population needs an explicit lifecycle override."""

    cloudwatch = _cloudwatch(objects=500_000_000, stored_gb=1_000)
    default = evaluate_bucket(_signals(), _seed_prices(), cloudwatch, {"resource_id": "bucket"})
    for scenario in default["scenarios"]:
        if scenario["ladder_group"] == "main":
            assert scenario["status"] == "size_distribution_unavailable"
            assert any(risk["code"] == "SMALL_OBJECTS_128KB" for risk in scenario["risks"])

    override = evaluate_bucket(
        _signals(),
        _seed_prices(),
        cloudwatch,
        {"resource_id": "bucket"},
        {"lifecycle_small_object_override": True},
    )
    deep_archive = next(item for item in override["scenarios"] if item["policy"] == "DEEP_ARCHIVE")
    assert deep_archive["status"] == "not_beneficial"
    assert any(risk["code"] == "TRANSITION_COST_DOMINATES" for risk in deep_archive["risks"])


def test_transition_cost_dominates_without_small_object_override_at_floor():
    """Transition fees can dominate a priced, at-floor population without an override."""

    result = evaluate_bucket(
        _signals(),
        _seed_prices(),
        _cloudwatch(objects=8_000, stored_gb=1),
        {"resource_id": "bucket"},
    )
    deep_archive = next(item for item in result["scenarios"] if item["policy"] == "DEEP_ARCHIVE")
    assert deep_archive["status"] in {"ok", "not_beneficial"}
    assert any(risk["code"] == "TRANSITION_COST_DOMINATES" for risk in deep_archive["risks"])


def test_min_duration_penalty_is_churn_risk_not_a_breakeven_trigger():
    """Fast object turnover fires the minimum-duration warning independently of payback."""

    signals = _signals()
    signals["monthly_data_write_requests"] = 1_000_000
    result = evaluate_bucket(signals, _seed_prices(), _cloudwatch(objects=500_000, stored_gb=1_000), {"resource_id": "bucket"})
    sia = next(item for item in result["scenarios"] if item["policy"] == "STANDARD_IA")
    assert sia["breakeven_months"] is not None
    assert any(risk["code"] == "MIN_DURATION_PENALTY" for risk in sia["risks"])

    no_churn = evaluate_risks({
        "target_class": "STANDARD_IA",
        "monthly_data_write_requests": 1,
        "total_objects": 1_000_000,
        "breakeven_months": 0.01,
    })
    assert not any(risk.code == "MIN_DURATION_PENALTY" for risk in no_churn)


def test_standard_ia_candidate_uses_instant_retrieval_price_and_persists_it():
    """IA candidates must price retrieval at the class-rule instant speed."""

    prices = _seed_prices()
    prices["resolved"]["retrieval.STANDARD_IA.instant.gb"] = {"price": 0.123}
    prices["resolved"]["retrieval.STANDARD_IA.standard.gb"] = {"price": 0.001}
    signals = _signals()
    signals["retrieval_gb_per_month_by_class"] = {"STANDARD_IA": 1.0}
    result = evaluate_bucket(signals, prices, _cloudwatch(), {"resource_id": "bucket"})
    scenario = next(item for item in result["scenarios"] if item["policy"] == "STANDARD_IA")
    assert scenario["monthly_costs"]["retrieval_observed"] == pytest.approx(0.123)
    assert scenario["assumptions"]["retrieval_speed_assumption"] == "instant"


def test_it_low_savings_is_exactly_negative_monitoring_fee():
    """The IT low bound is the §4.6 monitoring-only theoretical endpoint."""

    result = evaluate_bucket(_signals(), _seed_prices(), _cloudwatch(), {"resource_id": "bucket"})
    scenario = next(item for item in result["scenarios"] if item["policy"] == "INTELLIGENT_TIERING")
    assert scenario["savings_monthly"]["low"] == pytest.approx(-scenario["monthly_costs"]["it_monitoring"])


def test_write_only_bucket_has_direct_write_candidates_and_backlog_shape():
    """Write-only buckets can avoid existing-object transition fees for new data."""

    signals = _signals()
    signals["history"] = _history([0, 0, 0], [100, 100, 100])
    result = evaluate_bucket(signals, _seed_prices(), _cloudwatch(), {"resource_id": "bucket"})
    assert result["pattern"]["write_only"] is True
    direct = [item for item in result["scenarios"] if item["ladder_group"] == "direct_write"]
    assert direct
    assert all(item["transition_cost_new_objects"] == 0 for item in direct)
    assert all(item["backlog_scenario"] for item in direct)

    signals["history"] = _history([1, 1, 1], [100, 100, 100])
    ordinary = evaluate_bucket(signals, _seed_prices(), _cloudwatch(), {"resource_id": "bucket"})
    assert not any(item["ladder_group"] == "direct_write" for item in ordinary["scenarios"])


def test_back_to_standard_requires_retrieval_charging_bytes():
    """The side candidate is meaningful only when a retrieval class holds bytes."""

    standard = evaluate_bucket(_signals(), _seed_prices(), _cloudwatch(), {"resource_id": "bucket"})
    assert not any(item["policy"] == "BACK_TO_STANDARD" for item in standard["scenarios"])
    ia = evaluate_bucket(_signals(), _seed_prices(), _cloudwatch_class("StandardIAStorage"), {"resource_id": "bucket"})
    assert any(item["policy"] == "BACK_TO_STANDARD" for item in ia["scenarios"])


def test_bucket_a_retrieval_is_repriced_at_deep_archive_standard_speed():
    """Observed SIA/GIR bytes use the destination archive's standard retrieval rate."""

    payload = json.loads((Path(__file__).parent / "payloads/s3_optimizer/cur_fixture.json").read_text(encoding="utf-8"))
    rows = [row for row in payload["rows"] if row["bucket"] == "bucket-a"]
    from app.services.s3_bucket_source import coverage_by_bucket, derive_signals

    coverage = coverage_by_bucket(rows, "2026-07-01", "2026-09-21")["bucket-a"]
    signals = derive_signals(rows, coverage)
    prices = _seed_prices()
    prices["resolved"]["retrieval.DEEP_ARCHIVE.standard.gb"] = {"price": 0.077}
    expected = sum(value for value in signals["retrieval_gb_per_month_by_class"].values()) * 0.077
    expected += sum(value for value in (signals.get("restore_requests_by_class") or {}).values()) / 1000 * prices["resolved"]["restore_request.DEEP_ARCHIVE.standard.per_1000"]["price"]
    assert retrieval_cost(signals, prices, target_class="DEEP_ARCHIVE") == pytest.approx(expected)


def test_burst_recommendation_uses_observed_peak_volume():
    """Observed burst tolerance is peak retrieval GB divided by stored GB, annualized."""

    scenarios = [_scenario("STANDARD_IA", 1, 3), _scenario("DEEP_ARCHIVE", 4, 1.5)]
    pattern = {"pattern_label": "periodic_bursts", "write_only": False, "history": _history([100, 0, 0], [0, 0, 0], [20, 0, 0], [100, 100, 100])}
    signals = {"telemetry_status": "usable", "confidence": "high", "retrieval_gb_per_month_by_class": {"STANDARD_IA": 20}}
    recommendation, _ = choose_recommendation(scenarios, pattern, signals, {"full_retrievals_per_year": 1})
    assert recommendation == "STANDARD_IA"


def test_burst_recommendation_without_volume_uses_stress_and_adds_unknown_marker():
    """Without class-specific retrieval bytes, the generic stress parameter is used."""

    scenarios = [_scenario("STANDARD_IA", 1, 3), _scenario("DEEP_ARCHIVE", 4, 1.5)]
    pattern = {"pattern_label": "periodic_bursts", "write_only": False, "history": _history([100, 0, 0], [0, 0, 0], [0, 0, 0], [100, 100, 100])}
    signals = {"telemetry_status": "usable", "confidence": "high", "retrieval_gb_per_month_by_class": {}}
    recommendation, _ = choose_recommendation(scenarios, pattern, signals, {"full_retrievals_per_year": 1})
    assert recommendation == "DEEP_ARCHIVE"
    assert scenarios[1]["_burst_volume_unknown"] is True


@pytest.mark.parametrize(
    ("name", "signals", "pattern", "scenarios", "params", "expected"),
    [
        ("telemetry_unknown", {"telemetry_status": "unknown", "confidence": "low"}, {"pattern_label": "unknown", "write_only": False}, [_scenario("STANDARD_IA", 1, 2)], {}, None),
        ("low_confidence", {"telemetry_status": "usable", "confidence": "low"}, {"pattern_label": "steady_low_reads", "write_only": False}, [_scenario("STANDARD_IA", 1, 2), _scenario("GLACIER_IR", 2, 3)], {}, "STANDARD_IA"),
        ("write_only", {"telemetry_status": "usable", "confidence": "high"}, {"pattern_label": "log_sink", "write_only": True}, [_scenario("STANDARD_IA", 1, 2)], {}, "DIRECT_WRITE_STANDARD_IA"),
        ("observed_bursts", {"telemetry_status": "usable", "confidence": "high", "retrieval_gb_per_month_by_class": {"STANDARD_IA": 20}}, {"pattern_label": "periodic_bursts", "write_only": False, "history": _history([100, 0, 0], [0, 0, 0], [20, 0, 0], [100, 100, 100])}, [_scenario("STANDARD_IA", 1, 3), _scenario("DEEP_ARCHIVE", 4, 1.5)], {}, "STANDARD_IA"),
        ("unknown_bursts", {"telemetry_status": "usable", "confidence": "high", "retrieval_gb_per_month_by_class": {}}, {"pattern_label": "periodic_bursts", "write_only": False, "history": _history([100, 0, 0], [0, 0, 0], [0, 0, 0], [100, 100, 100])}, [_scenario("STANDARD_IA", 1, 3), _scenario("DEEP_ARCHIVE", 4, 1.5)], {}, "DEEP_ARCHIVE"),
        ("medium_high", {"telemetry_status": "usable", "confidence": "medium"}, {"pattern_label": "steady_low_reads", "write_only": False}, [_scenario("STANDARD_IA", 1, 2), _scenario("DEEP_ARCHIVE", 4, 3)], {}, "DEEP_ARCHIVE"),
        ("dominated", {"telemetry_status": "usable", "confidence": "high"}, {"pattern_label": "steady_low_reads", "write_only": False}, [_scenario("STANDARD_IA", 1, 2, ["TRANSITION_COST_DOMINATES"]), _scenario("DEEP_ARCHIVE", 4, 3, ["TRANSITION_COST_DOMINATES"])], {}, None),
        ("dominated_direct", {"telemetry_status": "usable", "confidence": "high"}, {"pattern_label": "log_sink", "write_only": True}, [_scenario("STANDARD_IA", 1, 2, ["TRANSITION_COST_DOMINATES"])], {}, "DIRECT_WRITE_STANDARD_IA"),
        ("no_eligible_rung", {"telemetry_status": "usable", "confidence": "high"}, {"pattern_label": "steady_low_reads", "write_only": False}, [_scenario("STANDARD_IA", 1, 1), _scenario("DEEP_ARCHIVE", 4, 2)], {"full_retrievals_per_year": 3}, None),
    ],
)
def test_each_recommendation_decision_table_row(name, signals, pattern, scenarios, params, expected):
    """Every §4.9 first-match row and modifier path has an executable fixture."""

    recommendation, _ = choose_recommendation(scenarios, pattern, signals, params)
    assert recommendation == expected, name


def test_recommendation_tie_breaks_by_deepest_rank_then_lowest_transition():
    """Equal tolerance candidates choose the deepest rank, then cheapest transition."""

    scenarios = [
        _scenario("STANDARD_IA", 1, 2, transition=5),
        _scenario("ONEZONE_IA", 1, 2, transition=1),
        _scenario("GLACIER_IR", 2, 1),
    ]
    recommendation, _ = choose_recommendation(
        scenarios,
        {"pattern_label": "steady_low_reads", "write_only": False},
        {"telemetry_status": "usable", "confidence": "high"},
        {"full_retrievals_per_year": 1},
    )
    assert recommendation == "ONEZONE_IA"


@pytest.mark.parametrize(
    ("cause", "scenarios", "params", "expected"),
    [
        (
            "size",
            [{**_scenario("STANDARD_IA", 1, 2), "status": "size_distribution_unavailable"}],
            {},
            "size_distribution_unavailable",
        ),
        (
            "pricing",
            [{**_scenario("STANDARD_IA", 1, 2), "status": "pricing_unavailable"}],
            {},
            "pricing_unavailable",
        ),
        (
            "transition",
            [
                {**_scenario("STANDARD_IA", 1, 2, ["TRANSITION_COST_DOMINATES"]), "status": "not_beneficial"},
                {**_scenario("DEEP_ARCHIVE", 4, 2, ["TRANSITION_COST_DOMINATES"]), "status": "not_beneficial"},
            ],
            {"transition_payback_months_max": 6},
            "one-time transition fee exceeds 6 months of savings on every candidate",
        ),
        (
            "retrieval",
            [_scenario("STANDARD_IA", 1, 0)],
            {"full_retrievals_per_year": 1},
            "no candidate's retrieval tolerance covers the assumed access pattern",
        ),
        ("generic", [], {}, "no eligible main-ladder candidate was available"),
    ],
)
def test_no_recommendation_reason_names_the_dominant_cause(
    cause, scenarios, params, expected
):
    """No-rung reasons follow the documented cause priority table."""

    recommendation, reason = choose_recommendation(
        scenarios,
        {"pattern_label": "steady_low_reads", "write_only": False},
        {"telemetry_status": "usable", "confidence": "high"},
        params,
    )

    assert recommendation is None, cause
    assert expected in reason, cause


@pytest.mark.parametrize(
    ("reads", "writes", "expected"),
    [
        ([0, 0, 0], [10, 0, 0], "write_once_cold"),
        ([0, 0, 0], [100, 100, 100], "log_sink"),
        ([100, 0, 0], [0, 0, 0], "periodic_bursts"),
        ([20, 20, 20], [0, 0, 0], "actively_read"),
        ([1, 1, 1], [0, 0, 0], "steady_low_reads"),
    ],
)
def test_pattern_fixture_for_each_known_label(reads, writes, expected):
    """The six-label registry has one explicit known-shape fixture per label."""

    requests_per_object = 0.2 if expected == "actively_read" else 0.01
    signals = {"telemetry_status": "usable", "cur_days_covered": 90, "requests_per_object_per_month": requests_per_object}
    assert classify_pattern(signals, _history(reads, writes))[0] == expected


def test_pattern_unknown_and_zero_write_cold_write_only_flags():
    """Unknown coverage stays unknown; a pre-window cold load is not write-only."""

    unknown, _ = classify_pattern({"telemetry_status": "unknown", "cur_days_covered": 0}, _history([0, 0, 0], [0, 0, 0]))
    assert unknown == "unknown"
    label, _ = classify_pattern({"telemetry_status": "usable", "cur_days_covered": 90}, _history([0, 0, 0], [0, 0, 0]))
    assert label == "write_once_cold"

    signals = {"telemetry_status": "usable", "cur_days_covered": 90}
    fields_label, _ = classify_pattern(signals, _history([0, 0, 0], [100, 0, 0]))
    assert fields_label == "write_once_cold"
    from app.services.s3_optimizer.patterns import build_pattern

    assert build_pattern(signals, _history([0, 0, 0], [100, 0, 0]))["write_only"] is True
    assert build_pattern(signals, _history([0, 0, 0], [0, 0, 0]))["write_only"] is False


def test_appendix_nested_key_sets_and_recommendation_agreement():
    """Every frozen nested Appendix block and recommendation flag is stable."""

    result = evaluate_bucket(_signals(), _seed_prices(), _cloudwatch(), {"resource_id": "bucket"})
    assert set(result["coverage"]) == {"cur_days_covered", "cur_first_day", "cur_last_day", "covered_days", "cloudwatch_days_covered"}
    assert result["coverage"]["covered_days"] == [point["date"] for point in _series(0)]
    assert set(result["current"]) == {"storage_class_breakdown", "monthly_storage_cost", "monthly_request_cost", "monthly_retrieval_cost"}
    assert set(result["signals"]) == {
        "monthly_data_read_requests",
        "monthly_data_write_requests",
        "monthly_list_requests",
        "monthly_config_requests",
        "monthly_tier1_requests",
        "monthly_tier2_requests",
        "restore_requests_by_class",
        "monthly_restore_requests",
        "retrieval_gb_per_month_by_class",
        "monthly_retrieval_gb_by_class",
        "requests_per_object_per_month",
        "retrieval_ratio_by_class",
        "per_class_observed_retrieval_cost",
        "standard_storage_cost_equivalent",
        "current_class_storage_cost",
        "request_cost_delta_vs_standard",
        "early_delete_cost",
        "monthly_early_delete_gb_hours_by_class",
        "config_requests",
        "window_totals",
    }
    assert set(result["recommendation"]) == {"policy", "reason"}
    assert set(result["pattern"]) == {"history", "peak_data_read_month", "peak_tier2_month", "zero_read_months", "implied_object_lifetime_days", "growth_pct_over_window", "small_object_share_estimate", "egress_share_of_bucket", "write_only", "pattern_label", "pattern_summary"}
    expected_scenario_keys = {"policy", "status", "ladder_rank", "ladder_group", "recommended", "recommendation_reason", "tolerated_full_retrievals_per_year", "monthly_costs", "transition_cost", "transition_cost_existing", "transition_cost_new_objects", "backlog_scenario", "savings_monthly_new_objects", "savings_monthly", "savings_yearly", "breakeven_months", "risks", "assumptions"}
    for scenario in result["scenarios"]:
        assert set(scenario) == expected_scenario_keys
        assert set(scenario["monthly_costs"]) == {"storage", "request", "retrieval_observed", "retrieval_stress", "it_monitoring"}
        assert set(scenario["savings_monthly"]) == {"low", "high"}
        assert set(scenario["savings_yearly"]) == {"low", "high"}
    recommended = [scenario["policy"] for scenario in result["scenarios"] if scenario["recommended"]]
    assert len(recommended) <= 1
    assert result["recommendation"]["policy"] == (recommended[0] if recommended else None)
