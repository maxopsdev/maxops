"""CloudWatch agent validation and remediation helpers for EC2 instances."""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, Optional

import boto3
from botocore.exceptions import ClientError


AGENT_POLICY_NAME = "MaxOpsCloudWatchAgentPublishMetrics"
AGENT_CONFIG_PATH = "/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json"
AGENT_CONFIG_DIR = "/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.d"
SSM_STATUS_PENDING = {"Pending", "InProgress", "Delayed"}
IAM_ACCESS_DENIED_CODES = {
    "AccessDenied",
    "AccessDeniedException",
    "UnauthorizedOperation",
    "Client.UnauthorizedOperation",
}


class CloudWatchAgentError(Exception):
    """Base error for CloudWatch agent orchestration."""


class ElevatedRoleRequiredError(CloudWatchAgentError):
    """Raised when a separate role is required to inspect or modify IAM."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.details = details or {}


def _policy_document() -> Dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowPublishCwAgentMetrics",
                "Effect": "Allow",
                "Action": "cloudwatch:PutMetricData",
                "Resource": "*",
                "Condition": {
                    "StringEquals": {
                        "cloudwatch:namespace": "CWAgent",
                    }
                },
            }
        ],
    }


def build_assume_role_permission_statement(role_arn: str) -> Dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowAssumeCloudWatchAgentRemediationRole",
                "Effect": "Allow",
                "Action": "sts:AssumeRole",
                "Resource": role_arn,
            }
        ],
    }


def build_assume_role_trust_statement(principal_arn: str) -> Dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowMaxOpsAssumeRole",
                "Effect": "Allow",
                "Principal": {"AWS": principal_arn},
                "Action": "sts:AssumeRole",
            }
        ],
    }


def build_cloudwatch_agent_config() -> Dict[str, Any]:
    return {
        "agent": {
            "metrics_collection_interval": 60,
            "run_as_user": "root",
        },
        "metrics": {
            "namespace": "CWAgent",
            "append_dimensions": {
                "InstanceId": "${aws:InstanceId}",
                "InstanceType": "${aws:InstanceType}",
                "ImageId": "${aws:ImageId}",
                "AutoScalingGroupName": "${aws:AutoScalingGroupName}",
            },
            "metrics_collected": {
                "mem": {
                    "measurement": ["mem_used_percent"],
                    "metrics_collection_interval": 60,
                },
                "disk": {
                    "measurement": ["used_percent"],
                    "resources": ["*"],
                    "ignore_file_system_types": [
                        "sysfs",
                        "devtmpfs",
                        "tmpfs",
                        "overlay",
                        "squashfs",
                    ],
                    "drop_device": True,
                    "metrics_collection_interval": 60,
                },
            },
        },
    }


def _client(session: boto3.Session, service_name: str, region: str):
    return session.client(service_name, region_name=region)


def get_caller_identity(session: boto3.Session, region: str) -> Dict[str, Any]:
    return _client(session, "sts", region).get_caller_identity()


def assume_role_session(
    source_session: boto3.Session,
    role_arn: str,
    region: str,
    session_name: str = "maxops-cloudwatch-agent-remediation",
) -> boto3.Session:
    sts = _client(source_session, "sts", region)
    response = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=session_name,
        DurationSeconds=3600,
    )
    credentials = response["Credentials"]
    return boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=region,
    )


def _parse_instance_profile_name(profile_arn: str) -> str:
    return profile_arn.rsplit("/", 1)[-1]


def _describe_instance(session: boto3.Session, instance_id: str, region: str) -> Dict[str, Any]:
    ec2 = _client(session, "ec2", region)
    response = ec2.describe_instances(InstanceIds=[instance_id])
    reservations = response.get("Reservations", [])
    if not reservations or not reservations[0].get("Instances"):
        raise CloudWatchAgentError(f"EC2 instance {instance_id} was not found.")
    return reservations[0]["Instances"][0]


def _matches_action(action_value: Any, expected: str) -> bool:
    if isinstance(action_value, str):
        normalized = action_value.lower()
        return normalized in {"*", expected.lower(), "cloudwatch:*"}
    if isinstance(action_value, Iterable):
        return any(_matches_action(item, expected) for item in action_value)
    return False


def _statement_allows_put_metric_data(statement: Dict[str, Any]) -> bool:
    if str(statement.get("Effect") or "").lower() != "allow":
        return False
    return _matches_action(statement.get("Action"), "cloudwatch:PutMetricData")


def _policy_allows_put_metric_data(policy_document: Dict[str, Any]) -> bool:
    statements = policy_document.get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    return any(_statement_allows_put_metric_data(statement) for statement in statements if isinstance(statement, dict))


def _role_has_put_metric_data_permission(iam_client: Any, role_name: str) -> tuple[bool, list[str]]:
    matched_policies: list[str] = []

    inline_names = iam_client.list_role_policies(RoleName=role_name).get("PolicyNames", [])
    for policy_name in inline_names:
        response = iam_client.get_role_policy(RoleName=role_name, PolicyName=policy_name)
        policy_document = response.get("PolicyDocument") or {}
        if _policy_allows_put_metric_data(policy_document):
            matched_policies.append(f"inline:{policy_name}")

    attached = iam_client.list_attached_role_policies(RoleName=role_name).get("AttachedPolicies", [])
    for policy in attached:
        policy_arn = policy.get("PolicyArn")
        policy_name = policy.get("PolicyName") or policy_arn or "managed"
        if not policy_arn:
            continue
        policy_metadata = iam_client.get_policy(PolicyArn=policy_arn).get("Policy", {})
        version_id = policy_metadata.get("DefaultVersionId")
        if not version_id:
            continue
        version = iam_client.get_policy_version(
            PolicyArn=policy_arn,
            VersionId=version_id,
        ).get("PolicyVersion", {})
        policy_document = version.get("Document") or {}
        if _policy_allows_put_metric_data(policy_document):
            matched_policies.append(f"managed:{policy_name}")

    return bool(matched_policies), matched_policies


def _iam_details_for_instance(
    base_session: boto3.Session,
    region: str,
    instance_id: str,
    elevated_role_arn: Optional[str] = None,
) -> Dict[str, Any]:
    instance = _describe_instance(base_session, instance_id, region)
    profile_arn = (instance.get("IamInstanceProfile") or {}).get("Arn")
    result: Dict[str, Any] = {
        "instance_profile_arn": profile_arn,
        "instance_profile_name": _parse_instance_profile_name(profile_arn) if profile_arn else None,
        "instance_role_name": None,
        "put_metric_data_allowed": None,
        "matched_policies": [],
        "permission_check_status": "not_attached" if not profile_arn else "unknown",
        "requires_elevated_role": False,
        "used_elevated_role": False,
        "elevated_role_arn": elevated_role_arn,
        "assume_role_status": None,
        "assume_role_error": None,
    }
    if not profile_arn:
        return result

    inspection_session = base_session
    if elevated_role_arn:
        try:
            inspection_session = assume_role_session(base_session, elevated_role_arn, region)
            result["used_elevated_role"] = True
            result["assume_role_status"] = "assumed"
        except ClientError as exc:
            caller = get_caller_identity(base_session, region)
            result["requires_elevated_role"] = True
            result["permission_check_status"] = "cannot_assume_elevated_role"
            result["assume_role_status"] = "failed"
            result["assume_role_error"] = str(exc)
            result["remediation_options"] = _remediation_options(
                caller_arn=str(caller.get("Arn") or ""),
                elevated_role_arn=elevated_role_arn,
                role_name=None,
                role_known=False,
                include_assume_role_guidance=True,
            )
            return result

    iam = _client(inspection_session, "iam", region)
    try:
        profile = iam.get_instance_profile(InstanceProfileName=result["instance_profile_name"]).get("InstanceProfile", {})
        roles = profile.get("Roles") or []
        if roles:
            result["instance_role_name"] = roles[0].get("RoleName")
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code in IAM_ACCESS_DENIED_CODES:
            caller = get_caller_identity(base_session, region)
            result["requires_elevated_role"] = True
            result["permission_check_status"] = "requires_elevated_role"
            result["remediation_options"] = _remediation_options(
                caller_arn=str(caller.get("Arn") or ""),
                elevated_role_arn=elevated_role_arn,
                role_name=None,
                role_known=False,
                include_assume_role_guidance=bool(elevated_role_arn),
            )
            return result
        raise

    role_name = result["instance_role_name"]
    if not role_name:
        result["permission_check_status"] = "role_not_found"
        return result

    try:
        allowed, matched = _role_has_put_metric_data_permission(iam, role_name)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code in IAM_ACCESS_DENIED_CODES:
            caller = get_caller_identity(base_session, region)
            result["requires_elevated_role"] = True
            result["permission_check_status"] = "requires_elevated_role"
            result["remediation_options"] = _remediation_options(
                caller_arn=str(caller.get("Arn") or ""),
                elevated_role_arn=elevated_role_arn,
                role_name=role_name,
                role_known=True,
                include_assume_role_guidance=bool(elevated_role_arn),
            )
            return result
        raise

    result["put_metric_data_allowed"] = allowed
    result["matched_policies"] = matched
    result["permission_check_status"] = "allowed" if allowed else "missing"
    if not allowed:
        caller = get_caller_identity(base_session, region)
        result["remediation_options"] = _remediation_options(
            caller_arn=str(caller.get("Arn") or ""),
            elevated_role_arn=elevated_role_arn,
            role_name=role_name,
            role_known=True,
            include_assume_role_guidance=False,
        )
    return result


def _remediation_options(
    caller_arn: str,
    elevated_role_arn: Optional[str],
    role_name: Optional[str],
    role_known: bool,
    include_assume_role_guidance: bool,
) -> list[Dict[str, Any]]:
    options: list[Dict[str, Any]] = []
    options.append(
        {
            "type": "provide_elevated_role",
            "message": "Provide an elevated role ARN so MaxOps can inspect or update the EC2 instance role.",
            "required_parameter": "elevated_role_arn",
        }
    )
    if role_known and role_name:
        options.append(
            {
                "type": "manual_policy_update",
                "message": "Attach a minimal CloudWatch publish policy to the EC2 instance role.",
                "target_role_name": role_name,
                "policy_name": AGENT_POLICY_NAME,
                "policy_document": _policy_document(),
            }
        )
    else:
        options.append(
            {
                "type": "manual_instance_role_lookup",
                "message": "Resolve the EC2 instance role from the instance profile, then attach CloudWatch PutMetricData permission.",
            }
        )
    if include_assume_role_guidance and elevated_role_arn:
        options.append(
            {
                "type": "allow_maxops_to_assume_elevated_role",
                "message": "Update both the MaxOps role policy and the elevated role trust policy so MaxOps can assume the elevated role.",
                "maxops_role_policy_statement": build_assume_role_permission_statement(elevated_role_arn),
                "elevated_role_trust_policy_statement": build_assume_role_trust_statement(caller_arn),
                "caller_arn": caller_arn,
                "elevated_role_arn": elevated_role_arn,
            }
        )
    return options


def _wait_for_command(ssm: Any, command_id: str, instance_id: str, timeout_seconds: int) -> Dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_status = "Pending"
    while time.time() < deadline:
        try:
            invocation = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "InvocationDoesNotExist":
                time.sleep(2)
                continue
            raise
        status = invocation.get("Status") or "Unknown"
        last_status = status
        if status not in SSM_STATUS_PENDING:
            return invocation
        time.sleep(3)
    raise CloudWatchAgentError(
        f"Timed out waiting for SSM command {command_id} on instance {instance_id}. Last status: {last_status}"
    )


def run_ssm_shell_command(
    session: boto3.Session,
    region: str,
    instance_id: str,
    commands: list[str],
    comment: str,
    timeout_seconds: int = 600,
) -> Dict[str, Any]:
    ssm = _client(session, "ssm", region)
    response = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": commands},
        Comment=comment,
        TimeoutSeconds=timeout_seconds,
    )
    command_id = response["Command"]["CommandId"]
    invocation = _wait_for_command(ssm, command_id, instance_id, timeout_seconds)
    return {
        "command_id": command_id,
        "status": invocation.get("Status"),
        "stdout": invocation.get("StandardOutputContent") or "",
        "stderr": invocation.get("StandardErrorContent") or "",
    }


def install_cloudwatch_agent_package(
    session: boto3.Session,
    region: str,
    instance_id: str,
    timeout_seconds: int = 900,
) -> Dict[str, Any]:
    ssm = _client(session, "ssm", region)
    response = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-ConfigureAWSPackage",
        Parameters={"action": ["Install"], "name": ["AmazonCloudWatchAgent"]},
        Comment="Install Amazon CloudWatch Agent",
        TimeoutSeconds=timeout_seconds,
    )
    command_id = response["Command"]["CommandId"]
    invocation = _wait_for_command(ssm, command_id, instance_id, timeout_seconds)
    return {
        "command_id": command_id,
        "status": invocation.get("Status"),
        "stdout": invocation.get("StandardOutputContent") or "",
        "stderr": invocation.get("StandardErrorContent") or "",
    }


def _ssm_managed_instance_status(session: boto3.Session, region: str, instance_id: str) -> Dict[str, Any]:
    ssm = _client(session, "ssm", region)
    response = ssm.describe_instance_information(
        Filters=[{"Key": "InstanceIds", "Values": [instance_id]}],
    )
    info = response.get("InstanceInformationList", [])
    if not info:
        return {
            "ssm_reachable": False,
            "ping_status": "NotManaged",
            "platform_type": None,
            "platform_name": None,
        }
    item = info[0]
    ping_status = item.get("PingStatus") or "Unknown"
    return {
        "ssm_reachable": ping_status == "Online",
        "ping_status": ping_status,
        "platform_type": item.get("PlatformType"),
        "platform_name": item.get("PlatformName"),
    }


def _validation_script() -> str:
    return """PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi
