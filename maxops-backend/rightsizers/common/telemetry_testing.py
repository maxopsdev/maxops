"""Shared primitives for explicitly invoked live rightsizer telemetry tests.

Nothing in this module runs during pytest discovery or application startup.
Service-specific create scripts must require ``--apply`` before calling any
mutating helper.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import boto3
from botocore.exceptions import ClientError, WaiterError


DEFAULT_PROFILE = "default"
DEFAULT_REGION = "us-east-1"
STATE_SCHEMA_VERSION = 1
TAG_PURPOSE = "rightsizer-telemetry"

RESULT_PASS = "PASS"
RESULT_NOT_READY = "NOT_READY"
RESULT_FAIL = "FAIL"
EXIT_CODES = {RESULT_PASS: 0, RESULT_NOT_READY: 2, RESULT_FAIL: 1}

_RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


class NotReady(RuntimeError):
    """The fixture is not ready for one-shot validation."""


class ValidationFailure(RuntimeError):
    """The live request or returned telemetry violated its contract."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def make_run_id(rightsizer: str) -> str:
    return f"{rightsizer}-{utc_now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}"


def validate_run_id(value: str) -> str:
    value = value.strip().lower()
    if not _RUN_ID.fullmatch(value):
        raise ValueError(
            "run ID must contain only lowercase letters, digits, and hyphens "
            "and be at most 48 characters"
        )
    return value


def aws_tags(rightsizer: str, run_id: str, expires_at: str) -> dict[str, str]:
    return {
        "maxops:test-purpose": TAG_PURPOSE,
        "maxops:rightsizer": rightsizer,
        "maxops:run-id": run_id,
        "maxops:expires-at": expires_at,
    }


def tag_list(tags: Mapping[str, str]) -> list[dict[str, str]]:
    return [{"Key": key, "Value": value} for key, value in sorted(tags.items())]


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class StateStore:
    """Versioned, atomically checkpointed ownership state."""

    def __init__(self, path: Path, data: dict[str, Any]):
        self.path = path.resolve()
        self.data = data

    @classmethod
    def create(
        cls,
        path: Path,
        *,
        rightsizer: str,
        run_id: str,
        profile: str,
        region: str,
        identity: Mapping[str, Any],
        ttl_hours: int = 8,
    ) -> "StateStore":
        path = path.resolve()
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing state: {path}")
        now = utc_now()
        store = cls(
            path,
            {
                "schema_version": STATE_SCHEMA_VERSION,
                "run_id": validate_run_id(run_id),
                "rightsizer": rightsizer,
                "profile": profile,
                "account_id": str(identity.get("Account") or ""),
                "caller_arn": str(identity.get("Arn") or ""),
                "region": region,
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(hours=ttl_hours)).isoformat(),
                "phase": "CREATING",
                "resources": [],
                "workload_commands": [],
                "validation_runs": [],
                "cleanup": {"result": "NOT_STARTED", "errors": []},
            },
        )
        store.save()
        return store

    @classmethod
    def load(cls, path: Path) -> "StateStore":
        path = path.resolve()
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("schema_version") != STATE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported telemetry state schema {data.get('schema_version')!r}"
            )
        return cls(path, data)

    def save(self) -> None:
        self.data["updated_at"] = iso_now()
        _atomic_json(self.path, self.data)

    def set_phase(self, phase: str) -> None:
        self.data["phase"] = phase
        self.save()

    def add_resource(
        self,
        logical_name: str,
        service: str,
        resource_type: str,
        id_or_arn: str,
        *,
        ownership: str = "created",
        dependencies: Sequence[str] = (),
        attributes: Mapping[str, Any] | None = None,
        ready: bool = False,
    ) -> dict[str, Any]:
        if any(item["logical_name"] == logical_name for item in self.data["resources"]):
            raise ValueError(f"duplicate state resource logical name: {logical_name}")
        resource = {
            "logical_name": logical_name,
            "service": service,
            "type": resource_type,
            "id_or_arn": id_or_arn,
            "ownership": ownership,
            "dependencies": list(dependencies),
            "attributes": dict(attributes or {}),
            "create_status": "CREATED" if ownership == "created" else "REUSED",
            "ready_status": "READY" if ready else "PENDING",
            "delete_status": "NOT_STARTED" if ownership == "created" else "NOT_OWNED",
        }
        self.data["resources"].append(resource)
        self.save()
        return resource

    def resource(self, logical_name: str) -> dict[str, Any]:
        for item in self.data["resources"]:
            if item["logical_name"] == logical_name:
                return item
        raise KeyError(f"state has no resource named {logical_name}")

    def maybe_resource(self, logical_name: str) -> dict[str, Any] | None:
        try:
            return self.resource(logical_name)
        except KeyError:
            return None

    def mark_ready(self, logical_name: str, **attributes: Any) -> None:
        resource = self.resource(logical_name)
        resource["ready_status"] = "READY"
        resource["attributes"].update(attributes)
        self.save()

    def mark_deleted(self, logical_name: str, status: str = "DELETED") -> None:
        self.resource(logical_name)["delete_status"] = status
        self.save()

    def add_command(self, command_id: str, target_ids: Sequence[str], purpose: str) -> None:
        self.data["workload_commands"].append(
            {
                "command_id": command_id,
                "target_ids": list(target_ids),
                "purpose": purpose,
                "submitted_at": iso_now(),
                "terminal_status": None,
            }
        )
        self.save()

    def record_validation(self, report: Mapping[str, Any], report_path: Path) -> None:
        _atomic_json(report_path, report)
        self.data["validation_runs"].append(
            {
                "started_at": report.get("started_at"),
                "completed_at": report.get("completed_at"),
                "window_minutes": report.get("window_minutes"),
                "result": report.get("result"),
                "report_path": str(report_path.resolve()),
            }
        )
        self.save()


