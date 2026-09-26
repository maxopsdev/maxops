"""Lambda Overprovisioned Memory Check - identifies Lambda functions with excessive memory allocation."""
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


def estimate_lambda_monthly_cost(memory_mb: int, monthly_invocations: int, avg_duration_ms: float) -> float:
    """
    Estimate monthly Lambda cost based on memory, invocations, and duration.
    
    Args:
        memory_mb: Allocated memory in MB
        monthly_invocations: Number of invocations per month
        avg_duration_ms: Average duration in milliseconds
    
    Returns:
        Estimated monthly cost in USD
    """
    # Lambda pricing (US East 1 as of 2024):
    # - $0.0000166667 per GB-second
    # - $0.20 per 1M requests
    
    gb_seconds = (memory_mb / 1024) * (avg_duration_ms / 1000) * monthly_invocations
    compute_cost = gb_seconds * 0.0000166667
    request_cost = (monthly_invocations / 1_000_000) * 0.20
    
    return compute_cost + request_cost


def _round_currency(value: float) -> float:
    """Preserve precision for tiny Lambda costs while keeping UI-friendly values."""
    if value == 0:
        return 0.0
    return round(value, 6) if abs(value) < 1 else round(value, 2)


def check_lambda_overprovisioned_memory(
    aws_adapter,
    lookback_days: int = 7,
    memory_utilization_threshold: float = 60.0,  # percent
    min_invocations: int = 100,  # minimum invocations to consider
    region: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Check for Lambda functions with overprovisioned memory.
    
    Identifies functions where actual memory usage is significantly lower than
    allocated memory, indicating opportunity for rightsizing.
    
    Args:
        aws_adapter: AWS adapter with credentials
        lookback_days: Number of days to analyze (default: 7)
        memory_utilization_threshold: Max memory usage % to flag (default: 60%)
        min_invocations: Minimum invocations to consider (default: 100)
        region: Optional specific region to check
    
    Returns:
        List of Lambda functions with overprovisioned memory
    """
    print(f"[LAMBDA_MEMORY_CHECK] Starting check - lookback_days={lookback_days}, threshold={memory_utilization_threshold}%, min_invocations={min_invocations}, region={region}")
    
    # Get all Lambda functions
    functions = aws_adapter.get_resources('lambda', {}, region)
    print(f"[LAMBDA_MEMORY_CHECK] Found {len(functions)} Lambda functions")
    
    if len(functions) == 0:
        print(f"[LAMBDA_MEMORY_CHECK] WARNING: No Lambda functions found")
        return []
    
    overprovisioned_functions = []
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=lookback_days)
    
    for function in functions:
        try:
            function_name = function['resource_id']
            func_region = function.get('region') or region
            metadata = function.get('metadata', {})
            
            print(f"[LAMBDA_MEMORY_CHECK] Checking function {function_name} in region {func_region}")
            
            # Get metrics from CloudWatch
            utilization = aws_adapter.get_resource_utilization(
                function_name,
                'lambda',
                start_date,
                end_date,
                region=func_region
            )
            
            invocations = utilization.get('invocations', 0)
            max_memory_used_mb = utilization.get('max_memory_used', 0)
            avg_memory_used_mb = utilization.get('avg_memory_used', 0)
            avg_duration_ms = utilization.get('avg_duration', 0)
            errors = utilization.get('errors', 0)
            
            # Get allocated memory from metadata
            allocated_memory_mb = metadata.get('MemorySize', metadata.get('memory_size', 128))
            
            print(f"[LAMBDA_MEMORY_CHECK] {function_name}: Allocated={allocated_memory_mb}MB, "
                  f"AvgUsed={avg_memory_used_mb}MB, MaxUsed={max_memory_used_mb}MB, "
                  f"Invocations={invocations}, AvgDuration={avg_duration_ms}ms")
            
            # Skip functions with insufficient data
            if invocations < min_invocations:
                print(f"[LAMBDA_MEMORY_CHECK] {function_name}: Skipping (only {invocations} invocations, need {min_invocations})")
                continue
            
            # Calculate memory utilization percentage
            if allocated_memory_mb > 0 and max_memory_used_mb > 0:
                memory_utilization_pct = (max_memory_used_mb / allocated_memory_mb) * 100
            else:
                continue
            
            print(f"[LAMBDA_MEMORY_CHECK] {function_name}: Memory utilization={memory_utilization_pct:.1f}%")
            
            # Check if overprovisioned
            if memory_utilization_pct < memory_utilization_threshold:
                # Calculate recommended memory (add 20% buffer above max used)
                recommended_memory_mb = int(max_memory_used_mb * 1.2)
                # Round up to nearest power of 2 or 64 MB increment (Lambda memory must be in 1 MB increments)
                if recommended_memory_mb < 128:
                    recommended_memory_mb = 128
                elif recommended_memory_mb > allocated_memory_mb:
                    recommended_memory_mb = allocated_memory_mb
                
                # Calculate monthly invocations (extrapolate from lookback period)
                monthly_invocations = int((invocations / lookback_days) * 30)
                
                # Calculate current and potential costs
                current_monthly_cost = estimate_lambda_monthly_cost(
                    allocated_memory_mb, monthly_invocations, avg_duration_ms
                )
                optimized_monthly_cost = estimate_lambda_monthly_cost(
                    recommended_memory_mb, monthly_invocations, avg_duration_ms
                )
                potential_savings_monthly = current_monthly_cost - optimized_monthly_cost
                
                # Add check-specific metadata
                metadata['lookback_days'] = lookback_days
                metadata['allocated_memory_mb'] = allocated_memory_mb
                metadata['avg_memory_used_mb'] = avg_memory_used_mb
                metadata['max_memory_used_mb'] = max_memory_used_mb
                metadata['memory_utilization_pct'] = round(memory_utilization_pct, 2)
                metadata['recommended_memory_mb'] = recommended_memory_mb
                metadata['invocations'] = invocations
                metadata['monthly_invocations_est'] = monthly_invocations
                metadata['avg_duration_ms'] = avg_duration_ms
                metadata['errors'] = errors
                metadata['current_monthly_cost'] = _round_currency(current_monthly_cost)
                metadata['optimized_monthly_cost'] = _round_currency(optimized_monthly_cost)
                metadata['potential_savings_monthly'] = _round_currency(potential_savings_monthly)
                metadata['potential_savings_yearly'] = _round_currency(potential_savings_monthly * 12)
                metadata['recommended_action'] = 'update_memory'
                metadata['check_reason'] = create_check_reason('overprovisioned_memory', {
                    'allocated_mb': allocated_memory_mb,
                    'max_used_mb': max_memory_used_mb,
                    'utilization_pct': round(memory_utilization_pct, 2),
                    'recommended_mb': recommended_memory_mb
                })
                
                function['metadata'] = metadata
                overprovisioned_functions.append(function)
                
                print(f"[LAMBDA_MEMORY_CHECK] {function_name}: OVERPROVISIONED - "
                      f"Recommend reducing from {allocated_memory_mb}MB to {recommended_memory_mb}MB "
                      f"(potential savings: ${potential_savings_monthly:.2f}/month)")
        
        except Exception as e:
            print(f"[LAMBDA_MEMORY_CHECK] Error checking {function.get('resource_id', 'unknown')}: {e}")
            continue
    
    print(f"[LAMBDA_MEMORY_CHECK] Found {len(overprovisioned_functions)} overprovisioned functions")
    return overprovisioned_functions


# Auto-register check
check_registry.register(CheckMetadata(
    check_id='lambda_overprovisioned_memory',
    name='Lambda Overprovisioned Memory',
    description='Identifies Lambda functions with memory allocation significantly higher than actual usage',
    resource_type='lambda',
    check_function=check_lambda_overprovisioned_memory,
    default_action='update_memory',
    parameters={
        'lookback_days': 7,
        'memory_utilization_threshold': 60.0,
        'min_invocations': 100,
        'region': None
    }
))
