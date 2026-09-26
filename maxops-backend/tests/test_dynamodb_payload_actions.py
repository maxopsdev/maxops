"""Offline DynamoDB action tests backed by real captured AWS responses."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.actions import action_registry
from app.actions.base import ActionExecutionContext
from app.schemas.check import CheckActionRequest
from tests.payload_helpers import (
    StrictActionReplay,
    action_values_match,
    build_dynamodb_action_payload_adapter,
    load_action_manifest,
)


ACTION_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "payloads" / "dynamodb" / "actions"
ACTION_KEYS = tuple(load_action_manifest("dynamodb"))
pytestmark = [pytest.mark.unit, pytest.mark.payload, pytest.mark.dynamodb]


def _model_dict(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


@pytest.mark.skipif(
    not ACTION_FIXTURE_ROOT.exists(),
    reason="DynamoDB action payloads have not been captured from AWS yet.",
)
@pytest.mark.parametrize("action_key", ACTION_KEYS)
def test_dynamodb_action_replays_captured_aws_calls(action_key):
    adapter, replay, metadata = build_dynamodb_action_payload_adapter(action_key)
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
        check=None,
        aws_adapter=adapter,
        db=None,
        action_execution=SimpleNamespace(status="running"),
    )

    response = action_registry.execute(context)

    replay.assert_complete()
    assert response.status == metadata["expected_status"]
    assert response.message == metadata["expected_message"]
    assert action_values_match(metadata["handler_response"], _model_dict(response))


def test_action_replay_rejects_request_parameter_drift():
    replay = StrictActionReplay(
        calls=[
            {
                "service": "dynamodb",
                "operation": "update_table",
                "kwargs": {"TableName": "expected-table"},
                "response_file": "response.json",
            }
        ],
        payloads={"response.json": {"TableDescription": {}}},
    )

    with pytest.raises(AssertionError, match="kwargs mismatch"):
        replay.client("dynamodb").update_table(TableName="wrong-table")


def test_action_replay_rejects_missing_calls():
    replay = StrictActionReplay(
        calls=[
            {
                "service": "dynamodb",
                "operation": "delete_table",
                "kwargs": {"TableName": "table"},
                "response_file": "response.json",
            }
        ],
        payloads={"response.json": {}},
    )

    with pytest.raises(AssertionError, match="were not made"):
        replay.assert_complete()


def test_action_replay_replays_client_errors():
    replay = StrictActionReplay(
        calls=[
            {
                "service": "s3",
                "operation": "get_bucket_lifecycle_configuration",
                "kwargs": {"Bucket": "bucket"},
                "response_file": "error.json",
                "raises_client_error": True,
            }
        ],
        payloads={
            "error.json": {
                "Error": {"Code": "NoSuchLifecycleConfiguration", "Message": "missing"}
            }
        },
    )

    with pytest.raises(Exception, match="NoSuchLifecycleConfiguration"):
        replay.client("s3").get_bucket_lifecycle_configuration(Bucket="bucket")
    replay.assert_complete()


def test_action_replay_replays_paginated_calls():
    replay = StrictActionReplay(
        calls=[
            {
                "service": "s3",
                "operation": "list_objects_v2",
                "kwargs": {"Bucket": "bucket"},
                "response_file": "response.json",
            }
        ],
        payloads={"response.json": {"Pages": [{"Contents": []}]}},
    )

    pages = list(replay.client("s3").get_paginator("list_objects_v2").paginate(Bucket="bucket"))

    assert pages == [{"Contents": []}]
    replay.assert_complete()