def new_session(profile: str = DEFAULT_PROFILE, region: str = DEFAULT_REGION):
    return boto3.Session(profile_name=profile, region_name=region)


def caller_identity(session: Any, region: str) -> dict[str, Any]:
    return session.client("sts", region_name=region).get_caller_identity()


def assert_state_identity(store: StateStore, session: Any) -> None:
    identity = caller_identity(session, str(store.data["region"]))
    actual = str(identity.get("Account") or "")
    expected = str(store.data.get("account_id") or "")
    if actual != expected:
        raise RuntimeError(
            f"state belongs to AWS account {expected}; active credentials resolve to {actual}"
        )


def default_network(ec2: Any) -> dict[str, str]:
    vpcs = ec2.describe_vpcs(
        Filters=[{"Name": "is-default", "Values": ["true"]}]
    ).get("Vpcs", [])
    if not vpcs:
        raise RuntimeError("default VPC is required by the telemetry harness")
    vpc_id = str(vpcs[0]["VpcId"])
    groups = ec2.describe_security_groups(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "group-name", "Values": ["default"]},
        ]
    ).get("SecurityGroups", [])
    if not groups:
        raise RuntimeError(f"default security group not found in {vpc_id}")
    subnets = ec2.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    ).get("Subnets", [])
    if not subnets:
        raise RuntimeError(f"no subnets found in default VPC {vpc_id}")
    subnets.sort(
        key=lambda item: (
            not bool(item.get("MapPublicIpOnLaunch")),
            str(item.get("AvailabilityZone") or ""),
            str(item.get("SubnetId") or ""),
        )
    )
    return {
        "vpc_id": vpc_id,
        "subnet_id": str(subnets[0]["SubnetId"]),
        "security_group_id": str(groups[0]["GroupId"]),
    }


def default_vpc_subnets(ec2: Any) -> tuple[str, list[str]]:
    network = default_network(ec2)
    subnets = ec2.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [network["vpc_id"]]}]
    ).get("Subnets", [])
    by_az: dict[str, str] = {}
    for subnet in sorted(subnets, key=lambda item: str(item.get("SubnetId") or "")):
        by_az.setdefault(str(subnet.get("AvailabilityZone") or ""), str(subnet["SubnetId"]))
    if len(by_az) < 2:
        raise RuntimeError("RDS/ElastiCache fixture requires default-VPC subnets in two AZs")
    return network["vpc_id"], list(by_az.values())


