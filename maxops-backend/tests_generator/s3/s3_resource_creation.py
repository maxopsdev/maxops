"""Create S3 resources used for payload capture."""

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


class S3PayloadResourceManager:
    """Create and clean up S3 resources used for payload capture."""

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
        self.s3 = self.session.client("s3", region_name=self.region)
        self.iam = self.session.client("iam")
        self.sts = self.session.client("sts")
        self.timestamp = int(time.time())
        self.account_id = self.sts.get_caller_identity()["Account"]

    def _bucket_name(self, placeholder_value: str) -> str:
        suffix = str(self.timestamp)
        base = placeholder_value.lower()
        max_prefix_length = 63 - len(suffix) - 1
        trimmed = base[:max_prefix_length].rstrip("-")
        return f"{trimmed}-{suffix}"

    def _create_bucket(self, bucket_name: str) -> None:
        if self.region == "us-east-1":
            self.s3.create_bucket(Bucket=bucket_name)
        else:
            self.s3.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": self.region},
            )
        self.s3.get_waiter("bucket_exists").wait(Bucket=bucket_name)

    def _put_bucket_tags(self, bucket_name: str, tags: Dict[str, str]) -> None:
        self.s3.put_bucket_tagging(
            Bucket=bucket_name,
            Tagging={"TagSet": [{"Key": key, "Value": value} for key, value in tags.items()]},
        )

    def _put_bucket_versioning(self, bucket_name: str, enabled: bool) -> None:
        if not enabled:
            return
        self.s3.put_bucket_versioning(
            Bucket=bucket_name,
            VersioningConfiguration={"Status": "Enabled"},
        )

    def _put_bucket_lifecycle(self, bucket_name: str, lifecycle_configuration: Optional[Dict[str, Any]]) -> None:
        if not lifecycle_configuration:
            return
        self.s3.put_bucket_lifecycle_configuration(
            Bucket=bucket_name,
            LifecycleConfiguration=lifecycle_configuration,
        )

    def _put_logging_target_acl(self, bucket_name: str) -> None:
        try:
            self.s3.put_bucket_acl(Bucket=bucket_name, ACL="log-delivery-write")
        except ClientError:
            # Some accounts enforce bucket-owner-only controls. Logging config is still
            # attempted later so the workflow can surface the account-specific failure.
            pass

    def _put_inventory_destination_policy(self, bucket_name: str) -> None:
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "AllowInventoryWrites",
                    "Effect": "Allow",
                    "Principal": {"Service": "s3.amazonaws.com"},
                    "Action": "s3:PutObject",
                    "Resource": f"arn:aws:s3:::{bucket_name}/inventory/*",
                    "Condition": {
                        "StringEquals": {
                            "aws:SourceAccount": self.account_id,
                            "s3:x-amz-acl": "bucket-owner-full-control",
                        }
                    },
                }
            ],
        }
        self.s3.put_bucket_policy(Bucket=bucket_name, Policy=json.dumps(policy))

    def _create_replication_role(self, role_name: str, policy_name: str, source_bucket: str, destination_bucket: str) -> str:
        assume_role_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "s3.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
        role = self.iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(assume_role_policy),
            Description="Role used for MaxOps S3 payload replication capture.",
        )
        policy_document = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetReplicationConfiguration", "s3:ListBucket"],
                    "Resource": [f"arn:aws:s3:::{source_bucket}"],
                },
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObjectVersionForReplication", "s3:GetObjectVersionAcl", "s3:GetObjectVersionTagging"],
                    "Resource": [f"arn:aws:s3:::{source_bucket}/*"],
                },
                {
                    "Effect": "Allow",
                    "Action": ["s3:ReplicateObject", "s3:ReplicateDelete", "s3:ReplicateTags"],
                    "Resource": [f"arn:aws:s3:::{destination_bucket}/*"],
                },
            ],
        }
        self.iam.put_role_policy(
            RoleName=role_name,
            PolicyName=policy_name,
            PolicyDocument=json.dumps(policy_document),
        )
        return role["Role"]["Arn"]

    def create_resources(self) -> Dict[str, Any]:
        placeholders = dict(self.config.get("placeholder_values", {}))
        placeholders["REGION"] = self.region
        placeholders["ACCOUNT_ID"] = self.account_id

        bucket_definitions = self.config["bucket_definitions"]
        bucket_state: Dict[str, Dict[str, Any]] = {}
        placeholder_map: Dict[str, str] = {}
        if self.config.get("placeholder_values", {}).get("ACCOUNT_ID"):
            placeholder_map[self.config["placeholder_values"]["ACCOUNT_ID"]] = self.account_id
        state: Dict[str, Any] = {
            "service": "s3",
            "region": self.region,
            "account_id": self.account_id,
            "created_at_epoch": self.timestamp,
            "resources": bucket_state,
            "supporting_resources": {},
            "placeholder_map": placeholder_map,
        }

        try:
            for alias, definition in bucket_definitions.items():
                placeholder_key = definition["name_placeholder_key"]
                placeholder_value = placeholders[placeholder_key]
                actual_bucket_name = self._bucket_name(placeholder_value)
                tags = _render_templates(definition.get("tags", {}), {**placeholders, placeholder_key: actual_bucket_name})

                self._create_bucket(actual_bucket_name)
                if tags:
                    self._put_bucket_tags(actual_bucket_name, tags)
                self._put_bucket_versioning(actual_bucket_name, definition.get("versioning", False))
                self._put_bucket_lifecycle(actual_bucket_name, definition.get("lifecycle_configuration"))

                bucket_state[alias] = {
                    "bucket_name": actual_bucket_name,
                    "placeholder_key": placeholder_key,
                    "placeholder_value": placeholder_value,
                    "versioning_enabled": bool(definition.get("versioning", False)),
                }
                placeholder_map[placeholder_value] = actual_bucket_name

            for alias, definition in bucket_definitions.items():
                bucket_name = bucket_state[alias]["bucket_name"]
                if definition.get("logging_target_alias"):
                    self._put_logging_target_acl(bucket_state[definition["logging_target_alias"]]["bucket_name"])
                    self.s3.put_bucket_logging(
                        Bucket=bucket_name,
                        BucketLoggingStatus={
                            "LoggingEnabled": {
                                "TargetBucket": bucket_state[definition["logging_target_alias"]]["bucket_name"],
                                "TargetPrefix": definition.get("logging_target_prefix", "logs/"),
                            }
                        },
                    )

                if definition.get("inventory_destination_alias"):
                    destination_bucket = bucket_state[definition["inventory_destination_alias"]]["bucket_name"]
                    self._put_inventory_destination_policy(destination_bucket)
                    self.s3.put_bucket_inventory_configuration(
                        Bucket=bucket_name,
                        Id=definition.get("inventory_configuration_id", "inventory"),
                        InventoryConfiguration={
                            "Destination": {
                                "S3BucketDestination": {
                                    "AccountId": self.account_id,
                                    "Bucket": f"arn:aws:s3:::{destination_bucket}",
                                    "Format": "CSV",
                                    "Prefix": "inventory/",
                                }
                            },
                            "IsEnabled": True,
                            "Id": definition.get("inventory_configuration_id", "inventory"),
                            "IncludedObjectVersions": "Current",
                            "OptionalFields": ["Size", "LastModifiedDate", "StorageClass"],
                            "Schedule": {"Frequency": "Daily"},
                        },
                    )

            replication_config = self.config.get("replication", {})
            source_alias = replication_config.get("source_bucket_alias", "replication_source")
            destination_alias = replication_config.get("destination_bucket_alias", "replication_dest")
            role_placeholder_key = replication_config.get("role_name_placeholder_key", "REPLICATION_ROLE_NAME")
            policy_placeholder_key = replication_config.get("policy_name_placeholder_key", "REPLICATION_POLICY_NAME")
            role_name = f"{placeholders[role_placeholder_key]}-{self.timestamp}"
            policy_name = f"{placeholders[policy_placeholder_key]}-{self.timestamp}"
            role_arn = self._create_replication_role(
                role_name,
                policy_name,
                bucket_state[source_alias]["bucket_name"],
                bucket_state[destination_alias]["bucket_name"],
            )
            placeholder_map[placeholders[role_placeholder_key]] = role_name
            placeholder_map[placeholders[policy_placeholder_key]] = policy_name
            state["supporting_resources"].update(
                {
                    "replication_role_name": role_name,
                    "replication_policy_name": policy_name,
                    "replication_role_arn": role_arn,
                }
            )
            self.s3.put_bucket_replication(
                Bucket=bucket_state[source_alias]["bucket_name"],
                ReplicationConfiguration={
                    "Role": role_arn,
                    "Rules": [
                        {
                            "ID": "replicate-all-objects",
                            "Status": "Enabled",
                            "Priority": 1,
                            "Filter": {"Prefix": ""},
                            "DeleteMarkerReplication": {"Status": "Disabled"},
                            "Destination": {
                                "Bucket": f"arn:aws:s3:::{bucket_state[destination_alias]['bucket_name']}",
                                "StorageClass": "STANDARD",
                            },
                        }
                    ],
                },
            )
        except Exception as exc:
            try:
                self.cleanup_resources(state=state)
            except Exception as cleanup_exc:
                raise RuntimeError(
                    "S3 payload capture resource creation failed and rollback also failed: "
                    f"{cleanup_exc}"
                ) from exc
            raise RuntimeError(
                "S3 payload capture resource creation failed; partial resources were rolled back."
            ) from exc

        _write_json(self.state_path, state)
        return state

    def _empty_bucket(self, bucket_name: str) -> None:
        versioning = self.s3.get_bucket_versioning(Bucket=bucket_name)
        is_versioned = versioning.get("Status") == "Enabled"
        if is_versioned:
            paginator = self.s3.get_paginator("list_object_versions")
            for page in paginator.paginate(Bucket=bucket_name):
                objects: List[Dict[str, str]] = []
                for version in page.get("Versions", []):
                    objects.append({"Key": version["Key"], "VersionId": version["VersionId"]})
                for marker in page.get("DeleteMarkers", []):
                    objects.append({"Key": marker["Key"], "VersionId": marker["VersionId"]})
                if objects:
                    self.s3.delete_objects(Bucket=bucket_name, Delete={"Objects": objects})
            return

        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket_name):
            contents = page.get("Contents", [])
            if contents:
                self.s3.delete_objects(
                    Bucket=bucket_name,
                    Delete={"Objects": [{"Key": item["Key"]} for item in contents]},
                )

    def cleanup_resources(self, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = state or _load_json(self.state_path)
        buckets = state.get("resources", {})
        targeted_resources: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []

        for alias, resource in buckets.items():
            targeted_resources.append(
                {
                    "resource_type": "bucket",
                    "resource_alias": alias,
                    "resource_name": resource["bucket_name"],
                }
            )

        role_name = state.get("supporting_resources", {}).get("replication_role_name")
        policy_name = state.get("supporting_resources", {}).get("replication_policy_name")
        if role_name:
            targeted_resources.append(
                {
                    "resource_type": "iam_role",
                    "resource_name": role_name,
                }
            )
        if role_name and policy_name:
            targeted_resources.append(
                {
                    "resource_type": "iam_role_policy",
                    "resource_name": policy_name,
                    "role_name": role_name,
                }
            )

        def record_error(resource_type: str, resource_name: str, operation: str, exc: ClientError, alias: Optional[str] = None) -> None:
            entry = {
                "resource_type": resource_type,
                "resource_name": resource_name,
                "operation": operation,
                "message": str(exc),
            }
            if alias is not None:
                entry["resource_alias"] = alias
            errors.append(entry)

        def error_code(exc: ClientError) -> str:
            return exc.response.get("Error", {}).get("Code", "")

        for alias, resource in buckets.items():
            bucket_name = resource["bucket_name"]
            try:
                inventory_response = self.s3.list_bucket_inventory_configurations(Bucket=bucket_name)
                for cfg in inventory_response.get("InventoryConfigurationList", []):
                    cfg_id = cfg.get("Id")
                    if cfg_id:
                        self.s3.delete_bucket_inventory_configuration(Bucket=bucket_name, Id=cfg_id)
            except ClientError as exc:
                if error_code(exc) != "NoSuchBucket":
                    record_error("bucket", bucket_name, "delete_bucket_inventory_configuration", exc, alias=alias)
            try:
                self.s3.put_bucket_logging(Bucket=bucket_name, BucketLoggingStatus={})
            except ClientError as exc:
                if error_code(exc) != "NoSuchBucket":
                    record_error("bucket", bucket_name, "put_bucket_logging", exc, alias=alias)
            try:
                self.s3.delete_bucket_replication(Bucket=bucket_name)
            except ClientError as exc:
                if error_code(exc) not in {"NoSuchBucket", "ReplicationConfigurationNotFoundError"}:
                    record_error("bucket", bucket_name, "delete_bucket_replication", exc, alias=alias)
            try:
                self.s3.delete_bucket_policy(Bucket=bucket_name)
            except ClientError as exc:
                if error_code(exc) not in {"NoSuchBucket", "NoSuchBucketPolicy"}:
                    record_error("bucket", bucket_name, "delete_bucket_policy", exc, alias=alias)

        for resource in buckets.values():
            bucket_name = resource["bucket_name"]
            alias = next(
                (resource_alias for resource_alias, details in buckets.items() if details["bucket_name"] == bucket_name),
                None,
            )
            try:
                self._empty_bucket(bucket_name)
            except ClientError as exc:
                if error_code(exc) != "NoSuchBucket":
                    record_error("bucket", bucket_name, "empty_bucket", exc, alias=alias)
            try:
                self.s3.delete_bucket(Bucket=bucket_name)
            except ClientError as exc:
                if error_code(exc) != "NoSuchBucket":
                    record_error("bucket", bucket_name, "delete_bucket", exc, alias=alias)

        if role_name and policy_name:
            try:
                self.iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
            except ClientError as exc:
                if error_code(exc) != "NoSuchEntity":
                    record_error("iam_role_policy", policy_name, "delete_role_policy", exc)
        if role_name:
            try:
                self.iam.delete_role(RoleName=role_name)
            except ClientError as exc:
                if error_code(exc) != "NoSuchEntity":
                    record_error("iam_role", role_name, "delete_role", exc)

        failed_resources = {
            (error["resource_type"], error["resource_name"]) for error in errors
        }
        targeted_count = len(targeted_resources)
        failed_count = len(failed_resources)
        summary = {
            "service": "s3",
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
        return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create or clean up S3 payload-capture resources. "
            "Defaults to 'create'; dedicated cleanup is also available via s3_resource_cleanup.py."
        )
    )
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
    manager = S3PayloadResourceManager(config_path=args.config, apply=args.apply)
    if args.command == "create":
        print(json.dumps(manager.create_resources(), indent=2, sort_keys=True))
        return
    summary = manager.cleanup_resources()
    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
