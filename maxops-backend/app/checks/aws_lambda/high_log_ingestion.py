"""Lambda High Log Ingestion Check - identifies functions with excessive CloudWatch Logs costs."""
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


def estimate_cloudwatch_logs_cost(log_ingestion_gb: float) -> float:
    """
    Estimate monthly CloudWatch Logs cost.
    
    Args:
        log_ingestion_gb: Log ingestion in GB per month
    
    Returns:
        Estimated monthly cost in USD
    """
    # CloudWatch Logs pricing (US East 1 as of 2024):
    # - $0.50 per GB ingested
    # - $0.03 per GB stored per month (first 5 GB free)
    
    ingestion_cost = log_ingestion_gb * 0.50
    storage_cost = max(0, log_ingestion_gb - 5) * 0.03  # First 5 GB free
    
    return ingestion_cost + storage_cost


def _round_currency(value: float) -> float:
    """Preserve precision for tiny Lambda costs while keeping UI-friendly values."""
    if value == 0:
        return 0.0
    return round(value, 6) if abs(value) < 1 else round(value, 2)


def check_lambda_high_log_ingestion(
    aws_adapter,
    lookback_days: int = 7,
    log_ingestion_threshold_mb: float = 1.0,  # MB per day
    min_invocations: int = 100,
    region: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Check for Lambda functions with high CloudWatch Logs ingestion.
    
    Identifies functions generating excessive logs, which can be reduced
    by lowering log verbosity or setting appropriate retention policies.
    
    Args:
        aws_adapter: AWS adapter with credentials
        lookback_days: Number of days to analyze (default: 7)
        log_ingestion_threshold_mb: Log ingestion threshold in MB/day (default: 1)
        min_invocations: Minimum invocations to consider (default: 100)
        region: Optional specific region to check
    
    Returns:
        List of Lambda functions with high log ingestion
    """
    print(f"[LAMBDA_LOGS_CHECK] Starting check - lookback_days={lookback_days}, threshold={log_ingestion_threshold_mb}MB/day, min_invocations={min_invocations}, region={region}")
    
    # Get all Lambda functions
    functions = aws_adapter.get_resources('lambda', {}, region)
    print(f"[LAMBDA_LOGS_CHECK] Found {len(functions)} Lambda functions")
    
    if len(functions) == 0:
        print(f"[LAMBDA_LOGS_CHECK] WARNING: No Lambda functions found")
        return []
    
    high_log_functions = []
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=lookback_days)
    
    for function in functions:
        try:
            function_name = function['resource_id']
            func_region = function.get('region') or region
            metadata = function.get('metadata', {})
            
            print(f"[LAMBDA_LOGS_CHECK] Checking function {function_name} in region {func_region}")
            
            # Get log group name
            log_group_name = f"/aws/lambda/{function_name}"
            
            # Get metrics from CloudWatch
            utilization = aws_adapter.get_resource_utilization(
                function_name,
                'lambda',
                start_date,
                end_date,
                region=func_region
            )
            
            invocations = utilization.get('invocations', 0)
            log_bytes_ingested = utilization.get('log_bytes_ingested', 0)
            
            # Get log group retention
            log_retention_days = metadata.get('log_retention_days', 'Never expire')
            
            print(f"[LAMBDA_LOGS_CHECK] {function_name}: Invocations={invocations}, "
                  f"LogBytes={log_bytes_ingested}, LogRetention={log_retention_days}")
            
            # Skip functions with insufficient data
            if invocations < min_invocations:
                print(f"[LAMBDA_LOGS_CHECK] {function_name}: Skipping (only {invocations} invocations, need {min_invocations})")
                continue
            
            # Calculate daily log ingestion
            log_mb_total = log_bytes_ingested / (1024 * 1024)
            log_mb_per_day = log_mb_total / lookback_days if lookback_days > 0 else 0
            log_mb_per_invocation = log_mb_total / invocations if invocations > 0 else 0
            
            print(f"[LAMBDA_LOGS_CHECK] {function_name}: LogMB/day={log_mb_per_day:.2f}, LogMB/invocation={log_mb_per_invocation:.4f}")
            
            # Check if exceeds threshold
            if log_mb_per_day > log_ingestion_threshold_mb:
                # Calculate monthly costs
                monthly_log_gb = (log_mb_per_day * 30) / 1024
                current_log_cost = estimate_cloudwatch_logs_cost(monthly_log_gb)
                
                # Estimate potential savings (assume 50% reduction with log optimization)
                optimized_log_cost = current_log_cost * 0.5
                potential_savings_monthly = current_log_cost - optimized_log_cost
                
                # Add check-specific metadata
                metadata['lookback_days'] = lookback_days
                metadata['log_bytes_ingested'] = log_bytes_ingested
                metadata['log_mb_total'] = round(log_mb_total, 2)
                metadata['log_mb_per_day'] = round(log_mb_per_day, 2)
                metadata['log_mb_per_invocation'] = round(log_mb_per_invocation, 4)
                metadata['log_ingestion_threshold_mb'] = log_ingestion_threshold_mb
                metadata['invocations'] = invocations
                metadata['log_group_name'] = log_group_name
                metadata['log_retention_days'] = log_retention_days
                metadata['monthly_log_gb'] = round(monthly_log_gb, 2)
                metadata['current_log_cost_monthly'] = _round_currency(current_log_cost)
                metadata['optimized_log_cost_monthly'] = _round_currency(optimized_log_cost)
                metadata['potential_savings_monthly'] = _round_currency(potential_savings_monthly)
                metadata['potential_savings_yearly'] = _round_currency(potential_savings_monthly * 12)
                metadata['recommended_action'] = 'reduce_log_verbosity_and_retention'
                metadata['check_reason'] = create_check_reason('high_log_ingestion', {
                    'log_mb_per_day': round(log_mb_per_day, 2),
                    'threshold_mb': log_ingestion_threshold_mb,
                    'log_mb_per_invocation': round(log_mb_per_invocation, 4)
                })
                
                function['metadata'] = metadata
                high_log_functions.append(function)
                
                print(f"[LAMBDA_LOGS_CHECK] {function_name}: HIGH LOG INGESTION - "
                      f"{log_mb_per_day:.2f}MB/day ({log_mb_per_invocation:.4f}MB/invocation) "
                      f"(potential savings: ${potential_savings_monthly:.2f}/month)")
        
        except Exception as e:
            print(f"[LAMBDA_LOGS_CHECK] Error checking {function.get('resource_id', 'unknown')}: {e}")
            continue
    
    print(f"[LAMBDA_LOGS_CHECK] Found {len(high_log_functions)} functions with high log ingestion")
    return high_log_functions


# Auto-register check
check_registry.register(CheckMetadata(
    check_id='lambda_high_log_ingestion',
    name='Lambda High Log Ingestion',
    description='Identifies Lambda functions with excessive CloudWatch Logs ingestion costs',
    resource_type='lambda',
    check_function=check_lambda_high_log_ingestion,
    default_action='reduce_log_verbosity_and_retention',
    parameters={
        'lookback_days': 7,
        'log_ingestion_threshold_mb': 1.0,
        'min_invocations': 100,
        'region': None
    }
))
