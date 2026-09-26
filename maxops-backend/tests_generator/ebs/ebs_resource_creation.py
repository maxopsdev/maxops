"""Create EBS resources used for payload capture."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError, WaiterError
from tests_generator.capture_gate import require_capture_gate


CONFIG_PATH = Path(__file__).with_name("resource_config.json")
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _render_templates(value: Any, substitutions: Dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _render_templates(item, substitutions) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_templates(item, substitutions) for item in value]
    if isinstance(value, str):
        rendered = value
        for token, replacement in substitutions.items():
            rendered = rendered.replace(f"{{{{{token}}}}}", str(replacement))
        return rendered
    return value


class EBSPayloadResourceManager:
    """Create and clean up EBS resources used for payload capture."""

    def __init__(self, config_path: Optional[Path] = None, *, apply: bool = False):
        require_capture_gate(apply)
        self.config_path = config_path or CONFIG_PATH
        self.config = _load_json(self.config_path)
        self.state_path = self.config_path.parents[2] / self.config["state_file"]
        profile = self.config.get("profile")
        self.region = self.config["region"]
        if profile:
            self.session = boto3.Session(profile_name=profile, region_name=self.region)
        else:
            self.session = boto3.Session(region_name=self.region)
        self.ec2 = self.session.client("ec2", region_name=self.region)
        self.iam = self.session.client("iam")
        self.dlm = self.session.client("dlm", region_name=self.region)
        self.ssm = self.session.client("ssm", region_name=self.region)
        self.sts = self.session.client("sts")
        self.timestamp = int(time.time())
        self.account_id = self.sts.get_caller_identity()["Account"]

    def _identifier(self, placeholder_value: str) -> str:
        return f"{placeholder_value}-{self.timestamp}"

    def _get_default_vpc_id(self) -> str:
        vpcs = self.ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}]).get("Vpcs", [])
        if not vpcs:
            raise RuntimeError("No default VPC found for EBS payload capture")
        return vpcs[0]["VpcId"]

    def _get_default_subnet_id(self, vpc_id: str) -> str:
        subnets = self.ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get("Subnets", [])
        if not subnets:
            raise RuntimeError("No subnet found in default VPC for EBS payload capture")
        return subnets[0]["SubnetId"]

    def _get_default_security_group_id(self, vpc_id: str) -> str:
        groups = self.ec2.describe_security_groups(
            Filters=[
                {"Name": "group-name", "Values": ["default"]},
                {"Name": "vpc-id", "Values": [vpc_id]},
            ]
        ).get("SecurityGroups", [])
        if not groups:
            raise RuntimeError("No default security group found for EBS payload capture")
        return groups[0]["GroupId"]

    def _get_latest_ami(self) -> str:
        parameter = self.ssm.get_parameter(Name="/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2")
        return parameter["Parameter"]["Value"]

    def _get_subnet_az(self, subnet_id: str) -> str:
        response = self.ec2.describe_subnets(SubnetIds=[subnet_id])
        return response["Subnets"][0]["AvailabilityZone"]

    def _tag_specifications(self, tags: Dict[str, str], resource_type: str) -> List[Dict[str, Any]]:
        return [
            {
                "ResourceType": resource_type,
                "Tags": [{"Key": key, "Value": value} for key, value in tags.items()],
            }
        ]

    def create_resources(self, instance_type_override: Optional[str] = None) -> Dict[str, Any]:
        placeholders = dict(self.config.get("placeholder_values", {}))
        placeholders["REGION"] = self.region
        placeholders["ACCOUNT_ID"] = self.account_id

        vpc_id = self._get_default_vpc_id()
        subnet_id = self._get_default_subnet_id(vpc_id)
        security_group_id = self._get_default_security_group_id(vpc_id)
        availability_zone = self._get_subnet_az(subnet_id)
        ami_id = self._get_latest_ami()
        capture_instance = self.config["capture_instance"]
        instance_name = self._identifier(placeholders[capture_instance["name_placeholder_key"]])
        instance_tags = _render_templates(
            capture_instance.get("tags", {}),
            {**placeholders, capture_instance["name_placeholder_key"]: instance_name},
        )

        placeholder_map: Dict[str, str] = {
            placeholders["ACCOUNT_ID"]: self.account_id,
            placeholders["VPC_ID"]: vpc_id,
            placeholders["SUBNET_ID"]: subnet_id,
            placeholders["SECURITY_GROUP_ID"]: security_group_id,
            placeholders["AMI_ID"]: ami_id,
            placeholders["AVAILABILITY_ZONE"]: availability_zone,
            placeholders["INSTANCE_NAME"]: instance_name,
        }
        state: Dict[str, Any] = {
            "service": "ebs",
            "region": self.region,
            "account_id": self.account_id,
            "created_at_epoch": self.timestamp,
            "resources": {},
            "snapshots": [],
            "dlm_policies": [],
            "supporting_resources": {
                "vpc_id": vpc_id,
                "subnet_id": subnet_id,
                "security_group_id": security_group_id,
                "ami_id": ami_id,
                "availability_zone": availability_zone,
            },
            "placeholder_map": placeholder_map,
        }

        try:
            role_name = self._identifier(placeholders["DLM_ROLE_NAME"])
            role = self.iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Principal": {"Service": "dlm.amazonaws.com"},
                                "Action": "sts:AssumeRole",
                            }
                        ],
                    }
                ),
                Description="Temporary MaxOps payload-capture DLM role.",
                Tags=[
                    {"Key": "maxops_payload_capture", "Value": "true"},
                ],
            )
            role_arn = role["Role"]["Arn"]
            managed_policy_arn = (
                "arn:aws:iam::aws:policy/service-role/"
                "AWSDataLifecycleManagerServiceRole"
            )
            self.iam.attach_role_policy(
                RoleName=role_name,
                PolicyArn=managed_policy_arn,
            )
            state["supporting_resources"]["dlm_role"] = {
                "role_name": role_name,
                "role_arn": role_arn,
                "managed_policy_arn": managed_policy_arn,
            }
            placeholder_map[placeholders["DLM_ROLE_NAME"]] = role_name
            placeholder_map[placeholders["DLM_ROLE_ARN"]] = role_arn

            instance_type = instance_type_override or capture_instance["instance_type"]
            response = self.ec2.run_instances(
                ImageId=ami_id,
                InstanceType=instance_type,
                MinCount=1,
                MaxCount=1,
                SubnetId=subnet_id,
                SecurityGroupIds=[security_group_id],
                TagSpecifications=self._tag_specifications(instance_tags, "instance"),
            )
            instance_id = response["Instances"][0]["InstanceId"]
            placeholder_map[placeholders["INSTANCE_ID"]] = instance_id
            state["supporting_resources"]["instance"] = {
                "instance_id": instance_id,
                "name": instance_name,
                "instance_type": instance_type,
            }
            self.ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id], WaiterConfig={"Delay": 10, "MaxAttempts": 60})

            volume_waiter = self.ec2.get_waiter("volume_available")
            in_use_waiter = self.ec2.get_waiter("volume_in_use")
            for alias, definition in self.config["volume_definitions"].items():
                volume_placeholder = placeholders[definition["volume_id_placeholder_key"]]
                name_placeholder_key = definition["name_placeholder_key"]
                volume_name = self._identifier(placeholders[name_placeholder_key])
                substitutions = {**placeholders, name_placeholder_key: volume_name}
                tags = _render_templates(definition.get("tags", {}), substitutions)
                create_args: Dict[str, Any] = {
                    "AvailabilityZone": availability_zone,
                    "Size": definition["size"],
                    "VolumeType": definition["volume_type"],
                    "TagSpecifications": self._tag_specifications(tags, "volume"),
                }
                if definition.get("iops") is not None:
                    create_args["Iops"] = definition["iops"]
                volume = self.ec2.create_volume(**create_args)
                volume_id = volume["VolumeId"]
                placeholder_map[volume_placeholder] = volume_id
                placeholder_map[placeholders[name_placeholder_key]] = volume_name
                state["resources"][alias] = {
                    "volume_id": volume_id,
                    "placeholder_key": definition["volume_id_placeholder_key"],
                    "placeholder_value": volume_placeholder,
                    "name_placeholder_key": name_placeholder_key,
                    "name": volume_name,
                    "volume_type": definition["volume_type"],
                    "size": definition["size"],
                    "iops": definition.get("iops"),
                    "attach": definition.get("attach", False),
                    "device": definition.get("device"),
                }
                volume_waiter.wait(VolumeIds=[volume_id], WaiterConfig={"Delay": 5, "MaxAttempts": 60})
                if definition.get("attach", False):
                    self.ec2.attach_volume(
                        Device=definition["device"],
                        InstanceId=instance_id,
                        VolumeId=volume_id,
                    )
                    in_use_waiter.wait(VolumeIds=[volume_id], WaiterConfig={"Delay": 5, "MaxAttempts": 60})

            self.ec2.stop_instances(InstanceIds=[instance_id])
            self.ec2.get_waiter("instance_stopped").wait(InstanceIds=[instance_id], WaiterConfig={"Delay": 10, "MaxAttempts": 60})
        except Exception as exc:
            try:
                self.cleanup_resources(state=state)
            except Exception as cleanup_exc:
                raise RuntimeError(
                    "EBS payload capture resource creation failed and rollback also failed: "
                    f"{cleanup_exc}"
                ) from exc
            raise RuntimeError("EBS payload capture resource creation failed; partial resources were rolled back.") from exc

        _write_json(self.state_path, state)
        return state

    def cleanup_resources(self, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = state or _load_json(self.state_path)
        resources = state.get("resources", {})
        instance_id = state.get("supporting_resources", {}).get("instance", {}).get("instance_id")
        targeted_resources: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        dlm_role = state.get("supporting_resources", {}).get("dlm_role", {})

        for policy in state.get("dlm_policies", []):
            policy_id = policy["policy_id"]
            targeted_resources.append(
                {
                    "resource_type": "dlm_policy",
                    "resource_name": policy_id,
                }
            )
            try:
                self.dlm.delete_lifecycle_policy(PolicyId=policy_id)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in {
                    "ResourceNotFoundException",
                    "InvalidRequestException",
                }:
                    errors.append(
                        {
                            "resource_type": "dlm_policy",
                            "resource_name": policy_id,
                            "operation": "delete_lifecycle_policy",
                            "message": str(exc),
                        }
                    )

        for snapshot in state.get("snapshots", []):
            targeted_resources.append(
                {
                    "resource_type": "ebs_snapshot",
                    "resource_alias": snapshot["resource_alias"],
                    "resource_name": snapshot["snapshot_id"],
                }
            )
            try:
                self.ec2.get_waiter("snapshot_completed").wait(
                    SnapshotIds=[snapshot["snapshot_id"]],
                    WaiterConfig={"Delay": 15, "MaxAttempts": 80},
                )
                self.ec2.delete_snapshot(SnapshotId=snapshot["snapshot_id"])
            except (ClientError, WaiterError) as exc:
                error_code = (
                    exc.response.get("Error", {}).get("Code")
                    if isinstance(exc, ClientError)
                    else None
                )
                if error_code != "InvalidSnapshot.NotFound":
                    errors.append(
                        {
                            "resource_type": "ebs_snapshot",
                            "resource_alias": snapshot["resource_alias"],
                            "resource_name": snapshot["snapshot_id"],
                            "operation": "delete_snapshot",
                            "message": str(exc),
                        }
                    )

        for alias, resource in resources.items():
            targeted_resources.append(
                {
                    "resource_type": "ebs_volume",
                    "resource_alias": alias,
                    "resource_name": resource["volume_id"],
                }
            )
        if instance_id:
            targeted_resources.append(
                {
                    "resource_type": "ec2_instance",
                    "resource_name": instance_id,
                }
            )

        for alias, resource in resources.items():
            if not resource.get("attach"):
                continue
            volume_id = resource["volume_id"]
            try:
                self.ec2.detach_volume(VolumeId=volume_id, Force=True)
            except ClientError as exc:
                error_code = exc.response.get("Error", {}).get("Code")
                if error_code not in {"IncorrectState", "InvalidAttachment.NotFound", "InvalidVolume.NotFound"}:
                    errors.append(
                        {
                            "resource_type": "ebs_volume",
                            "resource_alias": alias,
                            "resource_name": volume_id,
                            "operation": "detach_volume",
                            "message": str(exc),
                        }
                    )

        if resources:
            waiter = self.ec2.get_waiter("volume_available")
            for alias, resource in resources.items():
                volume_id = resource["volume_id"]
                if any(error["resource_name"] == volume_id for error in errors):
                    continue
                try:
                    waiter.wait(VolumeIds=[volume_id], WaiterConfig={"Delay": 5, "MaxAttempts": 60})
                except WaiterError as exc:
                    message = str(exc)
                    if "InvalidVolume.NotFound" in message or "does not exist" in message:
                        continue
                    errors.append(
                        {
                            "resource_type": "ebs_volume",
                            "resource_alias": alias,
                            "resource_name": volume_id,
                            "operation": "wait_for_available",
                            "message": message,
                        }
                    )
                except ClientError as exc:
                    error_code = exc.response.get("Error", {}).get("Code")
                    if error_code == "InvalidVolume.NotFound":
                        continue
                    errors.append(
                        {
                            "resource_type": "ebs_volume",
                            "resource_alias": alias,
                            "resource_name": volume_id,
                            "operation": "wait_for_available",
                            "message": str(exc),
                        }
                    )
                except Exception as exc:
                    errors.append(
                        {
                            "resource_type": "ebs_volume",
                            "resource_alias": alias,
                            "resource_name": volume_id,
                            "operation": "wait_for_available",
                            "message": str(exc),
                        }
                    )

        for alias, resource in resources.items():
            volume_id = resource["volume_id"]
            if any(error["resource_name"] == volume_id for error in errors):
                continue
            try:
                self.ec2.delete_volume(VolumeId=volume_id)
            except ClientError as exc:
                error_code = exc.response.get("Error", {}).get("Code")
                if error_code != "InvalidVolume.NotFound":
                    errors.append(
                        {
                            "resource_type": "ebs_volume",
                            "resource_alias": alias,
                            "resource_name": volume_id,
                            "operation": "delete_volume",
                            "message": str(exc),
                        }
                    )

        if instance_id:
            try:
                self.ec2.terminate_instances(InstanceIds=[instance_id])
            except ClientError as exc:
                errors.append(
                    {
                        "resource_type": "ec2_instance",
                        "resource_name": instance_id,
                        "operation": "terminate_instances",
                        "message": str(exc),
                    }
                )

        if dlm_role.get("role_name"):
            role_name = dlm_role["role_name"]
            targeted_resources.append(
                {
                    "resource_type": "iam_role",
                    "resource_name": role_name,
                }
            )
            try:
                self.iam.detach_role_policy(
                    RoleName=role_name,
                    PolicyArn=dlm_role["managed_policy_arn"],
                )
                self.iam.delete_role(RoleName=role_name)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "NoSuchEntity":
                    errors.append(
                        {
                            "resource_type": "iam_role",
                            "resource_name": role_name,
                            "operation": "delete_role",
                            "message": str(exc),
                        }
                    )

        failed_resources = {(error["resource_type"], error["resource_name"]) for error in errors}
        targeted_count = len(targeted_resources)
        failed_count = len(failed_resources)
        return {
            "service": "ebs",
            "cleanup_attempted": True,
            "cleanup_status": "success" if not errors else "partial_failure",
            "state_file": str(self.state_path),
            "resource_counts": {
                "targeted": targeted_count,
                "succeeded": targeted_count - failed_count,
                "failed": failed_count,
            },
            "resources_targeted": targeted_resources,
            "errors": errors,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or clean up EBS payload-capture resources.")
    parser.add_argument(
        "command",
        nargs="?",
        default="create",
        choices=["create", "cleanup"],
        help="Operation to perform (default: create).",
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to resource_config.json")
    parser.add_argument(
        "--instance-type",
        default=None,
        help="Optional EC2 instance type override for the capture instance, for example t3a.nano.",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    require_capture_gate(args.apply)
    manager = EBSPayloadResourceManager(config_path=args.config, apply=args.apply)
    if args.command == "create":
        print(json.dumps(manager.create_resources(instance_type_override=args.instance_type), indent=2, sort_keys=True))
        return
    summary = manager.cleanup_resources()
    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
