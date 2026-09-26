"""
EBS Snapshot test resource fixtures.

Creates EBS snapshots for testing snapshot-related checks:
- Old snapshots (for snapshot_old_snapshots check)
"""

import logging
import time
from typing import List, Dict, Any
import boto3
from botocore.exceptions import ClientError
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class SnapshotTestResources:
    """Manages EBS Snapshot test resource lifecycle."""
    
    def __init__(self, session: boto3.Session, region: str):
        self.session = session
        self.region = region
        self.ec2 = session.client('ec2', region_name=region)
        self.timestamp = int(time.time())
    
    def create(self, ebs_volumes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Create EBS snapshot test resources.
        
        Args:
            ebs_volumes: List of EBS volumes created by EBS fixtures
        
        Returns:
            List of created resource metadata dicts with keys:
            - resource_id: snapshot ID
            - resource_type: 'ebs_snapshot'
            - name: snapshot name
            - volume_id: source volume ID
        """
        logger.info("Creating EBS snapshot test resources...")
        
        if not ebs_volumes:
            logger.warning("No EBS volumes provided, skipping snapshot creation")
            return []
        
        resources = []
        
        # Create snapshots from the first few EBS volumes
        for idx, volume in enumerate(ebs_volumes[:2]):  # Create 2 snapshots
            volume_id = volume["resource_id"]
            snapshot_name = f"maxops-test-snapshot-{idx}-{self.timestamp}"
            
            try:
                response = self.ec2.create_snapshot(
                    VolumeId=volume_id,
                    Description=f"MaxOps test snapshot {idx}",
                    TagSpecifications=[
                        {
                            "ResourceType": "snapshot",
                            "Tags": [
                                {"Key": "Name", "Value": snapshot_name},
                                {"Key": "maxops_test", "Value": "true"},
                                {"Key": "maxops_test_timestamp", "Value": str(self.timestamp)},
                            ],
                        }
                    ],
                )
                snapshot_id = response["SnapshotId"]
                
                resources.append({
                    "resource_id": snapshot_id,
                    "resource_type": "ebs_snapshot",
                    "name": snapshot_name,
                    "volume_id": volume_id,
                })
                logger.info(f"Created EBS snapshot: {snapshot_id} from volume {volume_id}")
            except ClientError as e:
                logger.error(f"Failed to create snapshot from volume {volume_id}: {e}")
        
        logger.info(f"Created {len(resources)} EBS snapshots")
        return resources
    
    def wait_until_ready(self, resources: List[Dict[str, Any]]):
        """Wait for EBS snapshots to be completed."""
        snapshot_ids = [r["resource_id"] for r in resources]
        
        if not snapshot_ids:
            return
        
        logger.info(f"Waiting for EBS snapshots to be completed: {snapshot_ids}")
        logger.info("This may take 1-5 minutes depending on volume size...")
        
        waiter = self.ec2.get_waiter("snapshot_completed")
        for snapshot_id in snapshot_ids:
            try:
                waiter.wait(
                    SnapshotIds=[snapshot_id],
                    WaiterConfig={'Delay': 15, 'MaxAttempts': 40}  # 10 minutes max
                )
                logger.info(f"EBS snapshot completed: {snapshot_id}")
            except Exception as e:
                logger.error(f"Error waiting for snapshot {snapshot_id}: {e}")
    
    def cleanup(self, resources: List[Dict[str, Any]]):
        """Delete EBS snapshot test resources."""
        snapshot_ids = [r["resource_id"] for r in resources]
        
        if not snapshot_ids:
            return
        
        logger.info(f"Deleting EBS snapshots: {snapshot_ids}")
        
        for snapshot_id in snapshot_ids:
            try:
                self.ec2.delete_snapshot(SnapshotId=snapshot_id)
                logger.info(f"Deleted EBS snapshot: {snapshot_id}")
            except ClientError as e:
                logger.error(f"Failed to delete EBS snapshot {snapshot_id}: {e}")
