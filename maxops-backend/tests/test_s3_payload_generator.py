"""Validation tests for the S3 payload generator contract."""

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
from tests_generator.s3.s3_payloads_generator import capture_payloads
from tests_generator.s3.s3_resource_creation import S3PayloadResourceManager


pytestmark = [pytest.mark.unit, pytest.mark.payload, pytest.mark.s3]


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


class TestS3PayloadManifest:
    def test_all_manifest_payload_files_exist(self):
        checks = load_check_manifest("s3")

        for check_id, check_config in checks.items():
            for scenario_name, scenario_config in check_config["scenarios"].items():
                payloads = load_scenario_payloads("s3", check_id, scenario_name)
                for filename in scenario_config["payload_files"]:
                    assert filename in payloads, f"Missing payload file {filename} for {check_id}/{scenario_name}"

    def test_placeholder_values_are_unique(self):
        resource_config = load_resource_config("s3")
        placeholder_values = list(resource_config["placeholder_values"].values())

        assert len(placeholder_values) == len(set(placeholder_values))

    def test_payloads_do_not_leak_real_aws_identifiers(self):
        checks = load_check_manifest("s3")

        for check_id, check_config in checks.items():
            for scenario_name in check_config["scenarios"]:
                payloads = load_scenario_payloads("s3", check_id, scenario_name)
                leaked_ids = find_real_aws_identifiers(payloads)
                assert leaked_ids == [], f"Found non-sanitized AWS identifiers in {check_id}/{scenario_name}: {leaked_ids}"

    def test_payloads_do_not_contain_unsanitized_bucket_or_role_values(self):
        checks = load_check_manifest("s3")
        forbidden_values = [
            "maxops-payload-s3-plain-no-lifecycle-1700000000",
            "maxops-payload-s3-replication-role-1700000000",
            "arn:aws:iam::999999999999:role/maxops-payload-s3-replication-role-1700000000",
        ]

        for check_id, check_config in checks.items():
            for scenario_name in check_config["scenarios"]:
                payloads = load_scenario_payloads("s3", check_id, scenario_name)
                leaks = find_forbidden_strings(payloads, forbidden_values)
                assert leaks == [], f"Found unsanitized capture values in {check_id}/{scenario_name}: {leaks}"

    def test_capture_metadata_matches_manifest(self):
        checks = load_check_manifest("s3")
        resource_config = load_resource_config("s3")
        substitutions = dict(resource_config["placeholder_values"])
        substitutions["REGION"] = resource_config["region"]

        for check_id, check_config in checks.items():
            for scenario_name, scenario_config in check_config["scenarios"].items():
                payloads = load_scenario_payloads("s3", check_id, scenario_name)
                metadata = payloads["capture_metadata.json"]

                assert sorted(metadata["bucket_placeholders"]) == sorted(scenario_config["capture_buckets"])
                assert metadata["expected_matches"] == _render_manifest_value(scenario_config["expected_matches"], substitutions)
                assert metadata["check_parameters"] == _render_manifest_value(scenario_config["check_parameters"], substitutions)
                assert sorted(call["response_file"] for call in metadata["calls"]) == sorted(
                    filename for filename in scenario_config["payload_files"] if filename != "capture_metadata.json"
                )

    def test_cleanup_summary_success(self):
        manager = S3PayloadResourceManager.__new__(S3PayloadResourceManager)
        manager.state_path = Path("tests_generator/s3/.s3_capture_state.json")

        class FakeS3:
            def list_bucket_inventory_configurations(self, Bucket):
                return {}

            def put_bucket_logging(self, Bucket, BucketLoggingStatus):
                return {}

            def delete_bucket_replication(self, Bucket):
                return {}

            def delete_bucket_policy(self, Bucket):
                return {}

            def delete_bucket(self, Bucket):
                return {}

        class FakeIAM:
            def delete_role_policy(self, RoleName, PolicyName):
                return {}

            def delete_role(self, RoleName):
                return {}

        manager.s3 = FakeS3()
        manager.iam = FakeIAM()
        manager._empty_bucket = lambda bucket_name: None
        state = {
            "resources": {
                "bucket_a": {
                    "bucket_name": "bucket-a",
                }
            },
            "supporting_resources": {
                "replication_role_name": "role-a",
                "replication_policy_name": "policy-a",
            },
        }

        summary = S3PayloadResourceManager.cleanup_resources(manager, state=state)

        assert summary["cleanup_status"] == "success"
        assert summary["resource_counts"] == {"targeted": 3, "succeeded": 3, "failed": 0}
        assert summary["errors"] == []

    def test_cleanup_summary_partial_failure(self):
        manager = S3PayloadResourceManager.__new__(S3PayloadResourceManager)
        manager.state_path = Path("tests_generator/s3/.s3_capture_state.json")

        class FakeS3:
            def list_bucket_inventory_configurations(self, Bucket):
                return {}

            def put_bucket_logging(self, Bucket, BucketLoggingStatus):
                return {}

            def delete_bucket_replication(self, Bucket):
                return {}

            def delete_bucket_policy(self, Bucket):
                return {}

            def delete_bucket(self, Bucket):
                raise ClientError({"Error": {"Code": "AccessDenied", "Message": "nope"}}, "DeleteBucket")

        class FakeIAM:
            def delete_role_policy(self, RoleName, PolicyName):
                return {}

            def delete_role(self, RoleName):
                return {}

        manager.s3 = FakeS3()
        manager.iam = FakeIAM()
        manager._empty_bucket = lambda bucket_name: None
        state = {
            "resources": {
                "bucket_a": {
                    "bucket_name": "bucket-a",
                }
            },
            "supporting_resources": {},
        }

        summary = S3PayloadResourceManager.cleanup_resources(manager, state=state)

        assert summary["cleanup_status"] == "partial_failure"
        assert summary["resource_counts"]["failed"] == 1
        assert summary["errors"]
        assert summary["errors"][0]["operation"] == "delete_bucket"

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
    "PLAIN_BUCKET": "plain-bucket"
  }
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        checks_path.write_text(
            """
{
  "s3_no_lifecycle_policy": {
    "scenarios": {
      "flag_bucket_without_lifecycle": {
        "description": "negative",
        "capture_buckets": [
          "plain"
        ],
        "expected_matches": [
          "{{PLAIN_BUCKET}}"
        ],
        "check_parameters": {
          "region": "{{REGION}}"
        },
        "payload_files": [
          "list_buckets.json",
          "get_bucket_location__plain.json",
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
    "plain-bucket": "plain-bucket-actual"
  },
  "resources": {
    "plain": {
      "bucket_name": "plain-bucket-actual",
      "placeholder_key": "PLAIN_BUCKET"
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
                "service": "s3",
                "cleanup_attempted": True,
                "cleanup_status": "success",
                "state_file": str(state_path_arg),
                "resource_counts": {"targeted": 1, "succeeded": 1, "failed": 0},
                "resources_targeted": [{"resource_type": "bucket", "resource_alias": "plain", "resource_name": "plain-bucket-actual"}],
                "errors": [],
            }

        monkeypatch.setattr("tests_generator.s3.s3_payloads_generator.PAYLOAD_ROOT", tmp_path / "payloads")
        monkeypatch.setattr(
            "tests_generator.s3.s3_payloads_generator._capture_baselines",
            lambda session, region, state: {
                "list_buckets": {"Buckets": [{"Name": "plain-bucket-actual"}], "Owner": {}},
                "get_bucket_location__plain.json": {"LocationConstraint": "us-east-1"},
            },
        )
        monkeypatch.setattr("tests_generator.s3.s3_payloads_generator._finalize_cleanup", fake_cleanup)

        # Every AWS entry point below is mocked, so the billable-capture
        # gate is not what this test is about. Neutralise it here rather
        # than setting MAXOPS_RUN_AWS_INTEGRATION_TESTS.
        monkeypatch.setattr("tests_generator.s3.s3_payloads_generator.require_capture_gate", lambda *a, **k: None)

        capture_payloads(config_path, checks_path, state_path, capture_actions=False)

        payload_dir = tmp_path / "payloads" / "s3_no_lifecycle_policy" / "flag_bucket_without_lifecycle"
        assert (payload_dir / "list_buckets.json").exists()
        assert cleanup_calls
        out = capsys.readouterr().out
        assert '"cleanup_status": "success"' in out
