"""
RDS test resource fixtures.

Creates RDS instances for testing various checks:
- Idle RDS instances (low CPU/connections)
- Non-Graviton instances
"""

import logging
import time
from typing import List, Dict, Any
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class RDSTestResources:
    """Manages RDS test resource lifecycle."""
    
    def __init__(self, session: boto3.Session, region: str):
        self.session = session
        self.region = region
        self.rds = session.client('rds', region_name=region)
        self.ec2 = session.client('ec2', region_name=region)
        self.timestamp = int(time.time())
    
    def _get_default_vpc_id(self) -> str:
        """Get default VPC ID."""
        vpcs = self.ec2.describe_vpcs(
            Filters=[{"Name": "isDefault", "Values": ["true"]}]
        ).get("Vpcs", [])
        if not vpcs:
            raise RuntimeError("No default VPC found")
        return vpcs[0]["VpcId"]
    
    def _get_subnet_ids(self, vpc_id: str) -> List[str]:
        """Get subnet IDs from the default VPC."""
        subnets = self.ec2.describe_subnets(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        ).get("Subnets", [])
        if not subnets:
            raise RuntimeError("No subnets found in default VPC")
        return [s["SubnetId"] for s in subnets[:2]]  # Return at least 2 for DB subnet group
    
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
    
    def _create_db_subnet_group(self, vpc_id: str) -> str:
        """Create a DB subnet group for RDS instances."""
        subnet_ids = self._get_subnet_ids(vpc_id)
        group_name = f"maxops-test-db-subnet-{self.timestamp}"
        
        try:
            self.rds.create_db_subnet_group(
                DBSubnetGroupName=group_name,
                DBSubnetGroupDescription="MaxOps test DB subnet group",
                SubnetIds=subnet_ids,
                Tags=[
                    {"Key": "maxops_test", "Value": "true"},
                    {"Key": "maxops_test_timestamp", "Value": str(self.timestamp)},
                ]
            )
            logger.info(f"Created DB subnet group: {group_name}")
            return group_name
        except ClientError as e:
            if e.response['Error']['Code'] == 'DBSubnetGroupAlreadyExists':
                logger.info(f"DB subnet group already exists: {group_name}")
                return group_name
            raise
    
    def create(self) -> List[Dict[str, Any]]:
        """
        Create RDS test instances.
        
        Returns:
            List of created resource metadata dicts with keys:
            - resource_id: DB instance identifier
            - resource_type: 'rds_instance'
            - name: instance name
            - db_subnet_group: subnet group name
        """
        logger.info("Creating RDS test instances...")
        
        vpc_id = self._get_default_vpc_id()
        db_subnet_group = self._create_db_subnet_group(vpc_id)
        sg_id = self._get_default_security_group_id(vpc_id)
        
        idle_name = f"maxops-test-rds-idle-{self.timestamp}"
        non_graviton_name = f"maxops-test-rds-non-graviton-{self.timestamp}"
        
        resources = []
        
        # Create idle RDS instance (Graviton - db.t4g.micro)
        try:
            self.rds.create_db_instance(
                DBInstanceIdentifier=idle_name,
                DBInstanceClass="db.t4g.micro",
                Engine="mysql",
                MasterUsername="admin",
                MasterUserPassword="TestPassword123!",
                AllocatedStorage=20,
                DBSubnetGroupName=db_subnet_group,
                VpcSecurityGroupIds=[sg_id],
                BackupRetentionPeriod=0,  # No backups for test
                Tags=[
                    {"Key": "Name", "Value": idle_name},
                    {"Key": "maxops_test", "Value": "true"},
                    {"Key": "maxops_test_timestamp", "Value": str(self.timestamp)},
                ],
            )
            resources.append({
                "resource_id": idle_name,
                "resource_type": "rds_instance",
                "name": idle_name,
                "db_subnet_group": db_subnet_group,
                "instance_class": "db.t4g.micro",
            })
            logger.info(f"Created RDS instance: {idle_name}")
        except ClientError as e:
            logger.error(f"Failed to create RDS instance {idle_name}: {e}")
        
        # Create non-Graviton RDS instance (db.t3.micro)
        try:
            self.rds.create_db_instance(
                DBInstanceIdentifier=non_graviton_name,
                DBInstanceClass="db.t3.micro",
                Engine="mysql",
                MasterUsername="admin",
                MasterUserPassword="TestPassword123!",
                AllocatedStorage=20,
                DBSubnetGroupName=db_subnet_group,
                VpcSecurityGroupIds=[sg_id],
                BackupRetentionPeriod=0,
                Tags=[
                    {"Key": "Name", "Value": non_graviton_name},
                    {"Key": "maxops_test", "Value": "true"},
                    {"Key": "maxops_test_timestamp", "Value": str(self.timestamp)},
                ],
            )
            resources.append({
                "resource_id": non_graviton_name,
                "resource_type": "rds_instance",
                "name": non_graviton_name,
                "db_subnet_group": db_subnet_group,
                "instance_class": "db.t3.micro",
            })
            logger.info(f"Created RDS instance: {non_graviton_name}")
        except ClientError as e:
            logger.error(f"Failed to create RDS instance {non_graviton_name}: {e}")
        
        logger.info(f"Created {len(resources)} RDS instances")
        return resources
    
    def wait_until_ready(self, resources: List[Dict[str, Any]]):
        """Wait for RDS instances to be available."""
        instance_ids = [r["resource_id"] for r in resources]
        
        if not instance_ids:
            return
        
        logger.info(f"Waiting for RDS instances to be available: {instance_ids}")
        logger.info("This may take 5-10 minutes...")
        
        waiter = self.rds.get_waiter("db_instance_available")
        for instance_id in instance_ids:
            try:
                waiter.wait(
                    DBInstanceIdentifier=instance_id,
                    WaiterConfig={'Delay': 30, 'MaxAttempts': 40}  # 20 minutes max
                )
                logger.info(f"RDS instance available: {instance_id}")
            except Exception as e:
                logger.error(f"Error waiting for RDS instance {instance_id}: {e}")
    
    def cleanup(self, resources: List[Dict[str, Any]]):
        """Delete RDS test instances and subnet group."""
        instance_ids = [r["resource_id"] for r in resources]

        if not instance_ids:
            return

        logger.info(f"Deleting RDS instances: {instance_ids}")

        # Delete instances. An instance can still be in a transient,
        # non-deletable state (e.g. "configuring-enhanced-monitoring") right
        # after wait_until_ready() considers it available, so a single
        # attempt here can silently orphan a real, billable instance —
        # retry instead of giving up on the first InvalidDBInstanceState.
        for instance_id in instance_ids:
            self._delete_instance_with_retry(instance_id)

        # Delete DB subnet group after instances are deleted. Deletion is
        # async and can take several minutes, so retry instead of a single
        # fixed sleep-then-try (which reliably fails while instances are
        # still mid-deletion).
        if resources:
            db_subnet_group = resources[0].get("db_subnet_group")
            if db_subnet_group:
                self._delete_subnet_group_with_retry(db_subnet_group)

    def _delete_instance_with_retry(
        self, instance_id: str, max_attempts: int = 6, delay_seconds: int = 20
    ) -> None:
        for attempt in range(1, max_attempts + 1):
            try:
                self.rds.delete_db_instance(
                    DBInstanceIdentifier=instance_id,
                    SkipFinalSnapshot=True,
                    DeleteAutomatedBackups=True
                )
                logger.info(f"Initiated deletion of RDS instance: {instance_id}")
                return
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code == "InvalidDBInstanceState" and attempt < max_attempts:
                    logger.warning(
                        f"RDS instance {instance_id} not yet deletable "
                        f"(attempt {attempt}/{max_attempts}), retrying in "
                        f"{delay_seconds}s: {e}"
                    )
                    time.sleep(delay_seconds)
                    continue
                logger.error(f"Failed to delete RDS instance {instance_id}: {e}")
                return

    def _delete_subnet_group_with_retry(
        self, db_subnet_group: str, max_attempts: int = 6, delay_seconds: int = 20
    ) -> None:
        for attempt in range(1, max_attempts + 1):
            time.sleep(delay_seconds)
            try:
                self.rds.delete_db_subnet_group(DBSubnetGroupName=db_subnet_group)
                logger.info(f"Deleted DB subnet group: {db_subnet_group}")
                return
            except ClientError as e:
                if attempt < max_attempts:
                    logger.warning(
                        f"DB subnet group {db_subnet_group} not yet deletable "
                        f"(attempt {attempt}/{max_attempts}), retrying: {e}"
                    )
                    continue
                logger.warning(f"Failed to delete DB subnet group {db_subnet_group}: {e}")
