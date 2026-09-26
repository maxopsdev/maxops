"""Validation tests for the RDS payload generator contract."""

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
from tests_generator.rds.rds_payloads_generator import capture_payloads
from tests_generator.rds.rds_resource_creation import RDSPayloadResourceManager


pytestmark = [pytest.mark.unit, pytest.mark.payload, pytest.mark.rds]


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


class TestRDSPayloadManifest:
    def test_all_manifest_payload_files_exist(self):
        checks = load_check_manifest("rds")

        for check_id, check_config in checks.items():
            for scenario_name, scenario_config in check_config["scenarios"].items():
                payloads = load_scenario_payloads("rds", check_id, scenario_name)
                for filename in scenario_config["payload_files"]:
                    assert filename in payloads, f"Missing payload file {filename} for {check_id}/{scenario_name}"

    def test_placeholder_values_are_unique(self):
        resource_config = load_resource_config("rds")
        placeholder_values = list(resource_config["placeholder_values"].values())

        assert len(placeholder_values) == len(set(placeholder_values))

    def test_payloads_do_not_leak_real_aws_identifiers(self):
        checks = load_check_manifest("rds")

        for check_id, check_config in checks.items():
            for scenario_name in check_config["scenarios"]:
                payloads = load_scenario_payloads("rds", check_id, scenario_name)
                leaked_ids = find_real_aws_identifiers(payloads)
                assert leaked_ids == [], f"Found non-sanitized AWS identifiers in {check_id}/{scenario_name}: {leaked_ids}"

    def test_payloads_do_not_contain_unsanitized_rds_capture_values(self):
        checks = load_check_manifest("rds")
        forbidden_values = [
            "maxops-payload-rds-idle-graviton-1700000000",
            "maxops-payload-rds-subnet-group-1700000000",
            "arn:aws:rds:us-east-1:999999999999:db:maxops-payload-rds-idle-graviton-1700000000",
        ]

        for check_id, check_config in checks.items():
            for scenario_name in check_config["scenarios"]:
                payloads = load_scenario_payloads("rds", check_id, scenario_name)
                leaks = find_forbidden_strings(payloads, forbidden_values)
                assert leaks == [], f"Found unsanitized capture values in {check_id}/{scenario_name}: {leaks}"

    def test_capture_metadata_matches_manifest(self):
        checks = load_check_manifest("rds")
        resource_config = load_resource_config("rds")
        substitutions = dict(resource_config["placeholder_values"])
        substitutions["REGION"] = resource_config["region"]

        for check_id, check_config in checks.items():
            for scenario_name, scenario_config in check_config["scenarios"].items():
                payloads = load_scenario_payloads("rds", check_id, scenario_name)
                metadata = payloads["capture_metadata.json"]

                assert sorted(metadata["db_placeholders"]) == sorted(scenario_config["capture_instances"])
                assert metadata["expected_matches"] == _render_manifest_value(scenario_config["expected_matches"], substitutions)
                assert metadata["check_parameters"] == _render_manifest_value(scenario_config["check_parameters"], substitutions)
                assert sorted(call["response_file"] for call in metadata["calls"]) == sorted(
                    filename for filename in scenario_config["payload_files"] if filename != "capture_metadata.json"
                )

    def test_only_metric_override_sections_are_allowed(self):
        checks = load_check_manifest("rds")

        for check_id, check_config in checks.items():
            for scenario_name, scenario in check_config["scenarios"].items():
                assert "inventory_overrides" not in scenario, f"{check_id}/{scenario_name} must not use inventory overrides"
                assert "response_overrides" not in scenario, f"{check_id}/{scenario_name} must not use response overrides"
                assert "describe_overrides" not in scenario, f"{check_id}/{scenario_name} must not use describe overrides"
                assert "tag_overrides" not in scenario, f"{check_id}/{scenario_name} must not use tag overrides"

    def test_metric_payloads_use_small_datapoint_sets(self):
        payloads = load_scenario_payloads("rds", "rds_idle_databases", "flag_idle_database")

        for filename in [
            "get_metric_statistics__idle_graviton_capture__cpuutilization.json",
            "get_metric_statistics__idle_graviton_capture__databaseconnections.json",
            "get_metric_statistics__idle_graviton_capture__readiops.json",
            "get_metric_statistics__idle_graviton_capture__writeiops.json",
        ]:
            assert len(payloads[filename]["Datapoints"]) <= 1

    def test_cleanup_summary_success(self):
        manager = RDSPayloadResourceManager.__new__(RDSPayloadResourceManager)
        manager.state_path = Path("tests_generator/rds/.rds_capture_state.json")

        class FakeWaiter:
            def wait(self, **kwargs):
                return None

        class FakeRDS:
            def delete_db_instance(self, **kwargs):
                return {}

            def get_waiter(self, name):
                assert name == "db_instance_deleted"
                return FakeWaiter()

            def delete_db_subnet_group(self, **kwargs):
                return {}

        manager.rds = FakeRDS()
        state = {
            "resources": {
                "idle_graviton_capture": {
                    "db_instance_identifier": "maxops-payload-rds-idle-graviton",
                }
            },
            "supporting_resources": {
                "db_subnet_group_name": "maxops-payload-rds-subnet-group",
            },
        }

        summary = RDSPayloadResourceManager.cleanup_resources(manager, state=state)

        assert summary["cleanup_status"] == "success"
        assert summary["resource_counts"] == {"targeted": 2, "succeeded": 2, "failed": 0}
        assert summary["errors"] == []

    def test_cleanup_summary_partial_failure(self):
        manager = RDSPayloadResourceManager.__new__(RDSPayloadResourceManager)
        manager.state_path = Path("tests_generator/rds/.rds_capture_state.json")

        class FakeWaiter:
            def wait(self, **kwargs):
                return None

        class FakeRDS:
            def delete_db_instance(self, **kwargs):
                raise ClientError({"Error": {"Code": "AccessDenied", "Message": "nope"}}, "DeleteDBInstance")

            def get_waiter(self, name):
                return FakeWaiter()

            def delete_db_subnet_group(self, **kwargs):
                return {}

        manager.rds = FakeRDS()
        state = {
            "resources": {
                "idle_graviton_capture": {
                    "db_instance_identifier": "maxops-payload-rds-idle-graviton",
                }
            },
            "supporting_resources": {
                "db_subnet_group_name": "maxops-payload-rds-subnet-group",
            },
        }

        summary = RDSPayloadResourceManager.cleanup_resources(manager, state=state)

        assert summary["cleanup_status"] == "partial_failure"
        assert summary["resource_counts"]["failed"] == 1
        assert summary["errors"]
        assert summary["errors"][0]["operation"] == "delete_db_instance"

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
    "GRAVITON_DB_IDENTIFIER": "maxops-payload-rds-idle-graviton"
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
  "rds_non_graviton_instance_class": {
    "scenarios": {
      "pass_graviton_instance": {
        "description": "negative",
        "capture_instances": [
          "idle_graviton_capture"
        ],
        "expected_matches": [],
        "check_parameters": {
          "region": "{{REGION}}"
        },
        "payload_files": [
          "describe_db_instances.json",
          "list_tags_for_resource__idle_graviton_capture.json",
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
    "maxops-payload-rds-idle-graviton": "actual-db"
  },
  "resources": {
    "idle_graviton_capture": {
      "db_instance_identifier": "actual-db",
      "placeholder_key": "GRAVITON_DB_IDENTIFIER",
      "db_instance_arn": "arn:aws:rds:us-east-1:999999999999:db:actual-db"
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
                "service": "rds",
                "cleanup_attempted": True,
                "cleanup_status": "success",
                "state_file": str(state_path_arg),
                "resource_counts": {"targeted": 1, "succeeded": 1, "failed": 0},
                "resources_targeted": [{"resource_type": "db_instance", "resource_alias": "idle_graviton_capture", "resource_name": "actual-db"}],
                "errors": [],
            }

        monkeypatch.setattr("tests_generator.rds.rds_payloads_generator.PAYLOAD_ROOT", tmp_path / "payloads")
        monkeypatch.setattr(
            "tests_generator.rds.rds_payloads_generator._capture_baselines",
            lambda session, region, state, start_time, end_time: {
                "describe_db_instances": {
                    "DBInstances": [
                        {
                            "DBInstanceIdentifier": "actual-db",
                            "DBInstanceArn": "arn:aws:rds:us-east-1:999999999999:db:actual-db",
                            "AvailabilityZone": "us-east-1a",
                            "DBInstanceStatus": "available",
                            "DBInstanceClass": "db.t4g.micro",
                            "Engine": "mysql",
                            "AllocatedStorage": 20,
                            "MultiAZ": False
                        }
                    ]
                },
                "list_tags_for_resource__idle_graviton_capture.json": {"TagList": [{"Key": "Name", "Value": "actual-db"}]},
            },
        )
        monkeypatch.setattr("tests_generator.rds.rds_payloads_generator._finalize_cleanup", fake_cleanup)

        # Every AWS entry point below is mocked, so the billable-capture
        # gate is not what this test is about. Neutralise it here rather
        # than setting MAXOPS_RUN_AWS_INTEGRATION_TESTS.
        monkeypatch.setattr("tests_generator.rds.rds_payloads_generator.require_capture_gate", lambda *a, **k: None)

        capture_payloads(config_path, checks_path, state_path, capture_actions=False)

        payload_dir = tmp_path / "payloads" / "rds_non_graviton_instance_class" / "pass_graviton_instance"
        assert (payload_dir / "describe_db_instances.json").exists()
        assert cleanup_calls
        out = capsys.readouterr().out
        assert '"cleanup_status": "success"' in out