def latest_al2023_ami(ssm: Any, *, architecture: str = "x86_64") -> str:
    suffix = "arm64" if architecture == "arm64" else "x86_64"
    name = f"/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-{suffix}"
    return str(ssm.get_parameter(Name=name)["Parameter"]["Value"])


def select_offered_type(ec2: Any, candidates: Sequence[str]) -> str:
    response = ec2.describe_instance_type_offerings(
        LocationType="region",
        Filters=[{"Name": "instance-type", "Values": list(candidates)}],
    )
    offered = {str(item["InstanceType"]) for item in response.get("InstanceTypeOfferings", [])}
    for candidate in candidates:
        if candidate in offered:
            return candidate
    raise RuntimeError(f"none of the required instance types is offered: {', '.join(candidates)}")


def state_path_for(folder: Path, run_id: str) -> Path:
    return folder.resolve() / ".telemetry_test_state" / f"{validate_run_id(run_id)}.json"


def add_reused_network(store: StateStore, network: Mapping[str, str]) -> None:
    store.add_resource(
        "vpc",
        "ec2",
        "vpc",
        network["vpc_id"],
        ownership="reused",
        ready=True,
    )
    store.add_resource(
        "subnet",
        "ec2",
        "subnet",
        network["subnet_id"],
        ownership="reused",
        dependencies=["vpc"],
        ready=True,
    )
    store.add_resource(
        "default_security_group",
        "ec2",
        "security_group",
        network["security_group_id"],
        ownership="reused",
        dependencies=["vpc"],
        ready=True,
    )


def launch_managed_instance(
    session: Any,
    store: StateStore,
    *,
    logical_name: str,
    image_id: str,
    instance_type: str,
    subnet_id: str,
    security_group_ids: Sequence[str],
    profile_name: str,
    tags: Mapping[str, str],
    detailed_monitoring: bool = True,
) -> str:
    ec2 = session.client("ec2", region_name=store.data["region"])
    request = {
        "ImageId": image_id, "InstanceType": instance_type, "MinCount": 1,
        "MaxCount": 1, "SubnetId": subnet_id,
        "SecurityGroupIds": list(security_group_ids),
        "IamInstanceProfile": {"Name": profile_name},
        "Monitoring": {"Enabled": detailed_monitoring},
        "MetadataOptions": {"HttpTokens": "required", "HttpEndpoint": "enabled"},
        "TagSpecifications": [
            {"ResourceType": "instance", "Tags": tag_list({**tags, "Name": f"maxops-{store.data['run_id']}"})},
            {"ResourceType": "volume", "Tags": tag_list(tags)},
        ],
    }
    for attempt in range(12):
        try:
            response = ec2.run_instances(**request)
            break
        except ClientError as exc:
            if error_code(exc) != "InvalidParameterValue" or "Instance Profile" not in str(exc) or attempt == 11:
                raise
            time.sleep(10)
    instance_id = str(response["Instances"][0]["InstanceId"])
    store.add_resource(
        logical_name,
        "ec2",
        "instance",
        instance_id,
        dependencies=["instance_profile", "subnet"],
        attributes={"instance_type": instance_type, "image_id": image_id},
    )
    ec2.get_waiter("instance_running").wait(
        InstanceIds=[instance_id], WaiterConfig={"Delay": 10, "MaxAttempts": 60}
    )
    ec2.get_waiter("instance_status_ok").wait(
        InstanceIds=[instance_id], WaiterConfig={"Delay": 15, "MaxAttempts": 40}
    )
    described = ec2.describe_instances(InstanceIds=[instance_id])
    instance = described["Reservations"][0]["Instances"][0]
    store.mark_ready(
        logical_name,
        launch_time=instance.get("LaunchTime"),
        private_ip=instance.get("PrivateIpAddress"),
        availability_zone=(instance.get("Placement") or {}).get("AvailabilityZone"),
        volume_ids=[
            mapping["Ebs"]["VolumeId"]
            for mapping in instance.get("BlockDeviceMappings", [])
            if mapping.get("Ebs", {}).get("VolumeId")
        ],
    )
    return instance_id


