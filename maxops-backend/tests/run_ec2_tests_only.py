#!/usr/bin/env python3
"""
Simple script to test just EC2 fixtures and checks.
This allows validating the test framework works before creating all resources.
"""

import sys
import os
import logging

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fixtures.ec2_fixtures import EC2TestResources
import boto3

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    profile = "maxops"
    region = "us-east-1"
    
    logger.info("=" * 80)
    logger.info("EC2 Test Framework Validation")
    logger.info("=" * 80)
    
    session = boto3.Session(profile_name=profile, region_name=region)
    ec2_manager = EC2TestResources(session, region)
    
    try:
        # Create EC2 resources
        logger.info("\n[1/3] Creating EC2 test instances...")
        resources = ec2_manager.create()
        logger.info(f"Created {len(resources)} EC2 instances")
        for r in resources:
            logger.info(f"  - {r['name']}: {r['resource_id']}")
        
        # Wait for resources
        logger.info("\n[2/3] Waiting for EC2 instances to be ready...")
        ec2_manager.wait_until_ready(resources)
        logger.info("All EC2 instances are ready")
        
        # Test a check (optional - requires database)
        logger.info("\n[3/3] Running EC2 idle instances check...")
        try:
            from app.database import SessionLocal
            from app.checks.ec2.idle_instances import check as check_ec2_idle
            
            db = SessionLocal()
            try:
                result = check_ec2_idle(
                    db=db,
                    account_id="123456789012",
                    region=region
                )
                logger.info(f"Check result: Found {result['resources_found']} idle instances")
                if result['resources_found'] > 0:
                    logger.info(f"  Instance IDs: {[r['resource_id'] for r in result['resources']]}")
            finally:
                db.close()
        except ImportError as e:
            logger.warning(f"Could not run check (database not initialized): {e}")
        except Exception as e:
            logger.error(f"Error running check: {e}")
        
        # Cleanup
        logger.info("\n[CLEANUP] Deleting EC2 test resources...")
        ec2_manager.cleanup(resources)
        logger.info("Cleanup complete")
        
        logger.info("\n" + "=" * 80)
        logger.info("✓ EC2 Test Framework Validation: SUCCESS")
        logger.info("=" * 80)
        logger.info("\nYou can now run the full test suite with:")
        logger.info("  pytest tests/integration/test_ec2_checks.py -v")
        
        return 0
        
    except Exception as e:
        logger.error(f"\n✗ Test failed: {e}", exc_info=True)
        
        # Attempt cleanup on error
        try:
            if 'resources' in locals():
                logger.info("\nAttempting cleanup after error...")
                ec2_manager.cleanup(resources)
        except Exception as cleanup_error:
            logger.error(f"Cleanup also failed: {cleanup_error}")
        
        return 1


if __name__ == '__main__':
    sys.exit(main())
