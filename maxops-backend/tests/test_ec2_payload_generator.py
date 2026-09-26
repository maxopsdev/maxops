"""Validation tests for the EC2 payload generator contract."""

from __future__ import annotations

from pathlib import Path

from botocore.exceptions import ClientError
from botocore.exceptions import WaiterError
from tests.payload_helpers import (
    find_real_aws_identifiers,
    load_action_manifest,
    load_action_payloads,
    load_check_manifest,
    load_resource_config,
    load_scenario_payloads,
)

import pytest

from tests_generator.ec2.ec2_payloads_generator import capture_payloads
from tests_generator.ec2.ec2_resource_creation import EC2PayloadResourceManager


pytestmark = [pytest.mark.unit, pytest.mark.payload, pytest.mark.ec2]
ACTION_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "payloads" / "ec2" / "actions"


class TestEC2PayloadManifest:
    def test_all_manifest_payload_files_exist(self):
        checks = load_check_manifest("ec2")

        for check_id, check_config in checks.items():
            for scenario_name, scenario_config in check_config["scenarios"].items():
                payloads = load_scenario_payloads("ec2", check_id, scenario_name)
                for filename in scenario_config["payload_files"]:
                    assert filename in payloads, f"Missing payload file {filename} for {check_id}/{scenario_name}"

    def test_placeholder_values_are_unique(self):
        resource_config = load_resource_config("ec2")
        placeholder_values = list(resource_config["placeholder_values"].values())

        assert len(placeholder_values) == len(set(placeholder_values))

    def test_payloads_do_not_leak_real_aws_identifiers(self):
        checks = load_check_manifest("ec2")

        for check_id, check_config in checks.items():
            for scenario_name in check_config["scenarios"]:
                payloads = load_scenario_payloads("ec2", check_id, scenario_name)
                leaked_ids = find_real_aws_identifiers(payloads)
                assert leaked_ids == [], f"Found non-sanitized AWS identifiers in {check_id}/{scenario_name}: {leaked_ids}"

    def test_metric_data_payloads_use_minimal_non_synthetic_series(self):
        payloads = load_scenario_payloads("ec2", "ec2_idle_instances", "pass_idle_instance")

        for filename in ["get_metric_data_ec2.json", "get_metric_data_memory.json"]:
            for result in payloads[filename]["MetricDataResults"]:
                timestamps = result["Timestamps"]
                values = result["Values"]
                assert len(timestamps) <= 1
                assert len(values) <= 1

    def test_fail_busy_instance_payload_matches_manifest_overrides(self):
        checks = load_check_manifest("ec2")
        payloads = load_scenario_payloads("ec2", "ec2_idle_instances", "fail_busy_instance")
        overrides = checks["ec2_idle_instances"]["scenarios"]["fail_busy_instance"]["metric_overrides"]["ec2"]
        results = {item["Id"]: item for item in payloads["get_metric_data_ec2.json"]["MetricDataResults"]}

        assert results["cpu_average"]["Values"] == [overrides["CPUUtilization"]["Average"]]
        assert results["cpu_maximum"]["Values"] == [overrides["CPUUtilization"]["Maximum"]]
        assert results["cpu_p90"]["Values"] == [overrides["CPUUtilization"]["p90"]]
        assert results["cpu_p95"]["Values"] == [overrides["CPUUtilization"]["p95"]]
        assert results["cpu_p99"]["Values"] == [overrides["CPUUtilization"]["p99"]]

    @pytest.mark.skipif(not ACTION_FIXTURE_ROOT.exists(), reason="EC2 action payloads have not been captured from AWS yet.")
    def test_action_capture_metadata_matches_manifest(self):
        actions = load_action_manifest("ec2")

        for action_key, action_config in actions.items():
            payloads = load_action_payloads("ec2", action_key, action_config["scenario"])
            metadata = payloads.get("capture_metadata.json")
            if metadata is None:
                continue

            assert metadata["action_key"] == action_key
            assert metadata["capture_source"] == "aws"
            assert metadata["check_id"] == action_config["check_id"]
            assert metadata["expected_status"] == action_config["expected_status"]
            assert metadata["expected_message"] == action_config["expected_message"]
            assert [
                (call["service"], call["operation"])
                for call in metadata["calls"]
            ] == [
                (call["service"], call["operation"])
                for call in action_config["expected_calls"]
            ]
            assert all(call["response_file"] in payloads for call in metadata["calls"])
            assert find_real_aws_identifiers(payloads) == []

    def test_cleanup_summary_success(self, monkeypatch):
        manager = EC2PayloadResourceManager.__new__(EC2PayloadResourceManager)
        manager.state_path = Path("tests_generator/ec2/.ec2_capture_state.json")

        class FakeWaiter:
            def wait(self, **kwargs):
                return None

        class FakeEC2:
            def terminate_instances(self, InstanceIds):
                return {"TerminatingInstances": InstanceIds}

            def get_waiter(self, name):
                assert name == "instance_terminated"
                return FakeWaiter()

        manager.ec2 = FakeEC2()
        state = {
            "resources": {
                "capture_instance": {
                    "instance_id": "i-abc12345",
                }
            }
        }

        summary = EC2PayloadResourceManager.cleanup_resources(manager, state=state)

        assert summary["cleanup_status"] == "success"
        assert summary["resource_counts"] == {"targeted": 1, "succeeded": 1, "failed": 0}
        assert summary["resources_targeted"] == ["i-abc12345"]
        assert summary["errors"] == []

    def test_cleanup_summary_failure(self):
        manager = EC2PayloadResourceManager.__new__(EC2PayloadResourceManager)
        manager.state_path = Path("tests_generator/ec2/.ec2_capture_state.json")

        class FakeEC2:
            def terminate_instances(self, InstanceIds):
                raise ClientError({"Error": {"Code": "UnauthorizedOperation", "Message": "boom"}}, "TerminateInstances")

        manager.ec2 = FakeEC2()
        state = {
            "resources": {
                "capture_instance": {
                    "instance_id": "i-abc12345",
                }
            }
        }

        summary = EC2PayloadResourceManager.cleanup_resources(manager, state=state)

        assert summary["cleanup_status"] == "failure"
        assert summary["resource_counts"] == {"targeted": 1, "succeeded": 0, "failed": 1}
        assert summary["errors"]

    def test_cleanup_counts_missing_elastic_ip_as_success(self):
        manager = EC2PayloadResourceManager.__new__(EC2PayloadResourceManager)
        manager.state_path = Path("tests_generator/ec2/.ec2_capture_state.json")

        class FakeEC2:
            def release_address(self, **kwargs):
                raise ClientError(
                    {
                        "Error": {
                            "Code": "InvalidAllocationID.NotFound",
                            "Message": "already gone",
                        }
                    },
                    "ReleaseAddress",
                )

        manager.ec2 = FakeEC2()
        state = {
            "resources": {
                "unused_elastic_ip": {
                    "allocation_id": "eipalloc-abc12345",
                }
            }
        }

        summary = EC2PayloadResourceManager.cleanup_resources(manager, state=state)

        assert summary["cleanup_status"] == "success"
        assert summary["resource_counts"] == {"targeted": 1, "succeeded": 1, "failed": 0}
        assert summary["resources_targeted"] == ["eipalloc-abc12345"]
        assert summary["errors"] == []

    def test_cleanup_counts_missing_volume_from_waiter_as_success(self):
        manager = EC2PayloadResourceManager.__new__(EC2PayloadResourceManager)
        manager.state_path = Path("tests_generator/ec2/.ec2_capture_state.json")

        class MissingVolumeWaiter:
            def wait(self, **kwargs):
                raise WaiterError(
                    name="volume_available",
                    reason="resource missing",
                    last_response={
                        "Error": {
                            "Code": "InvalidVolume.NotFound",
                            "Message": "already deleted",
                        }
                    },
                )

        class FakeEC2:
            def get_waiter(self, name):
                assert name == "volume_available"
                return MissingVolumeWaiter()

        manager.ec2 = FakeEC2()
        state = {
            "resources": {
                "leave_volume_instance": {
                    "preserve_root_volume": True,
                    "volume_ids": ["vol-missing"],
                }
            }
        }

        summary = EC2PayloadResourceManager.cleanup_resources(manager, state=state)

        assert summary["cleanup_status"] == "success"
        assert summary["resource_counts"] == {"targeted": 1, "succeeded": 1, "failed": 0}
        assert summary["resources_targeted"] == ["vol-missing"]
        assert summary["errors"] == []

    def test_cleanup_removes_preserved_volume_and_action_snapshot(self):
        manager = EC2PayloadResourceManager.__new__(EC2PayloadResourceManager)
        manager.state_path = Path("tests_generator/ec2/.ec2_capture_state.json")

        class FakeWaiter:
            def __init__(self, calls, name):
                self.calls = calls
                self.name = name

            def wait(self, **kwargs):
                self.calls.append((f"wait:{self.name}", kwargs))

        class FakeEC2:
            def __init__(self):
                self.calls = []

            def terminate_instances(self, **kwargs):
                self.calls.append(("terminate_instances", kwargs))
                return {}

            def get_waiter(self, name):
                return FakeWaiter(self.calls, name)

            def delete_volume(self, **kwargs):
                self.calls.append(("delete_volume", kwargs))
                return {}

            def delete_snapshot(self, **kwargs):
                self.calls.append(("delete_snapshot", kwargs))
                return {}

        manager.ec2 = FakeEC2()
        state = {
            "resources": {
                "leave_volume_instance": {
                    "instance_id": "i-leave",
                    "preserve_root_volume": True,
                    "volume_ids": ["vol-leave"],
                }
            },
            "snapshots": [{"snapshot_id": "snap-action"}],
        }

        summary = EC2PayloadResourceManager.cleanup_resources(manager, state=state)

        assert summary["cleanup_status"] == "success"
        assert summary["resource_counts"] == {
            "targeted": 3,
            "succeeded": 3,
            "failed": 0,
        }
        assert manager.ec2.calls == [
            ("terminate_instances", {"InstanceIds": ["i-leave"]}),
            (
                "wait:instance_terminated",
                {
                    "InstanceIds": ["i-leave"],
                    "WaiterConfig": {"Delay": 5, "MaxAttempts": 60},
                },
            ),
            (
                "wait:volume_available",
                {
                    "VolumeIds": ["vol-leave"],
                    "WaiterConfig": {"Delay": 5, "MaxAttempts": 60},
                },
            ),
            ("delete_volume", {"VolumeId": "vol-leave"}),
            (
                "wait:snapshot_completed",
                {
                    "SnapshotIds": ["snap-action"],
                    "WaiterConfig": {"Delay": 5, "MaxAttempts": 60},
                },
            ),
            ("delete_snapshot", {"SnapshotId": "snap-action"}),
        ]

    def test_action_manifest_uses_disposable_instances_for_destructive_actions(self):
        actions = load_action_manifest("ec2")
        config = load_resource_config("ec2")

        assert actions["terminate"]["resource_ref"] == "terminate_instance"
        assert (
            actions["terminate_with_snapshot"]["resource_ref"]
            == "snapshot_terminate_instance"
        )
        assert (
            actions["terminate_leave_volume"]["resource_ref"]
            == "leave_volume_instance"
        )
        assert actions["migrate_to_graviton"]["capture_enabled"] is False
        assert set(config["action_instance_definitions"]) == {
            "terminate_instance",
            "snapshot_terminate_instance",
            "leave_volume_instance",
        }

    def test_payload_generator_triggers_cleanup(self, monkeypatch, tmp_path, capsys):
        config_path = tmp_path / "resource_config.json"
        checks_path = tmp_path / "checks.json"
        state_path = tmp_path / "state.json"

        config_path.write_text(
            """
{
  "profile": null,
  "region": "us-east-1",
  "placeholder_values": {
    "INSTANCE_ID": "i-PLACEHOLDER-INSTANCE",
    "REGION": "us-east-1"
  },
  "capture_window_hours": 24
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        checks_path.write_text(
            """
{
  "ec2_unused_instances": {
    "scenarios": {
      "fail_no_stopped_instances": {
        "description": "negative",
        "base_capture": "empty_stopped",
        "expected_matches": [],
        "check_parameters": {
          "region": "{{REGION}}"
        },
        "payload_files": [
          "describe_instances.json",
          "capture_metadata.json"
        ]
      }
    }
  }
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        state_path.write_text(
            """
{
  "region": "us-east-1",
  "placeholder_map": {
    "i-PLACEHOLDER-INSTANCE": "i-abc12345"
  },
  "resources": {
    "capture_instance": {
      "instance_id": "i-abc12345"
    }
  }
}
""".strip()
            + "\n",
            encoding="utf-8",
        )

        cleanup_calls = []

        def fake_cleanup(config_path_arg, state_arg, state_path_arg):
            cleanup_calls.append((config_path_arg, state_arg, state_path_arg))
            return {
                "service": "ec2",
                "cleanup_attempted": True,
                "cleanup_status": "success",
                "state_file": str(state_path_arg),
                "resource_counts": {"targeted": 1, "succeeded": 1, "failed": 0},
                "resources_targeted": ["i-abc12345"],
                "errors": [],
            }

        monkeypatch.setattr("tests_generator.ec2.ec2_payloads_generator.PAYLOAD_ROOT", tmp_path / "payloads")
        monkeypatch.setattr(
            "tests_generator.ec2.ec2_payloads_generator._capture_baselines",
            lambda session, region, instance_id, substitutions: {
                "running_describe": {"Reservations": []},
                "stopped_describe": {"Reservations": []},
                "get_metric_data_ec2.json": {"MetricDataResults": []},
                "get_metric_data_memory.json": {"MetricDataResults": []},
            },
        )
        monkeypatch.setattr("tests_generator.ec2.ec2_payloads_generator._finalize_cleanup", fake_cleanup)

        # Every AWS entry point below is mocked, so the billable-capture
        # gate is not what this test is about. Neutralise it here rather
        # than setting MAXOPS_RUN_AWS_INTEGRATION_TESTS.
        monkeypatch.setattr("tests_generator.ec2.ec2_payloads_generator.require_capture_gate", lambda *a, **k: None)

        capture_payloads(config_path, checks_path, state_path, capture_actions=False)

        payload_dir = tmp_path / "payloads" / "ec2_unused_instances" / "fail_no_stopped_instances"
        assert (payload_dir / "describe_instances.json").exists()
        assert cleanup_calls
        out = capsys.readouterr().out
        assert '"cleanup_status": "success"' in out