def create_security_group(
    session: Any,
    store: StateStore,
    *,
    logical_name: str,
    group_name: str,
    description: str,
    vpc_id: str,
    tags: Mapping[str, str],
) -> str:
    ec2 = session.client("ec2", region_name=store.data["region"])
    response = ec2.create_security_group(
        GroupName=group_name,
        Description=description,
        VpcId=vpc_id,
        TagSpecifications=[{"ResourceType": "security-group", "Tags": tag_list(tags)}],
    )
    group_id = str(response["GroupId"])
    store.add_resource(
        logical_name,
        "ec2",
        "security_group",
        group_id,
        dependencies=["vpc"],
        ready=True,
    )
    return group_id


def delete_security_group(
    session: Any, store: StateStore, logical_name: str, errors: list[dict[str, Any]]
) -> None:
    resource = store.maybe_resource(logical_name)
    if not resource or resource.get("ownership") != "created" or resource.get("delete_status") in {"DELETED", "ALREADY_ABSENT"}:
        return
    ec2 = session.client("ec2", region_name=store.data["region"])
    try:
        ec2.delete_security_group(GroupId=resource["id_or_arn"])
        store.mark_deleted(logical_name)
    except ClientError as exc:
        if is_not_found(exc, {"InvalidGroup.NotFound"}):
            store.mark_deleted(logical_name, "ALREADY_ABSENT")
        else:
            errors.append(cleanup_error(resource, "delete_security_group", exc))


def create_instance_role(
    session: Any,
    store: StateStore,
    *,
    name_prefix: str,
    tags: Mapping[str, str],
) -> str:
    iam = session.client("iam")
    role_name = f"{name_prefix}-role"[:64]
    profile_name = f"{name_prefix}-profile"[:128]
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "ec2.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }
    role = iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(trust),
        Description="Temporary MaxOps rightsizer telemetry fixture role",
        Tags=tag_list(tags),
    )["Role"]
    store.add_resource("instance_role", "iam", "role", role_name, attributes={"arn": role.get("Arn")})
    policy_arns = [
        "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
        "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy",
    ]
    for policy_arn in policy_arns:
        iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
    store.resource("instance_role")["attributes"]["managed_policy_arns"] = policy_arns
    store.save()
    iam.create_instance_profile(InstanceProfileName=profile_name, Tags=tag_list(tags))
    store.add_resource(
        "instance_profile",
        "iam",
        "instance_profile",
        profile_name,
        dependencies=["instance_role"],
    )
    iam.add_role_to_instance_profile(InstanceProfileName=profile_name, RoleName=role_name)
    store.save()
    return profile_name


def wait_for_instance_profile(iam: Any, profile_name: str, role_name: str, timeout: int = 180) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            profile = iam.get_instance_profile(InstanceProfileName=profile_name)["InstanceProfile"]
            if any(item.get("RoleName") == role_name for item in profile.get("Roles", [])):
                return
        except ClientError:
            pass
        time.sleep(5)
    raise TimeoutError(f"instance profile {profile_name} did not propagate")


def wait_for_ssm(ssm: Any, instance_ids: Sequence[str], timeout: int = 600) -> None:
    pending = set(instance_ids)
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        response = ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": sorted(pending)}]
        )
        for item in response.get("InstanceInformationList", []):
            if item.get("PingStatus") == "Online":
                pending.discard(str(item.get("InstanceId")))
        if pending:
            time.sleep(10)
    if pending:
        raise TimeoutError(f"instances did not become SSM-online: {sorted(pending)}")


def send_shell_command(
    ssm: Any,
    store: StateStore,
    instance_ids: Sequence[str],
    commands: Sequence[str],
    *,
    purpose: str,
    timeout_seconds: int = 900,
) -> str:
    response = ssm.send_command(
        InstanceIds=list(instance_ids),
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": list(commands)},
        TimeoutSeconds=timeout_seconds,
        Comment=f"MaxOps {store.data['rightsizer']} telemetry {purpose}"[:100],
    )
    command_id = str(response["Command"]["CommandId"])
    store.add_command(command_id, instance_ids, purpose)
    return command_id


