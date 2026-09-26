"""Database seeding utility for loading default policies."""
from datetime import datetime, timedelta
from pathlib import Path
from sqlalchemy.orm import Session
from app.models.policy import Policy, PolicyExecution, PolicyExecutionResult, PolicyCostSavings
from app.utils.policy_id_generator import generate_policy_code
from app.checks.registry import check_registry
import app.checks  # noqa: F401  # Ensure check modules are imported and registered.


S3_OPTIMIZER_SEED_POLICIES = (
    {
        "name": "S3 Bucket Unused",
        "description": "Identifies fully covered S3 buckets with no data-family activity",
        "check_id": "s3_bucket_unused",
        "parameters_json": {"region": None, "window_days": 90},
        "resource_type": "s3",
    },
    {
        "name": "S3 Bucket Low Access",
        "description": "Identifies S3 buckets with low request and retrieval activity",
        "check_id": "s3_bucket_low_access",
        "parameters_json": {
            "region": None,
            "min_bucket_gb": 1.0,
            "requests_threshold": 0.01,
            "retrieval_threshold": 0.1,
        },
        "resource_type": "s3",
    },
    {
        "name": "S3 Bucket Retrieval Cost Dominant",
        "description": "Identifies S3 IA and archive classes whose observed retrieval cost dominates storage savings",
        "check_id": "s3_bucket_retrieval_cost_dominant",
        "parameters_json": {"region": None},
        "resource_type": "s3",
    },
)

# Legacy imports - only used if legacy mode is available
try:
    from app.utils.policy_parser import PolicyParser
    from app.utils.yaml_to_filters_converter import convert_yaml_to_filters
    _LEGACY_MODE = True
except ImportError:
    _LEGACY_MODE = False
    PolicyParser = None
    convert_yaml_to_filters = None


def get_seed_data_dir() -> Path:
    """Get the path to seed data directory."""
    # Get the backend directory (parent of app)
    backend_dir = Path(__file__).parent.parent.parent
    return backend_dir / "seed_data"


def load_seed_policies() -> list[dict]:
    """Load default policies using the new check-based approach.
    
    Automatically creates a policy for each registered check using the check's
    default parameters from the check metadata.
    """
    policies = []
    
    # Get all registered checks from the registry
    all_checks = check_registry.list_checks()
    
    if not all_checks:
        print("Warning: No checks found in registry. Cannot create policies.")
        return policies
    
    # Create a policy for each check using its default parameters
    for check in all_checks:
        policy_data = {
            'name': check.name,
            'description': check.description,
            'check_id': check.check_id,
            'parameters_json': check.parameters.copy(),  # Use check's default parameters
            'resource_type': check.resource_type,  # Get from check metadata
            'status': 'active',
            'policy_yaml': ''  # Empty string for database compatibility
        }
        
        policies.append(policy_data)
        print(f"✅ Created policy definition for check: {check.check_id} ({check.name})")
    
    print(f"\n✅ Generated {len(policies)} policy definitions from {len(all_checks)} registered checks")

    # Keep the optimizer's persisted policy contract explicit in the seed
    # source.  The registry normally supplies these entries; this merge also
    # protects startup seeding if a check import is temporarily unavailable.
    policies_by_check_id = {
        policy.get("check_id"): policy
        for policy in policies
        if policy.get("check_id")
    }
    for seed_policy in S3_OPTIMIZER_SEED_POLICIES:
        existing = policies_by_check_id.get(seed_policy["check_id"])
        if existing is None:
            policy = dict(seed_policy)
            policy["parameters_json"] = dict(seed_policy["parameters_json"])
            policy.update({"status": "active", "policy_yaml": ""})
            policies.append(policy)
        else:
            existing.update(seed_policy)
            existing["parameters_json"] = dict(seed_policy["parameters_json"])
    
    # Legacy: Try to load from YAML files if available
    if _LEGACY_MODE:
        seed_dir = get_seed_data_dir()
        if seed_dir.exists():
            parser = PolicyParser()
            for yaml_file in seed_dir.glob("*.yaml"):
                try:
                    with open(yaml_file, 'r') as f:
                        policy_yaml = f.read()
                    
                    parsed = parser.parse(policy_yaml)
                    resource_type = parser.extract_resource_type(parsed)
                    
                    try:
                        filters_json, _ = convert_yaml_to_filters(policy_yaml)
                    except Exception as e:
                        print(f"Warning: Failed to convert YAML to filters for {yaml_file.name}: {e}")
                        filters_json = None
                    
                    policies.append({
                        'name': parsed.get('name', yaml_file.stem),
                        'description': parsed.get('description', ''),
                        'policy_yaml': policy_yaml,
                        'filters_json': filters_json,
                        'resource_type': resource_type,
                        'status': 'active',
                    })
                except Exception as e:
                    print(f"Warning: Failed to load seed policy {yaml_file.name}: {e}")
                    continue
    
    return policies


