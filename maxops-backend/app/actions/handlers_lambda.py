"""Lambda action handlers."""
from __future__ import annotations

import logging
from fastapi import HTTPException
from app.schemas.check import CheckActionResponse
from app.actions.base import ActionExecutionContext

logger = logging.getLogger("uvicorn.error")


def handle_update_memory_configuration(context: ActionExecutionContext):
    """Update Lambda function memory configuration."""
    try:
        params = context.payload.parameters or {}
        memory_size = params.get("memory_size")
        
        if not memory_size:
            raise HTTPException(status_code=400, detail="Missing memory_size parameter")
        
        try:
            memory_size = int(memory_size)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="memory_size must be an integer")
        
        if memory_size < 128 or memory_size > 10240:
            raise HTTPException(status_code=400, detail="memory_size must be between 128 and 10240 MB")
        
        lambda_client = context.aws_adapter.session.client("lambda", region_name=context.payload.region)
        response = lambda_client.update_function_configuration(
            FunctionName=context.payload.resource_id,
            MemorySize=memory_size
        )
        
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message=f"Lambda memory updated to {memory_size} MB.",
            details={
                "function_name": context.payload.resource_id,
                "memory_size": memory_size,
                "response": {
                    "FunctionName": response.get("FunctionName"),
                    "MemorySize": response.get("MemorySize"),
                    "LastModified": response.get("LastModified"),
                }
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to update Lambda memory configuration",
            extra={
                "check_id": context.check_id,
                "action": context.payload.action,
                "account_id": context.payload.account_id,
                "region": context.payload.region,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update Lambda memory: {str(exc)}"
        )


def handle_reduce_provisioned_concurrency(context: ActionExecutionContext):
    """Reduce or disable Lambda provisioned concurrency."""
    try:
        params = context.payload.parameters or {}
        provisioned_concurrent_executions = params.get("provisioned_concurrent_executions")
        qualifier = params.get("qualifier")
        
        if provisioned_concurrent_executions is None:
            raise HTTPException(status_code=400, detail="Missing provisioned_concurrent_executions parameter")
        
        try:
            provisioned_concurrent_executions = int(provisioned_concurrent_executions)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="provisioned_concurrent_executions must be an integer")
        
        if provisioned_concurrent_executions < 0:
            raise HTTPException(status_code=400, detail="provisioned_concurrent_executions must be >= 0")
        
        lambda_client = context.aws_adapter.session.client("lambda", region_name=context.payload.region)

        # Provisioned concurrency must target an alias/version (not $LATEST).
        # Prefer caller-provided qualifier, otherwise auto-detect from existing config.
        if not qualifier:
            try:
                aliases_response = lambda_client.list_aliases(FunctionName=context.payload.resource_id)
                for alias in aliases_response.get("Aliases", []):
                    alias_name = alias.get("Name")
                    if not alias_name:
                        continue
                    try:
                        lambda_client.get_provisioned_concurrency_config(
                            FunctionName=context.payload.resource_id,
                            Qualifier=alias_name
                        )
                        qualifier = alias_name
                        break
                    except Exception:
                        continue
            except Exception:
                pass

        if not qualifier:
            raise HTTPException(
                status_code=400,
                detail="No provisioned concurrency qualifier found. Provide parameters.qualifier (alias/version) for this function."
            )
        
        if provisioned_concurrent_executions == 0:
            # Delete provisioned concurrency configuration
            try:
                lambda_client.delete_provisioned_concurrency_config(
                    FunctionName=context.payload.resource_id,
                    Qualifier=qualifier
                )
                message = "Provisioned concurrency disabled."
                details = {
                    "function_name": context.payload.resource_id,
                    "provisioned_concurrency": 0,
                    "qualifier": qualifier,
                }
            except Exception as e:
                if "ResourceNotFoundException" in str(e):
                    message = "Provisioned concurrency already disabled."
                    details = {
                        "function_name": context.payload.resource_id,
                        "provisioned_concurrency": 0,
                        "qualifier": qualifier,
                    }
                else:
                    raise
        else:
            # Update provisioned concurrency
            response = lambda_client.put_provisioned_concurrency_config(
                FunctionName=context.payload.resource_id,
                Qualifier=qualifier,
                ProvisionedConcurrentExecutions=provisioned_concurrent_executions
            )
            message = f"Provisioned concurrency set to {provisioned_concurrent_executions}."
            details = {
                "function_name": context.payload.resource_id,
                "provisioned_concurrency": provisioned_concurrent_executions,
                "qualifier": qualifier,
                "response": {
                    "RequestedProvisionedConcurrentExecutions": response.get("RequestedProvisionedConcurrentExecutions"),
                    "Status": response.get("Status"),
                }
            }
        
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message=message,
            details=details,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to update Lambda provisioned concurrency",
            extra={
                "check_id": context.check_id,
                "action": context.payload.action,
                "account_id": context.payload.account_id,
                "region": context.payload.region,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update provisioned concurrency: {str(exc)}"
        )


def handle_set_log_retention(context: ActionExecutionContext):
    """Set CloudWatch Logs retention for Lambda function."""
    try:
        params = context.payload.parameters or {}
        retention_days = params.get("retention_days", 7)
        
        try:
            retention_days = int(retention_days)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="retention_days must be an integer")
        
        # Valid CloudWatch Logs retention values (in days)
        valid_retention = [1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653]
        if retention_days not in valid_retention:
            raise HTTPException(
                status_code=400,
                detail=f"retention_days must be one of: {', '.join(map(str, valid_retention))}"
            )
        
        log_group_name = f"/aws/lambda/{context.payload.resource_id}"
        logs_client = context.aws_adapter.session.client("logs", region_name=context.payload.region)
        
        # Set retention policy
        logs_client.put_retention_policy(
            logGroupName=log_group_name,
            retentionInDays=retention_days
        )
        
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message=f"Log retention set to {retention_days} days for {log_group_name}. "
                    f"Consider reducing log verbosity in function code to further reduce costs.",
            details={
                "function_name": context.payload.resource_id,
                "log_group_name": log_group_name,
                "retention_days": retention_days,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to set Lambda log retention",
            extra={
                "check_id": context.check_id,
                "action": context.payload.action,
                "account_id": context.payload.account_id,
                "region": context.payload.region,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to set log retention: {str(exc)}"
        )
