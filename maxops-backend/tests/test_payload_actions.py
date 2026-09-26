"""Cross-service action tests backed by generated real-AWS payloads."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.actions import action_registry
from app.actions.base import ActionExecutionContext
from app.checks.registry import check_registry
from app.schemas.check import CheckActionRequest
from tests.payload_helpers import (
    action_values_match,
    build_action_payload_adapter,
    load_action_manifest,
    load_resource_config,
)


SERVICES = ("cloudwatch", "ec2", "ebs", "elasticache", "rds", "s3", "vpc", "sagemaker")
ACTION_CASES = tuple(
    (service, action_key)
    for service in SERVICES
    for action_key, action_config in load_action_manifest(service).items()
    if action_config.get("capture_enabled", True)
)
PAYLOAD_ROOT = Path(__file__).resolve().parent / "payloads"
pytestmark = [pytest.mark.unit, pytest.mark.payload]


def _model_dict(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


class _NullDB:
    def commit(self) -> None:
        return None


@pytest.mark.parametrize("service,action_key", ACTION_CASES)
def test_action_manifest_replays_captured_aws_calls(service, action_key):
    action_config = load_action_manifest(service)[action_key]
    scenario = action_config["scenario"]
    fixture_dir = PAYLOAD_ROOT / service / "actions" / action_key / scenario
    if not (fixture_dir / "capture_metadata.json").is_file():
        pytest.skip(f"{service} action payload {action_key}/{scenario} has not been captured")

    adapter, replay, metadata = build_action_payload_adapter(service, action_key, scenario)
    payload = CheckActionRequest(
        action=action_key,
        account_id=metadata["account_id"],
        region=metadata["region"],
        resource_id=metadata["resource_id"],
        parameters=metadata["parameters"],
    )
    context = ActionExecutionContext(
        check_id=metadata["check_id"],
        action_key=action_key,
        payload=payload,
        check=check_registry.get_check(metadata["check_id"]),
        aws_adapter=adapter,
        db=_NullDB(),
        action_execution=SimpleNamespace(
            status="running",
            message=None,
            details_json=None,
            error_message=None,
        ),
    )

    response = action_registry.execute(context)

    replay.assert_complete()
    assert response.status == metadata["expected_status"]
    assert response.message == metadata["expected_message"]
    assert action_values_match(metadata["handler_response"], _model_dict(response))


@pytest.mark.parametrize("service", SERVICES)
def test_action_manifest_references_registered_checks_actions_and_resources(service):
    actions = load_action_manifest(service)
    config = load_resource_config(service)
    resource_aliases = set()
    for key in (
        "volume_definitions",
        "cache_cluster_definitions",
        "replication_group_definitions",
        "db_instance_definitions",
        "bucket_definitions",
        "resources",
    ):
        resource_aliases.update(config.get(key, {}))
    if service == "ec2":
        resource_aliases.add(config["capture_instance"]["key"])
        resource_aliases.add(config["elastic_ip"]["key"])
        resource_aliases.update(config.get("action_instance_definitions", {}))
    if service == "cloudwatch":
        resource_aliases.update(
            {
                "log_group_no_retention",
                "log_group_retained",
                "log_group_delete_target",
                "no_action_alarm",
                "noisy_alarm",
                "duplicate_keeper_alarm",
                "duplicate_delete_alarm",
                "actionful_alarm",
            }
        )
    if service == "vpc":
        resource_aliases.update(
            {
                "endpoint_vpc",
                "disable_vpc",
                "expiration_vpc",
                "deletion_vpc",
            }
        )

    for action_key, action_config in actions.items():
        assert action_registry.get_action(action_key) is not None
        assert check_registry.get_check(action_config["check_id"]) is not None
        assert action_config["resource_ref"] in resource_aliases
        assert action_config["expected_calls"]
        assert all(
            call.get("service") and call.get("operation")
            for call in action_config["expected_calls"]
        )