def seed_policies(db: Session, force: bool = False) -> int:
    """
    Seed the database with default policies.
    
    Args:
        db: Database session
        force: If True, re-seed even if policies exist
        
    Returns:
        Number of policies seeded
    """
    # Check if policies already exist
    existing_count = db.query(Policy).count()

    if force and existing_count > 0:
        print(f"Force mode: Removing {existing_count} existing policies...")
        db.query(Policy).delete()
        db.commit()
    
    # Load seed policies
    seed_policies_list = load_seed_policies()
    
    if not seed_policies_list:
        print("No seed policies found in seed_data directory.")
        return 0
    
    # Create policies
    created_count = 0
    existing_check_ids = {
        check_id
        for (check_id,) in db.query(Policy.check_id).all()
        if check_id
    }
    for policy_data in seed_policies_list:
        try:
            check_id = policy_data.get("check_id")
            if check_id and check_id in existing_check_ids:
                continue
            # Legacy YAML policies have no check_id, so retain the historical
            # name guard for those rows.  Check-based seeds are deduplicated
            # by check_id so a missing check policy is always added.
            if not check_id:
                existing = db.query(Policy).filter(Policy.name == policy_data['name']).first()
                if existing:
                    print(f"Policy '{policy_data['name']}' already exists. Skipping.")
                    continue
            
            # Generate policy_code
            policy_code = generate_policy_code(db)
            policy_data['policy_code'] = policy_code
            
            # Ensure policy_yaml is set (empty string if None for database compatibility)
            # Some databases may have NOT NULL constraint from old migrations
            if 'policy_yaml' not in policy_data:
                policy_data['policy_yaml'] = ''  # Empty string for compatibility
            
            policy = Policy(**policy_data)
            db.add(policy)
            if check_id:
                existing_check_ids.add(check_id)
            created_count += 1
        except Exception as e:
            print(f"Error creating policy '{policy_data['name']}': {e}")
            continue
    
    db.commit()
    if existing_count > 0 and not force:
        print(f"Added {created_count} missing seed policies.")
    else:
        print(f"Successfully seeded {created_count} default policies.")
    return created_count


