"""Validation tests for the DynamoDB payload generator contract."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from botocore.exceptions import ClientError
import pytest

from tests.payload_helpers import (
    find_forbidden_strings,
    find_real_aws_identifiers,
    load_action_manifest,
    load_action_payloads,
    load_check_manifest,
    load_resource_config,
    load_scenario_payloads,
)
from tests_generator.dynamodb.dynamodb_payloads_generator import (
    TABLE_METRIC_STATISTICS,
    _action_payload_complete,
    _apply_metric_override,
    _select_check_scenarios,
    capture_payloads,
)
from tests_generator.dynamodb.dynamodb_resource_creation import DynamoDBPayloadResourceManager


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "payloads" / "dynamodb"
ACTION_FIXTURE_ROOT = FIXTURE_ROOT / "actions"
pytestmark = [pytest.mark.unit, pytest.mark.payload, pytest.mark.dynamodb]


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


class TestDynamoDBPayloadManifest:
    def test_placeholder_values_are_unique(self):
        resource_config = load_resource_config("dynamodb")
        placeholder_values = list(resource_config["placeholder_values"].values())

        assert len(placeholder_values) == len(set(placeholder_values))

    def test_capture_resources_use_minimum_provisioned_capacity(self):
        resource_config = load_resource_config("dynamodb")
        provisioned = resource_config["table_definitions"]["provisioned_capture"]

        assert provisioned["provisioned_throughput"] == {"ReadCapacityUnits": 1, "WriteCapacityUnits": 1}
        assert provisioned["global_secondary_indexes"][0]["ProvisionedThroughput"] == {
            "ReadCapacityUnits": 1,
            "WriteCapacityUnits": 1,
        }
        assert resource_config["table_definitions"]["ondemand_capture"]["billing_mode"] == "PAY_PER_REQUEST"

    def test_action_resources_are_isolated_from_minimum_check_resources(self):
        resource_config = load_resource_config("dynamodb")
        action_capacity = resource_config["table_definitions"]["action_capacity_capture"]
        action_backup = resource_config["table_definitions"]["action_backup_capture"]

        assert action_capacity["provisioned_throughput"] == {"ReadCapacityUnits": 4, "WriteCapacityUnits": 4}
        assert action_backup["billing_mode"] == "PAY_PER_REQUEST"

    def test_action_manifest_covers_registered_dynamodb_actions(self):
        actions = load_action_manifest("dynamodb")

        assert set(actions) == {
            "use_provisioned_capacity",
            "enable_autoscaling",
            "delete_gsi",
            "reduce_rcu",
            "reduce_wcu",
            "delete_table",
            "backup_and_delete_table",
            "switch_to_on_demand_billing",
        }
        assert list(actions)[-1] == "delete_table"
        assert all(config["scenario"] == "success" for config in actions.values())
        assert all(config["expected_calls"] for config in actions.values())

    def test_only_metric_override_sections_are_allowed(self):
        checks = load_check_manifest("dynamodb")

        for check_id, check_config in checks.items():
            for scenario_name, scenario in check_config["scenarios"].items():
                assert "inventory_overrides" not in scenario, f"{check_id}/{scenario_name} must not use inventory overrides"
                assert "response_overrides" not in scenario, f"{check_id}/{scenario_name} must not use response overrides"
                assert "describe_overrides" not in scenario, f"{check_id}/{scenario_name} must not use describe overrides"
                assert "tag_overrides" not in scenario, f"{check_id}/{scenario_name} must not use tag overrides"

    def test_table_consumed_capacity_overrides_are_period_sums(self):
        checks = load_check_manifest("dynamodb")
        consumed_metrics = {"ConsumedReadCapacityUnits", "ConsumedWriteCapacityUnits"}

        for check_id, check_config in checks.items():
            for scenario_name, scenario in check_config["scenarios"].items():
                for resource_ref, overrides in scenario.get("metric_overrides", {}).items():
                    if ":" in resource_ref:
                        continue
                    for metric_name in consumed_metrics.intersection(overrides):
                        override = overrides[metric_name]
                        assert "Average" not in override, f"{check_id}/{scenario_name}/{metric_name} must use Sum"
                        assert "Sum" in override or "Values" in override

    def test_metric_override_values_use_default_metric_statistic(self):
        payload = _apply_metric_override(
            {"Datapoints": []},
            {"Values": [7200.0, 3600.0]},
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            default_statistic=TABLE_METRIC_STATISTICS["ConsumedWriteCapacityUnits"],
        )

        assert [set(datapoint) for datapoint in payload["Datapoints"]] == [
            {"Sum", "Timestamp", "Unit"},
            {"Sum", "Timestamp", "Unit"},
        ]
        assert [datapoint["Sum"] for datapoint in payload["Datapoints"]] == [7200.0, 3600.0]

    def test_default_selection_only_returns_missing_or_incomplete_mapped_scenarios(self, tmp_path):
        checks = {
            "dynamodb_example": {
                "scenarios": {
                    "complete": {"payload_files": ["one.json", "capture_metadata.json"]},
                    "incomplete": {"payload_files": ["one.json", "two.json", "capture_metadata.json"]},
                    "missing": {"payload_files": ["one.json", "capture_metadata.json"]},
                }
            }
        }
        payload_check_map = {
            "dynamodb_example": {
                "service": "dynamodb",
                "positive_scenarios": ["complete", "incomplete"],
                "negative_scenarios": ["missing"],
            }
        }
        complete_dir = tmp_path / "dynamodb_example" / "complete"
        complete_dir.mkdir(parents=True)
        (complete_dir / "one.json").write_text("{}", encoding="utf-8")
        (complete_dir / "capture_metadata.json").write_text("{}", encoding="utf-8")
        incomplete_dir = tmp_path / "dynamodb_example" / "incomplete"
        incomplete_dir.mkdir(parents=True)
        (incomplete_dir / "one.json").write_text("{}", encoding="utf-8")

        selected = _select_check_scenarios(checks, payload_check_map, payload_root=tmp_path)

        assert list(selected) == ["dynamodb_example"]
        assert list(selected["dynamodb_example"]["scenarios"]) == ["incomplete", "missing"]

    def test_all_check_selection_ignores_existing_payloads(self, tmp_path):
        checks = {
            "dynamodb_example": {
                "scenarios": {
                    "scenario": {"payload_files": ["capture_metadata.json"]},
                }
            }
        }
        payload_check_map = {
            "dynamodb_example": {
                "service": "dynamodb",
                "positive_scenarios": ["scenario"],
                "negative_scenarios": [],
            }
        }
        scenario_dir = tmp_path / "dynamodb_example" / "scenario"
        scenario_dir.mkdir(parents=True)
        (scenario_dir / "capture_metadata.json").write_text("{}", encoding="utf-8")

        selected = _select_check_scenarios(
            checks,
            payload_check_map,
            regenerate_all=True,
            payload_root=tmp_path,
        )

        assert list(selected["dynamodb_example"]["scenarios"]) == ["scenario"]

    def test_check_selection_rejects_manifest_scenarios_missing_from_map(self, tmp_path):
        checks = {
            "dynamodb_example": {
                "scenarios": {
                    "mapped": {"payload_files": ["capture_metadata.json"]},
                    "unmapped": {"payload_files": ["capture_metadata.json"]},
                }
            }
        }
        payload_check_map = {
            "dynamodb_example": {
                "service": "dynamodb",
                "positive_scenarios": ["mapped"],
                "negative_scenarios": [],
            }
        }

        with pytest.raises(ValueError, match="does not cover DynamoDB scenarios"):
            _select_check_scenarios(checks, payload_check_map, payload_root=tmp_path)

    def test_action_payload_is_complete_only_with_metadata_and_all_responses(self, tmp_path):
        action_config = {
            "scenario": "success",
            "expected_calls": [
                {"service": "dynamodb", "operation": "delete_table"},
            ],
        }
        scenario_dir = tmp_path / "actions" / "delete_table" / "success"
        scenario_dir.mkdir(parents=True)
        (scenario_dir / "capture_metadata.json").write_text(
            (
                '{"action_key":"delete_table","scenario":"success",'
                '"calls":[{"service":"dynamodb","operation":"delete_table",'
                '"response_file":"01_dynamodb__delete_table.json"}]}'
            ),
            encoding="utf-8",
        )

        assert not _action_payload_complete("delete_table", action_config, tmp_path)

        (scenario_dir / "01_dynamodb__delete_table.json").write_text("{}", encoding="utf-8")

        assert _action_payload_complete("delete_table", action_config, tmp_path)

    @pytest.mark.skipif(not FIXTURE_ROOT.exists(), reason="DynamoDB payload fixtures have not been captured from AWS yet.")
    def test_all_manifest_payload_files_exist(self):
        checks = load_check_manifest("dynamodb")

        for check_id, check_config in checks.items():
            for scenario_name, scenario_config in check_config["scenarios"].items():
                payloads = load_scenario_payloads("dynamodb", check_id, scenario_name)
                for filename in scenario_config["payload_files"]:
                    assert filename in payloads, f"Missing payload file {filename} for {check_id}/{scenario_name}"

    @pytest.mark.skipif(not FIXTURE_ROOT.exists(), reason="DynamoDB payload fixtures have not been captured from AWS yet.")
    def test_payloads_do_not_leak_real_aws_identifiers(self):
        checks = load_check_manifest("dynamodb")

        for check_id, check_config in checks.items():
            for scenario_name in check_config["scenarios"]:
                payloads = load_scenario_payloads("dynamodb", check_id, scenario_name)
                leaked_ids = find_real_aws_identifiers(payloads)
                assert leaked_ids == [], f"Found non-sanitized AWS identifiers in {check_id}/{scenario_name}: {leaked_ids}"

    @pytest.mark.skipif(not FIXTURE_ROOT.exists(), reason="DynamoDB payload fixtures have not been captured from AWS yet.")
    def test_payloads_do_not_contain_unsanitized_dynamodb_capture_values(self):
        checks = load_check_manifest("dynamodb")
        forbidden_values = [
            "maxops-payload-dynamodb-provisioned-1700000000",
            "maxops-payload-dynamodb-ondemand-1700000000",
            "arn:aws:dynamodb:us-east-1:999999999999:table/maxops-payload-dynamodb-provisioned-1700000000",
        ]

        for check_id, check_config in checks.items():
            for scenario_name in check_config["scenarios"]:
                payloads = load_scenario_payloads("dynamodb", check_id, scenario_name)
                leaks = find_forbidden_strings(payloads, forbidden_values)
                assert leaks == [], f"Found unsanitized capture values in {check_id}/{scenario_name}: {leaks}"

    @pytest.mark.skipif(not FIXTURE_ROOT.exists(), reason="DynamoDB payload fixtures have not been captured from AWS yet.")
    def test_capture_metadata_matches_manifest(self):
        checks = load_check_manifest("dynamodb")
        resource_config = load_resource_config("dynamodb")
        substitutions = dict(resource_config["placeholder_values"])
        substitutions["REGION"] = resource_config["region"]

        for check_id, check_config in checks.items():
            for scenario_name, scenario_config in check_config["scenarios"].items():
                payloads = load_scenario_payloads("dynamodb", check_id, scenario_name)
                metadata = payloads["capture_metadata.json"]

                assert sorted(metadata["table_placeholders"]) == sorted(scenario_config["capture_tables"])
                assert sorted(metadata["gsi_placeholders"]) == sorted(scenario_config.get("capture_gsis", []))
                assert metadata["expected_matches"] == _render_manifest_value(scenario_config["expected_matches"], substitutions)
                assert metadata["check_parameters"] == _render_manifest_value(scenario_config["check_parameters"], substitutions)
                assert sorted(call["response_file"] for call in metadata["calls"]) == sorted(
                    filename for filename in scenario_config["payload_files"] if filename != "capture_metadata.json"
                )

    @pytest.mark.skipif(not ACTION_FIXTURE_ROOT.exists(), reason="DynamoDB action payloads have not been captured from AWS yet.")
    def test_action_capture_metadata_matches_manifest(self):
        actions = load_action_manifest("dynamodb")

        for action_key, action_config in actions.items():
            payloads = load_action_payloads("dynamodb", action_key, action_config["scenario"])
            metadata = payloads["capture_metadata.json"]

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

    def test_cleanup_summary_success(self):
        manager = DynamoDBPayloadResourceManager.__new__(DynamoDBPayloadResourceManager)
        manager.state_path = Path("tests_generator/dynamodb/.dynamodb_capture_state.json")

        class FakeWaiter:
            def wait(self, **kwargs):
                return None

        class FakeDynamoDB:
            def delete_table(self, **kwargs):
                return {}

            def get_waiter(self, name):
                assert name == "table_not_exists"
                return FakeWaiter()

        manager.dynamodb = FakeDynamoDB()
        state = {
            "resources": {
                "provisioned_capture": {
                    "table_name": "maxops-payload-dynamodb-provisioned",
                }
            }
        }

        summary = DynamoDBPayloadResourceManager.cleanup_resources(manager, state=state)

        assert summary["cleanup_status"] == "success"
        assert summary["resource_counts"] == {"targeted": 1, "succeeded": 1, "failed": 0}
        assert summary["errors"] == []

    def test_cleanup_summary_partial_failure(self):
        manager = DynamoDBPayloadResourceManager.__new__(DynamoDBPayloadResourceManager)
        manager.state_path = Path("tests_generator/dynamodb/.dynamodb_capture_state.json")

        class FakeWaiter:
            def wait(self, **kwargs):
                return None

        class FakeDynamoDB:
            def delete_table(self, **kwargs):
                raise ClientError({"Error": {"Code": "AccessDenied", "Message": "nope"}}, "DeleteTable")

            def get_waiter(self, name):
                return FakeWaiter()

        manager.dynamodb = FakeDynamoDB()
        state = {
            "resources": {
                "provisioned_capture": {
                    "table_name": "maxops-payload-dynamodb-provisioned",
                }
            }
        }

        summary = DynamoDBPayloadResourceManager.cleanup_resources(manager, state=state)

        assert summary["cleanup_status"] == "partial_failure"
        assert summary["resource_counts"]["failed"] == 1
        assert summary["errors"]
        assert summary["errors"][0]["operation"] == "delete_table"

    def test_cleanup_removes_action_created_artifacts(self):
        manager = DynamoDBPayloadResourceManager.__new__(DynamoDBPayloadResourceManager)
        manager.state_path = Path("tests_generator/dynamodb/.dynamodb_capture_state.json")
        calls = []

        class FakeDynamoDB:
            def describe_backup(self, **kwargs):
                calls.append(("describe_backup", kwargs))
                return {"BackupDescription": {"BackupDetails": {"BackupStatus": "AVAILABLE"}}}

            def delete_backup(self, **kwargs):
                calls.append(("delete_backup", kwargs))
                return {}

        class FakeAutoscaling:
            def delete_scaling_policy(self, **kwargs):
                calls.append(("delete_scaling_policy", kwargs))
                return {}

            def deregister_scalable_target(self, **kwargs):
                calls.append(("deregister_scalable_target", kwargs))
                return {}

        manager.dynamodb = FakeDynamoDB()
        manager.autoscaling = FakeAutoscaling()
        state = {
            "resources": {},
            "autoscaling_policies": [
                {
                    "resource_alias": "provisioned_capture",
                    "policy_name": "policy",
                    "resource_id": "table/example",
                    "scalable_dimension": "dynamodb:table:ReadCapacityUnits",
                }
            ],
            "autoscaling_targets": [
                {
                    "resource_alias": "provisioned_capture",
                    "resource_id": "table/example",
                    "scalable_dimension": "dynamodb:table:ReadCapacityUnits",
                }
            ],
            "backups": [
                {
                    "resource_alias": "action_backup_capture",
                    "backup_arn": "arn:aws:dynamodb:us-east-1:123456789012:table/example/backup/1",
                }
            ],
        }

        summary = DynamoDBPayloadResourceManager.cleanup_resources(manager, state=state)

        assert summary["cleanup_status"] == "success"
        assert summary["resource_counts"] == {"targeted": 3, "succeeded": 3, "failed": 0}
        assert [operation for operation, _ in calls] == [
            "delete_scaling_policy",
            "deregister_scalable_target",
            "describe_backup",
            "delete_backup",
        ]

    def test_payload_generator_triggers_cleanup(self, monkeypatch, tmp_path, capsys):
        config_path = tmp_path / "resource_config.json"
        checks_path = tmp_path / "checks.json"
        payload_check_map_path = tmp_path / "payload_check_map.json"
        state_path = tmp_path / "state.json"

        config_path.write_text(
            """
{
  "profile": null,
  "region": "us-east-1",
  "placeholder_values": {
    "ACCOUNT_ID": "123456789012",
    "REGION": "us-east-1",
    "PROVISIONED_TABLE_NAME": "maxops-payload-dynamodb-provisioned",
    "UNUSED_GSI_NAME": "maxops_payload_unused_gsi"
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
  "dynamodb_gsi_unused": {
    "scenarios": {
      "flag_unused_gsi": {
        "description": "positive",
        "capture_tables": [
          "provisioned_capture"
        ],
        "capture_gsis": [
          "provisioned_capture:maxops_payload_unused_gsi"
        ],
        "expected_matches": [
          "{{PROVISIONED_TABLE_NAME}}/{{UNUSED_GSI_NAME}}"
        ],
        "check_parameters": {
          "region": "{{REGION}}"
        },
        "metric_overrides": {
          "provisioned_capture:maxops_payload_unused_gsi": {
            "ConsumedReadCapacityUnits": {
              "Sum": 0.0
            }
          }
        },
        "payload_files": [
          "list_tables.json",
          "describe_table__provisioned_capture.json",
          "get_metric_statistics__provisioned_capture__maxops_payload_unused_gsi__consumedreadcapacityunits.json",
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
        payload_check_map_path.write_text(
            """
{
  "dynamodb_gsi_unused": {
    "service": "dynamodb",
    "check_module": "app.checks.dynamodb.gsi_unused",
    "check_function": "check_dynamodb_gsi_unused",
    "positive_scenarios": ["flag_unused_gsi"],
    "negative_scenarios": []
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
    "maxops-payload-dynamodb-provisioned": "actual-table"
  },
  "resources": {
    "provisioned_capture": {
      "table_name": "actual-table",
      "table_arn": "arn:aws:dynamodb:us-east-1:999999999999:table/actual-table",
      "placeholder_key": "PROVISIONED_TABLE_NAME",
      "global_secondary_indexes": [
        {
          "index_name": "maxops_payload_unused_gsi"
        }
      ]
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
                "service": "dynamodb",
                "cleanup_attempted": True,
                "cleanup_status": "success",
                "state_file": str(state_path_arg),
                "resource_counts": {"targeted": 1, "succeeded": 1, "failed": 0},
                "resources_targeted": [
                    {"resource_type": "dynamodb_table", "resource_alias": "provisioned_capture", "resource_name": "actual-table"}
                ],
                "errors": [],
            }

        monkeypatch.setattr("tests_generator.dynamodb.dynamodb_payloads_generator.PAYLOAD_ROOT", tmp_path / "payloads")
        monkeypatch.setattr(
            "tests_generator.dynamodb.dynamodb_payloads_generator._capture_baselines",
            lambda session, region, state, start_time, end_time: {
                "list_tables": {"TableNames": ["actual-table"]},
                "describe_table__provisioned_capture.json": {
                    "Table": {
                        "TableName": "actual-table",
                        "TableArn": "arn:aws:dynamodb:us-east-1:999999999999:table/actual-table",
                        "TableStatus": "ACTIVE",
                        "ItemCount": 0,
                        "BillingModeSummary": {"BillingMode": "PROVISIONED"},
                        "ProvisionedThroughput": {"ReadCapacityUnits": 1, "WriteCapacityUnits": 1},
                        "GlobalSecondaryIndexes": [
                            {
                                "IndexName": "maxops_payload_unused_gsi",
                                "IndexStatus": "ACTIVE",
                                "ProvisionedThroughput": {"ReadCapacityUnits": 1, "WriteCapacityUnits": 1}
                            }
                        ]
                    }
                },
                "get_metric_statistics__provisioned_capture__maxops_payload_unused_gsi__consumedreadcapacityunits.json": {
                    "Datapoints": []
                },
            },
        )
        monkeypatch.setattr("tests_generator.dynamodb.dynamodb_payloads_generator._finalize_cleanup", fake_cleanup)

        # Every AWS entry point below is mocked, so the billable-capture
        # gate is not what this test is about. Neutralise it here rather
        # than setting MAXOPS_RUN_AWS_INTEGRATION_TESTS.
        monkeypatch.setattr("tests_generator.dynamodb.dynamodb_payloads_generator.require_capture_gate", lambda *a, **k: None)

        capture_payloads(
            config_path,
            checks_path,
            state_path,
            payload_check_map_path=payload_check_map_path,
            capture_actions=False,
        )

        payload_dir = tmp_path / "payloads" / "dynamodb_gsi_unused" / "flag_unused_gsi"
        assert (payload_dir / "list_tables.json").exists()
        assert (payload_dir / "describe_table__provisioned_capture.json").exists()
        assert cleanup_calls
        out = capsys.readouterr().out
        assert '"cleanup_status": "success"' in out
