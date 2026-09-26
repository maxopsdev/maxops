"""VPC action handlers."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from botocore.exceptions import ClientError
from fastapi import HTTPException

from app.actions.base import ActionExecutionContext
from app.schemas.check import CheckActionResponse

logger = logging.getLogger("uvicorn.error")


def _normalize_route_table_ids(value: Any) -> Optional[List[str]]:
    """Normalize route table IDs to a list of strings."""
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return None


def _normalize_flow_log_ids(value: Any) -> Optional[List[str]]:
    """Normalize flow log IDs to a list of strings."""
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return None


def _normalize_bucket_names(value: Any) -> Optional[List[str]]:
    """Normalize bucket names to a unique ordered list."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        return None
    return list(dict.fromkeys(item.strip() for item in value))


def _normalize_int(value: Any) -> Optional[int]:
    """Normalize value to integer."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def handle_dynamodb_vpc_endpoint(context: ActionExecutionContext) -> CheckActionResponse:
    """Handle DynamoDB VPC endpoint actions."""
    try:
        ec2 = context.aws_adapter.session.client("ec2", region_name=context.payload.region)

        # Verify VPC exists
        try:
            vpcs = ec2.describe_vpcs(VpcIds=[context.payload.resource_id])
            if not vpcs.get("Vpcs"):
                raise HTTPException(
                    status_code=404,
                    detail=f"VPC {context.payload.resource_id} not found",
                )
        except HTTPException:
            raise
        except Exception as verify_exc:
            raise HTTPException(
                status_code=404,
                detail=f"Failed to verify VPC: {str(verify_exc)}",
            )

        service_name = f"com.amazonaws.{context.payload.region}.dynamodb"

        # Check if a DynamoDB endpoint already exists in the VPC
        existing = ec2.describe_vpc_endpoints(
            Filters=[
                {"Name": "vpc-id", "Values": [context.payload.resource_id]},
                {"Name": "service-name", "Values": [service_name]},
            ]
        )
        if existing.get("VpcEndpoints"):
            endpoint_id = existing["VpcEndpoints"][0].get("VpcEndpointId")
            raise HTTPException(
                status_code=409,
                detail=f"DynamoDB VPC endpoint already exists: {endpoint_id}",
            )

        params = context.payload.parameters or {}
        route_table_ids = _normalize_route_table_ids(
            params.get("route_table_ids")
            or params.get("routeTableIds")
            or params.get("route_tables")
        )

        if not route_table_ids:
            # Best-effort: use the main route table for the VPC.
            route_tables = ec2.describe_route_tables(
                Filters=[{"Name": "vpc-id", "Values": [context.payload.resource_id]}]
            ).get("RouteTables", [])
            main_rt = next(
                (
                    rt
                    for rt in route_tables
                    if any(assoc.get("Main") for assoc in rt.get("Associations", []))
                ),
                None,
            )
            target_rt = main_rt or (route_tables[0] if route_tables else None)
            if not target_rt:
                raise HTTPException(
                    status_code=400,
                    detail="No route tables found for VPC. Provide route_table_ids.",
                )
            route_table_ids = [target_rt["RouteTableId"]]

        policy_document = params.get("policy_document") or params.get("policyDocument")
        tags = params.get("tags")

        create_params: Dict[str, Any] = {
            "VpcId": context.payload.resource_id,
            "ServiceName": service_name,
            "VpcEndpointType": "Gateway",
            "RouteTableIds": route_table_ids,
        }
        if policy_document:
            create_params["PolicyDocument"] = policy_document
        if isinstance(tags, list):
            create_params["TagSpecifications"] = [
                {"ResourceType": "vpc-endpoint", "Tags": tags}
            ]

        response = ec2.create_vpc_endpoint(**create_params)
        endpoint_id = response.get("VpcEndpoint", {}).get("VpcEndpointId")

        return CheckActionResponse(
            check_id=context.check_id,
            resource_id=context.payload.resource_id,
            action=context.payload.action,
            status="submitted",
            message="DynamoDB VPC endpoint creation submitted",
            details={
                "vpc_endpoint_id": endpoint_id,
                "vpc_id": context.payload.resource_id,
                "region": context.payload.region,
                "service_name": service_name,
                "route_table_ids": route_table_ids,
                "response": response,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to create DynamoDB VPC endpoint",
            extra={
                "check_id": context.check_id,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create DynamoDB VPC endpoint: {str(exc)}",
        )


def handle_s3_vpc_endpoint(context: ActionExecutionContext) -> CheckActionResponse:
    """Handle S3 VPC endpoint actions."""
    try:
        ec2 = context.aws_adapter.session.client("ec2", region_name=context.payload.region)

        # Verify VPC exists
        try:
            vpcs = ec2.describe_vpcs(VpcIds=[context.payload.resource_id])
            if not vpcs.get("Vpcs"):
                raise HTTPException(
                    status_code=404,
                    detail=f"VPC {context.payload.resource_id} not found",
                )
        except HTTPException:
            raise
        except Exception as verify_exc:
            raise HTTPException(
                status_code=404,
                detail=f"Failed to verify VPC: {str(verify_exc)}",
            )

        service_name = f"com.amazonaws.{context.payload.region}.s3"

        # Check if an S3 endpoint already exists in the VPC
        existing = ec2.describe_vpc_endpoints(
            Filters=[
                {"Name": "vpc-id", "Values": [context.payload.resource_id]},
                {"Name": "service-name", "Values": [service_name]},
            ]
        )
        if existing.get("VpcEndpoints"):
            endpoint_id = existing["VpcEndpoints"][0].get("VpcEndpointId")
            raise HTTPException(
                status_code=409,
                detail=f"S3 VPC endpoint already exists: {endpoint_id}",
            )

        params = context.payload.parameters or {}
        route_table_ids = _normalize_route_table_ids(
            params.get("route_table_ids")
            or params.get("routeTableIds")
            or params.get("route_tables")
        )

        if not route_table_ids:
            # Best-effort: use the main route table for the VPC.
            route_tables = ec2.describe_route_tables(
                Filters=[{"Name": "vpc-id", "Values": [context.payload.resource_id]}]
            ).get("RouteTables", [])
            main_rt = next(
                (
                    rt
                    for rt in route_tables
                    if any(assoc.get("Main") for assoc in rt.get("Associations", []))
                ),
                None,
            )
            target_rt = main_rt or (route_tables[0] if route_tables else None)
            if not target_rt:
                raise HTTPException(
                    status_code=400,
                    detail="No route tables found for VPC. Provide route_table_ids.",
                )
            route_table_ids = [target_rt["RouteTableId"]]

        policy_document = params.get("policy_document") or params.get("policyDocument")
        tags = params.get("tags")

        create_params: Dict[str, Any] = {
            "VpcId": context.payload.resource_id,
            "ServiceName": service_name,
            "VpcEndpointType": "Gateway",
            "RouteTableIds": route_table_ids,
        }
        if policy_document:
            create_params["PolicyDocument"] = policy_document
        if isinstance(tags, list):
            create_params["TagSpecifications"] = [
                {"ResourceType": "vpc-endpoint", "Tags": tags}
            ]

        response = ec2.create_vpc_endpoint(**create_params)
        endpoint_id = response.get("VpcEndpoint", {}).get("VpcEndpointId")

        return CheckActionResponse(
            check_id=context.check_id,
            resource_id=context.payload.resource_id,
            action=context.payload.action,
            status="submitted",
            message="S3 VPC endpoint creation submitted",
            details={
                "vpc_endpoint_id": endpoint_id,
                "vpc_id": context.payload.resource_id,
                "region": context.payload.region,
                "service_name": service_name,
                "route_table_ids": route_table_ids,
                "response": response,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to create S3 VPC endpoint",
            extra={
                "check_id": context.check_id,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create S3 VPC endpoint: {str(exc)}",
        )


def handle_vpc_flow_logs_disabling(context: ActionExecutionContext) -> CheckActionResponse:
    """Disable VPC flow logs by deleting flow log resources for a VPC."""
    try:
        ec2 = context.aws_adapter.session.client("ec2", region_name=context.payload.region)

        # Verify VPC exists
        try:
            vpcs = ec2.describe_vpcs(VpcIds=[context.payload.resource_id])
            if not vpcs.get("Vpcs"):
                raise HTTPException(
                    status_code=404,
                    detail=f"VPC {context.payload.resource_id} not found",
                )
        except HTTPException:
            raise
        except Exception as verify_exc:
            raise HTTPException(
                status_code=404,
                detail=f"Failed to verify VPC: {str(verify_exc)}",
            )

        params = context.payload.parameters or {}
        flow_log_ids = _normalize_flow_log_ids(
            params.get("flow_log_ids")
            or params.get("flowLogIds")
            or params.get("flow_log_id")
            or params.get("flow_logs")
        )

        if not flow_log_ids:
            # Best-effort: delete all flow logs for the VPC.
            existing = ec2.describe_flow_logs(
                Filters=[{"Name": "resource-id", "Values": [context.payload.resource_id]}]
            )
            flow_log_ids = [
                fl.get("FlowLogId")
                for fl in existing.get("FlowLogs", [])
                if fl.get("FlowLogId")
            ]
            if not flow_log_ids:
                raise HTTPException(
                    status_code=404,
                    detail="No VPC flow logs found to disable.",
                )

        response = ec2.delete_flow_logs(FlowLogIds=flow_log_ids)

        return CheckActionResponse(
            check_id=context.check_id,
            resource_id=context.payload.resource_id,
            action=context.payload.action,
            status="submitted",
            message="VPC flow logs deletion submitted",
            details={
                "vpc_id": context.payload.resource_id,
                "region": context.payload.region,
                "flow_log_ids": flow_log_ids,
                "response": response,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to disable VPC flow logs",
            extra={
                "check_id": context.check_id,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to disable VPC flow logs: {str(exc)}",
        )


def _extract_s3_bucket_name(flow_log: Dict[str, Any]) -> Optional[str]:
    """Extract S3 bucket name from flow log destination."""
    dest_type = flow_log.get("LogDestinationType") or flow_log.get("log_destination_type")
    if dest_type and str(dest_type).lower() != "s3":
        return None

    dest = flow_log.get("LogDestination") or flow_log.get("log_destination")
    if not dest or not isinstance(dest, str):
        return None

    bucket_and_prefix = dest
    if dest.startswith("arn:") and ":::" in dest:  # arn:aws:s3:::bucket/prefix
        parts = dest.split(":::")
        if len(parts) > 1:
            bucket_and_prefix = parts[1]
    elif dest.startswith("s3://"):
        bucket_and_prefix = dest[5:]

    bucket = bucket_and_prefix.split("/", 1)[0].strip()
    return bucket or None


def _apply_bucket_expiration_policy(
    s3,
    bucket: str,
    expiration_days: int,
    rule_id: str,
) -> None:
    """Apply expiration policy to S3 bucket."""
    try:
        existing = s3.get_bucket_lifecycle_configuration(Bucket=bucket)
        rules = existing.get("Rules", [])
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code != "NoSuchLifecycleConfiguration":
            raise
        rules = []

    rules = [rule for rule in rules if rule.get("ID") != rule_id]
    rules.append(
        {
            "ID": rule_id,
            "Status": "Enabled",
            "Filter": {"Prefix": ""},
            "Expiration": {"Days": expiration_days},
        }
    )

    s3.put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration={"Rules": rules},
    )


def handle_vpc_flow_logs_disabling_with_bucket_expiration(context: ActionExecutionContext) -> CheckActionResponse:
    """Disable VPC flow logs and set S3 log expiration policy for any discovered log buckets."""
    try:
        ec2 = context.aws_adapter.session.client("ec2", region_name=context.payload.region)
        s3 = context.aws_adapter.session.client("s3", region_name=context.payload.region)

        # Verify VPC exists
        try:
            vpcs = ec2.describe_vpcs(VpcIds=[context.payload.resource_id])
            if not vpcs.get("Vpcs"):
                raise HTTPException(
                    status_code=404,
                    detail=f"VPC {context.payload.resource_id} not found",
                )
        except HTTPException:
            raise
        except Exception as verify_exc:
            raise HTTPException(
                status_code=404,
                detail=f"Failed to verify VPC: {str(verify_exc)}",
            )

        params = context.payload.parameters or {}
        flow_log_ids = _normalize_flow_log_ids(
            params.get("flow_log_ids")
            or params.get("flowLogIds")
            or params.get("flow_log_id")
            or params.get("flow_logs")
        )
        expiration_days = _normalize_int(
            params.get("expiration_days")
            or params.get("expirationDays")
            or params.get("lifecycle_days")
            or params.get("retention_days")
        )
        if not expiration_days or expiration_days <= 0:
            raise HTTPException(
                status_code=400,
                detail="expiration_days is required and must be a positive integer.",
            )

        flow_logs: List[Dict[str, Any]] = []
        if flow_log_ids:
            existing = ec2.describe_flow_logs(FlowLogIds=flow_log_ids)
            flow_logs = existing.get("FlowLogs", [])
        else:
            # Best-effort: delete all flow logs for the VPC.
            existing = ec2.describe_flow_logs(
                Filters=[{"Name": "resource-id", "Values": [context.payload.resource_id]}]
            )
            flow_logs = existing.get("FlowLogs", [])
            flow_log_ids = [
                fl.get("FlowLogId") for fl in flow_logs if fl.get("FlowLogId")
            ]

        if not flow_log_ids:
            raise HTTPException(
                status_code=404,
                detail="No VPC flow logs found to disable.",
            )

        rule_id = "maxops-vpc-flow-logs-expiration"
        bucket_names: Set[str] = set()
        for fl in flow_logs:
            bucket = _extract_s3_bucket_name(fl)
            if bucket:
                bucket_names.add(bucket)

        for bucket in sorted(bucket_names):
            _apply_bucket_expiration_policy(s3, bucket, expiration_days, rule_id)

        response = ec2.delete_flow_logs(FlowLogIds=flow_log_ids)

        return CheckActionResponse(
            check_id=context.check_id,
            resource_id=context.payload.resource_id,
            action=context.payload.action,
            status="submitted",
            message="VPC flow logs deletion submitted",
            details={
                "vpc_id": context.payload.resource_id,
                "region": context.payload.region,
                "flow_log_ids": flow_log_ids,
                "expiration_days": expiration_days,
                "lifecycle_rule_id": rule_id,
                "log_buckets": sorted(bucket_names),
                "response": response,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to disable VPC flow logs with bucket expiration",
            extra={
                "check_id": context.check_id,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to disable VPC flow logs: {str(exc)}",
        )


def _describe_all_flow_logs(ec2) -> List[Dict[str, Any]]:
    """Describe all flow logs, following the EC2 continuation token."""
    flow_logs: List[Dict[str, Any]] = []
    next_token: Optional[str] = None
    while True:
        request = {"NextToken": next_token} if next_token else {}
        response = ec2.describe_flow_logs(**request)
        flow_logs.extend(response.get("FlowLogs", []))
        next_token = response.get("NextToken")
        if not next_token:
            return flow_logs


def _delete_s3_objects(s3, bucket_name: str, objects: List[Dict[str, str]]) -> None:
    """Delete S3 objects in API-sized batches."""
    for start in range(0, len(objects), 1000):
        batch = objects[start:start + 1000]
        if batch:
            s3.delete_objects(
                Bucket=bucket_name,
                Delete={"Objects": batch, "Quiet": True},
            )


def _empty_and_delete_bucket(s3, bucket_name: str) -> None:
    """Delete all current and versioned objects before deleting a bucket."""
    versioned_objects: List[Dict[str, str]] = []
    versions = s3.get_paginator("list_object_versions")
    for page in versions.paginate(Bucket=bucket_name):
        for item in page.get("Versions", []) + page.get("DeleteMarkers", []):
            key = item.get("Key")
            version_id = item.get("VersionId")
            if key and version_id:
                versioned_objects.append({"Key": key, "VersionId": version_id})
    _delete_s3_objects(s3, bucket_name, versioned_objects)

    current_objects: List[Dict[str, str]] = []
    objects = s3.get_paginator("list_objects_v2")
    for page in objects.paginate(Bucket=bucket_name):
        current_objects.extend(
            {"Key": item["Key"]}
            for item in page.get("Contents", [])
            if item.get("Key")
        )
    _delete_s3_objects(s3, bucket_name, current_objects)
    s3.delete_bucket(Bucket=bucket_name)


def handle_vpc_flow_logs_disabling_with_bucket_deletion(
    context: ActionExecutionContext,
) -> CheckActionResponse:
    """Disable selected VPC flow logs and explicitly approved S3 destinations."""
    try:
        ec2 = context.aws_adapter.session.client(
            "ec2", region_name=context.payload.region
        )
        s3 = context.aws_adapter.session.client(
            "s3", region_name=context.payload.region
        )
        params = context.payload.parameters or {}
        bucket_names = _normalize_bucket_names(
            params.get("bucket_names") or params.get("bucketNames")
        )
        if not bucket_names:
            raise HTTPException(
                status_code=400,
                detail="bucket_names is required (string or list of strings).",
            )
        if params.get("confirm_delete_buckets") is not True:
            raise HTTPException(
                status_code=400,
                detail="confirm_delete_buckets=true is required.",
            )

        vpc_id = context.payload.resource_id
        try:
            if not ec2.describe_vpcs(VpcIds=[vpc_id]).get("Vpcs"):
                raise HTTPException(
                    status_code=404, detail=f"VPC {vpc_id} not found"
                )
        except HTTPException:
            raise
        except Exception as verify_exc:
            raise HTTPException(
                status_code=404,
                detail=f"Failed to verify VPC: {str(verify_exc)}",
            )

        flow_log_ids = _normalize_flow_log_ids(
            params.get("flow_log_ids")
            or params.get("flowLogIds")
            or params.get("flow_log_id")
            or params.get("flow_logs")
        )
        if flow_log_ids:
            selected_response = ec2.describe_flow_logs(FlowLogIds=flow_log_ids)
        else:
            selected_response = ec2.describe_flow_logs(
                Filters=[{"Name": "resource-id", "Values": [vpc_id]}]
            )

        selected_logs = selected_response.get("FlowLogs", [])
        selected_by_id = {
            item.get("FlowLogId"): item
            for item in selected_logs
            if item.get("FlowLogId")
        }
        if flow_log_ids:
            missing = [item for item in flow_log_ids if item not in selected_by_id]
            if missing:
                raise HTTPException(
                    status_code=404,
                    detail=f"Flow log(s) not found: {', '.join(missing)}",
                )
        else:
            flow_log_ids = list(selected_by_id)
        if not flow_log_ids:
            raise HTTPException(
                status_code=404, detail="No VPC flow logs found to disable."
            )

        wrong_vpc = [
            flow_log_id
            for flow_log_id, flow_log in selected_by_id.items()
            if flow_log.get("ResourceId") != vpc_id
        ]
        if wrong_vpc:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Selected flow logs do not belong to the requested VPC: "
                    + ", ".join(wrong_vpc)
                ),
            )

        selected_buckets = {
            bucket
            for bucket in (
                _extract_s3_bucket_name(flow_log)
                for flow_log in selected_by_id.values()
            )
            if bucket
        }
        unrelated_buckets = [
            bucket for bucket in bucket_names if bucket not in selected_buckets
        ]
        if unrelated_buckets:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Bucket(s) are not S3 destinations of the selected flow logs: "
                    + ", ".join(unrelated_buckets)
                ),
            )

        selected_id_set = set(flow_log_ids)
        shared_references: Dict[str, List[str]] = {}
        for flow_log in _describe_all_flow_logs(ec2):
            flow_log_id = flow_log.get("FlowLogId")
            if not flow_log_id or flow_log_id in selected_id_set:
                continue
            bucket = _extract_s3_bucket_name(flow_log)
            if bucket in bucket_names:
                shared_references.setdefault(bucket, []).append(flow_log_id)
        if shared_references:
            references = ", ".join(
                f"{bucket}: {', '.join(ids)}"
                for bucket, ids in sorted(shared_references.items())
            )
            raise HTTPException(
                status_code=409,
                detail=f"Bucket(s) are referenced by unselected flow logs: {references}",
            )

        ec2.delete_flow_logs(FlowLogIds=flow_log_ids)
        for bucket_name in bucket_names:
            _empty_and_delete_bucket(s3, bucket_name)

        return CheckActionResponse(
            check_id=context.check_id,
            resource_id=vpc_id,
            action=context.payload.action,
            status="submitted",
            message="VPC flow logs and approved destination buckets deleted",
            details={
                "vpc_id": vpc_id,
                "region": context.payload.region,
                "deleted_flow_log_ids": flow_log_ids,
                "deleted_bucket_names": bucket_names,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to disable VPC flow logs and delete destination buckets",
            extra={
                "check_id": context.check_id,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to disable VPC flow logs and delete destination buckets: "
                f"{str(exc)}"
            ),
        )
