"""Validation tests for the EBS payload generator contract."""

from __future__ import annotations

from pathlib import Path

from botocore.exceptions import ClientError
import pytest

from tests.payload_helpers import (
    find_forbidden_strings,
    find_real_aws_identifiers,
    load_check_manifest,
    load_resource_config,
    load_scenario_payloads,
)
from tests_generator.ebs.ebs_payloads_generator import capture_payloads
from tests_generator.ebs.ebs_resource_creation import EBSPayloadResourceManager


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "payloads" / "ebs"
pytestmark = [pytest.mark.unit, pytest.mark.payload, pytest.mark.ebs]


def _render_manifest_value(value, substitutions):
    if isinstance(value, dict):
        return {key: _render_manifest_value(item, substitutions) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_manifest_value(item, substitutions) for item in value]
    if isinstance(value, str):
        rendered = value
        for token, replacement in substitutions.items():
            rendered = rendered.replace(f"{{{{{token}}}}}", str(replacement))
        return rendered
    return value


class TestEBSPayloadManifest:
    def test_placeholder_values_are_unique(self):
        resource_config = load_resource_config("ebs")
        placeholder_values = list(resource_config["placeholder_values"].values())

        assert len(placeholder_values) == len(set(placeholder_values))

    def test_capture_resources_use_minimum_size_and_iops(self):
        resource_config = load_resource_config("ebs")
        volumes = resource_config["volume_definitions"]

        assert volumes["unattached_gp3"]["size"] == 1
        assert volumes["attached_gp2"]["size"] == 1
        assert volumes["provisioned_io1"]["size"] == 4
        assert volumes["provisioned_io1"]["iops"] == 200
        assert resource_config["capture_instance"]["instance_type"] == "t3a.nano"

    def test_only_metric_override_sections_are_allowed(self):
        checks = load_check_manifest("ebs")

        for check_id, check_config in checks.items():
            for scenario_name, scenario in check_config["scenarios"].items():
                assert "inventory_overrides" not in scenario, f"{check_id}/{scenario_name} must not use inventory overrides"
                assert "response_overrides" not in scenario, f"{check_id}/{scenario_name} must not use response overrides"
                assert "describe_overrides" not in scenario, f"{check_id}/{scenario_name} must not use describe overrides"
                assert "tag_overrides" not in scenario, f"{check_id}/{scenario_name} must not use tag overrides"

    @pytest.mark.skipif(not FIXTURE_ROOT.exists(), reason="EBS payload fixtures have not been captured from AWS yet.")
    def test_all_manifest_payload_files_exist(self):
        checks = load_check_manifest("ebs")

        for check_id, check_config in checks.items():
            for scenario_name, scenario_config in check_config["scenarios"].items():
                payloads = load_scenario_payloads("ebs", check_id, scenario_name)
                for filename in scenario_config["payload_files"]:
                    assert filename in payloads, f"Missing payload file {filename} for {check_id}/{scenario_name}"

    @pytest.mark.skipif(not FIXTURE_ROOT.exists(), reason="EBS payload fixtures have not been captured from AWS yet.")
    def test_payloads_do_not_leak_real_aws_identifiers(self):
        checks = load_check_manifest("ebs")

        for check_id, check_config in checks.items():
            for scenario_name in check_config["scenarios"]:
                payloads = load_scenario_payloads("ebs", check_id, scenario_name)
                leaked_ids = find_real_aws_identifiers(payloads)
                assert leaked_ids == [], f"Found non-sanitized AWS identifiers in {check_id}/{scenario_name}: {leaked_ids}"

    @pytest.mark.skipif(not FIXTURE_ROOT.exists(), reason="EBS payload fixtures have not been captured from AWS yet.")
    def test_payloads_do_not_contain_unsanitized_ebs_capture_values(self):
        checks = load_check_manifest("ebs")
        forbidden_values = [
            "vol-0123456789abcdef0",
            "i-0123456789abcdef0",
            "maxops-payload-ebs-attached-gp2-1700000000",
        ]

        for check_id, check_config in checks.items():
            for scenario_name in check_config["scenarios"]:
                payloads = load_scenario_payloads("ebs", check_id, scenario_name)
                leaks = find_forbidden_strings(payloads, forbidden_values)
                assert leaks == [], f"Found unsanitized capture values in {check_id}/{scenario_name}: {leaks}"

    @pytest.mark.skipif(not FIXTURE_ROOT.exists(), reason="EBS payload fixtures have not been captured from AWS yet.")
    def test_capture_metadata_matches_manifest(self):
        checks = load_check_manifest("ebs")
        resource_config = load_resource_config("ebs")
        substitutions = dict(resource_config["placeholder_values"])
        substitutions["REGION"] = resource_config["region"]

        for check_id, check_config in checks.items():
            for scenario_name, scenario_config in check_config["scenarios"].items():
                payloads = load_scenario_payloads("ebs", check_id, scenario_name)
                metadata = payloads["capture_metadata.json"]

                assert sorted(metadata["volume_placeholders"]) == sorted(scenario_config["capture_volumes"])
                assert metadata["expected_matches"] == _render_manifest_value(scenario_config["expected_matches"], substitutions)
                assert metadata["check_parameters"] == _render_manifest_value(scenario_config["check_parameters"], substitutions)
                assert sorted(call["response_file"] for call in metadata["calls"]) == sorted(
                    filename for filename in scenario_config["payload_files"] if filename != "capture_metadata.json"
                )

    def test_cleanup_summary_success(self):
        manager = EBSPayloadResourceManager.__new__(EBSPayloadResourceManager)
        manager.state_path = Path("tests_generator/ebs/.ebs_capture_state.json")

        class FakeWaiter:
            def wait(self, **kwargs):
                return None

        class FakeEC2:
            def detach_volume(self, **kwargs):
                return {}

            def delete_volume(self, **kwargs):
                return {}

            def terminate_instances(self, **kwargs):
                return {}

            def get_waiter(self, name):
                assert name == "volume_available"
                return FakeWaiter()

        manager.ec2 = FakeEC2()
        state = {
            "resources": {
                "attached_gp2": {
                    "volume_id": "vol-ATTACHEDGP2",
                    "attach": True,
                }
            },
            "supporting_resources": {
                "instance": {
                    "instance_id": "i-EBSINSTANCE",
                }
            },
        }

        summary = EBSPayloadResourceManager.cleanup_resources(manager, state=state)

        assert summary["cleanup_status"] == "success"
        assert summary["resource_counts"] == {"targeted": 2, "succeeded": 2, "failed": 0}
        assert summary["errors"] == []

    def test_cleanup_summary_partial_failure(self):
        manager = EBSPayloadResourceManager.__new__(EBSPayloadResourceManager)
        manager.state_path = Path("tests_generator/ebs/.ebs_capture_state.json")

        class FakeWaiter:
            def wait(self, **kwargs):
                return None

        class FakeEC2:
            def detach_volume(self, **kwargs):
                return {}

            def delete_volume(self, **kwargs):
                raise ClientError({"Error": {"Code": "AccessDenied", "Message": "nope"}}, "DeleteVolume")

            def terminate_instances(self, **kwargs):
                return {}

            def get_waiter(self, name):
                return FakeWaiter()

        manager.ec2 = FakeEC2()
        state = {
            "resources": {
                "attached_gp2": {
                    "volume_id": "vol-ATTACHEDGP2",
                    "attach": True,
                }
            },
            "supporting_resources": {},
        }

        summary = EBSPayloadResourceManager.cleanup_resources(manager, state=state)

        assert summary["cleanup_status"] == "partial_failure"
        assert summary["resource_counts"]["failed"] == 1
        assert summary["errors"]
        assert summary["errors"][0]["operation"] == "delete_volume"

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
    "ACCOUNT_ID": "123456789012",
    "REGION": "us-east-1",
    "INSTANCE_ID": "i-EBSINSTANCE",
    "AVAILABILITY_ZONE": "us-east-1a",
    "ATTACHED_GP2_VOLUME_ID": "vol-ATTACHEDGP2",
    "ATTACHED_GP2_VOLUME_NAME": "maxops-payload-ebs-attached-gp2"
  },
  "capture_window_days": 7
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        checks_path.write_text(
            """
{
  "ebs_underutilized_volume": {
    "scenarios": {
      "flag_low_io_gp2": {
        "description": "positive",
        "capture_volumes": [
          "attached_gp2"
        ],
        "expected_matches": [
          "{{ATTACHED_GP2_VOLUME_ID}}"
        ],
        "check_parameters": {
          "region": "{{REGION}}"
        },
        "metric_overrides": {
          "attached_gp2": {
            "VolumeReadOps": {
              "Average": 1.0
            }
          }
        },
        "payload_files": [
          "describe_volumes.json",
          "get_metric_statistics__attached_gp2__volumereadops.json",
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
  "account_id": "999999999999",
  "placeholder_map": {
    "123456789012": "999999999999",
    "i-EBSINSTANCE": "i-0123456789abcdef0",
    "vol-ATTACHEDGP2": "vol-0123456789abcdef0",
    "us-east-1a": "us-east-1a"
  },
  "resources": {
    "attached_gp2": {
      "volume_id": "vol-0123456789abcdef0",
      "placeholder_key": "ATTACHED_GP2_VOLUME_ID",
      "name_placeholder_key": "ATTACHED_GP2_VOLUME_NAME",
      "name": "maxops-payload-ebs-attached-gp2-1700000000"
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
                "service": "ebs",
                "cleanup_attempted": True,
                "cleanup_status": "success",
                "state_file": str(state_path_arg),
                "resource_counts": {"targeted": 1, "succeeded": 1, "failed": 0},
                "resources_targeted": [
                    {"resource_type": "ebs_volume", "resource_alias": "attached_gp2", "resource_name": "vol-0123456789abcdef0"}
                ],
                "errors": [],
            }

        monkeypatch.setattr("tests_generator.ebs.ebs_payloads_generator.PAYLOAD_ROOT", tmp_path / "payloads")
        monkeypatch.setattr(
            "tests_generator.ebs.ebs_payloads_generator._capture_baselines",
            lambda session, region, state, start_time, end_time: {
                "describe_volumes": {
                    "Volumes": [
                        {
                            "VolumeId": "vol-0123456789abcdef0",
                            "AvailabilityZone": "us-east-1a",
                            "State": "in-use",
                            "Size": 1,
                            "VolumeType": "gp2",
                            "Attachments": [
                                {
                                    "InstanceId": "i-0123456789abcdef0",
                                    "VolumeId": "vol-0123456789abcdef0",
                                    "State": "attached",
                                    "Device": "/dev/sdf"
                                }
                            ],
                            "Tags": [
                                {"Key": "Name", "Value": "maxops-payload-ebs-attached-gp2-1700000000"}
                            ]
                        }
                    ]
                },
                "get_metric_statistics__attached_gp2__volumereadops.json": {
                    "Datapoints": []
                },
            },
        )
        monkeypatch.setattr("tests_generator.ebs.ebs_payloads_generator._finalize_cleanup", fake_cleanup)

        # Every AWS entry point below is mocked, so the billable-capture
        # gate is not what this test is about. Neutralise it here rather
        # than setting MAXOPS_RUN_AWS_INTEGRATION_TESTS.
        monkeypatch.setattr("tests_generator.ebs.ebs_payloads_generator.require_capture_gate", lambda *a, **k: None)

        capture_payloads(config_path, checks_path, state_path, capture_actions=False)

        payload_dir = tmp_path / "payloads" / "ebs_underutilized_volume" / "flag_low_io_gp2"
        assert (payload_dir / "describe_volumes.json").exists()
        assert (payload_dir / "get_metric_statistics__attached_gp2__volumereadops.json").exists()
        assert cleanup_calls
        out = capsys.readouterr().out
        assert '"cleanup_status": "success"' in out
