"""Offline adapter tests backed by checked-in RDS payload fixtures."""

from __future__ import annotations

import pytest

from tests.payload_helpers import build_rds_payload_adapter


pytestmark = [pytest.mark.unit, pytest.mark.payload, pytest.mark.rds]


class TestRDSPayloadAdapter:
    def test_rds_instance_payload_is_normalized(self):
        adapter = build_rds_payload_adapter("rds_idle_databases", "flag_idle_database")

        resources = adapter.get_resources("rds", {"state": "available"}, region="us-east-1")

        assert len(resources) == 1
        resource = resources[0]
        assert resource["resource_id"] == "maxops-payload-rds-idle-graviton"
        assert resource["resource_type"] == "rds"
        assert resource["resource_name"] == "maxops-payload-rds-idle-graviton"
        assert resource["state"] == "available"
        assert resource["instance_type"] == "db.t4g.micro"
        assert resource["engine"] == "mysql"
        assert resource["metadata"]["DBInstanceClass"] == "db.t4g.micro"

    def test_rds_instance_alias_resource_type_reuses_same_payload(self):
        adapter = build_rds_payload_adapter("rds_non_graviton_instance_class", "flag_non_graviton_instance")

        resources = adapter.get_resources("rds_instance", {}, region="us-east-1")

        assert len(resources) == 1
        assert resources[0]["resource_id"] == "maxops-payload-rds-non-graviton"
        assert resources[0]["instance_type"] == "db.t3.micro"

    def test_rds_utilization_parses_metric_statistics_payloads(self):
        adapter = build_rds_payload_adapter("rds_idle_databases", "flag_idle_database")

        utilization = adapter.get_resource_utilization(
            "maxops-payload-rds-idle-graviton",
            "rds",
            start_date=None,
            end_date=None,
            region="us-east-1",
        )

        assert utilization["cpuutilization"] == 1.25
        assert utilization["databaseconnections"] == 1.0
        assert utilization["readiops"] == 0.5
        assert utilization["writeiops"] == 0.25

    def test_rds_utilization_defaults_to_zero_without_datapoints(self):
        adapter = build_rds_payload_adapter("rds_idle_databases", "edge_no_metric_datapoints")

        utilization = adapter.get_resource_utilization(
            "maxops-payload-rds-idle-graviton",
            "rds",
            start_date=None,
            end_date=None,
            region="us-east-1",
        )

        assert utilization["cpuutilization"] == 0.0
        assert utilization["databaseconnections"] == 0.0
        assert utilization["readiops"] == 0.0
        assert utilization["writeiops"] == 0.0