if [ -z "$PYTHON_BIN" ]; then
  echo '{"python_available": false, "agent_installed": false, "agent_running": false, "config_present": false, "memory_metrics_enabled": false, "disk_metrics_enabled": false, "config_path": "/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json"}'
  exit 0
fi
"$PYTHON_BIN" <<'PY'
import json
import os
import shutil
import subprocess
from pathlib import Path

config_path = "/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json"
config_dir = "/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.d"
ctl_path = "/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl"
installed = os.path.exists(ctl_path) or shutil.which("amazon-cloudwatch-agent-ctl") is not None
config_candidates = []
primary_path = Path(config_path)
if primary_path.exists():
    config_candidates.append(primary_path)
config_dir_path = Path(config_dir)
if config_dir_path.exists():
    config_candidates.extend(sorted(config_dir_path.glob("*.json")))
selected_config_path = str(config_candidates[0]) if config_candidates else config_path
config_present = bool(config_candidates)
agent_running = False
try:
    result = subprocess.run(
        ["systemctl", "is-active", "amazon-cloudwatch-agent"],
        check=False,
        capture_output=True,
        text=True,
    )
    agent_running = result.returncode == 0 and result.stdout.strip() == "active"
except Exception:
    agent_running = False

memory_enabled = False
disk_enabled = False
config_error = None
if config_present:
    try:
        with open(selected_config_path, "r", encoding="utf-8") as handle:
            config = json.load(handle)
        metrics = ((config.get("metrics") or {}).get("metrics_collected") or {})
        mem = metrics.get("mem") or {}
        disk = metrics.get("disk") or {}
        mem_measurement = mem.get("measurement") or []
        disk_measurement = disk.get("measurement") or []
        memory_enabled = "mem_used_percent" in mem_measurement
        disk_enabled = "used_percent" in disk_measurement
    except Exception as exc:
        config_error = str(exc)

