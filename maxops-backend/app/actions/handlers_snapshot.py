"""Snapshot action handlers."""
from __future__ import annotations

import logging
from fastapi import HTTPException
from app.schemas.check import CheckActionResponse
from app.actions.base import ActionExecutionContext

logger = logging.getLogger("uvicorn.error")


def handle_delete_snapshot(context: ActionExecutionContext):
    """Delete an EBS snapshot."""
    try:
        ec2 = context.aws_adapter.session.client("ec2", region_name=context.payload.region)
    
        # Verify the snapshot exists before attempting deletion
        try:
            snapshots = ec2.describe_snapshots(SnapshotIds=[context.payload.resource_id])
            if not snapshots.get('Snapshots'):
                raise HTTPException(
                    status_code=404,
                    detail=f"Snapshot {context.payload.resource_id} not found"
                )
        except Exception as verify_exc:
            raise HTTPException(
                status_code=404,
                detail=f"Failed to verify snapshot: {str(verify_exc)}"
            )
    
        # Delete the snapshot
        response = ec2.delete_snapshot(SnapshotId=context.payload.resource_id)
    
        return CheckActionResponse(
            check_id=context.check_id,
            resource_id=context.payload.resource_id,
            action=context.payload.action,
            status="success",
            message=f"Snapshot {context.payload.resource_id} deleted successfully",
            details={
                "snapshot_id": context.payload.resource_id,
                "region": context.payload.region,
                "response": response
            }
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete snapshot: {str(exc)}"
        )
