"""Create EC2 resources used for payload capture."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError
from botocore.exceptions import WaiterError
from tests_generator.capture_gate import require_capture_gate


CONFIG_PATH = Path(__file__).with_name("resource_config.json")
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
IDE_SAMPLE_ARGS = [
    "create",
    "--config",
    str(CONFIG_PATH),
    "--instance-type",
    "t3a.micro",
]


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _render_placeholders(value: Any, placeholders: Dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _render_placeholders(item, placeholders) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_placeholders(item, placeholders) for item in value]
    if isinstance(value, str):
        rendered = value
        for placeholder_key, replacement in placeholders.items():
            rendered = rendered.replace(f"{{{{{placeholder_key}}}}}", replacement)
        return rendered
    return value


def _waiter_reports_missing_resource(exc: WaiterError, code: str) -> bool:
    last_response = getattr(exc, "last_response", None) or {}
    error = last_response.get("Error", {})
    return error.get("Code") == code


class EC2PayloadResourceManager:
    """Create and clean up EC2 resources used for payload capture."""

    def __init__(self, config_path: Optional[Path] = None, *, apply: bool = False):
        require_capture_gate(apply)
        self.config_path = config_path or CONFIG_PATH
        self.config = _load_json(self.config_path)
        self.state_path = self.config_path.parents[2] / self.config["state_file"]
        profile = self.config.get("profile")
        region = self.config["region"]
        if profile:
            self.session = boto3.Session(profile_name=profile, region_name=region)
        else:
            self.session = boto3.Session(region_name=region)
        self.region = region
        self.ec2 = self.session.client("ec2", region_name=region)
        self.ssm = self.session.client("ssm", region_name=region)
        self.timestamp = int(time.time())

    def _get_default_vpc_id(self) -> str:
        vpcs = self.ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}]).get("Vpcs", [])
        if not vpcs:
            raise RuntimeError("No default VPC found for EC2 payload capture")
        return vpcs[0]["VpcId"]

    def _get_default_subnet_id(self, vpc_id: str) -> str:
        subnets = self.ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get("Subnets", [])
        if not subnets:
            raise RuntimeError("No subnet found in default VPC for EC2 payload capture")
        return subnets[0]["SubnetId"]

    def _get_default_security_group_id(self, vpc_id: str) -> str:
        groups = self.ec2.describe_security_groups(
            Filters=[
                {"Name": "group-name", "Values": ["default"]},
                {"Name": "vpc-id", "Values": [vpc_id]},
            ]
        ).get("SecurityGroups", [])
        if not groups:
            raise RuntimeError("No default security group found for EC2 payload capture")
        return groups[0]["GroupId"]

    def _get_latest_ami(self) -> str:
        parameter = self.ssm.get_parameter(Name="/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2")
        return parameter["Parameter"]["Value"]

    def _launch_instance(
        self,
        *,
        definition: Dict[str, Any],
        placeholder_values: Dict[str, str],
        ami_id: str,
        subnet_id: str,
        security_group_id: str,
        instance_type_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        name_placeholder_key = definition["name_placeholder_key"]
        actual_name = f"{placeholder_values[name_placeholder_key]}-{self.timestamp}"
        substitutions = dict(placeholder_values)
        substitutions[name_placeholder_key] = actual_name
        tags = _render_placeholders(definition.get("tags", {}), substitutions)
        response = self.ec2.run_instances(
            ImageId=ami_id,
            InstanceType=instance_type_override or definition["instance_type"],
            MinCount=1,
            MaxCount=1,
            SubnetId=subnet_id,
            SecurityGroupIds=[security_group_id],
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": key, "Value": value}
                        for key, value in tags.items()
                    ],
                }
            ],
        )
        instance_id = response["Instances"][0]["InstanceId"]
        self.ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
        described = self.ec2.describe_instances(InstanceIds=[instance_id])
        instance = described["Reservations"][0]["Instances"][0]
        volume_ids = [
            mapping["Ebs"]["VolumeId"]
            for mapping in instance.get("BlockDeviceMappings", [])
            if mapping.get("Ebs", {}).get("VolumeId")
        ]
        return {
            "instance_id": instance_id,
            "name": actual_name,
            "instance_type": instance_type_override or definition["instance_type"],
            "desired_state": "running",
            "volume_ids": volume_ids,
            "instance_id_placeholder_key": definition["instance_id_placeholder_key"],
            "name_placeholder_key": name_placeholder_key,
            "preserve_root_volume": bool(definition.get("preserve_root_volume")),
            "volume_id_placeholder_key": definition.get("volume_id_placeholder_key"),
        }

    def create_resources(self, instance_type_override: Optional[str] = None) -> Dict[str, Any]:
        vpc_id = self._get_default_vpc_id()
        subnet_id = self._get_default_subnet_id(vpc_id)
        security_group_id = self._get_default_security_group_id(vpc_id)
        ami_id = self._get_latest_ami()

        placeholder_values = dict(self.config.get("placeholder_values", {}))
        capture_definition = {
            **self.config["capture_instance"],
            "instance_id_placeholder_key": self.config["capture_instance"]["placeholder_key"],
        }
        resources = {
            "capture_instance": self._launch_instance(
                definition=capture_definition,
                placeholder_values=placeholder_values,
                ami_id=ami_id,
                subnet_id=subnet_id,
                security_group_id=security_group_id,
                instance_type_override=instance_type_override,
            )
        }
        for alias, definition in self.config.get("action_instance_definitions", {}).items():
            resources[alias] = self._launch_instance(
                definition=definition,
                placeholder_values=placeholder_values,
                ami_id=ami_id,
                subnet_id=subnet_id,
                security_group_id=security_group_id,
            )

        address = self.ec2.allocate_address(
            Domain="vpc",
            TagSpecifications=[
                {
                    "ResourceType": "elastic-ip",
                    "Tags": [
                        {"Key": "Name", "Value": "maxops-payload-ec2-unused-eip"},
                        {"Key": "maxops_payload_capture", "Value": "true"},
                    ],
                }
            ],
        )
        allocation_id = address["AllocationId"]
        public_ip = address["PublicIp"]
        placeholder_map = {
            placeholder_values["VPC_ID"]: vpc_id,
            placeholder_values["SUBNET_ID"]: subnet_id,
            placeholder_values["SECURITY_GROUP_ID"]: security_group_id,
            placeholder_values["AMI_ID"]: ami_id,
            placeholder_values["ELASTIC_IP_ALLOCATION_ID"]: allocation_id,
            placeholder_values["ELASTIC_IP_ADDRESS"]: public_ip,
        }
        for resource in resources.values():
            placeholder_map[
                placeholder_values[resource["instance_id_placeholder_key"]]
            ] = resource["instance_id"]
            placeholder_map[
                placeholder_values[resource["name_placeholder_key"]]
            ] = resource["name"]
            volume_placeholder_key = resource.get("volume_id_placeholder_key")
            if volume_placeholder_key and resource["volume_ids"]:
                placeholder_map[
                    placeholder_values[volume_placeholder_key]
                ] = resource["volume_ids"][0]

        state = {
            "service": "ec2",
            "region": self.region,
            "created_at_epoch": self.timestamp,
            "resources": {
                **resources,
                "unused_elastic_ip": {
                    "allocation_id": allocation_id,
                    "public_ip": public_ip,
                },
            },
            "snapshots": [],
            "supporting_resources": {
                "vpc_id": vpc_id,
                "subnet_id": subnet_id,
                "security_group_id": security_group_id,
                "ami_id": ami_id,
            },
            "placeholder_map": placeholder_map,
        }
        _write_json(self.state_path, state)
        return state

    def cleanup_resources(self, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = state or _load_json(self.state_path)
        instance_ids = [
            resource["instance_id"]
            for resource in state.get("resources", {}).values()
            if resource.get("instance_id")
        ]
        allocation_ids = [
            resource["allocation_id"]
            for resource in state.get("resources", {}).values()
            if resource.get("allocation_id")
        ]
        retained_volume_ids = [
            volume_id
            for resource in state.get("resources", {}).values()
            if resource.get("preserve_root_volume")
            for volume_id in resource.get("volume_ids", [])
        ]
        snapshot_ids = [
            snapshot["snapshot_id"]
            for snapshot in state.get("snapshots", [])
            if snapshot.get("snapshot_id")
        ]
        targets = instance_ids + allocation_ids + retained_volume_ids + snapshot_ids
        summary = {
            "service": "ec2",
            "cleanup_attempted": True,
            "cleanup_status": "success",
            "state_file": str(self.state_path),
            "resource_counts": {
                "targeted": len(targets),
                "succeeded": 0,
                "failed": 0,
            },
            "resources_targeted": list(targets),
            "errors": [],
        }
        if instance_ids:
            try:
                self.ec2.terminate_instances(InstanceIds=instance_ids)
                self.ec2.get_waiter("instance_terminated").wait(
                    InstanceIds=instance_ids,
                    WaiterConfig={"Delay": 5, "MaxAttempts": 60},
                )
                summary["resource_counts"]["succeeded"] += len(instance_ids)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") == "InvalidInstanceID.NotFound":
                    summary["resource_counts"]["succeeded"] += len(instance_ids)
                else:
                    summary["cleanup_status"] = "failure"
                    summary["resource_counts"]["failed"] += len(instance_ids)
                    summary["errors"].append(
                        {
                            "resource_type": "instance",
                            "resources": list(instance_ids),
                            "operation": "terminate_instances",
                            "message": str(exc),
                        }
                    )
        for allocation_id in allocation_ids:
            try:
                self.ec2.release_address(AllocationId=allocation_id)
                summary["resource_counts"]["succeeded"] += 1
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") == "InvalidAllocationID.NotFound":
                    summary["resource_counts"]["succeeded"] += 1
                else:
                    summary["cleanup_status"] = "failure"
                    summary["resource_counts"]["failed"] += 1
                    summary["errors"].append(
                        {
                            "resource_type": "elastic_ip",
                            "resources": [allocation_id],
                            "operation": "release_address",
                            "message": str(exc),
                        }
                    )
        for volume_id in retained_volume_ids:
            try:
                self.ec2.get_waiter("volume_available").wait(
                    VolumeIds=[volume_id],
                    WaiterConfig={"Delay": 5, "MaxAttempts": 60},
                )
                self.ec2.delete_volume(VolumeId=volume_id)
                summary["resource_counts"]["succeeded"] += 1
            except WaiterError as exc:
                if _waiter_reports_missing_resource(exc, "InvalidVolume.NotFound"):
                    summary["resource_counts"]["succeeded"] += 1
                else:
                    summary["cleanup_status"] = "failure"
                    summary["resource_counts"]["failed"] += 1
                    summary["errors"].append(
                        {
                            "resource_type": "volume",
                            "resources": [volume_id],
                            "operation": "wait_for_volume_available",
                            "message": str(exc),
                        }
                    )
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") == "InvalidVolume.NotFound":
                    summary["resource_counts"]["succeeded"] += 1
                else:
                    summary["cleanup_status"] = "failure"
                    summary["resource_counts"]["failed"] += 1
                    summary["errors"].append(
                        {
                            "resource_type": "volume",
                            "resources": [volume_id],
                            "operation": "delete_volume",
                            "message": str(exc),
                        }
                    )
        for snapshot_id in snapshot_ids:
            try:
                self.ec2.get_waiter("snapshot_completed").wait(
                    SnapshotIds=[snapshot_id],
                    WaiterConfig={"Delay": 5, "MaxAttempts": 60},
                )
                self.ec2.delete_snapshot(SnapshotId=snapshot_id)
                summary["resource_counts"]["succeeded"] += 1
            except WaiterError as exc:
                if _waiter_reports_missing_resource(exc, "InvalidSnapshot.NotFound"):
                    summary["resource_counts"]["succeeded"] += 1
                else:
                    summary["cleanup_status"] = "failure"
                    summary["resource_counts"]["failed"] += 1
                    summary["errors"].append(
                        {
                            "resource_type": "snapshot",
                            "resources": [snapshot_id],
                            "operation": "wait_for_snapshot_completed",
                            "message": str(exc),
                        }
                    )
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") == "InvalidSnapshot.NotFound":
                    summary["resource_counts"]["succeeded"] += 1
                else:
                    summary["cleanup_status"] = "failure"
                    summary["resource_counts"]["failed"] += 1
                    summary["errors"].append(
                        {
                            "resource_type": "snapshot",
                            "resources": [snapshot_id],
                            "operation": "delete_snapshot",
                            "message": str(exc),
                        }
                    )
        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or clean up EC2 payload-capture resources.")
    parser.add_argument("command", choices=["create", "cleanup"], help="Operation to perform.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to resource_config.json")
    parser.add_argument(
        "--instance-type",
        default=None,
        help="Optional EC2 instance type override for all generated resources, for example t3.small.",
    )
    parser.add_argument("--apply", action="store_true")

    args = parser.parse_args()

    require_capture_gate(args.apply)
    manager = EC2PayloadResourceManager(config_path=args.config, apply=args.apply)
    if args.command == "create":
        state = manager.create_resources(instance_type_override=args.instance_type)
        print(json.dumps(state, indent=2, sort_keys=True))
    else:
        summary = manager.cleanup_resources()
        print(json.dumps(summary, indent=2, sort_keys=True))
        if summary["errors"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
