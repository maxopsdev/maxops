"""
S3 test resource fixtures.

Creates S3 buckets for testing various checks:
- Logging enabled/disabled buckets
- Inventory enabled/disabled buckets
- Buckets without lifecycle policies
- Buckets without expiration policies
- Buckets without archival policies
"""

import logging
import time
from typing import List, Dict, Any
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class S3TestResources:
    """Manages S3 test resource lifecycle."""
    
    def __init__(self, session: boto3.Session, region: str):
        self.session = session
        self.region = region
        self.s3 = session.client('s3', region_name=region)
        self.sts = session.client('sts')
        self.timestamp = int(time.time())
        self.account_id = self.sts.get_caller_identity()["Account"]
    
    def _create_bucket(self, name: str) -> str:
        """Create an S3 bucket."""
        try:
            # Check if bucket already exists
            self.s3.head_bucket(Bucket=name)
            return name
        except ClientError:
            pass
        
        # Create bucket
        try:
            if self.region == "us-east-1":
                self.s3.create_bucket(Bucket=name)
            else:
                self.s3.create_bucket(
                    Bucket=name,
                    CreateBucketConfiguration={"LocationConstraint": self.region},
                )
            self.s3.get_waiter("bucket_exists").wait(Bucket=name)
            logger.info(f"Created S3 bucket: {name}")
            return name
        except ClientError as e:
            logger.error(f"Failed to create bucket {name}: {e}")
            raise
    
    def create(self) -> List[Dict[str, Any]]:
        """
        Create S3 test buckets.
        
        Returns:
            List of created resource metadata dicts with keys:
            - resource_id: bucket name
            - resource_type: 's3_bucket'
            - name: bucket name
            - purpose: purpose of the bucket
        """
        logger.info("Creating S3 test buckets...")
        
        resources = []
        
        # Create buckets for various test scenarios
        buckets_to_create = [
            ("no-lifecycle", "Bucket without lifecycle policy"),
            ("no-expiration", "Bucket without expiration policy"),
            ("no-archival", "Bucket without archival policy"),
            ("no-mpu", "Bucket without MPU cleanup policy"),
            ("no-noncurrent-exp", "Bucket without noncurrent expiration"),
            ("logging-src", "Source bucket for logging test"),
            ("logging-target", "Target bucket for logging test"),
            ("inventory-src", "Source bucket for inventory test"),
            ("inventory-dest", "Destination bucket for inventory test"),
        ]
        
        for bucket_key, purpose in buckets_to_create:
            bucket_name = f"maxops-test-{bucket_key}-{self.timestamp}"
            
            try:
                self._create_bucket(bucket_name)
                
                # Enable versioning on some buckets
                if "noncurrent" in bucket_key or "expiration" in bucket_key:
                    self.s3.put_bucket_versioning(
                        Bucket=bucket_name,
                        VersioningConfiguration={"Status": "Enabled"}
                    )
                
                resources.append({
                    "resource_id": bucket_name,
                    "resource_type": "s3_bucket",
                    "name": bucket_name,
                    "purpose": purpose,
                })
            except Exception as e:
                logger.error(f"Failed to create bucket {bucket_name}: {e}")
                continue
        
        # Configure logging (source -> target)
        logging_src = [r for r in resources if "logging-src" in r["resource_id"]]
        logging_target = [r for r in resources if "logging-target" in r["resource_id"]]
        
        if logging_src and logging_target:
            try:
                src_bucket = logging_src[0]["resource_id"]
                target_bucket = logging_target[0]["resource_id"]
                
                # Set up logging
                self.s3.put_bucket_logging(
                    Bucket=src_bucket,
                    BucketLoggingStatus={
                        "LoggingEnabled": {
                            "TargetBucket": target_bucket,
                            "TargetPrefix": "logs/",
                        }
                    },
                )
                logger.info(f"Configured logging: {src_bucket} -> {target_bucket}")
            except Exception as e:
                logger.error(f"Failed to configure logging: {e}")
        
        logger.info(f"Created {len(resources)} S3 buckets")
        return resources
    
    def wait_until_ready(self, resources: List[Dict[str, Any]]):
        """S3 buckets are immediately ready after creation."""
        logger.info("S3 buckets are ready (no wait required)")
    
    def cleanup(self, resources: List[Dict[str, Any]]):
        """Delete S3 test buckets."""
        bucket_names = [r["resource_id"] for r in resources]
        
        if not bucket_names:
            return
        
        logger.info(f"Deleting S3 buckets: {bucket_names}")
        
        for bucket_name in bucket_names:
            try:
                # Empty the bucket first
                self._empty_bucket(bucket_name)
                
                # Delete the bucket
                self.s3.delete_bucket(Bucket=bucket_name)
                logger.info(f"Deleted S3 bucket: {bucket_name}")
            except ClientError as e:
                logger.error(f"Failed to delete bucket {bucket_name}: {e}")
    
    def _empty_bucket(self, bucket_name: str):
        """Empty a bucket by deleting all objects and versions."""
        try:
            # Check if versioning is enabled
            versioning = self.s3.get_bucket_versioning(Bucket=bucket_name)
            is_versioned = versioning.get('Status') == 'Enabled'
            
            if is_versioned:
                # Delete all versions and delete markers
                paginator = self.s3.get_paginator('list_object_versions')
                for page in paginator.paginate(Bucket=bucket_name):
                    objects_to_delete = []
                    
                    # Add versions
                    for version in page.get('Versions', []):
                        objects_to_delete.append({
                            'Key': version['Key'],
                            'VersionId': version['VersionId']
                        })
                    
                    # Add delete markers
                    for marker in page.get('DeleteMarkers', []):
                        objects_to_delete.append({
                            'Key': marker['Key'],
                            'VersionId': marker['VersionId']
                        })
                    
                    # Delete in batches
                    if objects_to_delete:
                        self.s3.delete_objects(
                            Bucket=bucket_name,
                            Delete={'Objects': objects_to_delete}
                        )
            else:
                # Delete all objects (non-versioned)
                paginator = self.s3.get_paginator('list_objects_v2')
                for page in paginator.paginate(Bucket=bucket_name):
                    if 'Contents' in page:
                        objects_to_delete = [{'Key': obj['Key']} for obj in page['Contents']]
                        
                        if objects_to_delete:
                            self.s3.delete_objects(
                                Bucket=bucket_name,
                                Delete={'Objects': objects_to_delete}
                            )
        except ClientError as e:
            logger.warning(f"Error emptying bucket {bucket_name}: {e}")
