"""Offline EC2 check tests backed by saved payload fixtures."""

from __future__ import annotations

import pytest

from app.checks.ec2.graviton_candidate import check_ec2_graviton_candidate
from app.checks.ec2.idle_instances import check_ec2_idle_instances
from app.checks.ec2.unused_instances import check_ec2_unused_instances
from tests.payload_helpers import FakeEC2CheckAdapter
from tests.test_ec2_payload_adapter import (
    _build_adapter_for_graviton_scenario,
    _build_adapter_for_idle_scenario,
    _build_adapter_for_unused_scenario,
)


pytestmark = [pytest.mark.unit, pytest.mark.payload, pytest.mark.ec2]


class TestEC2PayloadBackedChecks:
    def test_idle_check_matches_low_average_and_low_peak_cpu_from_payloads(self):
        adapter = _build_adapter_for_idle_scenario("pass_idle_instance")

        results = check_ec2_idle_instances(
            aws_adapter=adapter,
            idle_days=7,
            cpu_threshold=5.0,
            cpu_max_threshold=15.0,
            cpu_p90_threshold=6.0,
            cpu_p95_threshold=8.0,
            cpu_p99_threshold=10.0,
            region="us-east-1",
        )

        assert [resource["resource_id"] for resource in results] == ["i-PLACEHOLDER-INSTANCE"]
        metadata = results[0]["metadata"]
        assert metadata["instance_type"] == "t3a.micro"
        assert metadata["recommended_action"] == "stop"
        assert metadata["recommended_actions"] == [
            "stop",
            "schedule_off_hours",
            "migrate_to_graviton",
        ]
        assert metadata["avg_cpu_utilization"] == 1.25
        assert metadata["cpu_maximum"] == 4.5

    def test_idle_check_excludes_instance_with_low_average_but_high_peaks(self):
        adapter = _build_adapter_for_idle_scenario("fail_busy_instance")

        results = check_ec2_idle_instances(
            aws_adapter=adapter,
            idle_days=7,
            cpu_threshold=5.0,
            cpu_max_threshold=15.0,
            cpu_p90_threshold=6.0,
            cpu_p95_threshold=8.0,
            cpu_p99_threshold=10.0,
            region="us-east-1",
        )

        assert results == []

    def test_idle_check_does_not_flag_missing_metric_data(self):
        adapter = _build_adapter_for_idle_scenario("edge_no_datapoints")

        results = check_ec2_idle_instances(
            aws_adapter=adapter,
            idle_days=7,
            cpu_threshold=5.0,
            cpu_max_threshold=15.0,
            cpu_p90_threshold=6.0,
            cpu_p95_threshold=8.0,
            cpu_p99_threshold=10.0,
            region="us-east-1",
        )

        assert results == []

    def test_unused_check_returns_stopped_instance_from_payloads(self):
        adapter = _build_adapter_for_unused_scenario("pass_stopped_instance")

        results = check_ec2_unused_instances(
            aws_adapter=adapter,
            stopped_days=30,
            region="us-east-1",
        )

        assert [resource["resource_id"] for resource in results] == ["i-PLACEHOLDER-INSTANCE"]
        metadata = results[0]["metadata"]
        assert metadata["instance_type"] == "t3a.micro"
        assert metadata["recommended_action"] == "terminate"
        assert metadata["recommended_actions"] == [
            "terminate",
            "terminate_with_snapshot",
            "terminate_leave_volume",
        ]

    def test_unused_check_returns_empty_when_no_stopped_instances_exist(self):
        adapter = _build_adapter_for_unused_scenario("fail_no_stopped_instances")

        results = check_ec2_unused_instances(
            aws_adapter=adapter,
            stopped_days=30,
            region="us-east-1",
        )

        assert results == []

    def test_graviton_check_flags_non_graviton_instance_from_payloads(self):
        adapter = _build_adapter_for_graviton_scenario("flag_non_graviton_instance")

        results = check_ec2_graviton_candidate(
            aws_adapter=adapter,
            allowed_families=["m", "c", "r", "t"],
            region="us-east-1",
        )

        assert [resource["resource_id"] for resource in results] == ["i-PLACEHOLDER-INSTANCE"]
        metadata = results[0]["metadata"]
        assert metadata["recommended_action"] == "migrate_to_graviton"
        assert metadata["recommended_instance_type"] == "t4g.micro"

    def test_graviton_check_skips_graviton_instance_from_payloads(self):
        adapter = _build_adapter_for_graviton_scenario("pass_graviton_instance")

        results = check_ec2_graviton_candidate(
            aws_adapter=adapter,
            allowed_families=["m", "c", "r", "t"],
            region="us-east-1",
        )

        assert results == []


class TestEC2PureCheckLogic:
    def test_idle_check_only_flags_instance_with_low_average_and_low_peaks(self):
        idle_instance = {
            "resource_id": "i-PLACEHOLDER-IDLE",
            "resource_type": "ec2",
            "resource_name": "maxops-payload-ec2-idle",
            "region": "us-east-1",
            "state": "running",
            "instance_type": "t3.micro",
            "metadata": {},
        }
        busy_instance = {
            "resource_id": "i-PLACEHOLDER-BUSY",
            "resource_type": "ec2",
            "resource_name": "maxops-payload-ec2-busy",
            "region": "us-east-1",
            "state": "running",
            "instance_type": "t3.micro",
            "metadata": {},
        }
        adapter = FakeEC2CheckAdapter(
            resources_by_state={"running": [idle_instance, busy_instance]},
            utilization={
                "i-PLACEHOLDER-IDLE": {
                    "cpuutilization": 2.0,
                    "networkin": 100.0,
                    "networkout": 120.0,
                    "metric_summary": {
                        "cpuutilization": {
                            "maximum": 4.0,
                            "p90": 3.0,
                            "p95": 3.5,
                            "p99": 3.8,
                        }
                    },
                    "metric_history": {
                        "cpuutilization": {
                            "timestamps": ["2026-04-04T13:55:00+00:00"],
                            "average": [2.0],
                            "maximum": [4.0],
                            "p90": [3.0],
                            "p95": [3.5],
                            "p99": [3.8],
                        }
                    },
                },
                "i-PLACEHOLDER-BUSY": {
                    "cpuutilization": 3.5,
                    "networkin": 3000.0,
                    "networkout": 5000.0,
                    "metric_summary": {
                        "cpuutilization": {
                            "maximum": 22.0,
                            "p90": 9.0,
                            "p95": 12.0,
                            "p99": 16.0,
                        }
                    },
                    "metric_history": {
                        "cpuutilization": {
                            "timestamps": ["2026-04-04T13:55:00+00:00"],
                            "average": [3.5],
                            "maximum": [22.0],
                            "p90": [9.0],
                            "p95": [12.0],
                            "p99": [16.0],
                        }
                    },
                },
            },
        )

        results = check_ec2_idle_instances(
            aws_adapter=adapter,
            idle_days=7,
            cpu_threshold=5.0,
            cpu_max_threshold=15.0,
            cpu_p90_threshold=6.0,
            cpu_p95_threshold=8.0,
            cpu_p99_threshold=10.0,
            region="us-east-1",
        )

        assert [resource["resource_id"] for resource in results] == ["i-PLACEHOLDER-IDLE"]
