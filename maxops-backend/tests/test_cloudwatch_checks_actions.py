from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.actions.base import ActionExecutionContext
from app.actions.handlers_cloudwatch import (
    handle_cloudwatch_consolidate_duplicate_alarms,
    handle_cloudwatch_disable_alarm_actions,
    handle_cloudwatch_tune_alarm,
)
from app.checks.cloudwatch.alarms_no_actions import (
    check_cloudwatch_alarms_no_actions,
)
from app.checks.cloudwatch.alarms_with_duplicates import (
    check_cloudwatch_duplicate_alarms,
)
from app.checks.cloudwatch.alarms_with_high_volume import (
    check_cloudwatch_high_volume_alerts_triggered,
)
from app.checks.cloudwatch.log_groups_no_expiration import (
    check_cloudwatch_log_groups_no_expiration,
)
from app.schemas.check import CheckActionRequest


class ResourceAdapter:
    def __init__(self, resources):
        self.resources = resources

    def get_resources(self, resource_type, filters, region):
        return self.resources[resource_type]

    def get_resource_utilization(self, resource_id, resource_type, start, end):
        return self.resources.get("utilization", {}).get(resource_id, {})


class CloudWatchClient:
    def __init__(self, metric_alarms=None, composite_alarms=None):
        self.metric_alarms = metric_alarms or []
        self.composite_alarms = composite_alarms or []
        self.calls = []

    def describe_alarms(self, **kwargs):
        self.calls.append(("describe_alarms", kwargs))
        requested = set(kwargs.get("AlarmNames", []))
        return {
            "MetricAlarms": [
                alarm
                for alarm in self.metric_alarms
                if not requested or alarm["AlarmName"] in requested
            ],
            "CompositeAlarms": [
                alarm
                for alarm in self.composite_alarms
                if not requested or alarm["AlarmName"] in requested
            ],
        }

    def disable_alarm_actions(self, **kwargs):
        self.calls.append(("disable_alarm_actions", kwargs))
        return {}

    def put_metric_alarm(self, **kwargs):
        self.calls.append(("put_metric_alarm", kwargs))
        return {}

    def delete_alarms(self, **kwargs):
        self.calls.append(("delete_alarms", kwargs))
        return {}


class Session:
    def __init__(self, cloudwatch):
        self.cloudwatch = cloudwatch

    def client(self, service_name, region_name=None):
        assert service_name == "cloudwatch"
        assert region_name == "us-east-1"
        return self.cloudwatch


def make_context(client, action, resource_id="cpu-alarm", parameters=None):
    payload = CheckActionRequest(
        action=action,
        account_id="123456789012",
        region="us-east-1",
        resource_id=resource_id,
        parameters=parameters or {},
    )
    return ActionExecutionContext(
        check_id="test-check",
        action_key=action,
        payload=payload,
        check=None,
        aws_adapter=SimpleNamespace(session=Session(client)),
        db=None,
        action_execution=None,
    )


def metric_alarm(name="cpu-alarm", **overrides):
    alarm = {
        "AlarmName": name,
        "AlarmDescription": "CPU is high",
        "ActionsEnabled": True,
        "OKActions": ["arn:ok"],
        "AlarmActions": ["arn:alarm"],
        "InsufficientDataActions": ["arn:insufficient"],
        "MetricName": "CPUUtilization",
        "Namespace": "AWS/EC2",
        "Statistic": "Average",
        "Dimensions": [{"Name": "InstanceId", "Value": "i-123"}],
        "Period": 300,
        "Unit": "Percent",
        "EvaluationPeriods": 3,
        "DatapointsToAlarm": 2,
        "Threshold": 80.0,
        "ComparisonOperator": "GreaterThanThreshold",
        "TreatMissingData": "missing",
        "StateValue": "OK",
        "AlarmArn": f"arn:aws:cloudwatch:::alarm:{name}",
    }
    alarm.update(overrides)
    return alarm


