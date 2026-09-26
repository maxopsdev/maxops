"""
ElastiCache test resource fixtures.

Creates ElastiCache clusters for testing various checks:
- Low item count clusters
- Non-Graviton instance types
- Redis clusters convertible to Valkey
"""

import logging
import time
from typing import List, Dict, Any
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class ElastiCacheTestResources:
    """Manages ElastiCache test resource lifecycle."""
    
    def __init__(self, session: boto3.Session, region: str):
        self.session = session
        self.region = region
        self.elasticache = session.client('elasticache', region_name=region)
        self.timestamp = int(time.time())
    
    def create(self) -> List[Dict[str, Any]]:
        """
        Create ElastiCache test clusters.
        
        Returns:
            List of created resource metadata dicts with keys:
            - resource_id: cache cluster/replication group ID
            - resource_type: 'elasticache_cluster' or 'elasticache_replication_group'
            - name: cluster name
            - node_type: cache node type
            - engine: redis or valkey
        """
        logger.info("Creating ElastiCache test clusters...")
        
        resources = []
        
        # Create standalone Redis cluster (low item count test)
        low_items_name = f"maxops-test-elasticache-low-items-{self.timestamp}"
        try:
            self.elasticache.create_cache_cluster(
                CacheClusterId=low_items_name,
                CacheNodeType="cache.t4g.micro",
                Engine="redis",
                NumCacheNodes=1,
                Tags=[
                    {"Key": "Name", "Value": low_items_name},
                    {"Key": "maxops_test", "Value": "true"},
                    {"Key": "maxops_test_timestamp", "Value": str(self.timestamp)},
                ],
            )
            resources.append({
                "resource_id": low_items_name,
                "resource_type": "elasticache_cluster",
                "name": low_items_name,
                "node_type": "cache.t4g.micro",
                "engine": "redis",
            })
            logger.info(f"Created ElastiCache cluster: {low_items_name}")
        except ClientError as e:
            logger.error(f"Failed to create ElastiCache cluster {low_items_name}: {e}")
        
        # Create non-Graviton Redis cluster
        non_graviton_name = f"maxops-test-elasticache-non-graviton-{self.timestamp}"
        try:
            self.elasticache.create_cache_cluster(
                CacheClusterId=non_graviton_name,
                CacheNodeType="cache.t3.micro",  # Non-Graviton
                Engine="redis",
                NumCacheNodes=1,
                Tags=[
                    {"Key": "Name", "Value": non_graviton_name},
                    {"Key": "maxops_test", "Value": "true"},
                    {"Key": "maxops_test_timestamp", "Value": str(self.timestamp)},
                ],
            )
            resources.append({
                "resource_id": non_graviton_name,
                "resource_type": "elasticache_cluster",
                "name": non_graviton_name,
                "node_type": "cache.t3.micro",
                "engine": "redis",
            })
            logger.info(f"Created ElastiCache cluster: {non_graviton_name}")
        except ClientError as e:
            logger.error(f"Failed to create ElastiCache cluster {non_graviton_name}: {e}")
        
        # Create Redis replication group (for Valkey conversion test)
        redis_valkey_name = f"maxops-test-elasticache-redis-valkey-{self.timestamp}"
        try:
            self.elasticache.create_replication_group(
                ReplicationGroupId=redis_valkey_name,
                ReplicationGroupDescription="MaxOps test Redis to Valkey conversion",
                CacheNodeType="cache.t4g.micro",
                Engine="redis",
                EngineVersion="6.2",
                NumCacheClusters=2,
                AutomaticFailoverEnabled=False,
                Tags=[
                    {"Key": "Name", "Value": redis_valkey_name},
                    {"Key": "maxops_test", "Value": "true"},
                    {"Key": "maxops_test_timestamp", "Value": str(self.timestamp)},
                ],
            )
            resources.append({
                "resource_id": redis_valkey_name,
                "resource_type": "elasticache_replication_group",
                "name": redis_valkey_name,
                "node_type": "cache.t4g.micro",
                "engine": "redis",
            })
            logger.info(f"Created ElastiCache replication group: {redis_valkey_name}")
        except ClientError as e:
            logger.error(f"Failed to create ElastiCache replication group {redis_valkey_name}: {e}")
        
        logger.info(f"Created {len(resources)} ElastiCache clusters/replication groups")
        return resources
    
    def wait_until_ready(self, resources: List[Dict[str, Any]]):
        """Wait for ElastiCache clusters to be available."""
        logger.info("Waiting for ElastiCache clusters to be available...")
        logger.info("This may take 5-15 minutes...")
        
        for resource in resources:
            resource_id = resource["resource_id"]
            resource_type = resource["resource_type"]
            
            try:
                if resource_type == "elasticache_cluster":
                    waiter = self.elasticache.get_waiter("cache_cluster_available")
                    waiter.wait(
                        CacheClusterId=resource_id,
                        WaiterConfig={'Delay': 30, 'MaxAttempts': 40}  # 20 minutes max
                    )
                    logger.info(f"ElastiCache cluster available: {resource_id}")
                else:  # replication_group
                    waiter = self.elasticache.get_waiter("replication_group_available")
                    waiter.wait(
                        ReplicationGroupId=resource_id,
                        WaiterConfig={'Delay': 30, 'MaxAttempts': 40}
                    )
                    logger.info(f"ElastiCache replication group available: {resource_id}")
            except Exception as e:
                logger.error(f"Error waiting for ElastiCache resource {resource_id}: {e}")
    
    def cleanup(self, resources: List[Dict[str, Any]]):
        """Delete ElastiCache test clusters and replication groups."""
        if not resources:
            return
        
        logger.info(f"Deleting ElastiCache resources...")
        
        for resource in resources:
            resource_id = resource["resource_id"]
            resource_type = resource["resource_type"]
            
            try:
                if resource_type == "elasticache_cluster":
                    self.elasticache.delete_cache_cluster(CacheClusterId=resource_id)
                    logger.info(f"Initiated deletion of ElastiCache cluster: {resource_id}")
                else:  # replication_group
                    self.elasticache.delete_replication_group(
                        ReplicationGroupId=resource_id,
                        RetainPrimaryCluster=False
                    )
                    logger.info(f"Initiated deletion of ElastiCache replication group: {resource_id}")
            except ClientError as e:
                logger.error(f"Failed to delete ElastiCache resource {resource_id}: {e}")
