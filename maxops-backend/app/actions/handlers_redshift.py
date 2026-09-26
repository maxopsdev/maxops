"""Redshift action handlers."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.actions.base import ActionExecutionContext
from app.schemas.check import CheckActionResponse

logger = logging.getLogger("uvicorn.error")


def _normalize_snapshot_ids(value: Any) -> Optional[List[str]]:
    """Normalize snapshot IDs to a list of strings."""
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return None


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


def handle_redshift_delete_snapshot(context: ActionExecutionContext) -> CheckActionResponse:
    """Delete Redshift snapshots by snapshot identifier(s)."""
    try:
        redshift = context.aws_adapter.session.client("redshift", region_name=context.payload.region)

        params = context.payload.parameters or {}
        snapshot_ids = _normalize_snapshot_ids(
            params.get("snapshot_ids")
            or params.get("snapshotIds")
            or params.get("snapshot_id")
            or params.get("snapshotId")
        )

        if not snapshot_ids:
            raise HTTPException(
                status_code=400,
                detail="snapshot_ids is required (string or list of strings).",
            )

        deleted: List[Dict[str, Any]] = []
        for snapshot_id in snapshot_ids:
            try:
                response = redshift.delete_cluster_snapshot(
                    SnapshotIdentifier=snapshot_id
                )
                deleted.append(
                    {
                        "snapshot_id": snapshot_id,
                        "response": response,
                    }
                )
            except Exception as delete_exc:
                deleted.append(
                    {
                        "snapshot_id": snapshot_id,
                        "error": str(delete_exc),
                    }
                )

        return CheckActionResponse(
            check_id=context.check_id,
            resource_id=context.payload.resource_id,
            action=context.payload.action,
            status="submitted",
            message="Redshift snapshot deletion submitted",
            details={
                "region": context.payload.region,
                "snapshot_ids": snapshot_ids,
                "results": deleted,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to delete Redshift snapshots",
            extra={
                "check_id": context.check_id,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete Redshift snapshots: {str(exc)}",
        )


def handle_redshift_reduce_snapshot_retention(context: ActionExecutionContext) -> CheckActionResponse:
    """Reduce automated snapshot retention period for a Redshift cluster."""
    try:
        redshift = context.aws_adapter.session.client("redshift", region_name=context.payload.region)

        # Verify cluster exists
        try:
            clusters = redshift.describe_clusters(
                ClusterIdentifier=context.payload.resource_id
            ).get("Clusters", [])
            if not clusters:
                raise HTTPException(
                    status_code=404,
                    detail=f"Redshift cluster {context.payload.resource_id} not found",
                )
        except HTTPException:
            raise
        except Exception as verify_exc:
            raise HTTPException(
                status_code=404,
                detail=f"Failed to verify Redshift cluster: {str(verify_exc)}",
            )

        params = context.payload.parameters or {}
        retention_days = _normalize_int(
            params.get("retention_days")
            or params.get("retentionDays")
            or params.get("snapshot_retention_days")
            or params.get("snapshotRetentionDays")
            or params.get("automated_snapshot_retention_days")
            or params.get("automatedSnapshotRetentionDays")
        )

        if retention_days is None or retention_days < 0:
            raise HTTPException(
                status_code=400,
                detail="retention_days is required and must be 0 or a positive integer.",
            )

        modify_params: Dict[str, Any] = {
            "ClusterIdentifier": context.payload.resource_id,
            "AutomatedSnapshotRetentionPeriod": retention_days,
        }

        response = redshift.modify_cluster(**modify_params)

        return CheckActionResponse(
            check_id=context.check_id,
            resource_id=context.payload.resource_id,
            action=context.payload.action,
            status="submitted",
            message="Redshift snapshot retention update submitted",
            details={
                "cluster_id": context.payload.resource_id,
                "region": context.payload.region,
                "retention_days": retention_days,
                "response": response,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to update Redshift snapshot retention",
            extra={
                "check_id": context.check_id,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update Redshift snapshot retention: {str(exc)}",
        )


def handle_redshift_rightsize_cluster(context: ActionExecutionContext) -> CheckActionResponse:
    """Right-size a Redshift cluster by changing node type / count / cluster type."""
    try:
        redshift = context.aws_adapter.session.client("redshift", region_name=context.payload.region)

        # Verify cluster exists
        try:
            clusters = redshift.describe_clusters(
                ClusterIdentifier=context.payload.resource_id
            ).get("Clusters", [])
            if not clusters:
                raise HTTPException(
                    status_code=404,
                    detail=f"Redshift cluster {context.payload.resource_id} not found",
                )
        except HTTPException:
            raise
        except Exception as verify_exc:
            raise HTTPException(
                status_code=404,
                detail=f"Failed to verify Redshift cluster: {str(verify_exc)}",
            )

        params = context.payload.parameters or {}
        node_type = params.get("node_type") or params.get("nodeType")
        cluster_type = params.get("cluster_type") or params.get("clusterType")
        number_of_nodes = _normalize_int(
            params.get("number_of_nodes")
            or params.get("numberOfNodes")
            or params.get("node_count")
            or params.get("nodeCount")
        )
        apply_immediately = params.get("apply_immediately")
        if apply_immediately is None:
            apply_immediately = params.get("applyImmediately")

        if not node_type and not cluster_type and number_of_nodes is None:
            raise HTTPException(
                status_code=400,
                detail="Provide at least one of node_type, cluster_type, or number_of_nodes.",
            )

        modify_params: Dict[str, Any] = {
            "ClusterIdentifier": context.payload.resource_id,
        }
        if node_type:
            modify_params["NodeType"] = node_type
        if cluster_type:
            modify_params["ClusterType"] = cluster_type
        if number_of_nodes is not None:
            modify_params["NumberOfNodes"] = number_of_nodes
        if isinstance(apply_immediately, bool):
            modify_params["ApplyImmediately"] = apply_immediately

        response = redshift.modify_cluster(**modify_params)

        return CheckActionResponse(
            check_id=context.check_id,
            resource_id=context.payload.resource_id,
            action=context.payload.action,
            status="submitted",
            message="Redshift cluster right-size submitted",
            details={
                "cluster_id": context.payload.resource_id,
                "region": context.payload.region,
                "node_type": node_type,
                "cluster_type": cluster_type,
                "number_of_nodes": number_of_nodes,
                "apply_immediately": modify_params.get("ApplyImmediately"),
                "response": response,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to right-size Redshift cluster",
            extra={
                "check_id": context.check_id,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to right-size Redshift cluster: {str(exc)}",
        )
