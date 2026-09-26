"""
pytest configuration and shared fixtures for MaxOps testing.

This module provides session-scoped fixtures that:
1. Create all AWS test resources in parallel at the start of test session
2. Wait for all resources to be ready
3. Clean up all created resources at the end, even if tests fail

WARNING FOR HUMANS AND LLM AGENTS: `test_resources` below (and everything
under tests/integration/ that depends on it) provisions REAL, BILLABLE AWS
resources — EC2, RDS, S3, EBS, ElastiCache, DynamoDB — via the `maxops` AWS
profile. It is not a simulator/mock.

Do not run this suite via a broad or keyword-based pytest invocation (e.g.
`pytest tests/ -k "ec2 or scan"`). Keyword filters match on test path/class/
function names, not markers — a generic keyword like "ec2" will pull in
tests/integration/test_ec2_*.py and silently trigger real resource creation.
This has happened before and left orphaned billable AWS resources (RDS
instances stuck mid-teardown, etc.) that required manual cleanup.

Two independent gates protect against this, and both must be cleared on
purpose, never incidentally:
1. `-m "not integration"` is the default in pytest.ini, so a bare `pytest`
   or `pytest -k ...` run skips anything tagged `integration` (this file
   auto-tags everything under tests/integration/ via
   pytest_collection_modifyitems below, as a backstop for files that don't
   set `pytestmark` themselves).
2. `test_resources` additionally requires MAXOPS_RUN_AWS_INTEGRATION_TESTS=1
   in the environment, and skips with a clear message otherwise.

If you need to run integration tests, scope the pytest invocation to the
specific test file/id you intend to run, pass `-m integration` explicitly,
and set MAXOPS_RUN_AWS_INTEGRATION_TESTS=1 — don't just remove these checks.
"""

import os
import pytest
import boto3
import time
import logging
from typing import Dict, List, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from tests.fixtures.ec2_fixtures import EC2TestResources
from tests.fixtures.rds_fixtures import RDSTestResources
from tests.fixtures.s3_fixtures import S3TestResources
from tests.fixtures.ebs_fixtures import EBSTestResources
from tests.fixtures.elasticache_fixtures import ElastiCacheTestResources
from tests.fixtures.dynamodb_fixtures import DynamoDBTestResources
from tests.fixtures.snapshot_fixtures import SnapshotTestResources

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

AWS_INTEGRATION_ENV_VAR = "MAXOPS_RUN_AWS_INTEGRATION_TESTS"


def _builds_real_aws_resources(item) -> bool:
    """Whether a test's module can construct real AWS resources.

    Path is not enough. tests/test_ec2_only.py sat directly in tests/, imported
    EC2TestResources, built its own class-scoped fixture around it, and so
    slipped past both the tests/integration/ tag and the
    MAXOPS_RUN_AWS_INTEGRATION_TESTS gate on `test_resources` -- launching real
    t3.micro instances on every full-suite run. This tags by what a module can
    reach instead, so a new file in the same shape is caught on arrival.
    """
    module = getattr(item, "module", None)
    if module is None:
        return False
    return any(
        name.endswith("TestResources") and isinstance(value, type)
        for name, value in vars(module).items()
    )


def pytest_collection_modifyitems(config, items):
    """Backstop-tag anything that can reach real AWS as `integration`.

    Several integration test modules already set `pytestmark =
    [pytest.mark.integration, ...]` themselves; this covers the ones that
    don't, so the default `-m "not integration"` (see pytest.ini) reliably
    excludes anything that can create billable resources, regardless of how
    tests are selected (by keyword, by path, etc).
    """
    for item in items:
        in_integration_dir = "tests/integration/" in str(item.fspath).replace("\\", "/")
        if in_integration_dir or _builds_real_aws_resources(item):
            item.add_marker(pytest.mark.integration)


