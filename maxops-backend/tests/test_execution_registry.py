"""Tests for execution registry."""
import pytest
from app.utils.execution_registry import execution_registry


class TestExecutionRegistry:
    """Test cases for execution registry."""
    
    def test_get_execution_config_ec2(self):
        """Test getting execution config for EC2."""
        config = execution_registry.get_execution_config("ec2")
        
        assert config is not None
        assert config["adapter_method"] == "get_ec2_instances"
        assert config["filter_converter"] == "convert_ec2_filters"
    
    def test_get_execution_config_rds(self):
        """Test getting execution config for RDS."""
        config = execution_registry.get_execution_config("rds")
        
        assert config is not None
        assert config["adapter_method"] == "get_rds_instances"
        assert config["filter_converter"] == "convert_rds_filters"
    
    def test_get_execution_config_ebs(self):
        """Test getting execution config for EBS."""
        config = execution_registry.get_execution_config("ebs")
        
        assert config is not None
        assert config["adapter_method"] == "get_ebs_volumes"
        assert config["filter_converter"] == "convert_ebs_filters"
    
    def test_get_execution_config_snapshot(self):
        """Test getting execution config for snapshot."""
        config = execution_registry.get_execution_config("snapshot")
        
        assert config is not None
        assert config["adapter_method"] == "get_ebs_snapshots"
        assert config["filter_converter"] == "convert_snapshot_filters"
    
    def test_get_execution_config_s3(self):
        """Test getting execution config for S3."""
        config = execution_registry.get_execution_config("s3")
        
        assert config is not None
        assert config["adapter_method"] == "get_s3_buckets"
        assert config["filter_converter"] == "convert_s3_filters"
    
    def test_get_execution_config_unknown(self):
        """Test getting execution config for unknown resource type."""
        config = execution_registry.get_execution_config("unknown")
        
        assert config is None
    
    def test_get_execution_config_with_aws_prefix(self):
        """Test that aws.ec2 and ec2 both work."""
        config1 = execution_registry.get_execution_config("aws.ec2")
        config2 = execution_registry.get_execution_config("ec2")
        
        assert config1 == config2
        assert config1["adapter_method"] == config2["adapter_method"]
    
    def test_execute_policy_mocked(self, mocker):
        """Test executing a policy with mocked adapter."""
        # Create a mock adapter with the required methods
        mock_adapter = mocker.Mock(spec=['get_ec2_instances', 'convert_ec2_filters'])
        mock_adapter.get_ec2_instances.return_value = [
            {
                'resource_id': 'i-1234567890abcdef0',
                'resource_type': 'ec2',
                'resource_name': 'test-instance',
                'region': 'us-east-1',
                'state': 'running',
                'tags': {}
            }
        ]
        mock_adapter.convert_ec2_filters.return_value = {}
        
        filters = [
            {
                "type": "idle",
                "operator": "greater_than",
                "value": 7
            }
        ]
        
        resources = execution_registry.execute_policy(mock_adapter, "ec2", filters)
        
        assert len(resources) == 1
        assert resources[0]['resource_id'] == 'i-1234567890abcdef0'
        mock_adapter.get_ec2_instances.assert_called_once()
        mock_adapter.convert_ec2_filters.assert_called_once()
    
    def test_execute_policy_no_resources(self, mocker):
        """Test executing a policy that returns no resources."""
        mock_adapter = mocker.Mock(spec=['get_ec2_instances', 'convert_ec2_filters'])
        mock_adapter.get_ec2_instances.return_value = []
        mock_adapter.convert_ec2_filters.return_value = {}
        
        filters = [{"type": "idle", "operator": "greater_than", "value": 7}]
        
        resources = execution_registry.execute_policy(mock_adapter, "ec2", filters)
        
        assert len(resources) == 0
        mock_adapter.get_ec2_instances.assert_called_once()
    
    def test_execute_policy_unknown_resource_type(self, mocker):
        """Test executing a policy with unknown resource type."""
        mock_adapter = mocker.Mock()
        
        with pytest.raises(ValueError, match="No execution handler"):
            execution_registry.execute_policy(mock_adapter, "unknown", [])
    
    def test_register_resource_type(self):
        """Test registering a new resource type."""
        execution_registry.register_resource_type(
            "test_resource",
            "get_test_resources",
            "convert_test_filters",
            "Test Resource"
        )
        
        config = execution_registry.get_execution_config("test_resource")
        assert config is not None
        assert config["adapter_method"] == "get_test_resources"
        assert config["filter_converter"] == "convert_test_filters"
        assert config["description"] == "Test Resource"