def test_log_groups_without_expiration_flags_only_missing_retention():
    adapter = ResourceAdapter({
        "cloudwatch_log_group": [
            {
                "resource_id": "/aws/lambda/no-retention",
                "metadata": {"retentionInDays": None, "storedBytes": 123},
            },
            {
                "resource_id": "/aws/lambda/retained",
                "metadata": {"retentionInDays": 30, "storedBytes": 456},
            },
        ]
    })

    result = check_cloudwatch_log_groups_no_expiration(adapter, "us-east-1")

    assert [item["resource_id"] for item in result] == ["/aws/lambda/no-retention"]
    metadata = result[0]["metadata"]
    assert metadata["current_retention_days"] is None
    assert metadata["stored_bytes"] == 123
    assert metadata["recommended_actions"] == [
        "cloudwatch_set_log_group_retention"
    ]


def test_alarms_without_actions_includes_metric_and_composite_context():
    adapter = ResourceAdapter({
        "cloudwatch_alarm": [
            {
                "resource_id": "metric",
                "metadata": {
                    "AlarmActions": [],
                    "OKActions": ["arn:ok"],
                    "InsufficientDataActions": [],
                },
            },
            {
                "resource_id": "composite",
                "metadata": {
                    "AlarmRule": 'ALARM("metric")',
                    "AlarmActions": [],
                    "OKActions": [],
                    "InsufficientDataActions": ["arn:insufficient"],
                },
            },
            {
                "resource_id": "actionful",
                "metadata": {"AlarmActions": ["arn:alarm"]},
            },
        ]
    })

    result = check_cloudwatch_alarms_no_actions(adapter, "us-east-1")

    assert [item["resource_id"] for item in result] == ["metric", "composite"]
    assert result[0]["metadata"]["ok_actions"] == ["arn:ok"]
    assert result[1]["metadata"]["insufficient_data_actions"] == [
        "arn:insufficient"
    ]
    assert all(
        item["metadata"]["recommended_actions"] == ["cloudwatch_delete_alarm"]
        for item in result
    )


def test_noisy_and_duplicate_checks_offer_supported_action_choices():
    duplicate_metadata = {
        "Namespace": "AWS/EC2",
        "MetricName": "CPUUtilization",
        "Statistic": "Average",
        "Period": 300,
        "EvaluationPeriods": 2,
        "Threshold": 80,
        "ComparisonOperator": "GreaterThanThreshold",
        "TreatMissingData": "missing",
        "Dimensions": [{"Name": "InstanceId", "Value": "i-123"}],
    }
    duplicate_adapter = ResourceAdapter({
        "cloudwatch_alarm": [
            {"resource_id": "keeper", "metadata": dict(duplicate_metadata)},
            {"resource_id": "duplicate", "metadata": dict(duplicate_metadata)},
        ]
    })
    duplicate_results = check_cloudwatch_duplicate_alarms(
        duplicate_adapter, "us-east-1"
    )
    assert all(
        result["metadata"]["recommended_actions"]
        == ["cloudwatch_consolidate_duplicate_alarms"]
        for result in duplicate_results
    )

    noisy_adapter = ResourceAdapter({
        "cloudwatch_alarm": [
            {"resource_id": "noisy", "metadata": {}, "state": "OK"}
        ],
        "utilization": {"noisy": {"trigger_count": 10}},
    })
    noisy_results = check_cloudwatch_high_volume_alerts_triggered(
        noisy_adapter,
        lookback_days=7,
        trigger_count_threshold=5,
        region="us-east-1",
    )
    assert noisy_results[0]["metadata"]["recommended_actions"] == [
        "cloudwatch_disable_alarm_actions",
        "cloudwatch_tune_alarm",
    ]


def test_disable_alarm_actions_verifies_then_disables():
    client = CloudWatchClient(metric_alarms=[metric_alarm()])

    response = handle_cloudwatch_disable_alarm_actions(
        make_context(client, "cloudwatch_disable_alarm_actions")
    )

    assert response.status == "submitted"
    assert client.calls == [
        ("describe_alarms", {"AlarmNames": ["cpu-alarm"]}),
        ("disable_alarm_actions", {"AlarmNames": ["cpu-alarm"]}),
    ]


def test_tune_alarm_preserves_unspecified_configuration():
    alarm = metric_alarm()
    client = CloudWatchClient(metric_alarms=[alarm])

    response = handle_cloudwatch_tune_alarm(
        make_context(
            client,
            "cloudwatch_tune_alarm",
            parameters={"threshold": 65, "evaluation_periods": 5},
        )
    )

    put_call = client.calls[1]
    assert put_call[0] == "put_metric_alarm"
    request = put_call[1]
    assert request["Threshold"] == 65.0
    assert request["EvaluationPeriods"] == 5
    assert request["Period"] == alarm["Period"]
    assert request["AlarmActions"] == alarm["AlarmActions"]
    assert request["Dimensions"] == alarm["Dimensions"]
    assert "StateValue" not in request
    assert "AlarmArn" not in request
    assert response.details["updated_fields"] == {
        "threshold": 65.0,
        "evaluation_periods": 5,
    }


