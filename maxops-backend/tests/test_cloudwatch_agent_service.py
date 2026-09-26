from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.actions.base import ActionExecutionContext
from app.actions.handlers_ec2 import handle_install_cloudwatch_agent
from app.schemas.check import CheckActionRequest
from app.services import cloudwatch_agent_service as service
from app.services.cloudwatch_agent_service import (
    ElevatedRoleRequiredError,
    build_assume_role_permission_statement,
    build_assume_role_trust_statement,
    build_cloudwatch_agent_config,
)


class Session:
    def client(self, service_name, region_name=None):
        raise AssertionError("No boto3 clients should be called in this unit test")


def make_context(parameters=None):
    payload = CheckActionRequest(
        action="install_cloudwatch_agent",
        account_id="123456789012",
        region="us-east-1",
        resource_id="i-1234567890",
        parameters=parameters or {},
    )
    return ActionExecutionContext(
        check_id="ec2_cloudwatch_agent",
        action_key="install_cloudwatch_agent",
        payload=payload,
        check=None,
        aws_adapter=SimpleNamespace(session=Session()),
        db=None,
        action_execution=None,
    )


def test_build_cloudwatch_agent_config_includes_memory_and_disk_metrics():
    config = build_cloudwatch_agent_config()

    metrics = config["metrics"]["metrics_collected"]
    assert metrics["mem"]["measurement"] == ["mem_used_percent"]
    assert metrics["disk"]["measurement"] == ["used_percent"]
    assert config["metrics"]["namespace"] == "CWAgent"


def test_assume_role_guidance_payloads_are_minimal_and_specific():
    permission = build_assume_role_permission_statement("arn:aws:iam::123456789012:role/Remediator")
    trust = build_assume_role_trust_statement("arn:aws:iam::123456789012:role/MaxOpsReadOnlyRole")

    assert permission["Statement"][0]["Action"] == "sts:AssumeRole"
    assert permission["Statement"][0]["Resource"].endswith(":role/Remediator")
    assert trust["Statement"][0]["Principal"]["AWS"].endswith(":role/MaxOpsReadOnlyRole")


def test_validate_cloudwatch_agent_reports_permission_gap_without_elevated_role(monkeypatch):
    monkeypatch.setattr(
        service,
        "_ssm_managed_instance_status",
        lambda session, region, instance_id: {
            "ssm_reachable": True,
            "ping_status": "Online",
            "platform_type": "Linux",
            "platform_name": "Amazon Linux",
        },
    )
    monkeypatch.setattr(
        service,
        "_iam_details_for_instance",
        lambda session, region, instance_id, elevated_role_arn=None: {
            "instance_profile_arn": "arn:aws:iam::123456789012:instance-profile/TestProfile",
            "instance_profile_name": "TestProfile",
            "instance_role_name": "TestRole",
            "put_metric_data_allowed": False,
            "matched_policies": [],
            "permission_check_status": "missing",
            "requires_elevated_role": False,
            "used_elevated_role": False,
            "remediation_options": [{"type": "provide_elevated_role"}],
        },
    )
    monkeypatch.setattr(
        service,
        "run_ssm_shell_command",
        lambda **kwargs: {
            "command_id": "cmd-123",
            "status": "Success",
            "stdout": (
                '{"agent_installed": true, "agent_running": true, "config_present": true, '
                '"memory_metrics_enabled": true, "disk_metrics_enabled": true}'
            ),
            "stderr": "",
        },
    )

    result = service.validate_cloudwatch_agent(Session(), "us-east-1", "i-123")

    assert result["validation_status"] == "permission_missing"
    assert result["put_metric_data_allowed"] is False
    assert result["agent_running"] is True


def test_install_handler_returns_conflict_when_elevated_role_is_required(monkeypatch):
    def _raise(*args, **kwargs):
        raise ElevatedRoleRequiredError(
            "missing permission",
            details={"remediation_options": [{"type": "provide_elevated_role"}]},
        )

    monkeypatch.setattr(
        "app.actions.handlers_ec2.install_or_configure_cloudwatch_agent",
        _raise,
    )

    with pytest.raises(HTTPException) as exc_info:
        handle_install_cloudwatch_agent(make_context())

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["message"] == "missing permission"


def test_install_handler_uses_elevated_role_parameter(monkeypatch):
    observed = {}

    def _install_or_configure(session, region, instance_id, elevated_role_arn=None):
        observed["elevated_role_arn"] = elevated_role_arn
        return {
            "installed_during_action": False,
            "agent_installed": True,
            "agent_running": True,
            "config_present": True,
            "memory_metrics_enabled": True,
            "disk_metrics_enabled": True,
        }

    monkeypatch.setattr(
        "app.actions.handlers_ec2.install_or_configure_cloudwatch_agent",
        _install_or_configure,
    )

    response = handle_install_cloudwatch_agent(
        make_context(parameters={"elevated_role_arn": "arn:aws:iam::123456789012:role/Remediator"})
    )

    assert observed["elevated_role_arn"].endswith(":role/Remediator")
    assert response.details["agent_running"] is True
