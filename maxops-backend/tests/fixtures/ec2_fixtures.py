"""
EC2 test resource fixtures.

Creates EC2 instances for testing various checks:
- Idle instances (running but low CPU)
- Stopped instances (for unused checks)
"""

import logging
import time
from typing import List, Dict, Any
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class EC2TestResources:
    """Manages EC2 test resource lifecycle."""
    
    def __init__(self, session: boto3.Session, region: str):
        self.session = session
        self.region = region
        self.ec2 = session.client('ec2', region_name=region)
        self.ssm = session.client('ssm', region_name=region)
        self.timestamp = int(time.time())
    
    def _get_default_vpc_id(self) -> str:
        """Get default VPC ID."""
        vpcs = self.ec2.describe_vpcs(
            Filters=[{"Name": "isDefault", "Values": ["true"]}]
        ).get("Vpcs", [])
        if not vpcs:
            raise RuntimeError("No default VPC found")
        return vpcs[0]["VpcId"]
    
    def _get_default_subnet_id(self, vpc_id: str) -> str:
        """Get a subnet from the default VPC."""
        subnets = self.ec2.describe_subnets(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        ).get("Subnets", [])
        if not subnets:
            raise RuntimeError("No subnets found in default VPC")
        return subnets[0]["SubnetId"]
    
    def _get_default_security_group_id(self, vpc_id: str) -> str:
        """Get default security group ID."""
        sgs = self.ec2.describe_security_groups(
            Filters=[
                {"Name": "group-name", "Values": ["default"]},
                {"Name": "vpc-id", "Values": [vpc_id]},
            ]
        ).get("SecurityGroups", [])
        if not sgs:
            raise RuntimeError("No default security group found")
        return sgs[0]["GroupId"]
    
    def _get_latest_ami(self) -> str:
        """Get latest Amazon Linux 2 AMI."""
        param = self.ssm.get_parameter(
            Name="/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2"
        )
        return param["Parameter"]["Value"]
    
    def create(self) -> List[Dict[str, Any]]:
        """
        Create EC2 test instances.
        
        Returns:
            List of created resource metadata dicts with keys:
            - resource_id: instance ID
            - resource_type: 'ec2_instance'
            - name: instance name
            - state: 'running' or 'stopped'
        """
        logger.info("Creating EC2 test instances...")
        
        vpc_id = self._get_default_vpc_id()
        subnet_id = self._get_default_subnet_id(vpc_id)
        sg_id = self._get_default_security_group_id(vpc_id)
        ami_id = self._get_latest_ami()
        
        idle_name = f"maxops-test-ec2-idle-{self.timestamp}"
        stopped_name = f"maxops-test-ec2-stopped-{self.timestamp}"
        
        try:
            response = self.ec2.run_instances(
                ImageId=ami_id,
                InstanceType="t3.micro",
                MinCount=2,
                MaxCount=2,
                SubnetId=subnet_id,
                SecurityGroupIds=[sg_id],
                TagSpecifications=[
                    {
                        "ResourceType": "instance",
                        "Tags": [
                            {"Key": "Name", "Value": idle_name},
                            {"Key": "maxops_test", "Value": "true"},
                            {"Key": "maxops_test_timestamp", "Value": str(self.timestamp)},
                        ],
                    }
                ],
            )
            instance_ids = [inst["InstanceId"] for inst in response["Instances"]]
        except ClientError as e:
            logger.error(f"Failed to create EC2 instances: {e}")
            raise
        
        # Tag and stop one instance for "stopped instance" checks
        self.ec2.create_tags(
            Resources=[instance_ids[0]],
            Tags=[
                {"Key": "Name", "Value": stopped_name},
            ],
        )
        
        resources = [
            {
                "resource_id": instance_ids[1],
                "resource_type": "ec2_instance",
                "name": idle_name,
                "state": "running",
                "instance_type": "t3.micro",
            },
            {
                "resource_id": instance_ids[0],
                "resource_type": "ec2_instance",
                "name": stopped_name,
                "state": "to_be_stopped",
                "instance_type": "t3.micro",
            },
        ]
        
        logger.info(f"Created {len(resources)} EC2 instances: {instance_ids}")
        return resources
    
    def wait_until_ready(self, resources: List[Dict[str, Any]]):
        """Wait for EC2 instances to be running, then stop one."""
        instance_ids = [r["resource_id"] for r in resources]
        
        logger.info(f"Waiting for EC2 instances to be running: {instance_ids}")
        waiter = self.ec2.get_waiter("instance_running")
        waiter.wait(InstanceIds=instance_ids)
        logger.info("EC2 instances are running")
        
        # Stop one instance
        stopped_instance = [r for r in resources if r["state"] == "to_be_stopped"]
        if stopped_instance:
            instance_id = stopped_instance[0]["resource_id"]
            logger.info(f"Stopping EC2 instance: {instance_id}")
            self.ec2.stop_instances(InstanceIds=[instance_id])
            stopped_instance[0]["state"] = "stopped"
    
    def cleanup(self, resources: List[Dict[str, Any]]):
        """Terminate EC2 test instances."""
        instance_ids = [r["resource_id"] for r in resources]
        
        if not instance_ids:
            return
        
        logger.info(f"Terminating EC2 instances: {instance_ids}")
        
        try:
            self.ec2.terminate_instances(InstanceIds=instance_ids)
            logger.info(f"Terminated {len(instance_ids)} EC2 instances")
        except ClientError as e:
            logger.error(f"Failed to terminate EC2 instances: {e}")
            raise