@pytest.mark.parametrize(
    ("alarm", "parameters", "message"),
    [
        (metric_alarm(), {}, "At least one tuning parameter"),
        (
            {"AlarmName": "cpu-alarm", "AlarmRule": 'ALARM("other")'},
            {"threshold": 1},
            "Composite alarms",
        ),
        (
            metric_alarm(Metrics=[{"Id": "m1"}]),
            {"threshold": 1},
            "Metric-math alarms",
        ),
        (
            metric_alarm(),
            {"period": 2.5},
            "period must be a positive integer",
        ),
    ],
)
def test_tune_alarm_rejects_unsupported_requests(alarm, parameters, message):
    client = CloudWatchClient(
        metric_alarms=[] if "AlarmRule" in alarm else [alarm],
        composite_alarms=[alarm] if "AlarmRule" in alarm else [],
    )

    with pytest.raises(HTTPException) as exc_info:
        handle_cloudwatch_tune_alarm(
            make_context(client, "cloudwatch_tune_alarm", parameters=parameters)
        )

    assert exc_info.value.status_code == 400
    assert message in exc_info.value.detail
    assert not any(call[0] == "put_metric_alarm" for call in client.calls)


def test_consolidate_duplicate_alarms_deletes_only_selected_duplicates():
    client = CloudWatchClient(
        metric_alarms=[
            metric_alarm("keeper"),
            metric_alarm("duplicate-a"),
            metric_alarm("duplicate-b"),
        ]
    )

    response = handle_cloudwatch_consolidate_duplicate_alarms(
        make_context(
            client,
            "cloudwatch_consolidate_duplicate_alarms",
            resource_id="keeper",
            parameters={"duplicate_alarm_names": ["duplicate-b", "duplicate-a"]},
        )
    )

    assert client.calls[-1] == (
        "delete_alarms",
        {"AlarmNames": ["duplicate-b", "duplicate-a"]},
    )
    assert response.details["keeper_alarm_name"] == "keeper"
    assert response.details["deleted_alarm_names"] == [
        "duplicate-b",
        "duplicate-a",
    ]


def test_consolidate_duplicate_alarms_rejects_mismatch_and_keeper_deletion():
    mismatch_client = CloudWatchClient(
        metric_alarms=[
            metric_alarm("keeper"),
            metric_alarm("different", Threshold=90.0),
        ]
    )
    with pytest.raises(HTTPException) as mismatch:
        handle_cloudwatch_consolidate_duplicate_alarms(
            make_context(
                mismatch_client,
                "cloudwatch_consolidate_duplicate_alarms",
                resource_id="keeper",
                parameters={"duplicate_alarm_names": ["different"]},
            )
        )
    assert mismatch.value.status_code == 400
    assert not any(call[0] == "delete_alarms" for call in mismatch_client.calls)

    with pytest.raises(HTTPException) as keeper:
        handle_cloudwatch_consolidate_duplicate_alarms(
            make_context(
                CloudWatchClient(metric_alarms=[metric_alarm("keeper")]),
                "cloudwatch_consolidate_duplicate_alarms",
                resource_id="keeper",
                parameters={"duplicate_alarm_names": ["keeper"]},
            )
        )
    assert keeper.value.status_code == 400


def test_new_cloudwatch_actions_are_registered_and_mapped():
    from app.actions import action_registry
    from app.actions.action_mapping import resolve_action_key

    for action_key in (
        "cloudwatch_disable_alarm_actions",
        "cloudwatch_tune_alarm",
        "cloudwatch_consolidate_duplicate_alarms",
    ):
        assert action_registry.get_action(action_key) is not None

    assert resolve_action_key("disable alarm actions") == (
        "cloudwatch_disable_alarm_actions"
    )
    assert resolve_action_key("tune alarm") == "cloudwatch_tune_alarm"
    assert resolve_action_key("consolidate duplicate alarms") == (
        "cloudwatch_consolidate_duplicate_alarms"
    )