def wait_for_command(ssm: Any, command_id: str, instance_ids: Sequence[str], timeout: int = 900) -> None:
    pending = set(instance_ids)
    deadline = time.monotonic() + timeout
    terminal = {"Success", "Cancelled", "TimedOut", "Failed", "Cancelling"}
    failures: dict[str, str] = {}
    while pending and time.monotonic() < deadline:
        for instance_id in list(pending):
            try:
                invocation = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
            except ClientError as exc:
                if error_code(exc) == "InvocationDoesNotExist":
                    continue
                raise
            status = str(invocation.get("Status") or "")
            if status in terminal:
                pending.remove(instance_id)
                if status != "Success":
                    failures[instance_id] = f"{status}: {invocation.get('StandardErrorContent', '')}"
        if pending:
            time.sleep(5)
    if pending:
        raise TimeoutError(f"SSM command {command_id} did not complete for {sorted(pending)}")
    if failures:
        raise RuntimeError(f"SSM command {command_id} failed: {failures}")


def configure_cloudwatch_agent(
    ssm: Any,
    store: StateStore,
    instance_ids: Sequence[str],
    config: Mapping[str, Any],
) -> None:
    encoded = base64.b64encode(json.dumps(config).encode("utf-8")).decode("ascii")
    commands = [
        "set -eu",
        "dnf install -y amazon-cloudwatch-agent",
        f"printf %s {encoded} | base64 -d > /tmp/maxops-cwagent.json",
        "/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl "
        "-a fetch-config -m ec2 -s -c file:/tmp/maxops-cwagent.json",
        "/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a status",
    ]
    command_id = send_shell_command(
        ssm, store, instance_ids, commands, purpose="configure-agent", timeout_seconds=600
    )
    wait_for_command(ssm, command_id, instance_ids, timeout=600)


def cloudwatch_agent_config(*, asg: bool = False) -> dict[str, Any]:
    dimensions: dict[str, str] = {"InstanceId": "${aws:InstanceId}"}
    aggregation: list[list[str]] = [["InstanceId"]]
    if asg:
        dimensions["AutoScalingGroupName"] = "${aws:AutoScalingGroupName}"
        aggregation.append(["AutoScalingGroupName"])
    return {
        "agent": {"metrics_collection_interval": 60, "run_as_user": "root"},
        "metrics": {
            "namespace": "CWAgent",
            "append_dimensions": dimensions,
            "aggregation_dimensions": aggregation,
            "metrics_collected": {
                "mem": {"measurement": ["mem_used_percent"], "metrics_collection_interval": 60},
                "ethtool": {
                    "interface_include": ["eth0", "ens5"],
                    "metrics_include": [
                        "bw_in_allowance_exceeded",
                        "bw_out_allowance_exceeded",
                        "pps_allowance_exceeded",
                        "conntrack_allowance_exceeded",
                    ],
                },
            },
        },
    }


def submit_host_workload(ssm: Any, store: StateStore, instance_ids: Sequence[str]) -> str:
    script = (
        "end=$((SECONDS+600)); f=/tmp/maxops-telemetry-${RANDOM}.bin; "
        "while [ $SECONDS -lt $end ]; do "
        "dd if=/dev/zero of=$f bs=1M count=16 conv=fsync status=none; "
        "dd if=$f of=/dev/null bs=1M status=none; "
        "sha256sum $f >/dev/null; "
        "curl -fsS --max-time 5 https://checkip.amazonaws.com >/dev/null || true; "
        "sleep 20; done; rm -f $f"
    )
    return send_shell_command(
        ssm,
        store,
        instance_ids,
        [f"nohup bash -c {json.dumps(script)} >/tmp/maxops-telemetry-workload.log 2>&1 &"],
        purpose="host-workload",
    )


