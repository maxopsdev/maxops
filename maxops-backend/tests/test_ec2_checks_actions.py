from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.actions import action_registry
from app.actions.action_mapping import resolve_action_key
from app.actions.base import ActionExecutionContext
from app.actions.handlers_ec2 import (
    handle_ec2_migrate_to_graviton,
    handle_ec2_rightsize,
    handle_ec2_schedule_off_hours,
)
from app.checks.ec2.graviton_candidate import check_ec2_graviton_candidate
from app.checks.ec2.idle_instances import check_ec2_idle_instances
from app.checks.ec2.unused_instances import check_ec2_unused_instances
from app.schemas.check import CheckActionRequest


class CheckAdapter:
    def __init__(self, resources, utilization=None):
        self.resources = resources
        self.utilization = utilization or {}

    def get_resources(self, resource_type, filters=None, region=None):
        items = list(self.resources.get(resource_type, []))
        if resource_type == "ec2" and filters and filters.get("state"):
            return [item for item in items if item.get("state") == filters["state"]]
        return items

    def get_resource_utilization(self, resource_id, resource_type, *args, **kwargs):
        return self.utilization.get(resource_id, {})


class Waiter:
    def __init__(self, calls):
        self.calls = calls

    def wait(self, **kwargs):
        self.calls.append(("wait", kwargs))


class EC2Client:
    def __init__(self, instance_type="m5.large"):
        self.calls = []
        self.instance_type = instance_type

    def stop_instances(self, **kwargs):
        self.calls.append(("stop_instances", kwargs))
        return {}

    def get_waiter(self, name):
        self.calls.append(("get_waiter", {"name": name}))
        return Waiter(self.calls)

    def modify_instance_attribute(self, **kwargs):
        self.calls.append(("modify_instance_attribute", kwargs))
        return {}

    def start_instances(self, **kwargs):
        self.calls.append(("start_instances", kwargs))
        return {}


class SchedulerClient:
    def __init__(self):
        self.calls = []

    def create_schedule(self, **kwargs):
        self.calls.append(("create_schedule", kwargs))
        return {"ScheduleArn": f"arn:aws:scheduler:::schedule/{kwargs['Name']}"}


class Session:
    def __init__(self, clients):
        self.clients = clients

    def client(self, service_name, region_name=None):
        assert region_name == "us-east-1"
        return self.clients[service_name]


def make_context(action, resource_id="i-1234567890", parameters=None, **clients):
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
        aws_adapter=SimpleNamespace(session=Session(clients)),
        db=None,
        action_execution=None,
    )


def test_idle_and_unused_checks_expose_expected_ec2_action_choices():
    idle = check_ec2_idle_instances(
        CheckAdapter(
            {
                "ec2": [
                    {
                        "resource_id": "i-idle",
                        "resource_type": "ec2",
                        "resource_name": "idle",
                        "region": "us-east-1",
                        "state": "running",
                        "instance_type": "m5.large",
                        "metadata": {},
                    }
                ]
            },
            {
                "i-idle": {
                    "cpuutilization": 1.2,
                    "networkin": 100.0,
                    "networkout": 200.0,
                    "memoryutilization": 30.0,
                    "metric_summary": {
                        "cpuutilization": {
                            "maximum": 4.0,
                            "p90": 3.0,
                            "p95": 3.5,
                            "p99": 3.8,
                        }
                    },
                    "metric_history": {
                        "cpuutilization": {
                            "maximum": [4.0],
                            "p90": [3.0],
                            "p95": [3.5],
                            "p99": [3.8],
                        }
                    },
                }
            },
        ),
        region="us-east-1",
    )
    assert idle[0]["metadata"]["recommended_actions"] == [
        "stop",
        "schedule_off_hours",
        "migrate_to_graviton",
    ]
    assert idle[0]["metadata"]["recommended_graviton_instance_type"] == "m6g.large"

    unused = check_ec2_unused_instances(
        CheckAdapter(
            {
                "ec2": [
                    {
                        "resource_id": "i-stopped",
                        "resource_type": "ec2",
                        "resource_name": "stopped",
                        "region": "us-east-1",
                        "state": "stopped",
                        "instance_type": "t3.medium",
                        "metadata": {
                            # long-stopped, so the stopped_days gate passes and
                            # this test stays focused on the action choices
                            "state_transition_reason": (
                                "User initiated (2024-01-15 10:30:00 GMT)"
                            ),
                        },
                    }
                ]
            }
        ),
        region="us-east-1",
    )
    assert unused[0]["metadata"]["recommended_actions"] == [
        "terminate",
        "terminate_with_snapshot",
        "terminate_leave_volume",
    ]


