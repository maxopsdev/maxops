from __future__ import annotations

import random

from tests.test_asg_rightsizer_v2 import demand, shape, v2_run


def test_max_then_percentile_numeric_counterexample_is_preserved():
    current = shape("m6i.large", monthly=80)
    points = demand(0.0, 0.0, memory_low=10.0, memory_high=0.0, high_points=1)
    # One CPU-only spike and one different memory-only spike make both independent
    # p99 values zero, while p99(max(cpu, memory)) is ten. The service must retain
    # the per-timestamp max before selecting p99.
    for index, point in enumerate(points["points"]):
        if index == 98:
            point["cpu_used_instance_equivalents"] = 10.0
            point["memory_used_instance_equivalents"] = 0.0
        elif index == 99:
            point["cpu_used_instance_equivalents"] = 0.0
            point["memory_used_instance_equivalents"] = 10.0
        else:
            point["cpu_used_instance_equivalents"] = 0.0
            point["memory_used_instance_equivalents"] = 0.0
    result = v2_run(
        [current],
        points,
        current_min=20,
        current_desired=20,
        current_max=30,
    )
    balanced = result["tiers"]["balanced"]
    assert balanced["target_desired_capacity"] == 15
    assert balanced["evidence"]["desired_record"]["required"] == 15


def test_generated_monotonic_demand_and_configuration_invariants():
    rng = random.Random(20260716)
    current = shape("m6i.large", monthly=80)
    previous_desired = 0
    for used in sorted(rng.uniform(0.5, 5.5) for _ in range(20)):
        result = v2_run(
            [current],
            demand(used, used, memory_low=used, memory_high=used),
        )
        returned = result["recommendations"]
        for option in returned:
            assert option["monthly_savings"] > 0
            assert option["target_max_size"] == 20
            assert option["target_min_size"] <= option["target_desired_capacity"] <= 10
        balanced = result["tiers"]["balanced"]
        if balanced is not None:
            assert balanced in returned
            assert balanced["target_desired_capacity"] >= previous_desired
            previous_desired = balanced["target_desired_capacity"]


def test_catalog_order_does_not_change_v2_output():
    current = shape("m6i.large", monthly=80)
    candidates = [
        shape("c7i.large", monthly=60),
        shape("r7i.large", monthly=60),
        shape("m7i.large", monthly=60),
    ]
    payload = demand(2.0, 4.5, memory_low=2.0, memory_high=4.5)
    forward = v2_run([current, *candidates], payload)
    reverse = v2_run([current, *reversed(candidates)], payload)
    for key in ("recommendations", "tiers", "rejection_summary", "classification"):
        assert forward[key] == reverse[key]
