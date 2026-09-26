"""
EBS test resource fixtures.

Creates EBS volumes for testing various checks:
- Unattached volumes
- Underutilized volumes
- IOPS over-provisioned volumes
"""

import logging
import time
from typing import List, Dict, Any
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class EBSTestResources:
    """Manages EBS test resource lifecycle."""
    
    def __init__(self, session: boto3.Session, region: str):
        self.session = session
        self.region = region
        self.ec2 = session.client('ec2', region_name=region)
        self.timestamp = int(time.time())
    
    def _get_availability_zone(self) -> str:
        """Get first availability zone in the region."""
        zones = self.ec2.describe_availability_zones()['AvailabilityZones']
        return zones[0]['ZoneName']
    
    def create(self) -> List[Dict[str, Any]]:
        """
        Create EBS test volumes.
        
        Returns:
            List of created resource metadata dicts with keys:
            - resource_id: volume ID
            - resource_type: 'ebs_volume'
            - name: volume name
            - volume_type: volume type (gp2, gp3, io1)
            - size_gb: volume size in GB
        """
        logger.info("Creating EBS test volumes...")
        
        az = self._get_availability_zone()
        resources = []
        
        volumes_to_create = [
            ("unattached", "gp2", 100, None),
            ("underutilized", "gp3", 200, None),
            ("iops-overprovisioned", "io1", 100, 10000),
        ]
        
        for vol_name, vol_type, size_gb, iops in volumes_to_create:
            name = f"maxops-test-ebs-{vol_name}-{self.timestamp}"
            
            try:
                params = {
                    "AvailabilityZone": az,
                    "Size": size_gb,
                    "VolumeType": vol_type,
                    "TagSpecifications": [
                        {
                            "ResourceType": "volume",
                            "Tags": [
                                {"Key": "Name", "Value": name},
                                {"Key": "maxops_test", "Value": "true"},
                                {"Key": "maxops_test_timestamp", "Value": str(self.timestamp)},
                            ],
                        }
                    ],
                }
                
                # Add IOPS for io1 volumes
                if vol_type == "io1" and iops:
                    params["Iops"] = iops
                
                response = self.ec2.create_volume(**params)
                volume_id = response["VolumeId"]
                
                resources.append({
                    "resource_id": volume_id,
                    "resource_type": "ebs_volume",
                    "name": name,
                    "volume_type": vol_type,
                    "size_gb": size_gb,
                    "iops": iops,
                })
                logger.info(f"Created EBS volume: {volume_id} ({name})")
            except ClientError as e:
                logger.error(f"Failed to create EBS volume {name}: {e}")
        
        logger.info(f"Created {len(resources)} EBS volumes")
        return resources
    
    def wait_until_ready(self, resources: List[Dict[str, Any]]):
        """Wait for EBS volumes to be available."""
        volume_ids = [r["resource_id"] for r in resources]
        
        if not volume_ids:
            return
        
        logger.info(f"Waiting for EBS volumes to be available: {volume_ids}")
        waiter = self.ec2.get_waiter("volume_available")
        waiter.wait(VolumeIds=volume_ids)
        logger.info("EBS volumes are available")
    
    def cleanup(self, resources: List[Dict[str, Any]]):
        """Delete EBS test volumes."""
        volume_ids = [r["resource_id"] for r in resources]
        
        if not volume_ids:
            return
        
        logger.info(f"Deleting EBS volumes: {volume_ids}")
        
        for volume_id in volume_ids:
            try:
                self.ec2.delete_volume(VolumeId=volume_id)
                logger.info(f"Deleted EBS volume: {volume_id}")
            except ClientError as e:
                logger.error(f"Failed to delete EBS volume {volume_id}: {e}")
