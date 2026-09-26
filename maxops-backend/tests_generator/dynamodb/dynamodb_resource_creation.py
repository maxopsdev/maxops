"""Create DynamoDB resources used for payload capture."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

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


class DynamoDBPayloadResourceManager:
    """Create and clean up DynamoDB resources used for payload capture."""

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
        self.dynamodb = self.session.client("dynamodb", region_name=self.region)
        self.autoscaling = self.session.client("application-autoscaling", region_name=self.region)
        self.sts = self.session.client("sts")
        self.timestamp = int(time.time())
        self.account_id = self.sts.get_caller_identity()["Account"]

    def _identifier(self, placeholder_value: str) -> str:
        hash_token = hashlib.sha1(placeholder_value.encode("utf-8")).hexdigest()[:6]
        suffix = f"{hash_token}-{self.timestamp}"
        max_prefix_length = 255 - len(suffix) - 1
        trimmed = placeholder_value.lower()[:max_prefix_length].rstrip("-")
        return f"{trimmed}-{suffix}"

    def _describe_table(self, table_name: str) -> Dict[str, Any]:
        response = self.dynamodb.describe_table(TableName=table_name)
        table = response.get("Table")
        if not table:
            raise RuntimeError(f"Unable to find created DynamoDB table {table_name}")
        return table

    def create_resources(self) -> Dict[str, Any]:
        placeholders = dict(self.config.get("placeholder_values", {}))
        placeholders["REGION"] = self.region
        placeholders["ACCOUNT_ID"] = self.account_id

        placeholder_map: Dict[str, str] = {
            placeholders["ACCOUNT_ID"]: self.account_id,
        }
        state: Dict[str, Any] = {
            "service": "dynamodb",
            "region": self.region,
            "account_id": self.account_id,
            "created_at_epoch": self.timestamp,
            "resources": {},
            "placeholder_map": placeholder_map,
            "autoscaling_policies": [],
            "autoscaling_targets": [],
            "backups": [],
        }

        try:
            waiter = self.dynamodb.get_waiter("table_exists")
            for alias, definition in self.config["table_definitions"].items():
                placeholder_key = definition["table_name_placeholder_key"]
                placeholder_value = placeholders[placeholder_key]
                actual_table_name = self._identifier(placeholder_value)
                substitutions = {**placeholders, placeholder_key: actual_table_name}

                create_args: Dict[str, Any] = {
                    "TableName": actual_table_name,
                    "KeySchema": definition["key_schema"],
                    "AttributeDefinitions": definition["attribute_definitions"],
                    "BillingMode": definition["billing_mode"],
                    "Tags": [
                        {"Key": key, "Value": value}
                        for key, value in _render_templates(definition.get("tags", {}), substitutions).items()
                    ],
                }
                if definition["billing_mode"] == "PROVISIONED":
                    create_args["ProvisionedThroughput"] = definition["provisioned_throughput"]

                gsis = []
                gsi_state = []
                for gsi in definition.get("global_secondary_indexes", []):
                    index_placeholder_key = gsi["index_name_placeholder_key"]
                    index_placeholder_value = placeholders[index_placeholder_key]
                    create_gsi = {
                        "IndexName": index_placeholder_value,
                        "KeySchema": gsi["KeySchema"],
                        "Projection": gsi["Projection"],
                    }
                    if definition["billing_mode"] == "PROVISIONED":
                        create_gsi["ProvisionedThroughput"] = gsi["ProvisionedThroughput"]
                    gsis.append(create_gsi)
                    gsi_state.append(
                        {
                            "index_name": index_placeholder_value,
                            "placeholder_key": index_placeholder_key,
                            "placeholder_value": index_placeholder_value,
                        }
                    )
                if gsis:
                    create_args["GlobalSecondaryIndexes"] = gsis

                self.dynamodb.create_table(**create_args)
                waiter.wait(TableName=actual_table_name, WaiterConfig={"Delay": 10, "MaxAttempts": 60})
                table = self._describe_table(actual_table_name)

                state["resources"][alias] = {
                    "table_name": actual_table_name,
                    "table_arn": table["TableArn"],
                    "placeholder_key": placeholder_key,
                    "placeholder_value": placeholder_value,
                    "billing_mode": definition["billing_mode"],
                    "global_secondary_indexes": gsi_state,
                }
                placeholder_map[placeholder_value] = actual_table_name
        except Exception as exc:
            try:
                self.cleanup_resources(state=state)
            except Exception as cleanup_exc:
                raise RuntimeError(
                    "DynamoDB payload capture resource creation failed and rollback also failed: "
                    f"{cleanup_exc}"
                ) from exc
            raise RuntimeError(
                "DynamoDB payload capture resource creation failed; partial resources were rolled back."
            ) from exc

        _write_json(self.state_path, state)
        return state

    def cleanup_resources(self, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = state or _load_json(self.state_path)
        resources = state.get("resources", {})
        targeted_resources = [
            {
                "resource_type": "dynamodb_table",
                "resource_alias": alias,
                "resource_name": resource["table_name"],
            }
            for alias, resource in resources.items()
        ]
        targeted_resources.extend(
            {
                "resource_type": "application_autoscaling_policy",
                "resource_alias": policy["resource_alias"],
                "resource_name": policy["policy_name"],
            }
            for policy in state.get("autoscaling_policies", [])
        )
        targeted_resources.extend(
            {
                "resource_type": "application_autoscaling_target",
                "resource_alias": target["resource_alias"],
                "resource_name": f"{target['resource_id']}:{target['scalable_dimension']}",
            }
            for target in state.get("autoscaling_targets", [])
        )
        targeted_resources.extend(
            {
                "resource_type": "dynamodb_backup",
                "resource_alias": backup["resource_alias"],
                "resource_name": backup["backup_arn"],
            }
            for backup in state.get("backups", [])
        )
        errors = []

        autoscaling = getattr(self, "autoscaling", None)
        if autoscaling is not None:
            for policy in state.get("autoscaling_policies", []):
                try:
                    autoscaling.delete_scaling_policy(
                        PolicyName=policy["policy_name"],
                        ServiceNamespace="dynamodb",
                        ResourceId=policy["resource_id"],
                        ScalableDimension=policy["scalable_dimension"],
                    )
                except ClientError as exc:
                    error_code = exc.response.get("Error", {}).get("Code")
                    if error_code not in {"ObjectNotFoundException", "ValidationException"}:
                        errors.append(
                            {
                                "resource_type": "application_autoscaling_policy",
                                "resource_alias": policy["resource_alias"],
                                "resource_name": policy["policy_name"],
                                "operation": "delete_scaling_policy",
                                "message": str(exc),
                            }
                        )
            for target in state.get("autoscaling_targets", []):
                try:
                    autoscaling.deregister_scalable_target(
                        ServiceNamespace="dynamodb",
                        ResourceId=target["resource_id"],
                        ScalableDimension=target["scalable_dimension"],
                    )
                except ClientError as exc:
                    error_code = exc.response.get("Error", {}).get("Code")
                    if error_code not in {"ObjectNotFoundException", "ValidationException"}:
                        errors.append(
                            {
                                "resource_type": "application_autoscaling_target",
                                "resource_alias": target["resource_alias"],
                                "resource_name": f"{target['resource_id']}:{target['scalable_dimension']}",
                                "operation": "deregister_scalable_target",
                                "message": str(exc),
                            }
                        )

        for backup in state.get("backups", []):
            try:
                for _ in range(60):
                    details = self.dynamodb.describe_backup(BackupArn=backup["backup_arn"]).get("BackupDescription", {})
                    status = details.get("BackupDetails", {}).get("BackupStatus")
                    if status == "AVAILABLE":
                        break
                    time.sleep(5)
                self.dynamodb.delete_backup(BackupArn=backup["backup_arn"])
            except ClientError as exc:
                error_code = exc.response.get("Error", {}).get("Code")
                if error_code not in {"BackupNotFoundException", "ResourceNotFoundException"}:
                    errors.append(
                        {
                            "resource_type": "dynamodb_backup",
                            "resource_alias": backup["resource_alias"],
                            "resource_name": backup["backup_arn"],
                            "operation": "delete_backup",
                            "message": str(exc),
                        }
                    )

        for alias, resource in resources.items():
            table_name = resource["table_name"]
            try:
                self.dynamodb.delete_table(TableName=table_name)
            except ClientError as exc:
                error_code = exc.response.get("Error", {}).get("Code")
                if error_code != "ResourceNotFoundException":
                    errors.append(
                        {
                            "resource_type": "dynamodb_table",
                            "resource_alias": alias,
                            "resource_name": table_name,
                            "operation": "delete_table",
                            "message": str(exc),
                        }
                    )

        if resources:
            waiter = self.dynamodb.get_waiter("table_not_exists")
            for alias, resource in resources.items():
                table_name = resource["table_name"]
                if any(error["resource_name"] == table_name for error in errors):
                    continue
                try:
                    waiter.wait(TableName=table_name, WaiterConfig={"Delay": 10, "MaxAttempts": 60})
                except Exception as exc:
                    errors.append(
                        {
                            "resource_type": "dynamodb_table",
                            "resource_alias": alias,
                            "resource_name": table_name,
                            "operation": "wait_for_deletion",
                            "message": str(exc),
                        }
                    )

        failed_resources = {(error["resource_type"], error["resource_name"]) for error in errors}
        targeted_count = len(targeted_resources)
        failed_count = len(failed_resources)
        return {
            "service": "dynamodb",
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
    parser = argparse.ArgumentParser(description="Create or clean up DynamoDB payload-capture resources.")
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
    manager = DynamoDBPayloadResourceManager(config_path=args.config, apply=args.apply)
    if args.command == "create":
        print(json.dumps(manager.create_resources(), indent=2, sort_keys=True))
        return
    summary = manager.cleanup_resources()
    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
