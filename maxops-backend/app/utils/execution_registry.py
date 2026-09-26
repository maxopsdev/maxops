"""Execution registry mapping resource types to execution methods."""
from typing import Dict, Any, Optional
from app.adapters.aws.adapter import AWSAdapter


class ExecutionRegistry:
    """Registry for policy execution methods."""
    
    def __init__(self):
        """Initialize execution registry."""
        self.registry: Dict[str, Dict[str, Any]] = {
            "aws.ec2": {
                "adapter_method": "get_ec2_instances",
                "filter_converter": "convert_ec2_filters",
                "description": "EC2 Instance Policy Execution"
            },
            "ec2": {
                "adapter_method": "get_ec2_instances",
                "filter_converter": "convert_ec2_filters",
                "description": "EC2 Instance Policy Execution"
            },
            "aws.rds": {
                "adapter_method": "get_rds_instances",
                "filter_converter": "convert_rds_filters",
                "description": "RDS Instance Policy Execution"
            },
            "rds": {
                "adapter_method": "get_rds_instances",
                "filter_converter": "convert_rds_filters",
                "description": "RDS Instance Policy Execution"
            },
            "aws.ebs": {
                "adapter_method": "get_ebs_volumes",
                "filter_converter": "convert_ebs_filters",
                "description": "EBS Volume Policy Execution"
            },
            "ebs": {
                "adapter_method": "get_ebs_volumes",
                "filter_converter": "convert_ebs_filters",
                "description": "EBS Volume Policy Execution"
            },
            "aws.snapshot": {
                "adapter_method": "get_ebs_snapshots",
                "filter_converter": "convert_snapshot_filters",
                "description": "EBS Snapshot Policy Execution"
            },
            "snapshot": {
                "adapter_method": "get_ebs_snapshots",
                "filter_converter": "convert_snapshot_filters",
                "description": "EBS Snapshot Policy Execution"
            },
            "aws.s3": {
                "adapter_method": "get_s3_buckets",
                "filter_converter": "convert_s3_filters",
                "description": "S3 Bucket Policy Execution"
            },
            "s3": {
                "adapter_method": "get_s3_buckets",
                "filter_converter": "convert_s3_filters",
                "description": "S3 Bucket Policy Execution"
            }
        }
    
    def get_execution_config(self, resource_type: str) -> Optional[Dict[str, Any]]:
        """
        Get execution configuration for a resource type.
        
        Args:
            resource_type: Resource type (e.g., 'aws.ec2')
            
        Returns:
            Execution configuration or None if not found
        """
        return self.registry.get(resource_type)
    
    def register_resource_type(
        self,
        resource_type: str,
        adapter_method: str,
        filter_converter: str,
        description: str = ""
    ):
        """
        Register a new resource type for execution.
        
        Args:
            resource_type: Resource type identifier
            adapter_method: Method name in AWSAdapter to call
            filter_converter: Method name to convert filters
            description: Description of the resource type
        """
        self.registry[resource_type] = {
            "adapter_method": adapter_method,
            "filter_converter": filter_converter,
            "description": description
        }
    
    def execute_policy(
        self,
        adapter: AWSAdapter,
        resource_type: str,
        filters: list[Dict[str, Any]]
    ) -> list[Dict[str, Any]]:
        """
        Execute a policy by routing to appropriate adapter method.
        
        Args:
            adapter: AWS adapter instance
            resource_type: Resource type
            filters: List of filter conditions
            
        Returns:
            List of matching resources
            
        Raises:
            ValueError: If resource type is not registered
        """
        config = self.get_execution_config(resource_type)
        if not config:
            raise ValueError(f"No execution handler registered for resource type: {resource_type}")
        
        # Get the adapter method
        adapter_method = getattr(adapter, config["adapter_method"], None)
        if not adapter_method:
            raise ValueError(f"Adapter method '{config['adapter_method']}' not found in AWSAdapter")
        
        # Convert filters to adapter format
        filter_converter = getattr(adapter, config["filter_converter"], None)
        if filter_converter and callable(filter_converter):
            converted_filters = filter_converter(filters)
        else:
            converted_filters = self._default_filter_converter(filters)
        
        # Execute
        resources = adapter_method(converted_filters)
        
        return resources
    
    def _default_filter_converter(self, filters: list[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Default filter converter (fallback).
        
        Args:
            filters: List of filter conditions
            
        Returns:
            Converted filters dictionary
        """
        converted = {}
        for filter_cond in filters:
            filter_type = filter_cond.get('type')
            operator = filter_cond.get('operator')
            value = filter_cond.get('value')
            
            # Basic conversion logic
            if filter_type == 'tags':
                if 'tag' not in converted:
                    converted['tag'] = {}
                if operator == 'equals' and isinstance(value, dict):
                    converted['tag'][value.get('key')] = value.get('value')
            elif filter_type == 'state':
                converted['state'] = value
            elif filter_type == 'region':
                converted['region'] = value
        
        return converted


# Global registry instance
execution_registry = ExecutionRegistry()

