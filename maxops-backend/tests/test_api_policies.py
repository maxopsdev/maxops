"""Tests for policy API endpoints."""
import pytest
from fastapi import status


class TestPolicyAPI:
    """Test cases for policy API endpoints."""
    
    def test_create_policy(self, client, sample_policy_data):
        """Test creating a policy via API."""
        response = client.post("/api/v1/policies", json=sample_policy_data)
        
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["name"] == sample_policy_data["name"]
        assert data["policy_code"] is not None
        assert len(data["policy_code"]) == 6
    
    def test_create_policy_invalid_data(self, client):
        """Test creating a policy with invalid data."""
        invalid_data = {
            "name": "",  # Empty name should fail
            "resource_type": "ec2"
        }
        
        response = client.post("/api/v1/policies", json=invalid_data)
        
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    
    def test_list_policies(self, client, sample_policies):
        """Test listing policies."""
        response = client.get("/api/v1/policies")
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= len(sample_policies)
    
    def test_list_policies_with_status_filter(self, client, sample_policies):
        """Test listing policies with status filter."""
        response = client.get("/api/v1/policies?status=active")
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert all(p["status"] == "active" for p in data)
    
    def test_get_policy_by_id(self, client, sample_policy):
        """Test getting a policy by ID."""
        response = client.get(f"/api/v1/policies/{sample_policy.id}")
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == sample_policy.id
        assert data["name"] == sample_policy.name
    
    def test_get_policy_by_code(self, client, sample_policy):
        """Test getting a policy by code."""
        response = client.get(f"/api/v1/policies/code/{sample_policy.policy_code}")
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["policy_code"] == sample_policy.policy_code
        assert data["id"] == sample_policy.id
    
    def test_get_policy_not_found(self, client):
        """Test getting a non-existent policy."""
        response = client.get("/api/v1/policies/99999")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_update_policy(self, client, sample_policy):
        """Test updating a policy."""
        update_data = {
            "name": "Updated Policy Name",
            "description": "Updated description"
        }
        
        response = client.put(f"/api/v1/policies/{sample_policy.id}", json=update_data)
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["name"] == "Updated Policy Name"
        assert data["description"] == "Updated description"
    
    def test_delete_policy(self, client, sample_policy):
        """Test deleting a policy."""
        response = client.delete(f"/api/v1/policies/{sample_policy.id}")
        
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Delete archives rather than removing, so the policy is still
        # retrievable and its execution history stays intact.
        get_response = client.get(f"/api/v1/policies/{sample_policy.id}")
        assert get_response.status_code == status.HTTP_200_OK
        assert get_response.json()["status"] == "inactive"
    
    def test_validate_filters(self, client):
        """Test validating filters via API."""
        validation_data = {
            "resource_type": "ec2",
            "filters_json": [
                {
                    "type": "idle",
                    "operator": "greater_than",
                    "value": 7
                }
            ]
        }
        
        response = client.post("/api/v1/policies/validate", json=validation_data)
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["valid"] is True
    
    def test_validate_filters_invalid(self, client):
        """Test validating invalid filters."""
        validation_data = {
            "resource_type": "ec2",
            "filters_json": [
                {
                    "type": "unknown_filter",
                    "operator": "equals",
                    "value": "test"
                }
            ]
        }
        
        response = client.post("/api/v1/policies/validate", json=validation_data)

        # Filter-based policies are deprecated and the registry that judged
        # them is gone, so this reports that rather than a false verdict.
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["valid"] is True
        assert any("deprecated" in warning for warning in data["warnings"])
    
    @pytest.mark.integration
    def test_execute_policy_dry_run(self, client, sample_policy, mocker, db_session):
        """Test executing a policy via API."""
        # Mock the service method to avoid AWS calls
        from app.services.policy_service import PolicyService
        from app.models.policy import PolicyExecution
        from datetime import datetime
        
        mock_execution = PolicyExecution(
            id=1,
            policy_id=sample_policy.id,
            execution_type="dry-run",
            status="completed",
            resources_found=0,
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow()
        )
        
        # Patch the method on instances
        mocker.patch(
            'app.services.policy_service.PolicyService.execute_policy',
            return_value=mock_execution
        )
        
        response = client.post(
            f"/api/v1/policies/{sample_policy.id}/execute",
            json={"execution_type": "dry-run"}
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["execution_type"] == "dry-run"
        assert data["policy_id"] == sample_policy.id
    
    def test_execute_policy_by_code(self, client, sample_policy, mocker):
        """Test executing a policy by code via API."""
        from app.services.policy_service import PolicyService
        from app.models.policy import PolicyExecution
        from datetime import datetime
        
        mock_execution = PolicyExecution(
            id=1,
            policy_id=sample_policy.id,
            execution_type="dry-run",
            status="completed",
            resources_found=0,
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow()
        )
        
        # Patch the method on instances
        mocker.patch(
            'app.services.policy_service.PolicyService.execute_policy',
            return_value=mock_execution
        )
        
        response = client.post(
            f"/api/v1/policies/code/{sample_policy.policy_code}/execute",
            json={"execution_type": "dry-run"}
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["policy_id"] == sample_policy.id
    
    def test_get_policy_executions(self, client, sample_policy, db_session):
        """Test getting execution history for a policy."""
        from app.models.policy import PolicyExecution
        from datetime import datetime
        
        # Create an execution
        execution = PolicyExecution(
            policy_id=sample_policy.id,
            execution_type="dry-run",
            status="completed",
            resources_found=0,
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow()
        )
        db_session.add(execution)
        db_session.commit()
        
        response = client.get(f"/api/v1/policies/{sample_policy.id}/executions")
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

