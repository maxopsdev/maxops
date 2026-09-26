"""Offline adapter tests backed by checked-in EC2 payload fixtures."""

from __future__ import annotations

import pytest

from app.adapters.aws.adapter import AWSAdapter
from tests.payload_helpers import (
    build_ec2_payload_adapter,
)


pytestmark = [pytest.mark.unit, pytest.mark.payload, pytest.mark.ec2]


def _build_adapter_for_idle_scenario(scenario: str) -> AWSAdapter:
    return build_ec2_payload_adapter("ec2_idle_instances", scenario)


def _build_adapter_for_unused_scenario(scenario: str) -> AWSAdapter:
    return build_ec2_payload_adapter("ec2_unused_instances", scenario)


def _build_adapter_for_graviton_scenario(scenario: str) -> AWSAdapter:
    return build_ec2_payload_adapter("ec2_graviton_candidate", scenario)


class TestEC2PayloadAdapter:
    def test_running_instance_payload_is_normalized(self):
        adapter = _build_adapter_for_idle_scenario("pass_idle_instance")

        resources = adapter.get_resources("ec2", {"state": "running"}, region="us-east-1")

        assert len(resources) == 1
        resource = resources[0]
        assert resource["resource_id"] == "i-PLACEHOLDER-INSTANCE"
        assert resource["resource_type"] == "ec2"
        assert resource["resource_name"] == "maxops-payload-ec2-capture"
        assert resource["state"] == "running"
        assert resource["region"] == "us-east-1"
        assert resource["instance_type"] == "t3a.micro"
        assert resource["metadata"]["cloudwatch_agent_installed"] is None
        assert resource["metadata"]["network_bandwidth_weighting"] == "default"

    def test_stopped_instance_payload_respects_filtering(self):
        adapter = _build_adapter_for_unused_scenario("pass_stopped_instance")

        resources = adapter.get_resources("ec2", {"state": "stopped"}, region="us-east-1")

        assert len(resources) == 1
        assert resources[0]["resource_id"] == "i-PLACEHOLDER-INSTANCE"
        assert resources[0]["state"] == "stopped"

    def test_ec2_utilization_parses_get_metric_data_rollups_and_history(self):
        adapter = _build_adapter_for_idle_scenario("pass_idle_instance")

        utilization = adapter.get_resource_utilization(
            "i-PLACEHOLDER-INSTANCE",
            "ec2",
            start_date=None,
            end_date=None,
            region="us-east-1",
        )

        assert utilization["cpuutilization"] == 1.25
        assert utilization["networkin"] == 120.0
        assert utilization["networkout"] == 180.0
        assert utilization["memoryutilization"] == 28.0

        cpu_history = utilization["metric_history"]["cpuutilization"]
        assert len(cpu_history["timestamps"]) == 1
        assert len(cpu_history["average"]) == 1
        assert cpu_history["maximum"][0] == 4.5
        assert cpu_history["p90"] == []
        assert cpu_history["p95"] == []
        assert cpu_history["p99"] == []
        assert utilization["metric_summary"]["cpuutilization"] == {
            "average": 1.25,
            "maximum": 4.5,
            "p90": 1.25,
            "p95": 1.25,
            "p99": 1.25,
            "sample_count": 1,
            "period_seconds": 300,
        }

    def test_ec2_utilization_defaults_to_zero_without_metric_data_points(self):
        adapter = _build_adapter_for_idle_scenario("edge_no_datapoints")

        utilization = adapter.get_resource_utilization(
            "i-PLACEHOLDER-INSTANCE",
            "ec2",
            start_date=None,
            end_date=None,
            region="us-east-1",
        )

        assert utilization["cpuutilization"] == 0.0
        assert utilization["networkin"] == 0.0
        assert utilization["networkout"] == 0.0
        assert utilization["memoryutilization"] == 0.0
        assert utilization["metric_history"]["cpuutilization"]["timestamps"] == []
        assert utilization["metric_history"]["cpuutilization"]["average"] == []
        assert utilization["metric_summary"]["cpuutilization"] == {
            "average": None,
            "maximum": None,
            "p90": None,
            "p95": None,
            "p99": None,
            "sample_count": 0,
            "period_seconds": 300,
        }

    def test_graviton_payload_supports_running_instance_inventory(self):
        adapter = _build_adapter_for_graviton_scenario("flag_non_graviton_instance")

        resources = adapter.get_resources("ec2", {"state": "running"}, region="us-east-1")

        assert len(resources) == 1
        assert resources[0]["resource_id"] == "i-PLACEHOLDER-INSTANCE"
        assert resources[0]["instance_type"] == "t3a.micro"
