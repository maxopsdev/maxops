"""Policy service for managing and executing policies."""
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.models.policy import Policy, PolicyExecution, PolicyExecutionResult
from app.schemas.policy import PolicyCreate, PolicyUpdate, PolicyValidationResponse
from app.adapters.aws.adapter import AWSAdapter
from app.utils.settings_guard import require_account_region, get_settings_scope, get_settings_regions
from app.utils.policy_id_generator import generate_policy_code
from app.checks.registry import check_registry
from app.services.pricing_integration import PricingIntegration
from app.services.settings_service import get_effective_check_parameters

# Deprecated imports - kept for backward compatibility during migration
try:
    from app.utils.policy_parser import PolicyParser, PolicyValidator
    from app.utils.filter_registry import validate_filter
    from app.utils.execution_registry import execution_registry
    _LEGACY_MODE = True
except ImportError:
    _LEGACY_MODE = False


class PolicyService:
    """Service for policy management and execution."""
    
    def __init__(self, db: Session, default_region: Optional[str] = None):
        """Initialize policy service."""
        self.db = db
        self.default_region = default_region
        self.aws_adapter = AWSAdapter(default_region=default_region)
        self.pricing_integration = PricingIntegration()

        # Legacy support - only initialize if in legacy mode
        if _LEGACY_MODE:
            self.parser = PolicyParser()
            self.validator = PolicyValidator()
        else:
            self.parser = None
            self.validator = None
    
    def create_policy(self, policy_data: PolicyCreate) -> Policy:
        """Create a new policy."""
        # New check-based approach
        if policy_data.check_id:
            # Validate check_id exists in registry
            check = check_registry.get_check(policy_data.check_id)
            if not check:
                raise ValueError(f"Check '{policy_data.check_id}' not found in registry")

            # Merge user params with check defaults
            parameters = {**check.parameters, **(policy_data.parameters_json or {})}
            resource_type = check.resource_type

            policy = Policy(
                policy_code=generate_policy_code(self.db),
                name=policy_data.name,
                description=policy_data.description,
                check_id=policy_data.check_id,
                parameters_json=parameters,
                resource_type=resource_type,
                status=policy_data.status
            )

        # Legacy approach: filters_json
        elif policy_data.filters_json and _LEGACY_MODE:
            resource_type = policy_data.resource_type
            errors = []
            for filter_cond in policy_data.filters_json:
                is_valid, error_msg = validate_filter(resource_type, filter_cond)
                if not is_valid:
                    errors.append(error_msg)

            if errors:
                raise ValueError(f"Invalid filters: {', '.join(errors)}")

            policy = Policy(
                policy_code=generate_policy_code(self.db),
                name=policy_data.name,
                description=policy_data.description,
                filters_json=policy_data.filters_json,
                resource_type=resource_type,
                status=policy_data.status
            )

        # Legacy approach: YAML
        elif policy_data.policy_yaml and _LEGACY_MODE:
            is_valid, errors, warnings = self.validator.validate(policy_data.policy_yaml)
            if not is_valid:
                raise ValueError(f"Invalid policy: {', '.join(errors)}")

            parsed = self.parser.parse(policy_data.policy_yaml)
            resource_type = policy_data.resource_type or self.parser.extract_resource_type(parsed)

            policy = Policy(
                policy_code=generate_policy_code(self.db),
                name=policy_data.name,
                description=policy_data.description,
                policy_yaml=policy_data.policy_yaml,
                resource_type=resource_type,
                status=policy_data.status
            )

        else:
            raise ValueError("Either check_id (new approach) or filters_json/policy_yaml (legacy) must be provided")

        self.db.add(policy)
        self.db.commit()
        self.db.refresh(policy)
        return policy
    
    def get_policy(self, policy_id: int) -> Optional[Policy]:
        """Get a policy by ID."""
        return self.db.query(Policy).filter(Policy.id == policy_id).first()
    
    def get_policy_by_code(self, policy_code: str) -> Optional[Policy]:
        """Get a policy by policy_code."""
        return self.db.query(Policy).filter(Policy.policy_code == policy_code).first()
    
    def get_policies(self, skip: int = 0, limit: int = 100, status: Optional[str] = None) -> List[Policy]:
        """Get all policies with optional filtering."""
        query = self.db.query(Policy)
        
        if status:
            query = query.filter(Policy.status == status)
        
        return query.offset(skip).limit(limit).all()
    
    def update_policy(self, policy_id: int, policy_data: PolicyUpdate) -> Optional[Policy]:
        """Update a policy."""
        policy = self.get_policy(policy_id)
        if not policy:
            return None

        # Update parameters_json if provided (new check-based approach)
        if policy_data.parameters_json is not None and policy.check_id:
            # Validate that check still exists
            check = check_registry.get_check(policy.check_id)
            if not check:
                raise ValueError(f"Check '{policy.check_id}' not found in registry")

            # Merge with existing parameters
            parameters = {**policy.parameters_json, **policy_data.parameters_json}
            policy.parameters_json = parameters

        # filters_json is still used by UI for check-level filtering metadata.
        elif policy_data.filters_json is not None:
            if _LEGACY_MODE:
                resource_type = policy_data.resource_type or policy.resource_type
                errors = []
                for filter_cond in policy_data.filters_json:
                    is_valid, error_msg = validate_filter(resource_type, filter_cond)
                    if not is_valid:
                        errors.append(error_msg)

                if errors:
                    raise ValueError(f"Invalid filters: {', '.join(errors)}")

            policy.filters_json = policy_data.filters_json

        # Legacy validation for YAML (backward compatibility)
        elif hasattr(policy_data, 'policy_yaml') and policy_data.policy_yaml and _LEGACY_MODE:
            is_valid, errors, warnings = self.validator.validate(policy_data.policy_yaml)
            if not is_valid:
                raise ValueError(f"Invalid policy: {', '.join(errors)}")
        
        # Generate policy_code if it doesn't exist (for existing policies)
        if not policy.policy_code:
            policy.policy_code = generate_policy_code(self.db)

        # Update basic fields
        if policy_data.name is not None:
            policy.name = policy_data.name
        if policy_data.description is not None:
            policy.description = policy_data.description
        if policy_data.status is not None:
            policy.status = policy_data.status

        self.db.commit()
        self.db.refresh(policy)
        return policy
    
    def delete_policy(self, policy_id: int) -> bool:
        """Archive a policy (soft delete)."""
        policy = self.get_policy(policy_id)
        if not policy:
            return False
        policy.status = "inactive"
        self.db.commit()
        return True
    
    def validate_policy(self, policy_yaml: Optional[str] = None) -> PolicyValidationResponse:
        """Validate a policy definition (YAML - backward compatibility)."""
        if not policy_yaml:
            return PolicyValidationResponse(
                valid=False,
                errors=["policy_yaml is required"],
                warnings=[]
            )
        is_valid, errors, warnings = self.validator.validate(policy_yaml)
        return PolicyValidationResponse(
            valid=is_valid,
            errors=errors,
            warnings=warnings
        )
    
    def validate_filters(self, resource_type: str, filters: List[Dict[str, Any]]) -> PolicyValidationResponse:
        """Validate filter conditions."""
        errors = []
        warnings = []
        
        if not resource_type:
            errors.append("resource_type is required")
            return PolicyValidationResponse(valid=False, errors=errors, warnings=warnings)
        
        if not filters:
            warnings.append("No filters specified - policy will match all resources")

        if filters and not _LEGACY_MODE:
            # Filter-based policies are superseded by check-based ones, and the
            # filter registry that backed this validation has been removed.
            # Without this guard the loop below raises NameError, so a request
            # carrying any filter fails as a 500 rather than a clear message.
            warnings.append(
                "Filter validation is no longer available - filter-based policies are "
                "deprecated. Use check_id and parameters_json instead."
            )
        elif filters:
            for filter_cond in filters:
                is_valid, error_msg = validate_filter(resource_type, filter_cond)
                if not is_valid:
                    errors.append(error_msg)

        return PolicyValidationResponse(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings
        )
    
    def execute_policy(
        self,
        policy_id: Optional[int] = None,
        policy_code: Optional[str] = None,
        additional_filters: Optional[Dict[str, Any]] = None
    ) -> PolicyExecution:
        """
        Execute a policy scan and return execution results with cost information.
        
        Args:
            policy_id: Policy ID (optional if policy_code provided)
            policy_code: Policy code (optional if policy_id provided)
            additional_filters: Additional runtime filters
            
        Returns:
            PolicyExecution object with cost data
        """
        # Ensure account and region are configured
        try:
            user_settings = require_account_region(self.db)
        except ValueError as exc:
            raise ValueError(str(exc))

        _, primary_region = get_settings_scope(user_settings)
        if not primary_region:
            raise ValueError("Missing required settings: region")
        if primary_region != self.default_region:
            self.default_region = primary_region
            self.aws_adapter = AWSAdapter(default_region=primary_region)

        # Get policy by ID or code
        if policy_code:
            policy = self.get_policy_by_code(policy_code)
            if not policy:
                raise ValueError(f"Policy with code {policy_code} not found")
        elif policy_id:
            policy = self.get_policy(policy_id)
            if not policy:
                raise ValueError(f"Policy {policy_id} not found")
        else:
            raise ValueError("Either policy_id or policy_code must be provided")
        
        if policy.status != "active":
            policy_identifier = policy_code or policy_id
            raise ValueError(f"Policy {policy_identifier} is not active")
        
        # Create execution record - use policy.id (not policy_id parameter which might be None)
        # Always use "scan" as execution type (no more dry-run/apply distinction)
        execution = PolicyExecution(
            policy_id=policy.id,
            execution_type="scan",
            status="running"
        )
        self.db.add(execution)
        self.db.commit()
        self.db.refresh(execution)
        
        try:
            # New check-based approach
            if policy.check_id:
                matching_resources = self._execute_check_with_settings_scope(
                    check_id=policy.check_id,
                    parameters=policy.parameters_json,
                    user_settings=user_settings,
                )

                # Get check metadata for action determination
                resource_type = policy.resource_type

            # Legacy approach: filters_json
            elif policy.filters_json and _LEGACY_MODE:
                filters = policy.filters_json
                resource_type = policy.resource_type

                # Use execution registry to get resources
                try:
                    resources = execution_registry.execute_policy(
                        adapter=self.aws_adapter,
                        resource_type=resource_type,
                        filters=filters
                    )
                except (ValueError, NameError):
                    # Fallback to old method
                    aws_filters = self._convert_filters_to_aws(filters, additional_filters)
                    resources = self.aws_adapter.get_resources(resource_type, aws_filters)

                # Apply policy filters
                matching_resources = self._apply_filters(resources, filters, resource_type)

            # Legacy approach: YAML
            elif policy.policy_yaml and _LEGACY_MODE:
                parsed = self.parser.parse(policy.policy_yaml)
                filters = self.parser.extract_filters(parsed)
                resource_type = self.parser.extract_resource_type(parsed)

                aws_filters = self._convert_filters_to_aws(filters, additional_filters)
                resources = self.aws_adapter.get_resources(resource_type, aws_filters)
                matching_resources = self._apply_filters(resources, filters, resource_type)

            else:
                raise ValueError("Policy has neither check_id nor filters_json/policy_yaml")

            # Calculate costs for all matching resources
            resources_with_costs = self.pricing_integration.calculate_costs(matching_resources)
            
            # Aggregate cost data
            cost_aggregation = self.pricing_integration.aggregate_costs(resources_with_costs)
            
            # Store initial results count
            execution.resources_found = len(matching_resources)
            
            # Store results_json with cost aggregation
            execution.results_json = {
                "total_resources_checked": len(matching_resources),
                "matching_resources": len(matching_resources),
                "execution_type": "scan",
                "total_monthly_cost": cost_aggregation.get('total_monthly_cost'),
                "cost_by_resource_type": cost_aggregation.get('cost_by_resource_type'),
                "cost_by_region": cost_aggregation.get('cost_by_region'),
                "resource_count_by_type": cost_aggregation.get('resource_count_by_type'),
                "average_cost_per_resource": cost_aggregation.get('average_cost_per_resource'),
                "resources_with_cost": cost_aggregation.get('resources_with_cost'),
                "resources_without_cost": cost_aggregation.get('resources_without_cost')
            }

            # Create detailed results with cost information
            for resource in resources_with_costs:
                # Build metadata from resource
                metadata = resource.get('metadata', {}).copy()
                
                # Add cost data to metadata
                if resource.get('monthly_cost') is not None:
                    metadata['monthly_cost'] = resource.get('monthly_cost')
                    metadata['cost_breakdown'] = resource.get('cost_breakdown', {})
                    metadata['cost_source'] = resource.get('cost_breakdown', {}).get('source', 'pricing_service')

                # Get reason from check metadata or generate from filters
                reason = resource.get('metadata', {}).get('check_reason')
                if not reason and policy.filters_json and _LEGACY_MODE:
                    reason = self._generate_reason(resource, policy.filters_json)
                elif not reason:
                    reason = "Matches policy criteria"

                result = PolicyExecutionResult(
                    execution_id=execution.id,
                    resource_id=resource['resource_id'],
                    resource_type=resource['resource_type'],
                    resource_name=resource.get('resource_name'),
                    region=resource.get('region'),
                    account_id=resource.get('account_id'),
                    reason=reason,
                    metadata_json=metadata
                )
                self.db.add(result)
            
            execution.status = "completed"
            execution.completed_at = datetime.now(timezone.utc)
            
        except Exception as e:
            execution.status = "failed"
            execution.error_message = str(e)
            execution.completed_at = datetime.now(timezone.utc)
        
        self.db.commit()
        self.db.refresh(execution)
        return execution

    def _execute_check_with_settings_scope(
        self,
        check_id: str,
        parameters: Optional[Dict[str, Any]],
        user_settings,
    ) -> List[Dict[str, Any]]:
        account, _ = get_settings_scope(user_settings)
        regions = get_settings_regions(user_settings)
        if not regions:
            return []

        aggregated_resources: List[Dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        resolved_parameters = get_effective_check_parameters(self.db, check_id, parameters)

        for region in regions:
            aws_adapter = AWSAdapter(default_region=region)
            region_resources = check_registry.execute_check(
                check_id=check_id,
                aws_adapter=aws_adapter,
                parameters=resolved_parameters,
            )

            for resource in region_resources:
                normalized_resource = dict(resource)
                if not normalized_resource.get("region"):
                    normalized_resource["region"] = region
                if not normalized_resource.get("account_id") and account:
                    normalized_resource["account_id"] = account

                key = (
                    str(normalized_resource.get("resource_type") or ""),
                    str(normalized_resource.get("resource_id") or ""),
                    str(normalized_resource.get("region") or ""),
                    str(normalized_resource.get("account_id") or ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                aggregated_resources.append(normalized_resource)

        return aggregated_resources
    
    def get_execution(self, execution_id: int) -> Optional[PolicyExecution]:
        """Get an execution by ID."""
        return self.db.query(PolicyExecution).filter(PolicyExecution.id == execution_id).first()
    
    def get_executions(self, policy_id: int, limit: int = 50) -> List[PolicyExecution]:
        """Get execution history for a policy."""
        return (
            self.db.query(PolicyExecution)
            .filter(PolicyExecution.policy_id == policy_id)
            .order_by(PolicyExecution.started_at.desc())
            .limit(limit)
            .all()
        )
    
    def get_all_executions(
        self, 
        limit: int = 100, 
        skip: int = 0,
        policy_id: Optional[int] = None,
        status: Optional[str] = None,
        execution_type: Optional[str] = None
    ) -> List[PolicyExecution]:
        """Get all executions across all policies with optional filters."""
        query = self.db.query(PolicyExecution)
        
        if policy_id:
            query = query.filter(PolicyExecution.policy_id == policy_id)
        if status:
            query = query.filter(PolicyExecution.status == status)
        if execution_type:
            query = query.filter(PolicyExecution.execution_type == execution_type)
        
        return (
            query
            .order_by(PolicyExecution.started_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )
    
    def _convert_filters_to_aws(
        self,
        filters: List[Dict[str, Any]],
        additional_filters: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Convert policy filters to AWS API filters."""
        aws_filters = {}
        
        for filter_item in filters:
            filter_type = filter_item.get('type')
            
            if filter_type == 'tag':
                if 'tag' not in aws_filters:
                    aws_filters['tag'] = {}
                aws_filters['tag']['key'] = filter_item.get('key')
                aws_filters['tag']['value'] = filter_item.get('value')
            elif filter_type == 'state':
                aws_filters['state'] = filter_item.get('value', 'running')
        
        # Merge additional filters
        if additional_filters:
            aws_filters.update(additional_filters)
        
        return aws_filters
    
    def _apply_filters(
        self,
        resources: List[Dict[str, Any]],
        filters: List[Dict[str, Any]],
        resource_type: str
    ) -> List[Dict[str, Any]]:
        """Apply policy filters to resources."""
        matching_resources = []
        
        for resource in resources:
            matches = True
            
            for filter_item in filters:
                filter_type = filter_item.get('type')
                operator = filter_item.get('operator', 'equals')
                value = filter_item.get('value')
                
                if filter_type == 'idle':
                    # Check if resource is idle
                    if not self._is_resource_idle(resource, filter_item, resource_type):
                        matches = False
                        break
                elif filter_type == 'tags':
                    # Check tag filter with operator support
                    if not self._matches_tag_filter(resource, filter_item, operator):
                        matches = False
                        break
                elif filter_type == 'state':
                    # Check state filter with operator support
                    if not self._matches_state_filter(resource, filter_item, operator, value):
                        matches = False
                        break
                elif filter_type == 'region':
                    # Check region filter
                    if not self._matches_region_filter(resource, operator, value):
                        matches = False
                        break
                elif filter_type == 'name':
                    # Check name filter
                    if not self._matches_name_filter(resource, operator, value):
                        matches = False
                        break
                elif filter_type == 'unattached':
                    # Check if resource is unattached
                    if not self._is_unattached(resource, resource_type):
                        matches = False
                        break
                elif filter_type == 'age':
                    # Check age filter (for snapshots)
                    if not self._matches_age_filter(resource, operator, value):
                        matches = False
                        break
            
            if matches:
                matching_resources.append(resource)
        
        return matching_resources
    
    def _is_resource_idle(
        self,
        resource: Dict[str, Any],
        filter_config: Dict[str, Any],
        resource_type: str
    ) -> bool:
        """Check if a resource is idle based on utilization metrics."""
        # Get days from value if operator is greater_than
        operator = filter_config.get('operator', 'greater_than')
        value = filter_config.get('value')
        
        if operator == 'greater_than' and isinstance(value, (int, float)):
            days = value
        else:
            days = filter_config.get('days', 7)
        
        metrics = filter_config.get('metrics', ['CPUUtilization'])
        
        # For MVP, we'll do a simple check
        # In production, we'd fetch CloudWatch metrics
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days)
        
        try:
            utilization = self.aws_adapter.get_resource_utilization(
                resource['resource_id'],
                resource_type,
                start_date,
                end_date
            )
            
            # Check if all specified metrics are below threshold
            for metric in metrics:
                metric_key = metric.lower()
                if metric_key in utilization:
                    value = utilization[metric_key]
                    if value is not None and value > 5.0:  # 5% threshold
                        return False
            
            return True
        except Exception:
            # If we can't get metrics, assume not idle
            return False
    
    def _matches_tag_filter(self, resource: Dict[str, Any], filter_config: Dict[str, Any], operator: str = 'equals') -> bool:
        """Check if resource matches tag filter."""
        tags = resource.get('tags', {})
        value = filter_config.get('value')
        
        # Handle different value formats
        if isinstance(value, dict):
            key = value.get('key')
            tag_value = value.get('value')
        else:
            # Legacy format
            key = filter_config.get('key')
            tag_value = value
        
        if not key:
            return True
        
        if operator == 'exists':
            return key in tags
        elif operator == 'not_exists':
            return key not in tags
        elif operator == 'equals':
            if key not in tags:
                return False
            if tag_value:
                return tags[key] == tag_value
            return True
        elif operator == 'contains':
            if key not in tags:
                return False
            return tag_value.lower() in tags[key].lower() if tag_value else True
        
        return False
    
    def _matches_state_filter(self, resource: Dict[str, Any], filter_config: Dict[str, Any], operator: str = 'equals', value: Any = None) -> bool:
        """Check if resource matches state filter."""
        resource_state = resource.get('state', '').lower()
        filter_state = (value or filter_config.get('value', '')).lower()
        
        if operator == 'equals':
            return resource_state == filter_state
        elif operator == 'in':
            if isinstance(filter_state, list):
                return resource_state in [s.lower() for s in filter_state]
            return resource_state == filter_state
        
        return False
    
    def _matches_region_filter(self, resource: Dict[str, Any], operator: str, value: Any) -> bool:
        """Check if resource matches region filter."""
        resource_region = resource.get('region', '').lower()
        filter_region = (value or '').lower()
        
        if operator == 'equals':
            return resource_region == filter_region
        elif operator == 'in':
            if isinstance(filter_region, list):
                return resource_region in [r.lower() for r in filter_region]
            return resource_region == filter_region
        
        return False
    
    def _matches_name_filter(self, resource: Dict[str, Any], operator: str, value: Any) -> bool:
        """Check if resource matches name filter."""
        resource_name = resource.get('resource_name', '').lower()
        filter_value = (value or '').lower()
        
        if operator == 'equals':
            return resource_name == filter_value
        elif operator == 'contains':
            return filter_value in resource_name
        elif operator == 'starts_with':
            return resource_name.startswith(filter_value)
        elif operator == 'ends_with':
            return resource_name.endswith(filter_value)
        
        return False
    
    def _matches_age_filter(self, resource: Dict[str, Any], operator: str, value: Any) -> bool:
        """Check if resource matches age filter (for snapshots)."""
        if operator != 'greater_than':
            return False
        
        if not isinstance(value, (int, float)):
            return False
        
        # Get creation time from resource
        creation_time = resource.get('start_time') or resource.get('creation_time')
        if not creation_time:
            return False
        
        if isinstance(creation_time, str):
            creation_time = datetime.fromisoformat(creation_time.replace('Z', '+00:00'))
        
        age_days = (datetime.utcnow() - creation_time.replace(tzinfo=None)).days
        return age_days > value
    
    def _is_unattached(self, resource: Dict[str, Any], resource_type: str) -> bool:
        """Check if resource is unattached."""
        if resource_type == 'ebs':
            return not resource.get('attached', False)
        elif resource_type == 'snapshot':
            # Snapshots are always "unattached" in a sense
            # We might want to check if the source volume still exists
            return True
        return False
    
    def _generate_reason(self, resource: Dict[str, Any], filters: List[Dict[str, Any]]) -> str:
        """Generate human-readable reason why resource matched policy."""
        reasons = []
        
        for filter_item in filters:
            filter_type = filter_item.get('type')
            operator = filter_item.get('operator', 'equals')
            value = filter_item.get('value')
            
            if filter_type == 'idle':
                if isinstance(value, (int, float)):
                    days = value
                else:
                    days = filter_item.get('days', 7)
                reasons.append(f"Idle for {days}+ days")
            elif filter_type == 'tags':
                if isinstance(value, dict):
                    key = value.get('key')
                    tag_value = value.get('value')
                else:
                    key = filter_item.get('key')
                    tag_value = value
                if key and tag_value:
                    reasons.append(f"Tag {key}={tag_value}")
                elif key:
                    reasons.append(f"Has tag {key}")
            elif filter_type == 'state':
                reasons.append(f"State: {value}")
            elif filter_type == 'region':
                reasons.append(f"Region: {value}")
            elif filter_type == 'name':
                reasons.append(f"Name {operator} {value}")
            elif filter_type == 'unattached':
                reasons.append("Unattached")
            elif filter_type == 'age':
                reasons.append(f"Age > {value} days")
        
        return "; ".join(reasons) if reasons else "Matches policy filters"

    
    def _determine_action(self, resource_type: str, filters: List[Dict[str, Any]]) -> str:
        """
        Determine the action to perform based on resource type and filters.
        
        Args:
            resource_type: Type of resource
            filters: Policy filters
            
        Returns:
            Action name (stop, terminate, delete)
        """
        # Check filters for action hints
        for filter_item in filters:
            filter_type = filter_item.get('type')
            
            # If filter suggests a specific action, use it
            if filter_type == 'unattached' and resource_type in ['ebs', 'snapshot']:
                return 'delete'
            elif filter_type == 'age' and resource_type == 'snapshot':
                return 'delete'
            elif filter_type == 'unused':
                if resource_type == 'ec2':
                    return 'terminate'  # Terminate unused instances
                elif resource_type == 'rds':
                    return 'delete'  # Delete unused RDS
                else:
                    return 'delete'
        
        # Default actions based on resource type
        if resource_type == 'ec2':
            return 'stop'  # Default: stop idle instances
        elif resource_type == 'rds':
            return 'stop'  # Default: stop idle RDS
        elif resource_type == 'ebs':
            return 'delete'  # Default: delete unattached volumes
        elif resource_type == 'snapshot':
            return 'delete'  # Default: delete old snapshots
        
        return 'delete'  # Fallback

