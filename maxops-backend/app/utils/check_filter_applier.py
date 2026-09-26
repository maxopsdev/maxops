"""Utility to apply filters to check results."""
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone


def apply_check_filters(
    resources: List[Dict[str, Any]],
    filters: List[Dict[str, Any]],
    resource_type: str,
    exemptions: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """
    Apply filters and exemptions to resources returned by a check.
    
    Args:
        resources: List of resources from check execution
        filters: List of filter conditions
        resource_type: Resource type for context
        exemptions: List of exemption records with check_id, resource_id, exempted, snoozed_until
        
    Returns:
        Filtered list of resources (excluding exempted and snoozed)
    """
    # Create exemption lookup map
    exemption_map = {}
    if exemptions:
        for exemption in exemptions:
            resource_id = exemption.get('resource_id')
            if resource_id:
                exemption_map[resource_id] = exemption
    
    # First, filter out exempted and snoozed resources
    filtered_resources = []
    current_time = datetime.now(timezone.utc)
    
    for resource in resources:
        resource_id = resource.get('resource_id')
        if not resource_id:
            continue
            
        exemption = exemption_map.get(resource_id)
        
        # Skip if permanently exempted
        if exemption and exemption.get('exempted'):
            continue
            
        # Skip if snoozed and snooze period hasn't expired
        if exemption and exemption.get('snoozed_until'):
            snoozed_until = exemption.get('snoozed_until')
            if isinstance(snoozed_until, str):
                try:
                    snoozed_until = datetime.fromisoformat(snoozed_until.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    pass
            if isinstance(snoozed_until, datetime):
                if snoozed_until.replace(tzinfo=timezone.utc) > current_time:
                    continue
        
        filtered_resources.append(resource)
    
    # Apply regular filters if any
    if not filters:
        return filtered_resources
    
    resources = filtered_resources
    
    matching_resources = []
    
    for resource in resources:
        matches = True
        
        for filter_item in filters:
            filter_type = filter_item.get('type')
            operator = filter_item.get('operator', 'equals')
            value = filter_item.get('value')
            key = filter_item.get('key')  # For tag filters
            
            if filter_type == 'tags':
                # Tag filter: check if resource has matching tag
                resource_tags = resource.get('tags', {}) or resource.get('metadata', {}).get('tags', {})
                if not resource_tags:
                    matches = False
                    break
                
                tag_value = resource_tags.get(key) if key else None
                if not _matches_operator(tag_value, operator, value):
                    matches = False
                    break
                    
            elif filter_type == 'region':
                # Region filter
                resource_region = resource.get('region') or resource.get('metadata', {}).get('region')
                if not _matches_operator(resource_region, operator, value):
                    matches = False
                    break
                    
            elif filter_type == 'name':
                # Name filter
                resource_name = resource.get('resource_name') or resource.get('name') or resource.get('resource_id')
                if not _matches_operator(resource_name, operator, value):
                    matches = False
                    break
                    
            elif filter_type == 'state':
                # State filter
                resource_state = resource.get('state') or resource.get('metadata', {}).get('state')
                if not _matches_operator(resource_state, operator, value):
                    matches = False
                    break
                    
            elif filter_type == 'resource_id':
                # Resource ID filter
                resource_id = resource.get('resource_id')
                if not _matches_operator(resource_id, operator, value):
                    matches = False
                    break
        
        if matches:
            matching_resources.append(resource)
    
    return matching_resources


def _matches_operator(actual_value: Any, operator: str, expected_value: Any) -> bool:
    """
    Check if actual value matches expected value using the specified operator.
    
    Args:
        actual_value: The actual value from the resource
        operator: Comparison operator (equals, contains, starts_with, ends_with, not_equals, greater_than, less_than)
        expected_value: The expected value to compare against
        
    Returns:
        True if match, False otherwise
    """
    if actual_value is None:
        return False
    
    # Convert to strings for text operations
    actual_str = str(actual_value).lower() if isinstance(actual_value, str) else actual_value
    expected_str = str(expected_value).lower() if isinstance(expected_value, str) else expected_value
    
    if operator == 'equals':
        return actual_str == expected_str
    elif operator == 'not_equals':
        return actual_str != expected_str
    elif operator == 'contains':
        return expected_str in actual_str if isinstance(actual_str, str) else False
    elif operator == 'starts_with':
        return actual_str.startswith(expected_str) if isinstance(actual_str, str) else False
    elif operator == 'ends_with':
        return actual_str.endswith(expected_str) if isinstance(actual_str, str) else False
    elif operator == 'greater_than':
        try:
            return float(actual_value) > float(expected_value)
        except (ValueError, TypeError):
            return False
    elif operator == 'less_than':
        try:
            return float(actual_value) < float(expected_value)
        except (ValueError, TypeError):
            return False
    else:
        # Default to equals
        return actual_str == expected_str
