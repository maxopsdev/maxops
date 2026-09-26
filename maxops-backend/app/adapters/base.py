"""Base cloud adapter interface."""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime


class CloudAdapter(ABC):
    """Base class for cloud provider adapters."""
    
    @abstractmethod
    def get_resources(
        self,
        resource_type: str,
        filters: Optional[Dict[str, Any]] = None,
        region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get resources of a specific type.
        
        Args:
            resource_type: Type of resource (e.g., 'ec2', 'rds', 'ebs')
            filters: Optional filters to apply
            region: Optional region filter
            
        Returns:
            List of resource dictionaries
        """
        pass
    
    @abstractmethod
    def get_resource_utilization(
        self,
        resource_id: str,
        resource_type: str,
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get utilization metrics for a resource.
        
        Args:
            resource_id: ID of the resource
            resource_type: Type of resource
            start_date: Start date for metrics
            end_date: End date for metrics
            region: Optional region for the resource
            
        Returns:
            Dictionary with utilization metrics
        """
        pass
    
    @abstractmethod
    def get_resource_tags(self, resource_id: str, resource_type: str) -> Dict[str, str]:
        """
        Get tags for a resource.
        
        Args:
            resource_id: ID of the resource
            resource_type: Type of resource
            
        Returns:
            Dictionary of tag key-value pairs
        """
        pass

