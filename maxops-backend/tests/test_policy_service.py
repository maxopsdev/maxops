"""Tests for PolicyService."""
import pytest
from datetime import datetime
from app.services.policy_service import PolicyService
from app.schemas.policy import PolicyCreate, PolicyUpdate
from app.models.policy import Policy, PolicyExecution


class TestPolicyService:
    """Test cases for PolicyService."""
    
    def test_create_policy(self, db_session, sample_policy_data):
        """Test creating a new policy."""
        service = PolicyService(db_session)
        policy_data = PolicyCreate(**sample_policy_data)
        
        policy = service.create_policy(policy_data)
        
        assert policy.id is not None
        assert policy.policy_code is not None
        assert policy.name == sample_policy_data["name"]
        assert policy.resource_type == sample_policy_data["resource_type"]
        assert policy.check_id == sample_policy_data["check_id"]
        assert len(policy.policy_code) == 6
        assert policy.policy_code.startswith("POL")
    
    def test_create_policy_generates_unique_code(self, db_session, sample_policy_data):
        """Test that policy codes are unique."""
        service = PolicyService(db_session)
        policy_data = PolicyCreate(**sample_policy_data)
        
        policy1 = service.create_policy(policy_data)
        sample_policy_data["name"] = "Another Policy"
        policy2 = service.create_policy(PolicyCreate(**sample_policy_data))
        
        assert policy1.policy_code != policy2.policy_code
    
    def test_get_policy(self, db_session, sample_policy):
        """Test getting a policy by ID."""
        service = PolicyService(db_session)
        
        policy = service.get_policy(sample_policy.id)
        
        assert policy is not None
        assert policy.id == sample_policy.id
        assert policy.name == sample_policy.name
    
    def test_get_policy_by_code(self, db_session, sample_policy):
        """Test getting a policy by code."""
        service = PolicyService(db_session)
        
        policy = service.get_policy_by_code(sample_policy.policy_code)
        
        assert policy is not None
        assert policy.policy_code == sample_policy.policy_code
        assert policy.id == sample_policy.id
    
    def test_get_policies(self, db_session, sample_policies):
        """Test getting all policies."""
        service = PolicyService(db_session)
        
        policies = service.get_policies()
        
        assert len(policies) >= len(sample_policies)
    
    def test_get_policies_with_status_filter(self, db_session, sample_policies):
        """Test getting policies filtered by status."""
        service = PolicyService(db_session)
        
        active_policies = service.get_policies(status="active")
        
        assert all(p.status == "active" for p in active_policies)
    
    def test_update_policy(self, db_session, sample_policy):
        """Test updating a policy."""
        service = PolicyService(db_session)
        
        update_data = PolicyUpdate(
            name="Updated Policy Name",
            description="Updated description"
        )
        
        updated_policy = service.update_policy(sample_policy.id, update_data)
        
        assert updated_policy is not None
        assert updated_policy.name == "Updated Policy Name"
        assert updated_policy.description == "Updated description"
        assert updated_policy.id == sample_policy.id
    
    def test_update_policy_generates_code_if_missing(self, db_session, sample_policy_data):
        """Test that updating a policy without code generates one."""
        service = PolicyService(db_session)
        # Create policy without code (simulate old policy)
        policy = Policy(**sample_policy_data)
        policy.policy_code = None
        db_session.add(policy)
        db_session.commit()
        db_session.refresh(policy)
        
        update_data = PolicyUpdate(name="Updated")
        updated = service.update_policy(policy.id, update_data)
        
        assert updated.policy_code is not None
        assert len(updated.policy_code) == 6
    
    def test_delete_policy(self, db_session, sample_policy):
        """Test deleting a policy."""
        service = PolicyService(db_session)
        policy_id = sample_policy.id
        
        success = service.delete_policy(policy_id)

        assert success is True
        # delete_policy archives rather than removing: the row survives with
        # status "inactive" so execution history stays intact.
        archived = service.get_policy(policy_id)
        assert archived is not None
        assert archived.status == "inactive"
    
    def test_delete_nonexistent_policy(self, db_session):
        """Test deleting a policy that doesn't exist."""
        service = PolicyService(db_session)
        
        success = service.delete_policy(99999)
        
        assert success is False
    
    def test_validate_filters(self, db_session):
        """Test filter validation."""
        service = PolicyService(db_session)
        
        filters = [
            {
                "type": "idle",
                "operator": "greater_than",
                "value": 7
            }
        ]
        
        result = service.validate_filters("ec2", filters)

        # Filter-based policies are deprecated; validation reports that rather
        # than pretending to check a registry that no longer exists.
        assert result.valid is True
        assert len(result.errors) == 0
        assert any("deprecated" in warning for warning in result.warnings)
    
    def test_validate_filters_invalid(self, db_session):
        """Test filter validation with invalid filters."""
        service = PolicyService(db_session)
        
        filters = [
            {
                "type": "unknown_filter",
                "operator": "equals",
                "value": "test"
            }
        ]
        
        result = service.validate_filters("ec2", filters)

        # Nothing can judge an unknown filter now that the registry is gone,
        # so this reports the deprecation instead of a false verdict.
        assert result.valid is True
        assert any("deprecated" in warning for warning in result.warnings)
    
    def test_validate_filters_empty(self, db_session):
        """Test filter validation with empty filters."""
        service = PolicyService(db_session)
        
        result = service.validate_filters("ec2", [])
        
        assert result.valid is True
        assert len(result.warnings) > 0  # Should warn about no filters
    
    @pytest.mark.integration
    def test_execute_policy_dry_run(self, db_session, sample_policy, mocker):
        """Test executing a policy in dry-run mode."""
        service = PolicyService(db_session)
        
        # Mock AWS adapter methods that execution registry calls
        mock_resources = [
            {
                'resource_id': 'i-1234567890abcdef0',
                'resource_type': 'ec2',
                'resource_name': 'test-instance',
                'region': 'us-east-1',
                'state': 'running',
                'tags': {}
            }
        ]
        
        # Mock the adapter methods used by execution registry
        mocker.patch.object(
            service.aws_adapter,
            'get_ec2_instances',
            return_value=mock_resources
        )
        mocker.patch.object(
            service.aws_adapter,
            'convert_ec2_filters',
            return_value={}
        )
        mocker.patch.object(
            service,
            '_apply_filters',
            return_value=mock_resources
        )
        
        execution = service.execute_policy(
            policy_id=sample_policy.id,
            execution_type="dry-run"
        )
        
        assert execution is not None
        assert execution.policy_id == sample_policy.id
        assert execution.execution_type == "dry-run"
        assert execution.status == "completed"
        assert execution.resources_found >= 0
    
    def test_execute_policy_by_code(self, db_session, sample_policy, onboarded, mocker):
        """Test executing a policy by code."""
        service = PolicyService(db_session)
        
        mock_resources = []
        # The legacy execution_registry is gone; stub the service's own scan
        # entry point so this stays an offline test.
        mocker.patch.object(service, "_run_check", return_value=mock_resources, create=True)
        
        execution = service.execute_policy(policy_code=sample_policy.policy_code)
        
        assert execution is not None
        assert execution.policy_id == sample_policy.id
    
    def test_execute_policy_requires_id_or_code(self, db_session, onboarded):
        """Test that execute_policy requires either id or code."""
        service = PolicyService(db_session)
        
        with pytest.raises(ValueError, match="Either policy_id or policy_code must be provided"):
            service.execute_policy()
    
    def test_execute_policy_inactive_policy(self, db_session, sample_policy, onboarded):
        """Test that executing an inactive policy fails."""
        service = PolicyService(db_session)
        
        # Make policy inactive
        sample_policy.status = "inactive"
        db_session.commit()
        
        with pytest.raises(ValueError, match="is not active"):
            service.execute_policy(policy_id=sample_policy.id)
    
    def test_get_executions(self, db_session, sample_policy):
        """Test getting execution history for a policy."""
        service = PolicyService(db_session)
        
        # Create an execution
        execution = PolicyExecution(
            policy_id=sample_policy.id,
            execution_type="dry-run",
            status="completed",
            resources_found=0
        )
        db_session.add(execution)
        db_session.commit()
        
        executions = service.get_executions(sample_policy.id)
        
        assert len(executions) >= 1
        assert executions[0].policy_id == sample_policy.id
    
    def test_get_all_executions(self, db_session, sample_policies):
        """Test getting all executions."""
        service = PolicyService(db_session)
        
        # Create executions for different policies
        for policy in sample_policies[:2]:
            execution = PolicyExecution(
                policy_id=policy.id,
                execution_type="dry-run",
                status="completed",
                resources_found=0
            )
            db_session.add(execution)
        db_session.commit()
        
        executions = service.get_all_executions()
        
        assert len(executions) >= 2