def seed_executions(db: Session, force: bool = False) -> int:
    """
    Seed the database with dummy execution data for testing.
    
    Args:
        db: Database session
        force: If True, re-seed even if executions exist
        
    Returns:
        Number of executions seeded
    """
    # Check if executions already exist
    existing_count = db.query(PolicyExecution).count()
    
    if existing_count > 0 and not force:
        print(f"Database already contains {existing_count} executions. Skipping seed.")
        return 0
    
    if force and existing_count > 0:
        print(f"Force mode: Removing {existing_count} existing executions...")
        db.query(PolicyExecutionResult).delete()
        db.query(PolicyExecution).delete()
        db.commit()
    
    # Get all policies
    policies = db.query(Policy).all()
    if not policies:
        print("No policies found. Please seed policies first.")
        return 0
    
    # Sample execution data
    execution_data = [
        {
            'execution_type': 'dry-run',
            'status': 'completed',
            'resources_found': 5,
            'hours_ago': 1,
            'results': [
                {'resource_id': 'i-1234567890abcdef0', 'resource_name': 'web-server-1', 'region': 'us-east-1', 'reason': 'Idle for 8 days'},
                {'resource_id': 'i-0987654321fedcba0', 'resource_name': 'app-server-2', 'region': 'us-west-2', 'reason': 'Idle for 12 days'},
                {'resource_id': 'i-abcdef1234567890a', 'resource_name': 'db-server-1', 'region': 'eu-west-1', 'reason': 'Idle for 15 days'},
                {'resource_id': 'i-fedcba0987654321f', 'resource_name': 'cache-server-1', 'region': 'us-east-1', 'reason': 'Idle for 9 days'},
                {'resource_id': 'i-1122334455667788a', 'resource_name': 'test-server-1', 'region': 'us-west-2', 'reason': 'Idle for 20 days'},
            ]
        },
        {
            'execution_type': 'dry-run',
            'status': 'completed',
            'resources_found': 3,
            'hours_ago': 3,
            'results': [
                {'resource_id': 'vol-1234567890abcdef0', 'resource_name': 'unattached-volume-1', 'region': 'us-east-1', 'reason': 'Unattached for 30 days'},
                {'resource_id': 'vol-0987654321fedcba0', 'resource_name': 'unattached-volume-2', 'region': 'us-west-2', 'reason': 'Unattached for 45 days'},
                {'resource_id': 'vol-abcdef1234567890a', 'resource_name': 'unattached-volume-3', 'region': 'eu-west-1', 'reason': 'Unattached for 60 days'},
            ]
        },
        {
            'execution_type': 'apply',
            'status': 'completed',
            'resources_found': 2,
            'hours_ago': 5,
            'results': [
                {'resource_id': 'snap-1234567890abcdef0', 'resource_name': 'old-snapshot-1', 'region': 'us-east-1', 'reason': 'Snapshot older than 90 days'},
                {'resource_id': 'snap-0987654321fedcba0', 'resource_name': 'old-snapshot-2', 'region': 'us-west-2', 'reason': 'Snapshot older than 120 days'},
            ]
        },
        {
            'execution_type': 'dry-run',
            'status': 'completed',
            'resources_found': 0,
            'hours_ago': 8,
            'results': []
        },
        {
            'execution_type': 'dry-run',
            'status': 'failed',
            'resources_found': 0,
            'hours_ago': 12,
            'error_message': 'Failed to connect to AWS API. Please check credentials.',
            'results': []
        },
        {
            'execution_type': 'dry-run',
            'status': 'running',
            'resources_found': 0,
            'hours_ago': 0.5,
            'results': []
        },
        {
            'execution_type': 'dry-run',
            'status': 'completed',
            'resources_found': 8,
            'hours_ago': 24,
            'results': [
                {'resource_id': 'i-11111111111111111', 'resource_name': 'dev-server-1', 'region': 'us-east-1', 'reason': 'Idle for 10 days'},
                {'resource_id': 'i-22222222222222222', 'resource_name': 'dev-server-2', 'region': 'us-east-1', 'reason': 'Idle for 12 days'},
                {'resource_id': 'i-33333333333333333', 'resource_name': 'dev-server-3', 'region': 'us-west-2', 'reason': 'Idle for 15 days'},
                {'resource_id': 'i-44444444444444444', 'resource_name': 'dev-server-4', 'region': 'us-west-2', 'reason': 'Idle for 8 days'},
                {'resource_id': 'i-55555555555555555', 'resource_name': 'dev-server-5', 'region': 'eu-west-1', 'reason': 'Idle for 20 days'},
                {'resource_id': 'i-66666666666666666', 'resource_name': 'dev-server-6', 'region': 'eu-west-1', 'reason': 'Idle for 18 days'},
                {'resource_id': 'i-77777777777777777', 'resource_name': 'dev-server-7', 'region': 'us-east-1', 'reason': 'Idle for 14 days'},
                {'resource_id': 'i-88888888888888888', 'resource_name': 'dev-server-8', 'region': 'us-west-2', 'reason': 'Idle for 16 days'},
            ]
        },
        {
            'execution_type': 'apply',
            'status': 'completed',
            'resources_found': 1,
            'hours_ago': 36,
            'results': [
                {'resource_id': 'eipalloc-1234567890abcdef0', 'resource_name': 'unused-eip-1', 'region': 'us-east-1', 'reason': 'Unassociated for 30 days'},
            ]
        },
        {
            'execution_type': 'dry-run',
            'status': 'completed',
            'resources_found': 4,
            'hours_ago': 48,
            'results': [
                {'resource_id': 'db-instance-1234567890', 'resource_name': 'idle-rds-1', 'region': 'us-east-1', 'reason': 'Idle for 7 days'},
                {'resource_id': 'db-instance-0987654321', 'resource_name': 'idle-rds-2', 'region': 'us-west-2', 'reason': 'Idle for 10 days'},
                {'resource_id': 'db-instance-abcdef1234', 'resource_name': 'idle-rds-3', 'region': 'eu-west-1', 'reason': 'Idle for 12 days'},
                {'resource_id': 'db-instance-fedcba9876', 'resource_name': 'idle-rds-4', 'region': 'us-east-1', 'reason': 'Idle for 9 days'},
            ]
        },
    ]
    
    created_count = 0
    now = datetime.utcnow()
    
    for i, exec_data in enumerate(execution_data):
        try:
            # Cycle through policies
            policy = policies[i % len(policies)]
            
            # Calculate timestamps
            started_at = now - timedelta(hours=exec_data['hours_ago'])
            completed_at = None
            if exec_data['status'] in ['completed', 'failed']:
                completed_at = started_at + timedelta(minutes=2)
            
            # Create execution
            execution = PolicyExecution(
                policy_id=policy.id,
                execution_type=exec_data['execution_type'],
                status=exec_data['status'],
                resources_found=exec_data['resources_found'],
                results_json={
                    'total_resources_checked': exec_data['resources_found'] + 10,
                    'matching_resources': exec_data['resources_found'],
                    'execution_type': exec_data['execution_type'],
                    'actions_taken': exec_data['execution_type'] == 'apply'
                },
                error_message=exec_data.get('error_message'),
                started_at=started_at,
                completed_at=completed_at
            )
            db.add(execution)
            db.flush()  # Get the execution ID
            
            # Create results
            for result_data in exec_data['results']:
                result = PolicyExecutionResult(
                    execution_id=execution.id,
                    resource_id=result_data['resource_id'],
                    resource_type=policy.resource_type,
                    resource_name=result_data.get('resource_name'),
                    region=result_data.get('region'),
                    reason=result_data.get('reason'),
                    metadata_json={}
                )
                db.add(result)
            
            created_count += 1
        except Exception as e:
            print(f"Error creating execution {i}: {e}")
            continue
    
    db.commit()
    print(f"Successfully seeded {created_count} dummy executions.")
    return created_count


