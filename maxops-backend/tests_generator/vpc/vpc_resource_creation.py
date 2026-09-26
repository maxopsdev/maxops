"""Create and clean up isolated VPC payload-capture resources."""

from __future__ import annotations

import argparse
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
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class VPCPayloadResourceManager:
    def __init__(self, config_path: Optional[Path] = None, *, apply: bool = False):
        require_capture_gate(apply)
        self.config_path = config_path or CONFIG_PATH
        self.config = _load_json(self.config_path)
        self.state_path = self.config_path.parents[2] / self.config["state_file"]
        profile = self.config.get("profile")
        self.region = self.config["region"]
        self.session = (
            boto3.Session(profile_name=profile, region_name=self.region)
            if profile
            else boto3.Session(region_name=self.region)
        )
        self.ec2 = self.session.client("ec2", region_name=self.region)
        self.s3 = self.session.client("s3", region_name=self.region)
        self.sts = self.session.client("sts")
        self.timestamp = int(time.time())

    def _create_bucket(self, name: str, account_id: str) -> None:
        kwargs: Dict[str, Any] = {"Bucket": name}
        if self.region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self.region}
        self.s3.create_bucket(**kwargs)
        self.s3.put_bucket_versioning(
            Bucket=name,
            VersioningConfiguration={"Status": "Enabled"},
        )
        self.s3.put_bucket_policy(
            Bucket=name,
            Policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "delivery.logs.amazonaws.com"},
                            "Action": "s3:GetBucketAcl",
                            "Resource": f"arn:aws:s3:::{name}",
                            "Condition": {"StringEquals": {"aws:SourceAccount": account_id}},
                        },
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "delivery.logs.amazonaws.com"},
                            "Action": "s3:PutObject",
                            "Resource": f"arn:aws:s3:::{name}/AWSLogs/{account_id}/*",
                            "Condition": {"StringEquals": {"s3:x-amz-acl": "bucket-owner-full-control", "aws:SourceAccount": account_id}},
                        },
                    ],
                }
            ),
        )

    def create_resources(self) -> Dict[str, Any]:
        placeholders = self.config["placeholder_values"]
        account_id = self.sts.get_caller_identity()["Account"]
        vpc_keys = {
            "endpoint_vpc": "ENDPOINT_VPC_ID",
            "disable_vpc": "DISABLE_VPC_ID",
            "expiration_vpc": "EXPIRATION_VPC_ID",
            "deletion_vpc": "DELETION_VPC_ID",
        }
        bucket_keys = {
            "disable_vpc": "DISABLE_BUCKET",
            "expiration_vpc": "EXPIRATION_BUCKET",
            "deletion_vpc": "DELETION_BUCKET",
        }
        flow_keys = {
            "disable_vpc": "DISABLE_FLOW_LOG_ID",
            "expiration_vpc": "EXPIRATION_FLOW_LOG_ID",
            "deletion_vpc": "DELETION_FLOW_LOG_ID",
        }
        state: Dict[str, Any] = {
            "service": "vpc",
            "region": self.region,
            "account_id": account_id,
            "resources": {},
            "vpc_endpoints": [],
            "placeholder_map": {placeholders["ACCOUNT_ID"]: account_id},
        }
        try:
            for index, (alias, placeholder_key) in enumerate(vpc_keys.items()):
                response = self.ec2.create_vpc(
                    CidrBlock=f"10.{index}.0.0/24",
                    TagSpecifications=[
                        {
                            "ResourceType": "vpc",
                            "Tags": [
                                {"Key": "Name", "Value": f"maxops-payload-{alias}-{self.timestamp}"},
                                {"Key": "maxops_payload_capture", "Value": "true"},
                            ],
                        }
                    ],
                )
                vpc_id = response["Vpc"]["VpcId"]
                self.ec2.get_waiter("vpc_available").wait(VpcIds=[vpc_id])
                route_tables = self.ec2.describe_route_tables(
                    Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
                )["RouteTables"]
                route_table_id = route_tables[0]["RouteTableId"]
                state["resources"][alias] = {
                    "resource_id": vpc_id,
                    "placeholder_key": placeholder_key,
                    "route_table_id": route_table_id,
                    "flow_log_ids": [],
                }
                state["placeholder_map"][placeholders[placeholder_key]] = vpc_id
                if alias == "endpoint_vpc":
                    state["placeholder_map"][placeholders["ENDPOINT_ROUTE_TABLE_ID"]] = route_table_id

            for alias, bucket_key in bucket_keys.items():
                bucket_name = f"{placeholders[bucket_key]}-{self.timestamp}"
                self._create_bucket(bucket_name, account_id)
                resource = state["resources"][alias]
                response = self.ec2.create_flow_logs(
                    ResourceIds=[resource["resource_id"]],
                    ResourceType="VPC",
                    TrafficType="ALL",
                    LogDestinationType="s3",
                    LogDestination=f"arn:aws:s3:::{bucket_name}",
                    TagSpecifications=[
                        {
                            "ResourceType": "vpc-flow-log",
                            "Tags": [{"Key": "maxops_payload_capture", "Value": "true"}],
                        }
                    ],
                )
                if response.get("Unsuccessful"):
                    raise RuntimeError(f"Flow log creation failed: {response['Unsuccessful']}")
                flow_log_id = response["FlowLogIds"][0]
                resource["bucket_name"] = bucket_name
                resource["flow_log_ids"] = [flow_log_id]
                state["placeholder_map"][placeholders[bucket_key]] = bucket_name
                state["placeholder_map"][placeholders[flow_keys[alias]]] = flow_log_id
        except Exception:
            self.cleanup_resources(state)
            raise
        _write_json(self.state_path, state)
        return state

    def cleanup_resources(self, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = state or _load_json(self.state_path)
        errors = []
        targets = []
        flow_ids = [
            flow_id
            for resource in state.get("resources", {}).values()
            for flow_id in resource.get("flow_log_ids", [])
        ]
        if flow_ids:
            try:
                self.ec2.delete_flow_logs(FlowLogIds=flow_ids)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "InvalidFlowLogId.NotFound":
                    errors.append({"operation": "delete_flow_logs", "message": str(exc)})
        endpoint_ids = state.get("vpc_endpoints", [])
        if endpoint_ids:
            try:
                self.ec2.delete_vpc_endpoints(VpcEndpointIds=endpoint_ids)
            except ClientError as exc:
                errors.append({"operation": "delete_vpc_endpoints", "message": str(exc)})
        for resource in state.get("resources", {}).values():
            bucket = resource.get("bucket_name")
            if bucket:
                targets.append(bucket)
                try:
                    for page in self.s3.get_paginator("list_object_versions").paginate(Bucket=bucket):
                        objects = [
                            {"Key": item["Key"], "VersionId": item["VersionId"]}
                            for item in page.get("Versions", []) + page.get("DeleteMarkers", [])
                        ]
                        if objects:
                            self.s3.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
                    self.s3.delete_bucket(Bucket=bucket)
                except ClientError as exc:
                    if exc.response.get("Error", {}).get("Code") != "NoSuchBucket":
                        errors.append({"resource_name": bucket, "operation": "delete_bucket", "message": str(exc)})
        for resource in state.get("resources", {}).values():
            vpc_id = resource["resource_id"]
            targets.append(vpc_id)
            try:
                self.ec2.delete_vpc(VpcId=vpc_id)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "InvalidVpcID.NotFound":
                    errors.append({"resource_name": vpc_id, "operation": "delete_vpc", "message": str(exc)})
        return {
            "service": "vpc",
            "cleanup_attempted": True,
            "cleanup_status": "success" if not errors else "partial_failure",
            "resource_counts": {"targeted": len(targets), "succeeded": len(targets) - len(errors), "failed": len(errors)},
            "resources_targeted": targets,
            "errors": errors,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", default="create", choices=["create", "cleanup"])
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    require_capture_gate(args.apply)
    manager = VPCPayloadResourceManager(args.config, apply=args.apply)
    result = manager.create_resources() if args.command == "create" else manager.cleanup_resources()
    print(json.dumps(result, indent=2, sort_keys=True))
    if result.get("errors"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