print(json.dumps({
    "python_available": True,
    "agent_installed": bool(installed),
    "agent_running": bool(agent_running),
    "config_present": bool(config_present),
    "memory_metrics_enabled": bool(memory_enabled),
    "disk_metrics_enabled": bool(disk_enabled),
    "config_path": selected_config_path,
    "config_candidates": [str(path) for path in config_candidates],
    "config_error": config_error,
}))
PY"""


def validate_cloudwatch_agent(
    session: boto3.Session,
    region: str,
    instance_id: str,
    elevated_role_arn: Optional[str] = None,
) -> Dict[str, Any]:
    status = {
        "instance_id": instance_id,
        "region": region,
    }
    status.update(_ssm_managed_instance_status(session, region, instance_id))
    status.update(_iam_details_for_instance(session, region, instance_id, elevated_role_arn=elevated_role_arn))

    if not status["ssm_reachable"]:
        status.update(
            {
                "agent_installed": None,
                "agent_running": None,
                "config_present": None,
                "memory_metrics_enabled": None,
                "disk_metrics_enabled": None,
                "config_path": AGENT_CONFIG_PATH,
                "validation_status": "ssm_unreachable",
            }
        )
        return status

    command_result = run_ssm_shell_command(
        session=session,
        region=region,
        instance_id=instance_id,
        commands=[_validation_script()],
        comment="Validate CloudWatch agent installation and configuration",
    )
    if command_result["status"] != "Success":
        raise CloudWatchAgentError(
            f"CloudWatch agent validation failed on instance {instance_id}: {command_result['stderr'] or command_result['stdout']}"
        )
    payload = json.loads(command_result["stdout"].strip() or "{}")
    status.update(payload)
    status["validation_command_id"] = command_result["command_id"]

    if not status.get("agent_installed"):
        status["validation_status"] = "not_installed"
    elif not status.get("config_present"):
        status["validation_status"] = "installed_not_configured"
    elif not status.get("memory_metrics_enabled") or not status.get("disk_metrics_enabled"):
        status["validation_status"] = "configured_incorrectly"
    elif not status.get("agent_running"):
        status["validation_status"] = "configured_not_running"
    elif status.get("put_metric_data_allowed") is False:
        status["validation_status"] = "permission_missing"
    else:
        status["validation_status"] = "ready"
    return status


def _config_commands() -> list[str]:
    config_json = json.dumps(build_cloudwatch_agent_config(), indent=2)
    return [
        "sudo mkdir -p /opt/aws/amazon-cloudwatch-agent/etc",
        "PYTHON_BIN=$(command -v python3 || command -v python)",
        (
            "cat <<'PY' | sudo \"$PYTHON_BIN\"\n"
            "from pathlib import Path\n"
            "content = '''" + config_json.replace("'''", "\\'\\'\\'") + "'''\n"
            f"path = Path('{AGENT_CONFIG_PATH}')\n"
            "path.parent.mkdir(parents=True, exist_ok=True)\n"
            "path.write_text(content, encoding='utf-8')\n"
            "print(path)\n"
            "PY"
        ),
        f"sudo test -f {AGENT_CONFIG_PATH}",
        "sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl "
        f"-a fetch-config -m ec2 -c file:{AGENT_CONFIG_PATH} -s",
        "sudo systemctl enable amazon-cloudwatch-agent || true",
        "sudo systemctl restart amazon-cloudwatch-agent",
    ]


def ensure_put_metric_data_permission(
    base_session: boto3.Session,
    region: str,
    instance_id: str,
    elevated_role_arn: str,
) -> Dict[str, Any]:
    iam_details = _iam_details_for_instance(base_session, region, instance_id, elevated_role_arn=elevated_role_arn)
    role_name = iam_details.get("instance_role_name")
    if not role_name:
        raise ElevatedRoleRequiredError(
            "Unable to resolve the EC2 instance role for CloudWatch remediation.",
            details=iam_details,
        )
    if iam_details.get("put_metric_data_allowed") is True:
        return iam_details
    if not iam_details.get("used_elevated_role"):
        raise ElevatedRoleRequiredError(
            "An elevated role is required to update the EC2 instance role policy.",
            details=iam_details,
        )

    remediation_session = assume_role_session(base_session, elevated_role_arn, region)
    iam = _client(remediation_session, "iam", region)
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=AGENT_POLICY_NAME,
        PolicyDocument=json.dumps(_policy_document()),
    )
    refreshed = _iam_details_for_instance(base_session, region, instance_id, elevated_role_arn=elevated_role_arn)
    if refreshed.get("put_metric_data_allowed") is not True:
        raise CloudWatchAgentError(
            f"Updated role {role_name} but CloudWatch PutMetricData permission is still not visible."
        )
    return refreshed


def install_or_configure_cloudwatch_agent(
    session: boto3.Session,
    region: str,
    instance_id: str,
    elevated_role_arn: Optional[str] = None,
) -> Dict[str, Any]:
    initial = validate_cloudwatch_agent(
        session=session,
        region=region,
        instance_id=instance_id,
        elevated_role_arn=elevated_role_arn,
    )
    if not initial.get("ssm_reachable"):
        raise CloudWatchAgentError(
            f"EC2 instance {instance_id} is not reachable through SSM. PingStatus={initial.get('ping_status')}"
        )
    if initial.get("put_metric_data_allowed") is False:
        if not elevated_role_arn:
            raise ElevatedRoleRequiredError(
                "EC2 instance role is missing cloudwatch:PutMetricData. Provide elevated_role_arn to remediate.",
                details=initial,
            )
        ensure_put_metric_data_permission(
            base_session=session,
            region=region,
            instance_id=instance_id,
            elevated_role_arn=elevated_role_arn,
        )

    install_result: Optional[Dict[str, Any]] = None
    if initial.get("agent_installed") is not True:
        install_result = install_cloudwatch_agent_package(
            session=session,
            region=region,
            instance_id=instance_id,
        )
        if install_result["status"] != "Success":
            raise CloudWatchAgentError(
                f"CloudWatch agent install failed on instance {instance_id}: "
                f"{install_result['stderr'] or install_result['stdout']}"
            )

    configure_result = run_ssm_shell_command(
        session=session,
        region=region,
        instance_id=instance_id,
        commands=_config_commands(),
        comment="Configure CloudWatch agent for memory and disk metrics",
    )
    if configure_result["status"] != "Success":
        raise CloudWatchAgentError(
            f"CloudWatch agent configuration failed on instance {instance_id}: "
            f"{configure_result['stderr'] or configure_result['stdout']}"
        )

    final = validate_cloudwatch_agent(
        session=session,
        region=region,
        instance_id=instance_id,
        elevated_role_arn=elevated_role_arn,
    )
    final["install_command_id"] = install_result["command_id"] if install_result else None
    final["configure_command_id"] = configure_result["command_id"]
    final["installed_during_action"] = install_result is not None
    return final
