"""Offline RDS check tests backed by saved payload fixtures."""

from __future__ import annotations

import pytest

from app.checks.rds.idle_databases import check_rds_idle_databases
from app.checks.rds.non_graviton import check_rds_non_graviton_instance_class
from tests.payload_helpers import build_rds_payload_adapter, load_scenario_payloads


pytestmark = [pytest.mark.unit, pytest.mark.payload, pytest.mark.rds]


class TestRDSPayloadBackedChecks:
    def test_idle_check_matches_low_cpu_and_connection_payloads(self):
        payloads = load_scenario_payloads("rds", "rds_idle_databases", "flag_idle_database")
        adapter = build_rds_payload_adapter("rds_idle_databases", "flag_idle_database")
        metadata = payloads["capture_metadata.json"]

        results = check_rds_idle_databases(
            aws_adapter=adapter,
            **metadata["check_parameters"],
        )

        assert [resource["resource_id"] for resource in results] == metadata["expected_matches"]
        result_metadata = results[0]["metadata"]
        assert result_metadata["recommended_action"] == "stop"
        assert result_metadata["avg_cpu_utilization"] == 1.25
        assert result_metadata["avg_connections"] == 1.0

    def test_idle_check_excludes_busy_database(self):
        adapter = build_rds_payload_adapter("rds_idle_databases", "pass_busy_database")

        results = check_rds_idle_databases(
            aws_adapter=adapter,
            idle_days=7,
            cpu_threshold=5.0,
            connections_threshold=5,
            region="us-east-1",
        )

        assert results == []

    def test_idle_check_treats_missing_datapoints_as_zero(self):
        adapter = build_rds_payload_adapter("rds_idle_databases", "edge_no_metric_datapoints")

        results = check_rds_idle_databases(
            aws_adapter=adapter,
            idle_days=7,
            cpu_threshold=5.0,
            connections_threshold=5,
            region="us-east-1",
        )

        assert [resource["resource_id"] for resource in results] == ["maxops-payload-rds-idle-graviton"]

    def test_non_graviton_check_flags_non_graviton_instance(self):
        payloads = load_scenario_payloads("rds", "rds_non_graviton_instance_class", "flag_non_graviton_instance")
        adapter = build_rds_payload_adapter("rds_non_graviton_instance_class", "flag_non_graviton_instance")
        metadata = payloads["capture_metadata.json"]

        results = check_rds_non_graviton_instance_class(
            aws_adapter=adapter,
            **metadata["check_parameters"],
        )

        assert [resource["resource_id"] for resource in results] == metadata["expected_matches"]
        assert results[0]["metadata"]["recommended_action"] == "rds_migrate_graviton"
        assert results[0]["metadata"]["recommended_actions"] == [
            "rds_migrate_graviton"
        ]
        assert results[0]["metadata"]["db_instance_class"] == "db.t3.micro"

    def test_non_graviton_check_skips_graviton_instance(self):
        adapter = build_rds_payload_adapter("rds_non_graviton_instance_class", "pass_graviton_instance")

        results = check_rds_non_graviton_instance_class(
            aws_adapter=adapter,
            region="us-east-1",
        )

        assert results == []
