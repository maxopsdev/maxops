"""Lambda Provisioned Concurrency Low Usage Check - identifies unnecessary provisioned concurrency."""
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


def estimate_provisioned_concurrency_cost(memory_mb: int, provisioned_units: int) -> float:
    """
    Estimate monthly cost for provisioned concurrency.
    
    Args:
        memory_mb: Allocated memory in MB
        provisioned_units: Number of provisioned concurrency units
    
    Returns:
        Estimated monthly cost in USD
    """
    # Provisioned concurrency pricing (US East 1 as of 2024):
    # - $0.0000041667 per GB-second
    # - Charged for all time the concurrency is provisioned (24/7)
    
    hours_per_month = 24 * 30  # 720 hours
    seconds_per_month = hours_per_month * 3600
    gb = memory_mb / 1024
    
    cost = gb * provisioned_units * seconds_per_month * 0.0000041667
    return cost


def _round_currency(value: float) -> float:
    """Preserve precision for tiny Lambda costs while keeping UI-friendly values."""
    if value == 0:
        return 0.0
    return round(value, 6) if abs(value) < 1 else round(value, 2)


def check_lambda_provisioned_concurrency_low_usage(
    aws_adapter,
    lookback_days: int = 7,
    utilization_threshold: float = 60.0,  # percent
    min_invocations: int = 100,  # minimum invocations to consider
    region: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Check for Lambda functions with underutilized provisioned concurrency.
    
    Identifies functions with provisioned concurrency that isn't being fully
    utilized, resulting in unnecessary costs.
    
    Args:
        aws_adapter: AWS adapter with credentials
        lookback_days: Number of days to analyze (default: 7)
        utilization_threshold: Max utilization % to flag (default: 60%)
        min_invocations: Minimum invocations to consider (default: 100)
        region: Optional specific region to check
    
    Returns:
        List of Lambda functions with underutilized provisioned concurrency
    """
    print(f"[LAMBDA_CONCURRENCY_CHECK] Starting check - lookback_days={lookback_days}, threshold={utilization_threshold}%, min_invocations={min_invocations}, region={region}")
    
    # Get all Lambda functions
    functions = aws_adapter.get_resources('lambda', {}, region)
    print(f"[LAMBDA_CONCURRENCY_CHECK] Found {len(functions)} Lambda functions")
    
    if len(functions) == 0:
        print(f"[LAMBDA_CONCURRENCY_CHECK] WARNING: No Lambda functions found")
        return []
    
    underutilized_functions = []
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=lookback_days)
    
    for function in functions:
        try:
            function_name = function['resource_id']
            func_region = function.get('region') or region
            metadata = function.get('metadata', {})
            
            # Check if function has provisioned concurrency
            pc_config = metadata.get('ProvisionedConcurrencyConfig')
            if not pc_config or pc_config.get('RequestedProvisionedConcurrentExecutions') is None:
                continue  # Skip functions without provisioned concurrency
            
            provisioned_concurrent_executions = pc_config.get('RequestedProvisionedConcurrentExecutions', 0)
            if provisioned_concurrent_executions == 0:
                continue  # Skip functions without provisioned concurrency
            
            print(f"[LAMBDA_CONCURRENCY_CHECK] Checking function {function_name} with {provisioned_concurrent_executions} provisioned units")
            
            # Get metrics from CloudWatch
            utilization = aws_adapter.get_resource_utilization(
                function_name,
                'lambda',
                start_date,
                end_date,
                region=func_region
            )
            
            invocations = utilization.get('invocations', 0)
            concurrent_executions_max = utilization.get('max_concurrent_executions', utilization.get('concurrent_executions_max', 0))
            concurrent_executions_avg = utilization.get('avg_concurrent_executions', utilization.get('concurrent_executions_avg', 0))
            provisioned_concurrency_invocations = utilization.get('provisionedconcurrencyinvocations', 0)  # Optional metric
            provisioned_concurrency_spillover = utilization.get('provisionedconcurrencyspilloverinvocations', 0)  # Optional metric
            
            memory_mb = metadata.get('MemorySize', metadata.get('memory_size', 128))
            
            print(f"[LAMBDA_CONCURRENCY_CHECK] {function_name}: Provisioned={provisioned_concurrent_executions}, "
                  f"MaxConcurrent={concurrent_executions_max}, AvgConcurrent={concurrent_executions_avg}, "
                  f"Invocations={invocations}, ProvisionedInvocations={provisioned_concurrency_invocations}")
            
            # Skip functions with insufficient data
            if invocations < min_invocations:
                print(f"[LAMBDA_CONCURRENCY_CHECK] {function_name}: Skipping (only {invocations} invocations, need {min_invocations})")
                continue
            
            # Calculate utilization of provisioned concurrency
            if provisioned_concurrent_executions > 0 and concurrent_executions_max > 0:
                utilization_pct = (concurrent_executions_max / provisioned_concurrent_executions) * 100
            else:
                utilization_pct = 0
            
            print(f"[LAMBDA_CONCURRENCY_CHECK] {function_name}: Utilization={utilization_pct:.1f}%")
            
            # Check if underutilized
            if utilization_pct < utilization_threshold:
                # Calculate recommended provisioned concurrency
                recommended_concurrency = max(1, int(concurrent_executions_max * 1.2))  # Add 20% buffer
                if recommended_concurrency >= provisioned_concurrent_executions:
                    recommended_concurrency = provisioned_concurrent_executions  # Already optimal
                    continue
                
                # Calculate costs
                current_monthly_cost = estimate_provisioned_concurrency_cost(
                    memory_mb, provisioned_concurrent_executions
                )
                optimized_monthly_cost = estimate_provisioned_concurrency_cost(
                    memory_mb, recommended_concurrency
                )
                potential_savings_monthly = current_monthly_cost - optimized_monthly_cost
                
                # Add check-specific metadata
                metadata['lookback_days'] = lookback_days
                metadata['provisioned_concurrent_executions'] = provisioned_concurrent_executions
                metadata['concurrent_executions_max'] = concurrent_executions_max
                metadata['concurrent_executions_avg'] = concurrent_executions_avg
                metadata['utilization_pct'] = round(utilization_pct, 2)
                metadata['recommended_concurrency'] = recommended_concurrency
                metadata['invocations'] = invocations
                metadata['provisioned_concurrency_invocations'] = provisioned_concurrency_invocations
                metadata['provisioned_concurrency_spillover'] = provisioned_concurrency_spillover
                metadata['current_monthly_cost'] = _round_currency(current_monthly_cost)
                metadata['optimized_monthly_cost'] = _round_currency(optimized_monthly_cost)
                metadata['potential_savings_monthly'] = _round_currency(potential_savings_monthly)
                metadata['potential_savings_yearly'] = _round_currency(potential_savings_monthly * 12)
                metadata['recommended_action'] = 'reduce_provisioned_concurrency' if recommended_concurrency > 0 else 'disable_provisioned_concurrency'
                metadata['check_reason'] = create_check_reason('underutilized_provisioned_concurrency', {
                    'provisioned': provisioned_concurrent_executions,
                    'max_concurrent': concurrent_executions_max,
                    'utilization_pct': round(utilization_pct, 2),
                    'recommended': recommended_concurrency
                })
                
                function['metadata'] = metadata
                underutilized_functions.append(function)
                
                print(f"[LAMBDA_CONCURRENCY_CHECK] {function_name}: UNDERUTILIZED - "
                      f"Recommend reducing from {provisioned_concurrent_executions} to {recommended_concurrency} units "
                      f"(potential savings: ${potential_savings_monthly:.2f}/month)")
        
        except Exception as e:
            print(f"[LAMBDA_CONCURRENCY_CHECK] Error checking {function.get('resource_id', 'unknown')}: {e}")
            continue
    
    print(f"[LAMBDA_CONCURRENCY_CHECK] Found {len(underutilized_functions)} underutilized functions")
    return underutilized_functions


# Auto-register check
check_registry.register(CheckMetadata(
    check_id='lambda_provisioned_concurrency_low_usage',
    name='Lambda Provisioned Concurrency Low Usage',
    description='Identifies Lambda functions with underutilized provisioned concurrency',
    resource_type='lambda',
    check_function=check_lambda_provisioned_concurrency_low_usage,
    default_action='reduce_provisioned_concurrency',
    parameters={
        'lookback_days': 7,
        'utilization_threshold': 60.0,
        'min_invocations': 100,
        'region': None
    }
))