def command_readiness(ssm: Any, store: StateStore) -> None:
    for command in store.data.get("workload_commands", []):
        command_id = str(command["command_id"])
        statuses: list[str] = []
        for instance_id in command.get("target_ids", []):
            try:
                result = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
            except ClientError as exc:
                raise NotReady(f"SSM command {command_id} not visible yet: {error_code(exc)}") from exc
            statuses.append(str(result.get("Status") or ""))
        if any(status in {"Failed", "Cancelled", "TimedOut", "Cancelling"} for status in statuses):
            raise ValidationFailure(f"SSM command {command_id} failed: {statuses}")
        if not statuses or any(status in {"Pending", "InProgress", "Delayed", ""} for status in statuses):
            raise NotReady(f"SSM command {command_id} is not ready: {statuses}")
        command["terminal_status"] = "Success"
    store.save()


def error_code(exc: BaseException) -> str:
    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code") or "")
    return ""


def is_not_found(exc: BaseException, codes: Iterable[str]) -> bool:
    return error_code(exc) in set(codes)


def cleanup_instance_role(session: Any, store: StateStore, errors: list[dict[str, Any]]) -> None:
    iam = session.client("iam")
    profile = store.maybe_resource("instance_profile")
    role = store.maybe_resource("instance_role")
    if profile and profile.get("ownership") == "created" and profile.get("delete_status") != "DELETED":
        try:
            if role:
                try:
                    iam.remove_role_from_instance_profile(
                        InstanceProfileName=profile["id_or_arn"], RoleName=role["id_or_arn"]
                    )
                except ClientError as exc:
                    if not is_not_found(exc, {"NoSuchEntity"}):
                        raise
            iam.delete_instance_profile(InstanceProfileName=profile["id_or_arn"])
            store.mark_deleted("instance_profile")
        except ClientError as exc:
            if is_not_found(exc, {"NoSuchEntity"}):
                store.mark_deleted("instance_profile", "ALREADY_ABSENT")
            else:
                errors.append(cleanup_error(profile, "delete_instance_profile", exc))
    if role and role.get("ownership") == "created" and role.get("delete_status") != "DELETED":
        try:
            for arn in role.get("attributes", {}).get("managed_policy_arns", []):
                try:
                    iam.detach_role_policy(RoleName=role["id_or_arn"], PolicyArn=arn)
                except ClientError as exc:
                    if not is_not_found(exc, {"NoSuchEntity"}):
                        raise
            for policy_name in role.get("attributes", {}).get("inline_policy_names", []):
                try:
                    iam.delete_role_policy(RoleName=role["id_or_arn"], PolicyName=policy_name)
                except ClientError as exc:
                    if not is_not_found(exc, {"NoSuchEntity"}):
                        raise
            iam.delete_role(RoleName=role["id_or_arn"])
            store.mark_deleted("instance_role")
        except ClientError as exc:
            if is_not_found(exc, {"NoSuchEntity"}):
                store.mark_deleted("instance_role", "ALREADY_ABSENT")
            else:
                errors.append(cleanup_error(role, "delete_role", exc))


def terminate_instance(session: Any, store: StateStore, logical_name: str, errors: list[dict[str, Any]]) -> None:
    resource = store.maybe_resource(logical_name)
    if not resource or resource.get("ownership") != "created" or resource.get("delete_status") in {"DELETED", "ALREADY_ABSENT"}:
        return
    ec2 = session.client("ec2", region_name=store.data["region"])
    instance_id = str(resource["id_or_arn"])
    try:
        ec2.terminate_instances(InstanceIds=[instance_id])
        ec2.get_waiter("instance_terminated").wait(
            InstanceIds=[instance_id], WaiterConfig={"Delay": 10, "MaxAttempts": 60}
        )
        store.mark_deleted(logical_name)
    except (ClientError, WaiterError) as exc:
        if is_not_found(exc, {"InvalidInstanceID.NotFound"}):
            store.mark_deleted(logical_name, "ALREADY_ABSENT")
        else:
            errors.append(cleanup_error(resource, "terminate_instance", exc))


def cleanup_error(resource: Mapping[str, Any], operation: str, exc: BaseException) -> dict[str, Any]:
    return {
        "logical_name": resource.get("logical_name"),
        "service": resource.get("service"),
        "type": resource.get("type"),
        "id_or_arn": resource.get("id_or_arn"),
        "operation": operation,
        "code": error_code(exc),
        "message": str(exc),
    }


