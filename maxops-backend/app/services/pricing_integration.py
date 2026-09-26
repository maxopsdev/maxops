"""Pricing integration service for calculating resource costs."""
import httpx
import logging
from typing import List, Dict, Any, Optional
from app.config import settings
from app.checks.base import (
    estimate_ec2_monthly_cost,
    estimate_ebs_monthly_cost,
    estimate_snapshot_monthly_cost,
    estimate_rds_monthly_cost
)

logger = logging.getLogger(__name__)


class PricingIntegration:
    """Service for integrating with pricing service or using estimates."""
    
    def __init__(self):
        """Initialize pricing integration."""
        self.pricing_service_url = settings.pricing_service_url
        self.timeout = settings.pricing_timeout
    
    def calculate_costs(
        self,
        resources: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Calculate costs for a list of resources.
        
        Args:
            resources: List of resource dictionaries with resource_id, resource_type, etc.
            
        Returns:
            List of resources with added cost information
        """
        if not resources:
            return []
        
        # Try to get costs from pricing service first
        costs = self._get_pricing_service_costs(resources)
        
        # Merge cost data into resources
        resources_with_costs = []
        for resource in resources:
            resource_id = resource.get('resource_id')
            cost_data = costs.get(resource_id, {})
            
            # Add cost fields to resource
            resource_copy = resource.copy()
            resource_copy['monthly_cost'] = cost_data.get('monthly_cost')
            resource_copy['cost_breakdown'] = cost_data.get('cost_breakdown', {})
            
            resources_with_costs.append(resource_copy)
        
        return resources_with_costs
    
    def _get_pricing_service_costs(
        self,
        resources: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get costs from pricing service API.
        
        Returns:
            Dictionary mapping resource_id to cost data
        """
        costs = {}
        
        try:
            # Prepare bulk pricing request
            pricing_resources = []
            for resource in resources:
                pricing_resources.append({
                    "resource_type": resource.get('resource_type'),
                    "resource_id": resource.get('resource_id'),
                    "region": resource.get('region')
                })
            
            # Call pricing service bulk endpoint
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.pricing_service_url}/api/v1/pricing/bulk",
                    json={
                        "cloud_provider": "aws",
                        "resources": pricing_resources
                    }
                )
                
                if response.status_code == 200:
                    pricing_results = response.json()
                    for result in pricing_results:
                        resource_id = result.get('resource_id')
                        if resource_id:
                            # Extract monthly cost from pricing response
                            pricing = result.get('pricing', {})
                            monthly_cost = self._extract_monthly_cost(pricing, result.get('resource_type'))
                            
                            costs[resource_id] = {
                                'monthly_cost': monthly_cost,
                                'cost_breakdown': pricing,
                                'currency': result.get('currency', 'USD'),
                                'unit': result.get('unit', 'Hrs')
                            }
                else:
                    logger.warning(f"Pricing service returned {response.status_code}, falling back to estimates")
                    return self._get_estimate_costs(resources)
                    
        except Exception as e:
            logger.warning(f"Failed to get costs from pricing service: {e}, falling back to estimates")
            return self._get_estimate_costs(resources)
        
        return costs
    
    def _get_estimate_costs(
        self,
        resources: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get cost estimates using fallback estimation functions.
        
        Returns:
            Dictionary mapping resource_id to cost data
        """
        costs = {}
        
        for resource in resources:
            resource_id = resource.get('resource_id')
            resource_type = resource.get('resource_type', '').lower()
            metadata = resource.get('metadata', {})
            
            monthly_cost = None
            cost_breakdown = {}
            
            if resource_type == 'ec2':
                instance_type = metadata.get('instance_type') or metadata.get('InstanceType')
                if instance_type:
                    monthly_cost = estimate_ec2_monthly_cost(instance_type)
                    cost_breakdown = {
                        'instance_type': instance_type,
                        'estimated_monthly': monthly_cost
                    }
            
            elif resource_type == 'rds' or resource_type == 'rds_instance':
                instance_class = metadata.get('instance_class') or metadata.get('DBInstanceClass')
                if instance_class:
                    monthly_cost = estimate_rds_monthly_cost(instance_class)
                    cost_breakdown = {
                        'instance_class': instance_class,
                        'estimated_monthly': monthly_cost
                    }
            
            elif resource_type == 'ebs':
                size_gb = metadata.get('size') or metadata.get('Size')
                volume_type = metadata.get('volume_type') or metadata.get('VolumeType') or 'gp2'
                if size_gb:
                    monthly_cost = estimate_ebs_monthly_cost(int(size_gb), volume_type)
                    cost_breakdown = {
                        'size_gb': size_gb,
                        'volume_type': volume_type,
                        'estimated_monthly': monthly_cost
                    }
            
            elif resource_type == 'snapshot':
                size_gb = metadata.get('size') or metadata.get('VolumeSize')
                if size_gb:
                    monthly_cost = estimate_snapshot_monthly_cost(int(size_gb))
                    cost_breakdown = {
                        'size_gb': size_gb,
                        'estimated_monthly': monthly_cost
                    }
            
            if monthly_cost is not None:
                costs[resource_id] = {
                    'monthly_cost': monthly_cost,
                    'cost_breakdown': cost_breakdown,
                    'currency': 'USD',
                    'unit': 'month',
                    'source': 'estimate'
                }
            else:
                # No estimate available
                costs[resource_id] = {
                    'monthly_cost': None,
                    'cost_breakdown': {},
                    'currency': 'USD',
                    'unit': 'month',
                    'source': 'unknown'
                }
        
        return costs
    
    def _extract_monthly_cost(
        self,
        pricing: Dict[str, Any],
        resource_type: Optional[str] = None
    ) -> Optional[float]:
        """
        Extract monthly cost from pricing service response.
        
        Args:
            pricing: Pricing data from service
            resource_type: Resource type for context
            
        Returns:
            Monthly cost in USD or None
        """
        if not pricing:
            return None
        
        # Try to get on-demand monthly cost
        on_demand = pricing.get('on_demand')
        if on_demand:
            hourly = on_demand.get('hourly')
            if hourly:
                # Convert hourly to monthly (730 hours per month)
                return float(hourly) * 730
        
        # Try direct monthly field
        monthly = pricing.get('monthly')
        if monthly:
            return float(monthly)
        
        # Try annual and convert
        annual = pricing.get('annual')
        if annual:
            return float(annual) / 12
        
        return None
    
    def aggregate_costs(
        self,
        resources_with_costs: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Aggregate cost data across resources.
        
        Args:
            resources_with_costs: List of resources with cost data
            
        Returns:
            Aggregated cost statistics
        """
        total_cost = 0.0
        cost_by_type = {}
        cost_by_region = {}
        count_by_type = {}
        resources_with_valid_costs = []
        
        for resource in resources_with_costs:
            resource_type = resource.get('resource_type', 'unknown')
            region = resource.get('region', 'unknown')
            monthly_cost = resource.get('monthly_cost')
            
            # Count by type
            count_by_type[resource_type] = count_by_type.get(resource_type, 0) + 1
            
            if monthly_cost is not None:
                total_cost += monthly_cost
                resources_with_valid_costs.append(resource)
                
                # Aggregate by type
                cost_by_type[resource_type] = cost_by_type.get(resource_type, 0.0) + monthly_cost
                
                # Aggregate by region
                cost_by_region[region] = cost_by_region.get(region, 0.0) + monthly_cost
        
        avg_cost = total_cost / len(resources_with_valid_costs) if resources_with_valid_costs else 0.0
        
        return {
            'total_monthly_cost': round(total_cost, 2),
            'cost_by_resource_type': {k: round(v, 2) for k, v in cost_by_type.items()},
            'cost_by_region': {k: round(v, 2) for k, v in cost_by_region.items()},
            'resource_count_by_type': count_by_type,
            'average_cost_per_resource': round(avg_cost, 2),
            'resources_with_cost': len(resources_with_valid_costs),
            'resources_without_cost': len(resources_with_costs) - len(resources_with_valid_costs)
        }