class TestResourceManager:
    """Manages creation and cleanup of all AWS test resources."""
    
    def __init__(self, profile: str = "maxops", region: str = "us-east-1"):
        self.profile = profile
        self.region = region
        self.session = boto3.Session(profile_name=profile, region_name=region)
        
        # Initialize resource managers
        self.ec2_resources = EC2TestResources(self.session, region)
        self.rds_resources = RDSTestResources(self.session, region)
        self.s3_resources = S3TestResources(self.session, region)
        self.ebs_resources = EBSTestResources(self.session, region)
        self.elasticache_resources = ElastiCacheTestResources(self.session, region)
        self.dynamodb_resources = DynamoDBTestResources(self.session, region)
        self.snapshot_resources = SnapshotTestResources(self.session, region)
        
        # Track created resources for cleanup
        self.created_resources = {
            'ec2': [],
            'rds': [],
            's3': [],
            'ebs': [],
            'elasticache': [],
            'dynamodb': [],
            'snapshots': []
        }
    
    def create_all_resources(self) -> Dict[str, Any]:
        """
        Create all test resources in parallel.
        Returns a dict of all created resources.
        """
        logger.info("=" * 80)
        logger.info("Starting test resource creation in parallel...")
        logger.info("=" * 80)
        
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=7) as executor:
            futures = {
                executor.submit(self._create_ec2): 'ec2',
                executor.submit(self._create_rds): 'rds',
                executor.submit(self._create_s3): 's3',
                executor.submit(self._create_ebs): 'ebs',
                executor.submit(self._create_elasticache): 'elasticache',
                executor.submit(self._create_dynamodb): 'dynamodb',
            }
            
            # Wait for all creations to complete
            for future in as_completed(futures):
                resource_type = futures[future]
                try:
                    result = future.result()
                    self.created_resources[resource_type] = result
                    logger.info(f"✓ {resource_type.upper()} resources created: {len(result)} items")
                except Exception as e:
                    logger.error(f"✗ Failed to create {resource_type} resources: {e}")
                    self.created_resources[resource_type] = []
        
        # Create snapshots after EBS volumes are ready (dependency)
        if self.created_resources['ebs']:
            logger.info("Creating EBS snapshots (depends on EBS volumes)...")
            try:
                snapshots = self._create_snapshots()
                self.created_resources['snapshots'] = snapshots
                logger.info(f"✓ SNAPSHOT resources created: {len(snapshots)} items")
            except Exception as e:
                logger.error(f"✗ Failed to create snapshot resources: {e}")
                self.created_resources['snapshots'] = []
        
        # Wait for all resources to be in ready state
        logger.info("\nWaiting for all resources to be ready...")
        self._wait_for_all_resources()
        
        elapsed = time.time() - start_time
        logger.info("=" * 80)
        logger.info(f"All test resources ready! (took {elapsed:.1f} seconds)")
        logger.info("=" * 80)
        
        return self.created_resources
    
    def _create_ec2(self) -> List[Dict[str, Any]]:
        """Create EC2 test instances."""
        return self.ec2_resources.create()
    
    def _create_rds(self) -> List[Dict[str, Any]]:
        """Create RDS test instances."""
        return self.rds_resources.create()
    
    def _create_s3(self) -> List[Dict[str, Any]]:
        """Create S3 test buckets."""
        return self.s3_resources.create()
    
    def _create_ebs(self) -> List[Dict[str, Any]]:
        """Create EBS test volumes."""
        return self.ebs_resources.create()
    
    def _create_elasticache(self) -> List[Dict[str, Any]]:
        """Create ElastiCache test clusters."""
        return self.elasticache_resources.create()
    
    def _create_dynamodb(self) -> List[Dict[str, Any]]:
        """Create DynamoDB test tables."""
        return self.dynamodb_resources.create()
    
    def _create_snapshots(self) -> List[Dict[str, Any]]:
        """Create EBS snapshot test resources."""
        return self.snapshot_resources.create(self.created_resources['ebs'])
    
    def _wait_for_all_resources(self):
        """Wait for all resources to reach ready state."""
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            
            if self.created_resources['ec2']:
                futures.append(executor.submit(self.ec2_resources.wait_until_ready, 
                                              self.created_resources['ec2']))
            if self.created_resources['rds']:
                futures.append(executor.submit(self.rds_resources.wait_until_ready, 
                                              self.created_resources['rds']))
            if self.created_resources['elasticache']:
                futures.append(executor.submit(self.elasticache_resources.wait_until_ready, 
                                              self.created_resources['elasticache']))
            if self.created_resources['snapshots']:
                futures.append(executor.submit(self.snapshot_resources.wait_until_ready, 
                                              self.created_resources['snapshots']))
            
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.warning(f"Warning during wait: {e}")
    
    def cleanup_all_resources(self):
        """
        Clean up all created resources.
        Uses strict pairing - only deletes what this test run created.
        """
        logger.info("=" * 80)
        logger.info("Starting test resource cleanup...")
        logger.info("=" * 80)
        
        # Cleanup in reverse order of dependencies
        cleanup_order = [
            ('snapshots', self.snapshot_resources),
            ('elasticache', self.elasticache_resources),
            ('rds', self.rds_resources),
            ('ec2', self.ec2_resources),
            ('ebs', self.ebs_resources),
            ('dynamodb', self.dynamodb_resources),
            ('s3', self.s3_resources),  # S3 last (may contain logs from other resources)
        ]
        
        for resource_type, manager in cleanup_order:
            resources = self.created_resources.get(resource_type, [])
            if resources:
                try:
                    logger.info(f"Cleaning up {resource_type.upper()} resources ({len(resources)} items)...")
                    manager.cleanup(resources)
                    logger.info(f"✓ {resource_type.upper()} resources cleaned up")
                except Exception as e:
                    logger.error(f"✗ Failed to cleanup {resource_type} resources: {e}")
        
        logger.info("=" * 80)
        logger.info("Test resource cleanup complete!")
        logger.info("=" * 80)