def finalize_cleanup(store: StateStore, errors: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    result = "SUCCESS" if not errors else "PARTIAL_FAILURE"
    store.data["cleanup"] = {
        "started_at": store.data.get("cleanup", {}).get("started_at") or iso_now(),
        "completed_at": iso_now(),
        "result": result,
        "errors": list(errors),
    }
    store.data["phase"] = "CLEANED" if not errors else "CLEANUP_FAILED"
    store.save()
    return store.data["cleanup"]


class RecordingClient:
    def __init__(self, service: str, delegate: Any, calls: list[dict[str, Any]]):
        self._service = service
        self._delegate = delegate
        self._calls = calls

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._delegate, name)
        if not callable(target) or name not in {"get_metric_data", "list_metrics", "get_resource_metrics"}:
            return target

        def recorded(**kwargs: Any) -> Any:
            call = {"service": self._service, "operation": name, "request": kwargs}
            try:
                response = target(**kwargs)
            except Exception as exc:
                call["error"] = {"type": type(exc).__name__, "message": str(exc), "code": error_code(exc)}
                self._calls.append(call)
                raise
            call["response"] = response
            self._calls.append(call)
            return response

        return recorded


class RecordingSession:
    def __init__(self, delegate: Any):
        self._delegate = delegate
        self.calls: list[dict[str, Any]] = []
        self._clients: dict[tuple[str, str | None], RecordingClient] = {}

    def client(self, service_name: str, **kwargs: Any) -> Any:
        region = kwargs.get("region_name")
        key = (service_name, region)
        if key not in self._clients:
            delegate = self._delegate.client(service_name, **kwargs)
            self._clients[key] = RecordingClient(service_name, delegate, self.calls)
        return self._clients[key]


def production_adapter(session: Any, region: str) -> tuple[Any, RecordingSession]:
    from app.adapters.aws.adapter import AWSAdapter

    recording = RecordingSession(session)
    adapter = object.__new__(AWSAdapter)
    adapter._default_region = region
    adapter._use_simulator = False
    adapter._ec2_memory_metric_cache = {}
    adapter.session = recording
    adapter.ec2_client = recording.client("ec2", region_name=region)
    adapter.cloudwatch_client = recording.client("cloudwatch", region_name=region)
    return adapter, recording


def metric_data_calls(recording: RecordingSession) -> list[dict[str, Any]]:
    return [call for call in recording.calls if call["operation"] == "get_metric_data"]


def flatten_queries(calls: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        query
        for call in calls
        for query in call.get("request", {}).get("MetricDataQueries", [])
    ]


def query_signature(query: Mapping[str, Any]) -> tuple[str, str, tuple[tuple[str, str], ...], int, str]:
    metric_stat = query.get("MetricStat") or {}
    metric = metric_stat.get("Metric") or {}
    dimensions = tuple(
        sorted(
            (str(item.get("Name") or ""), str(item.get("Value") or ""))
            for item in metric.get("Dimensions") or []
        )
    )
    return (
        str(metric.get("Namespace") or ""),
        str(metric.get("MetricName") or ""),
        dimensions,
        int(metric_stat.get("Period") or 0),
        str(metric_stat.get("Stat") or ""),
    )


def assert_query_map(
    calls: Sequence[Mapping[str, Any]],
    expected: Mapping[str, tuple[str, str, Sequence[tuple[str, str]], int, str]],
) -> None:
    found: dict[str, tuple[str, str, tuple[tuple[str, str], ...], int, str]] = {}
    for query in flatten_queries(calls):
        query_id = str(query.get("Id") or "")
        if query_id in expected and query_id not in found:
            found[query_id] = query_signature(query)
    missing = sorted(set(expected) - set(found))
    if missing:
        raise ValidationFailure(f"missing metric query IDs: {missing}")
    mismatches = {
        key: {"expected": expected[key], "actual": found[key]}
        for key in expected
        if (
            expected[key][0],
            expected[key][1],
            tuple(sorted(expected[key][2])),
            expected[key][3],
            expected[key][4],
        )
        != found[key]
    }
    if mismatches:
        raise ValidationFailure(f"metric query contract mismatches: {mismatches}")


