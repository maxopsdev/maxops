"""Rightsizer-driven action execution tests backed by hand-authored payload fixtures.

Unlike test_payload_actions.py, this module deliberately does NOT parametrize
from the tests_generator manifests: the fixtures under
tests/payloads/<service>/actions/<action_key>/<scenario>/ used here are
hand-authored (capture_source: "hand-authored"), never captured from real AWS,
and the case table lives inline below. See
docs/RIGHTSIZER_ACTION_EXECUTION_TEST_PLAN.md for the full plan.

All tests are offline: AWS calls are replayed strictly (exact operation order
and kwargs) through StrictActionReplay, so a handler that drifts from the
captured sequence fails loudly and no boto3 client is ever created.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.actions import action_registry
from app.actions.base import ActionExecutionContext
from app.api.routes.rightsizer import (
    EC2_RIGHTSIZER_ACTION_CHECK_ID,
    RightsizerApplyRequest,
    apply_ec2_recommendation,
)
from app.checks.registry import check_registry
from app.schemas.check import CheckActionRequest
from tests.payload_helpers import action_values_match, build_action_payload_adapter


# (service, action_key, scenario) triples with fixtures on disk.
SUCCESS_CASES = (
    ("ec2", "rightsize", "success"),
    # ASG capacity changes: only the provided values are sent to
    # UpdateAutoScalingGroup; omitted dimensions keep their current setting.
    ("asg", "asg_rightsize", "success"),
    ("asg", "asg_rightsize", "desired_only_success"),
    ("asg", "asg_rightsize", "min_desired_success"),
    ("asg", "asg_rightsize", "max_reduction_success"),
    # NODE_TYPE_CHANGE recommendation applied to a replication group (the path
    # the ElastiCache rightsizer targets; the captured "success" scenario only
    # covers the standalone cache-cluster fallback).
    ("elasticache", "elasticache_downsize", "replication_group_success"),
    # REPLICA_COUNT_REDUCTION recommendation: node type unchanged, replica
    # count lowered via ReplicasPerNodeGroup.
    ("elasticache", "elasticache_downsize", "replica_reduction_success"),
)

# Error scenarios; expected status code / detail fragment live in each
# fixture's capture_metadata.json under "expected_error".
ERROR_CASES = (
    ("ec2", "rightsize", "invalid_target"),
    ("ec2", "rightsize", "stop_fails"),
    # num_cache_nodes above the replication group's member count must be
    # rejected after the describe call, before any modify is attempted.
    ("elasticache", "elasticache_downsize", "node_count_exceeds_current"),
    # rds_migrate_graviton is the only RDS class-change handler today; these
    # pin its validation boundary until the generic rds_rightsize handler
    # exists (see docs/RIGHTSIZER_ACTION_EXECUTION_TEST_PLAN.md Phase 3).
    # Non-Graviton targets are rejected before any AWS call -- the exact gap
    # that rds_rightsize will fill.
    ("rds", "rds_migrate_graviton", "non_graviton_target"),
    ("rds", "rds_migrate_graviton", "same_class"),
    ("rds", "rds_migrate_graviton", "not_found"),
    ("rds", "rds_migrate_graviton", "modify_fails"),
    # asg_rightsize parameter validation rejects before any AWS call...
    ("asg", "asg_rightsize", "missing_targets"),
    ("asg", "asg_rightsize", "invalid_value"),
    ("asg", "asg_rightsize", "negative_value"),
    # ...ordering is validated against effective (requested or current) values
    # after the describe call, so a partial update can never leave the group
    # with min > desired or desired > max...
    ("asg", "asg_rightsize", "min_exceeds_desired"),
    ("asg", "asg_rightsize", "desired_exceeds_max"),
    # ...and describe-time outcomes stop before any update is attempted.
    ("asg", "asg_rightsize", "no_op"),
    ("asg", "asg_rightsize", "not_found"),
    ("asg", "asg_rightsize", "update_fails"),
)

# Rightsizer apply flows: (provenance label, action_key, resource_type). EC2
# uses the dedicated rightsizer route; ASG remains handler-only.
RIGHTSIZER_APPLY_FLOWS = (
    (EC2_RIGHTSIZER_ACTION_CHECK_ID, "rightsize", "ec2"),
    ("asg_low_cpu_overprovisioned", "asg_rightsize", "asg"),
)

pytestmark = [pytest.mark.unit, pytest.mark.payload]


def _model_dict(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


class _NullDB:
    def commit(self) -> None:
        return None


def _build_context(service: str, action_key: str, scenario: str):
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
    return context, replay, metadata


@pytest.mark.parametrize("service,action_key,scenario", SUCCESS_CASES)
def test_rightsizer_action_replays_expected_aws_calls(service, action_key, scenario):
    context, replay, metadata = _build_context(service, action_key, scenario)

    response = action_registry.execute(context)

    replay.assert_complete()
    assert response.status == metadata["expected_status"]
    assert response.message == metadata["expected_message"]
    assert action_values_match(metadata["handler_response"], _model_dict(response))


@pytest.mark.parametrize("service,action_key,scenario", ERROR_CASES)
def test_rightsizer_action_error_scenarios_stop_at_the_failing_call(service, action_key, scenario):
    context, replay, metadata = _build_context(service, action_key, scenario)
    expected_error = metadata["expected_error"]

    with pytest.raises(HTTPException) as exc_info:
        action_registry.execute(context)

    assert exc_info.value.status_code == expected_error["status_code"]
    # For AWS-failure scenarios the detail must carry the AWS error code: the
    # handler wraps *any* exception (including the replay's own unexpected-call
    # assertion) in a 500, so matching on the error code proves the failure came
    # from the intended call and not from a masked extra AWS call.
    assert expected_error["detail_contains"] in str(exc_info.value.detail)
    # Every call listed in the fixture was made -- and, because StrictActionReplay
    # rejects extra calls, none beyond it (e.g. modify/start never run after a
    # failed stop).
    replay.assert_complete()


@pytest.mark.parametrize("check_id,action_key,resource_type", RIGHTSIZER_APPLY_FLOWS)
def test_rightsizer_apply_flow_targets_provenance_and_action(check_id, action_key, resource_type):
    """The UI flow keeps EC2 provenance separate from the check registry."""
    action = action_registry.get_action(action_key)
    assert action is not None, f"action '{action_key}' is not registered"
    assert resource_type in action.resource_types

    check = check_registry.get_check(check_id)
    if check_id == EC2_RIGHTSIZER_ACTION_CHECK_ID:
        assert check is None
    else:
        assert check is not None, f"check '{check_id}' is not registered"
        assert check.resource_type == resource_type


def test_ec2_rightsizer_apply_route_uses_shared_action_helper(monkeypatch):
    captured = {}

    def fake_execute_action(check_id, payload, check, db):
        captured.update(check_id=check_id, payload=payload, check=check, db=db)
        return {"check_id": check_id, "action": payload.action, "status": "submitted", "message": "ok"}

    monkeypatch.setattr(
        "app.api.routes.rightsizer.execute_action_for_check",
        fake_execute_action,
    )
    body = RightsizerApplyRequest(
        target_instance_type="m5.large",
        recommendation_option="balanced",
        account_id="123456789012",
        region="us-east-1",
        resource_id="i-PLACEHOLDER-INSTANCE",
    )

    response = apply_ec2_recommendation(7, body, "db")

    assert response["status"] == "submitted"
    assert captured["check_id"] == EC2_RIGHTSIZER_ACTION_CHECK_ID
    assert captured["check"] is None
    assert captured["db"] == "db"
    assert captured["payload"].action == "rightsize"
    assert captured["payload"].parameters == {
        "target_instance_type": "m5.large",
        "recommendation_option": "balanced",
    }
