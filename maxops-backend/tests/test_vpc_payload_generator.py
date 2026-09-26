"""Validation tests for the VPC payload generator contract."""

from __future__ import annotations

from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from tests.payload_helpers import load_check_manifest, load_resource_config
from tests_generator.vpc.vpc_resource_creation import VPCPayloadResourceManager


pytestmark = [pytest.mark.unit, pytest.mark.payload]


class _NoOpWaiter:
    def wait(self, **kwargs) -> None:
        return None


class _FakeEC2:
    def __init__(self):
        self.create_vpc_calls = []
        self.describe_route_tables_calls = []
        self.create_flow_logs_calls = []
        self._vpc_counter = 0
        self._flow_counter = 0

    def create_vpc(self, **kwargs):
        self._vpc_counter += 1
        vpc_id = f"vpc-{self._vpc_counter:08d}"
        self.create_vpc_calls.append(kwargs)
        return {"Vpc": {"VpcId": vpc_id}}

    def get_waiter(self, waiter_name):
        assert waiter_name == "vpc_available"
        return _NoOpWaiter()

    def describe_route_tables(self, Filters):
        self.describe_route_tables_calls.append(Filters)
        vpc_id = Filters[0]["Values"][0]
        suffix = vpc_id.split("-", 1)[1]
        return {"RouteTables": [{"RouteTableId": f"rtb-{suffix}"}]}

    def create_flow_logs(self, **kwargs):
        self._flow_counter += 1
        flow_log_id = f"fl-{self._flow_counter:08d}"
        self.create_flow_logs_calls.append(kwargs)
        return {"FlowLogIds": [flow_log_id], "Unsuccessful": []}


class _FakeS3:
    def __init__(self):
        self.create_bucket_calls = []
        self.put_bucket_versioning_calls = []
        self.put_bucket_policy_calls = []

    def create_bucket(self, **kwargs):
        self.create_bucket_calls.append(kwargs)
        return {}

    def put_bucket_versioning(self, **kwargs):
        self.put_bucket_versioning_calls.append(kwargs)
        return {}

    def put_bucket_policy(self, **kwargs):
        self.put_bucket_policy_calls.append(kwargs)
        return {}


class _FakeSTS:
    def get_caller_identity(self):
        return {"Account": "999999999999"}


class TestVPCPayloadManifest:
    def test_placeholder_values_are_unique(self):
        resource_config = load_resource_config("vpc")
        placeholder_values = list(resource_config["placeholder_values"].values())

        assert len(placeholder_values) == len(set(placeholder_values))

    def test_negative_flow_log_scenario_reuses_endpoint_vpc(self):
        checks = load_check_manifest("vpc")
        scenario = checks["vpc_flow_logs_enabled"]["scenarios"]["pass_vpc_without_flow_logs"]

        assert scenario["resource_refs"] == ["endpoint_vpc"]
        assert scenario["expected_matches"] == []

    def test_create_resources_uses_four_vpcs_and_three_flow_logs(self, monkeypatch, tmp_path):
        manager = VPCPayloadResourceManager.__new__(VPCPayloadResourceManager)
        manager.config = load_resource_config("vpc")
        manager.state_path = tmp_path / ".vpc_capture_state.json"
        manager.region = "us-east-1"
        manager.timestamp = 1700000000
        manager.ec2 = _FakeEC2()
        manager.s3 = _FakeS3()
        manager.sts = _FakeSTS()

        monkeypatch.setattr(
            "tests_generator.vpc.vpc_resource_creation._write_json",
            lambda path, payload: None,
        )

        state = VPCPayloadResourceManager.create_resources(manager)

        assert list(state["resources"]) == [
            "endpoint_vpc",
            "disable_vpc",
            "expiration_vpc",
            "deletion_vpc",
        ]
        assert len(manager.ec2.create_vpc_calls) == 4
        assert len(manager.ec2.create_flow_logs_calls) == 3
        assert "NO_FLOW_VPC_ID" not in manager.config["placeholder_values"]
        assert state["placeholder_map"]["vpc-ENDPOINT"] == "vpc-00000001"
        assert state["placeholder_map"]["vpc-DISABLE"] == "vpc-00000002"
        assert state["placeholder_map"]["vpc-EXPIRATION"] == "vpc-00000003"
        assert state["placeholder_map"]["vpc-DELETION"] == "vpc-00000004"
        assert state["placeholder_map"]["rtb-ENDPOINT"] == "rtb-00000001"
        assert state["resources"]["endpoint_vpc"]["flow_log_ids"] == []

    def test_cleanup_ignores_missing_flow_logs_removed_by_actions(self):
        manager = VPCPayloadResourceManager.__new__(VPCPayloadResourceManager)
        manager.state_path = Path("tests_generator/vpc/.vpc_capture_state.json")

        class FakeEC2:
            def delete_flow_logs(self, FlowLogIds):
                raise ClientError(
                    {
                        "Error": {
                            "Code": "InvalidFlowLogId.NotFound",
                            "Message": "already removed",
                        }
                    },
                    "DeleteFlowLogs",
                )

            def delete_vpc(self, VpcId):
                return {}

        class FakeS3:
            def get_paginator(self, operation_name):
                class Paginator:
                    def paginate(self, **kwargs):
                        return iter([{}])

                return Paginator()

            def delete_bucket(self, Bucket):
                return {}

        manager.ec2 = FakeEC2()
        manager.s3 = FakeS3()
        state = {
            "resources": {
                "disable_vpc": {
                    "resource_id": "vpc-12345678",
                    "flow_log_ids": ["fl-12345678"],
                    "bucket_name": "bucket-a",
                }
            },
            "vpc_endpoints": [],
        }

        summary = VPCPayloadResourceManager.cleanup_resources(manager, state=state)

        assert summary["cleanup_status"] == "success"
        assert summary["errors"] == []