def assert_complete_responses(calls: Sequence[Mapping[str, Any]]) -> None:
    failures: list[dict[str, Any]] = []
    for call in calls:
        response = call.get("response") or {}
        for message in response.get("Messages") or []:
            failures.append({"scope": "response", "message": message})
        for result in response.get("MetricDataResults") or []:
            status = result.get("StatusCode")
            if status not in {None, "Complete"}:
                failures.append({"id": result.get("Id"), "status": status, "messages": result.get("Messages")})
    if failures:
        raise ValidationFailure(f"CloudWatch returned incomplete/error results: {failures}")


def finite_values(payload: Mapping[str, Any]) -> list[float]:
    values: list[float] = []
    for raw in payload.get("values") or []:
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            values.append(number)
    return values


def require_series(
    alias: str,
    payload: Mapping[str, Any],
    *,
    minimum: float | None = 0.0,
    maximum: float | None = None,
    positive: bool = False,
) -> dict[str, Any]:
    timestamps = payload.get("timestamps") or []
    raw_values = payload.get("values") or []
    if len(timestamps) != len(raw_values):
        raise ValidationFailure(f"{alias}: timestamps/values length mismatch")
    values = finite_values(payload)
    if not values:
        raise ValidationFailure(f"{alias}: no finite datapoints")
    if minimum is not None and any(value < minimum for value in values):
        raise ValidationFailure(f"{alias}: value below {minimum}")
    if maximum is not None and any(value > maximum for value in values):
        raise ValidationFailure(f"{alias}: value above {maximum}")
    if positive and not any(value > 0 for value in values):
        raise ValidationFailure(f"{alias}: workload produced no positive datapoint")
    return {
        "result": RESULT_PASS,
        "sample_count": len(values),
        "minimum": min(values),
        "maximum": max(values),
        "first_timestamp": min(str(item) for item in timestamps),
        "last_timestamp": max(str(item) for item in timestamps),
    }


def report_path(store: StateStore, started_at: datetime) -> Path:
    directory = store.path.parent / "reports"
    return directory / f"{store.data['run_id']}-{started_at:%Y%m%dT%H%M%SZ}.json"


def run_validation(
    store: StateStore,
    window_minutes: int,
    validator: Callable[[datetime, datetime], Mapping[str, Any]],
) -> tuple[dict[str, Any], int]:
    started = utc_now()
    report: dict[str, Any] = {
        "schema_version": 1,
        "run_id": store.data["run_id"],
        "rightsizer": store.data["rightsizer"],
        "account_id": store.data["account_id"],
        "region": store.data["region"],
        "window_minutes": window_minutes,
        "started_at": started.isoformat(),
    }
    try:
        details = dict(validator(started - timedelta(minutes=window_minutes), started))
        report.update(result=RESULT_PASS, details=details)
    except NotReady as exc:
        report.update(result=RESULT_NOT_READY, error={"type": type(exc).__name__, "message": str(exc)})
    except Exception as exc:
        report.update(result=RESULT_FAIL, error={"type": type(exc).__name__, "message": str(exc)})
    report["completed_at"] = iso_now()
    destination = report_path(store, started)
    store.record_validation(report, destination)
    return report, EXIT_CODES[report["result"]]


def common_create_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--state", type=Path, default=None)
    parser.add_argument("--apply", action="store_true", help="Create real, billable AWS resources")
    return parser


def common_state_parser(description: str, *, validate: bool = False) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--state", type=Path, required=True)
    if validate:
        parser.add_argument("--window-minutes", type=int, default=30)
    return parser


def require_apply(args: argparse.Namespace, plan: Mapping[str, Any]) -> None:
    print(json.dumps(plan, indent=2, sort_keys=True))
    if not args.apply:
        raise SystemExit("Refusing to create AWS resources without --apply")


def print_result(value: Mapping[str, Any]) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))
