"""Validation tests for the ElastiCache payload generator contract."""

from __future__ import annotations

from pathlib import Path

from botocore.exceptions import ClientError
import pytest

from tests.payload_helpers import (
    load_check_manifest,
    load_resource_config,
)
from tests_generator.elasticache.elasticache_payloads_generator import capture_payloads
from tests_generator.elasticache.elasticache_resource_creation import ElastiCachePayloadResourceManager


pytestmark = [pytest.mark.unit, pytest.mark.payload, pytest.mark.elasticache]


class TestElastiCachePayloadManifest:
    def test_placeholder_values_are_unique(self):
        resource_config = load_resource_config("elasticache")
        placeholder_values = list(resource_config["placeholder_values"].values())

        assert len(placeholder_values) == len(set(placeholder_values))

    def test_only_metric_override_sections_are_allowed(self):
        checks = load_check_manifest("elasticache")

        for check_id, check_config in checks.items():
            for scenario_name, scenario in check_config["scenarios"].items():
                assert "metric_overrides" in scenario or "metric_overrides" not in scenario
                assert "inventory_overrides" not in scenario, f"{check_id}/{scenario_name} must not use inventory overrides"
                assert "response_overrides" not in scenario, f"{check_id}/{scenario_name} must not use response overrides"
                assert "describe_overrides" not in scenario, f"{check_id}/{scenario_name} must not use describe overrides"

    def test_valkey_scenarios_capture_member_cluster_metadata(self):
        checks = load_check_manifest("elasticache")
        scenarios = checks["elasticache_redis_convertible_to_valkey"]["scenarios"]

        for scenario in scenarios.values():
            assert "describe_replication_groups.json" in scenario["payload_files"]
            assert "describe_cache_clusters.json" in scenario["payload_files"]

    def test_cleanup_summary_success(self):
        manager = ElastiCachePayloadResourceManager.__new__(ElastiCachePayloadResourceManager)
        manager.state_path = Path("tests_generator/elasticache/.elasticache_capture_state.json")

        class FakeWaiter:
            def wait(self, **kwargs):
                return None

        class FakeElastiCache:
            def delete_cache_cluster(self, **kwargs):
                return {}

            def delete_replication_group(self, **kwargs):
                return {}

            def get_waiter(self, name):
                return FakeWaiter()

        manager.elasticache = FakeElastiCache()
        state = {
            "resources": {
                "low_items_graviton_cluster": {
                    "resource_type": "elasticache_cluster",
                    "cluster_id": "cluster-a",
                },
                "redis_replication_group": {
                    "resource_type": "elasticache_replication_group",
                    "replication_group_id": "rg-a",
                },
            }
        }

        summary = ElastiCachePayloadResourceManager.cleanup_resources(manager, state=state)

        assert summary["cleanup_status"] == "success"
        assert summary["resource_counts"] == {"targeted": 2, "succeeded": 2, "failed": 0}
        assert summary["errors"] == []

    def test_cleanup_summary_partial_failure(self):
        manager = ElastiCachePayloadResourceManager.__new__(ElastiCachePayloadResourceManager)
        manager.state_path = Path("tests_generator/elasticache/.elasticache_capture_state.json")

        class FakeWaiter:
            def wait(self, **kwargs):
                return None

        class FakeElastiCache:
            def delete_cache_cluster(self, **kwargs):
                return {}

            def delete_replication_group(self, **kwargs):
                raise ClientError({"Error": {"Code": "AccessDenied", "Message": "nope"}}, "DeleteReplicationGroup")

            def get_waiter(self, name):
                return FakeWaiter()

        manager.elasticache = FakeElastiCache()
        state = {
            "resources": {
                "low_items_graviton_cluster": {
                    "resource_type": "elasticache_cluster",
                    "cluster_id": "cluster-a",
                },
                "redis_replication_group": {
                    "resource_type": "elasticache_replication_group",
                    "replication_group_id": "rg-a",
                },
            }
        }

        summary = ElastiCachePayloadResourceManager.cleanup_resources(manager, state=state)

        assert summary["cleanup_status"] == "partial_failure"
        assert summary["resource_counts"]["failed"] == 1
        assert summary["errors"]
        assert summary["errors"][0]["operation"] == "delete_replication_group"

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
    "LOW_ITEMS_CLUSTER_ID": "maxops-payload-elasticache-low-items"
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
  "elasticache_non_graviton_instance_class": {
    "scenarios": {
      "pass_graviton_cluster": {
        "description": "negative",
        "capture_resources": [
          "low_items_graviton_cluster"
        ],
        "expected_matches": [],
        "check_parameters": {
          "region": "{{REGION}}"
        },
        "payload_files": [
          "describe_cache_clusters.json",
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
    "maxops-payload-elasticache-low-items": "actual-cluster"
  },
  "resources": {
    "low_items_graviton_cluster": {
      "resource_type": "elasticache_cluster",
      "cluster_id": "actual-cluster",
      "placeholder_key": "LOW_ITEMS_CLUSTER_ID"
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
                "service": "elasticache",
                "cleanup_attempted": True,
                "cleanup_status": "success",
                "state_file": str(state_path_arg),
                "resource_counts": {"targeted": 1, "succeeded": 1, "failed": 0},
                "resources_targeted": [{"resource_type": "elasticache_cluster", "resource_alias": "low_items_graviton_cluster", "resource_name": "actual-cluster"}],
                "errors": [],
            }

        monkeypatch.setattr("tests_generator.elasticache.elasticache_payloads_generator.PAYLOAD_ROOT", tmp_path / "payloads")
        monkeypatch.setattr(
            "tests_generator.elasticache.elasticache_payloads_generator._capture_baselines",
            lambda session, region, state, start_time, end_time: {
                "describe_cache_clusters": {
                    "CacheClusters": [
                        {
                            "CacheClusterId": "actual-cluster",
                            "CacheNodeType": "cache.t4g.micro",
                            "Engine": "redis",
                            "EngineVersion": "7.1",
                            "NumCacheNodes": 1,
                            "CacheClusterStatus": "available"
                        }
                    ]
                },
                "describe_replication_groups": {"ReplicationGroups": []},
            },
        )
        monkeypatch.setattr("tests_generator.elasticache.elasticache_payloads_generator._finalize_cleanup", fake_cleanup)

        # Every AWS entry point below is mocked, so the billable-capture
        # gate is not what this test is about. Neutralise it here rather
        # than setting MAXOPS_RUN_AWS_INTEGRATION_TESTS.
        monkeypatch.setattr("tests_generator.elasticache.elasticache_payloads_generator.require_capture_gate", lambda *a, **k: None)

        capture_payloads(config_path, checks_path, state_path, capture_actions=False)

        payload_dir = tmp_path / "payloads" / "elasticache_non_graviton_instance_class" / "pass_graviton_cluster"
        assert (payload_dir / "describe_cache_clusters.json").exists()
        assert cleanup_calls
        out = capsys.readouterr().out
        assert '"cleanup_status": "success"' in out