def seed_cost_savings(db: Session, force: bool = False) -> int:
    """
    Seed the database with dummy cost savings data for testing.
    
    Args:
        db: Database session
        force: If True, re-seed even if cost savings exist
        
    Returns:
        Number of cost savings records seeded
    """
    # Check if cost savings already exist
    existing_count = db.query(PolicyCostSavings).count()
    
    if existing_count > 0 and not force:
        print(f"Database already contains {existing_count} cost savings records. Skipping seed.")
        return 0
    
    if force and existing_count > 0:
        print(f"Force mode: Removing {existing_count} existing cost savings records...")
        db.query(PolicyCostSavings).delete()
        db.commit()
    
    # Get first policy (or any policy)
    policy = db.query(Policy).first()
    if not policy:
        print("No policies found. Please seed policies first.")
        return 0
    
    # Generate cost savings data over the last 3 months
    now = datetime.utcnow()
    savings_data = []
    
    # Create monthly savings data showing progress
    for i in range(12, 0, -1):  # Last 12 weeks
        weeks_ago = i
        date = now - timedelta(weeks=weeks_ago)
        
        # Simulate increasing savings over time (as user fixes issues)
        # Start with higher waste, then decrease as issues are fixed
        base_savings = 500.0
        progress_factor = (12 - weeks_ago) / 12  # Progress from 0 to 1
        cost_saved = base_savings * (1 - progress_factor * 0.7)  # Savings decrease as waste is reduced
        resources_fixed = max(1, int(5 * progress_factor))  # More resources fixed over time
        
        savings_data.append({
            'date': date,
            'cost_saved': round(cost_saved, 2),
            'resources_fixed': resources_fixed,
        })
    
    created_count = 0
    for data in savings_data:
        try:
            cost_saving = PolicyCostSavings(
                policy_id=policy.id,
                date=data['date'],
                cost_saved=data['cost_saved'],
                resources_fixed=data['resources_fixed'],
                notes=f"Fixed {data['resources_fixed']} resources"
            )
            db.add(cost_saving)
            created_count += 1
        except Exception as e:
            print(f"Error creating cost saving record: {e}")
            continue
    
    db.commit()
    print(f"Successfully seeded {created_count} cost savings records for policy '{policy.name}'.")
    return created_count


def seed_database(db: Session) -> dict:
    """
    Main seeding function called on application startup.
    
    Returns:
        Dictionary with seeding results
    """
    result = {
        'seeded': False,
        'count': 0,
        'message': ''
    }
    
    try:
        count = seed_policies(db, force=False)
        result['seeded'] = count > 0
        result['count'] = count
        result['message'] = f"Seeded {count} default policies" if count > 0 else "No seeding needed"
    except Exception as e:
        result['message'] = f"Error during seeding: {str(e)}"
        print(f"Seeding error: {e}")
    
    return result
