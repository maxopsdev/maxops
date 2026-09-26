"""Create RDS resources used for payload capture."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError
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


class RDSPayloadResourceManager:
    """Create and clean up RDS resources used for payload capture."""

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
        self.rds = self.session.client("rds", region_name=self.region)
        self.ec2 = self.session.client("ec2", region_name=self.region)
        self.sts = self.session.client("sts")
        self.timestamp = int(time.time())
        self.account_id = self.sts.get_caller_identity()["Account"]

    def _identifier(self, placeholder_value: str) -> str:
        suffix = str(self.timestamp)
        max_prefix_length = 63 - len(suffix) - 1
        trimmed = placeholder_value.lower()[:max_prefix_length].rstrip("-")
        return f"{trimmed}-{suffix}"

    def _get_default_vpc_id(self) -> str:
        vpcs = self.ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}]).get("Vpcs", [])
        if not vpcs:
            raise RuntimeError("No default VPC found for RDS payload capture")
        return vpcs[0]["VpcId"]

    def _get_subnets(self, vpc_id: str) -> List[str]:
        subnets = self.ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get("Subnets", [])
        if len(subnets) < 2:
            raise RuntimeError("Need at least two subnets in the default VPC for RDS payload capture")
        return [subnet["SubnetId"] for subnet in subnets[:2]]

    def _get_default_security_group_id(self, vpc_id: str) -> str:
        groups = self.ec2.describe_security_groups(
            Filters=[
                {"Name": "group-name", "Values": ["default"]},
                {"Name": "vpc-id", "Values": [vpc_id]},
            ]
        ).get("SecurityGroups", [])
        if not groups:
            raise RuntimeError("No default security group found for RDS payload capture")
        return groups[0]["GroupId"]

    def _create_db_subnet_group(self, name: str, subnet_ids: List[str]) -> None:
        self.rds.create_db_subnet_group(
            DBSubnetGroupName=name,
            DBSubnetGroupDescription="MaxOps payload capture subnet group.",
            SubnetIds=subnet_ids,
            Tags=[
                {"Key": "Name", "Value": name},
                {"Key": "maxops_payload_capture", "Value": "true"},
            ],
        )

    def _describe_instance(self, identifier: str) -> Dict[str, Any]:
        response = self.rds.describe_db_instances(DBInstanceIdentifier=identifier)
        instances = response.get("DBInstances", [])
        if not instances:
            raise RuntimeError(f"Unable to find created RDS instance {identifier}")
        return instances[0]

    def create_resources(self) -> Dict[str, Any]:
        placeholders = dict(self.config.get("placeholder_values", {}))
        placeholders["REGION"] = self.region
        placeholders["ACCOUNT_ID"] = self.account_id

        vpc_id = self._get_default_vpc_id()
        subnet_ids = self._get_subnets(vpc_id)
        security_group_id = self._get_default_security_group_id(vpc_id)

        subnet_group_placeholder = placeholders["DB_SUBNET_GROUP"]
        subnet_group_name = self._identifier(subnet_group_placeholder)

        placeholder_map: Dict[str, str] = {
            placeholders["ACCOUNT_ID"]: self.account_id,
            placeholders["VPC_ID"]: vpc_id,
            placeholders["SUBNET_ID_A"]: subnet_ids[0],
            placeholders["SUBNET_ID_B"]: subnet_ids[1],
            placeholders["SECURITY_GROUP_ID"]: security_group_id,
            subnet_group_placeholder: subnet_group_name,
        }
        state: Dict[str, Any] = {
            "service": "rds",
            "region": self.region,
            "account_id": self.account_id,
            "created_at_epoch": self.timestamp,
            "resources": {},
            "supporting_resources": {
                "db_subnet_group_name": subnet_group_name,
                "vpc_id": vpc_id,
                "subnet_ids": subnet_ids,
                "security_group_id": security_group_id,
            },
            "placeholder_map": placeholder_map,
        }

        try:
            self._create_db_subnet_group(subnet_group_name, subnet_ids)
            waiter = self.rds.get_waiter("db_instance_available")
            for alias, definition in self.config["db_instance_definitions"].items():
                placeholder_key = definition["identifier_placeholder_key"]
                placeholder_value = placeholders[placeholder_key]
                actual_identifier = self._identifier(placeholder_value)
                tags = _render_templates(definition.get("tags", {}), {**placeholders, placeholder_key: actual_identifier})
                self.rds.create_db_instance(
                    DBInstanceIdentifier=actual_identifier,
                    DBInstanceClass=definition["db_instance_class"],
                    Engine=definition["engine"],
                    MasterUsername=definition["master_username"],
                    MasterUserPassword=definition["master_user_password"],
                    AllocatedStorage=definition["allocated_storage"],
                    DBSubnetGroupName=subnet_group_name,
                    VpcSecurityGroupIds=[security_group_id],
                    BackupRetentionPeriod=0,
                    Tags=[{"Key": key, "Value": value} for key, value in tags.items()],
                )
                waiter.wait(
                    DBInstanceIdentifier=actual_identifier,
                    WaiterConfig={"Delay": 30, "MaxAttempts": 40},
                )
                instance = self._describe_instance(actual_identifier)
                state["resources"][alias] = {
                    "db_instance_identifier": actual_identifier,
                    "db_instance_arn": instance["DBInstanceArn"],
                    "placeholder_key": placeholder_key,
                    "placeholder_value": placeholder_value,
                    "db_instance_class": definition["db_instance_class"],
                }
                placeholder_map[placeholder_value] = actual_identifier
        except Exception as exc:
            try:
                self.cleanup_resources(state=state)
            except Exception as cleanup_exc:
                raise RuntimeError(
                    "RDS payload capture resource creation failed and rollback also failed: "
                    f"{cleanup_exc}"
                ) from exc
            raise RuntimeError("RDS payload capture resource creation failed; partial resources were rolled back.") from exc

        _write_json(self.state_path, state)
        return state

    def cleanup_resources(self, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = state or _load_json(self.state_path)
        resources = state.get("resources", {})
        subnet_group_name = state.get("supporting_resources", {}).get("db_subnet_group_name")
        targeted_resources: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []

        for alias, resource in resources.items():
            targeted_resources.append(
                {
                    "resource_type": "db_instance",
                    "resource_alias": alias,
                    "resource_name": resource["db_instance_identifier"],
                }
            )
        if subnet_group_name:
            targeted_resources.append(
                {
                    "resource_type": "db_subnet_group",
                    "resource_name": subnet_group_name,
                }
            )

        for alias, resource in resources.items():
            identifier = resource["db_instance_identifier"]
            try:
                self.rds.delete_db_instance(
                    DBInstanceIdentifier=identifier,
                    SkipFinalSnapshot=True,
                    DeleteAutomatedBackups=True,
                )
            except ClientError as exc:
                errors.append(
                    {
                        "resource_type": "db_instance",
                        "resource_alias": alias,
                        "resource_name": identifier,
                        "operation": "delete_db_instance",
                        "message": str(exc),
                    }
                )

        if resources:
            waiter = self.rds.get_waiter("db_instance_deleted")
            for alias, resource in resources.items():
                identifier = resource["db_instance_identifier"]
                if any(error["resource_name"] == identifier for error in errors):
                    continue
                try:
                    waiter.wait(
                        DBInstanceIdentifier=identifier,
                        WaiterConfig={"Delay": 30, "MaxAttempts": 60},
                    )
                except Exception as exc:
                    errors.append(
                        {
                            "resource_type": "db_instance",
                            "resource_alias": alias,
                            "resource_name": identifier,
                            "operation": "wait_for_deletion",
                            "message": str(exc),
                        }
                    )

        if subnet_group_name:
            try:
                self.rds.delete_db_subnet_group(DBSubnetGroupName=subnet_group_name)
            except ClientError as exc:
                errors.append(
                    {
                        "resource_type": "db_subnet_group",
                        "resource_name": subnet_group_name,
                        "operation": "delete_db_subnet_group",
                        "message": str(exc),
                    }
                )

        failed_resources = {(error["resource_type"], error["resource_name"]) for error in errors}
        targeted_count = len(targeted_resources)
        failed_count = len(failed_resources)
        return {
            "service": "rds",
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
    parser = argparse.ArgumentParser(description="Create or clean up RDS payload-capture resources.")
    parser.add_argument(
        "command",
        nargs="?",
        default="create",
        choices=["create", "cleanup"],
        help="Operation to perform (default: create).",
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to resource_config.json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    require_capture_gate(args.apply)
    manager = RDSPayloadResourceManager(config_path=args.config, apply=args.apply)
    if args.command == "create":
        print(json.dumps(manager.create_resources(), indent=2, sort_keys=True))
        return
    summary = manager.cleanup_resources()
    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