def test_graviton_candidate_check_flags_only_supported_non_graviton_instances():
    result = check_ec2_graviton_candidate(
        CheckAdapter(
            {
                "ec2": [
                    {
                        "resource_id": "i-x86",
                        "resource_type": "ec2",
                        "resource_name": "x86",
                        "region": "us-east-1",
                        "state": "running",
                        "instance_type": "m5.large",
                        "metadata": {},
                    },
                    {
                        "resource_id": "i-graviton",
                        "resource_type": "ec2",
                        "resource_name": "graviton",
                        "region": "us-east-1",
                        "state": "running",
                        "instance_type": "m6g.large",
                        "metadata": {},
                    },
                    {
                        "resource_id": "i-unsupported",
                        "resource_type": "ec2",
                        "resource_name": "unsupported",
                        "region": "us-east-1",
                        "state": "running",
                        "instance_type": "i3.large",
                        "metadata": {},
                    },
                ]
            }
        ),
        region="us-east-1",
    )

    assert [item["resource_id"] for item in result] == ["i-x86"]
    assert result[0]["metadata"]["recommended_instance_type"] == "m6g.large"
    assert result[0]["metadata"]["recommended_actions"] == ["migrate_to_graviton"]




def test_rightsize_action_changes_instance_type():
    client = EC2Client()
    response = handle_ec2_rightsize(
        make_context(
            "rightsize",
            parameters={"target_instance_type": "m5.large"},
            ec2=client,
        )
    )

    assert response.details == {
        "instance_id": "i-1234567890",
        "target_instance_type": "m5.large",
    }
    assert client.calls == [
        ("stop_instances", {"InstanceIds": ["i-1234567890"]}),
        ("get_waiter", {"name": "instance_stopped"}),
        ("wait", {"InstanceIds": ["i-1234567890"]}),
        (
            "modify_instance_attribute",
            {
                "InstanceId": "i-1234567890",
                "InstanceType": {"Value": "m5.large"},
            },
        ),
        ("start_instances", {"InstanceIds": ["i-1234567890"]}),
    ]


def test_graviton_action_rejects_non_graviton_targets():
    with pytest.raises(HTTPException) as exc_info:
        handle_ec2_migrate_to_graviton(
            make_context(
                "migrate_to_graviton",
                parameters={"target_instance_type": "m6i.large"},
                ec2=EC2Client(),
            )
        )

    assert exc_info.value.status_code == 400
    assert "Graviton" in exc_info.value.detail


def test_schedule_off_hours_action_creates_stop_and_start_schedules():
    scheduler = SchedulerClient()
    response = handle_ec2_schedule_off_hours(
        make_context(
            "schedule_off_hours",
            parameters={
                "scheduler_role_arn": "arn:aws:iam::123456789012:role/maxops-scheduler",
                "stop_schedule_expression": "cron(0 20 ? * MON-FRI *)",
                "start_schedule_expression": "cron(0 8 ? * MON-FRI *)",
                "timezone": "America/New_York",
                "schedule_group": "maxops",
            },
            scheduler=scheduler,
        )
    )

    assert response.status == "submitted"
    assert [call[0] for call in scheduler.calls] == [
        "create_schedule",
        "create_schedule",
    ]
    stop_call = scheduler.calls[0][1]
    start_call = scheduler.calls[1][1]
    assert stop_call["Target"]["Arn"].endswith("ec2:stopInstances")
    assert start_call["Target"]["Arn"].endswith("ec2:startInstances")
    assert stop_call["GroupName"] == "maxops"
    assert start_call["ScheduleExpressionTimezone"] == "America/New_York"


def test_ec2_new_actions_are_registered_and_mapped():
    assert action_registry.get_action("rightsize") is not None
    assert action_registry.get_action("schedule_off_hours") is not None
    assert resolve_action_key("rightsize instance") == "rightsize"
    assert resolve_action_key("change instance family") == "migrate_to_graviton"
    assert resolve_action_key("schedule stop/start") == "schedule_off_hours"
