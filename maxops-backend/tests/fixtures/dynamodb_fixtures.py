"""
DynamoDB test resource fixtures.

Creates DynamoDB tables for testing various checks:
- Tables with unused GSIs
- Underutilized RCU/WCU tables
- Tables with low item count
- Best fit provisioned/on-demand tables
"""

import logging
import time
from typing import List, Dict, Any
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class DynamoDBTestResources:
    """Manages DynamoDB test resource lifecycle."""
    
    def __init__(self, session: boto3.Session, region: str):
        self.session = session
        self.region = region
        self.dynamodb = session.client('dynamodb', region_name=region)
        self.timestamp = int(time.time())
    
    def create(self) -> List[Dict[str, Any]]:
        """
        Create DynamoDB test tables.
        
        Returns:
            List of created resource metadata dicts with keys:
            - resource_id: table name
            - resource_type: 'dynamodb_table'
            - name: table name
            - billing_mode: PROVISIONED or PAY_PER_REQUEST
        """
        logger.info("Creating DynamoDB test tables...")
        
        resources = []
        
        # Table 1: Provisioned table with unused GSI
        gsi_unused_name = f"maxops_test-ddb-gsi-unused-{self.timestamp}"
        try:
            self.dynamodb.create_table(
                TableName=gsi_unused_name,
                KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
                AttributeDefinitions=[
                    {"AttributeName": "id", "AttributeType": "S"},
                    {"AttributeName": "gsi_key", "AttributeType": "S"},
                ],
                BillingMode="PROVISIONED",
                ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                GlobalSecondaryIndexes=[
                    {
                        "IndexName": "gsi1",
                        "KeySchema": [{"AttributeName": "gsi_key", "KeyType": "HASH"}],
                        "Projection": {"ProjectionType": "ALL"},
                        "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                    }
                ],
                Tags=[
                    {"Key": "Name", "Value": gsi_unused_name},
                    {"Key": "maxops_test", "Value": "true"},
                    {"Key": "maxops_test_timestamp", "Value": str(self.timestamp)},
                ],
            )
            resources.append({
                "resource_id": gsi_unused_name,
                "resource_type": "dynamodb_table",
                "name": gsi_unused_name,
                "billing_mode": "PROVISIONED",
            })
            logger.info(f"Created DynamoDB table: {gsi_unused_name}")
        except ClientError as e:
            logger.error(f"Failed to create DynamoDB table {gsi_unused_name}: {e}")
        
        # Table 2: Underutilized RCU table
        underutil_rcu_name = f"maxops_test-ddb-underutil-rcu-{self.timestamp}"
        try:
            self.dynamodb.create_table(
                TableName=underutil_rcu_name,
                KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
                BillingMode="PROVISIONED",
                ProvisionedThroughput={"ReadCapacityUnits": 10, "WriteCapacityUnits": 5},
                Tags=[
                    {"Key": "Name", "Value": underutil_rcu_name},
                    {"Key": "maxops_test", "Value": "true"},
                    {"Key": "maxops_test_timestamp", "Value": str(self.timestamp)},
                ],
            )
            resources.append({
                "resource_id": underutil_rcu_name,
                "resource_type": "dynamodb_table",
                "name": underutil_rcu_name,
                "billing_mode": "PROVISIONED",
            })
            logger.info(f"Created DynamoDB table: {underutil_rcu_name}")
        except ClientError as e:
            logger.error(f"Failed to create DynamoDB table {underutil_rcu_name}: {e}")
        
        # Table 3: Underutilized WCU table
        underutil_wcu_name = f"maxops_test-ddb-underutil-wcu-{self.timestamp}"
        try:
            self.dynamodb.create_table(
                TableName=underutil_wcu_name,
                KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
                BillingMode="PROVISIONED",
                ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 10},
                Tags=[
                    {"Key": "Name", "Value": underutil_wcu_name},
                    {"Key": "maxops_test", "Value": "true"},
                    {"Key": "maxops_test_timestamp", "Value": str(self.timestamp)},
                ],
            )
            resources.append({
                "resource_id": underutil_wcu_name,
                "resource_type": "dynamodb_table",
                "name": underutil_wcu_name,
                "billing_mode": "PROVISIONED",
            })
            logger.info(f"Created DynamoDB table: {underutil_wcu_name}")
        except ClientError as e:
            logger.error(f"Failed to create DynamoDB table {underutil_wcu_name}: {e}")
        
        # Table 4: Low item count table (on-demand)
        low_items_name = f"maxops_test-ddb-low-items-{self.timestamp}"
        try:
            self.dynamodb.create_table(
                TableName=low_items_name,
                KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
                Tags=[
                    {"Key": "Name", "Value": low_items_name},
                    {"Key": "maxops_test", "Value": "true"},
                    {"Key": "maxops_test_timestamp", "Value": str(self.timestamp)},
                ],
            )
            resources.append({
                "resource_id": low_items_name,
                "resource_type": "dynamodb_table",
                "name": low_items_name,
                "billing_mode": "PAY_PER_REQUEST",
            })
            logger.info(f"Created DynamoDB table: {low_items_name}")
        except ClientError as e:
            logger.error(f"Failed to create DynamoDB table {low_items_name}: {e}")
        
        logger.info(f"Created {len(resources)} DynamoDB tables")
        return resources
    
    def wait_until_ready(self, resources: List[Dict[str, Any]]):
        """Wait for DynamoDB tables to be active."""
        table_names = [r["resource_id"] for r in resources]
        
        if not table_names:
            return
        
        logger.info(f"Waiting for DynamoDB tables to be active: {table_names}")
        waiter = self.dynamodb.get_waiter("table_exists")
        
        for table_name in table_names:
            try:
                waiter.wait(TableName=table_name)
                logger.info(f"DynamoDB table active: {table_name}")
            except Exception as e:
                logger.error(f"Error waiting for DynamoDB table {table_name}: {e}")
    
    def cleanup(self, resources: List[Dict[str, Any]]):
        """Delete DynamoDB test tables."""
        table_names = [r["resource_id"] for r in resources]
        
        if not table_names:
            return
        
        logger.info(f"Deleting DynamoDB tables: {table_names}")
        
        for table_name in table_names:
            try:
                self.dynamodb.delete_table(TableName=table_name)
                logger.info(f"Deleted DynamoDB table: {table_name}")
            except ClientError as e:
                logger.error(f"Failed to delete DynamoDB table {table_name}: {e}")