@pytest.fixture(scope="session")
def aws_profile():
    """AWS profile to use for testing."""
    return "maxops"


@pytest.fixture(scope="session")
def aws_region():
    """AWS region to use for testing."""
    return "us-east-1"


@pytest.fixture(scope="session")
def test_resources(aws_profile, aws_region):
    """
    Session-scoped fixture that creates all AWS test resources.
    
    Resources are created once at the start of the test session and
    cleaned up at the end, even if tests fail.

    Gated behind MAXOPS_RUN_AWS_INTEGRATION_TESTS=1 because this creates
    real, billable AWS resources (EC2, RDS, S3, EBS, ElastiCache, DynamoDB)
    under the `maxops` profile — see the module warning above.
    """
    if os.environ.get(AWS_INTEGRATION_ENV_VAR) != "1":
        pytest.skip(
            f"Set {AWS_INTEGRATION_ENV_VAR}=1 to run AWS integration tests. "
            "This fixture provisions REAL, BILLABLE AWS resources (EC2, RDS, "
            "S3, EBS, ElastiCache, DynamoDB) under the 'maxops' profile — "
            "never enable this for a broad/keyword pytest run; scope it to "
            "the specific integration test(s) you intend to run."
        )
    manager = TestResourceManager(profile=aws_profile, region=aws_region)
    
    # Setup: Create all resources
    resources = manager.create_all_resources()
    
    # Yield resources to tests
    yield resources
    
    # Teardown: Clean up all resources
    manager.cleanup_all_resources()


@pytest.fixture(scope="session")
def ec2_resources(test_resources):
    """EC2 test instances."""
    return test_resources['ec2']


@pytest.fixture(scope="session")
def rds_resources(test_resources):
    """RDS test instances."""
    return test_resources['rds']


@pytest.fixture(scope="session")
def s3_resources(test_resources):
    """S3 test buckets."""
    return test_resources['s3']


@pytest.fixture(scope="session")
def ebs_resources(test_resources):
    """EBS test volumes."""
    return test_resources['ebs']


@pytest.fixture(scope="session")
def elasticache_resources(test_resources):
    """ElastiCache test clusters."""
    return test_resources['elasticache']


@pytest.fixture(scope="session")
def dynamodb_resources(test_resources):
    """DynamoDB test tables."""
    return test_resources['dynamodb']


@pytest.fixture(scope="session")
def snapshot_resources(test_resources):
    """EBS snapshot test resources."""
    return test_resources['snapshots']


# ---------------------------------------------------------------------------
# Database and API fixtures
#
# These create no AWS resources and cost nothing. They back the policy service
# and policy API tests, which had been erroring at setup because the fixtures
# they ask for were never defined anywhere in the suite.
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session():
    """An isolated in-memory database with the full schema."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import Base
    import app.models  # noqa: F401 - registers every model on Base.metadata

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        # One shared in-memory database for the whole fixture, so the API's
        # session and the test's session see the same rows.
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(db_session):
    """FastAPI test client wired to the same database as `db_session`."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def sample_policy_data():
    """Payload accepted by both PolicyService.create_policy and the API."""
    return {
        "name": "Idle EC2 instances",
        "description": "Flag EC2 instances that have been idle.",
        "resource_type": "ec2",
        "check_id": "ec2_idle_instances",
        "parameters_json": {"cpu_threshold": 5.0},
        "filters_json": [{"field": "state", "operator": "eq", "value": "running"}],
        "status": "active",
    }


@pytest.fixture
def sample_policy(db_session, sample_policy_data):
    from app.schemas.policy import PolicyCreate
    from app.services.policy_service import PolicyService

    # The service takes the Pydantic schema; the API takes the raw dict.
    return PolicyService(db_session).create_policy(PolicyCreate(**sample_policy_data))


@pytest.fixture
def sample_policies(db_session, sample_policy_data):
    from app.schemas.policy import PolicyCreate
    from app.services.policy_service import PolicyService

    service = PolicyService(db_session)
    created = []
    for index, status in enumerate(("active", "active", "inactive")):
        payload = dict(sample_policy_data)
        payload["name"] = f"{sample_policy_data['name']} {index}"
        payload["status"] = status
        created.append(service.create_policy(PolicyCreate(**payload)))
    return created


@pytest.fixture
def onboarded(db_session):
    """Minimal completed onboarding.

    Policy execution goes through `require_account_region`, which refuses to
    run before onboarding is complete. Tests that exercise execution need an
    account and region on record.
    """
    from app.models.settings import AccountSettings

    settings_row = AccountSettings(
        environment="test",
        account="123456789012",
        environment_options=["test"],
        regions=["us-east-1"],
        onboarding_completed=True,
        onboarding_step="complete",
    )
    db_session.add(settings_row)
    db_session.commit()
    return settings_row
