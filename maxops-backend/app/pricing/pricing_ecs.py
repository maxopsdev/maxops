"""ECS pricing handler."""
from __future__ import annotations


from app.pricing.base import PricingContext


def handle_ecs_pricing(context: PricingContext) -> None:
    """Apply ECS estimate-based pricing and savings metadata."""
    savings_ratio = context.savings_ratio or 0.0
    check_id = context.check_id
    resources = context.resources
    
    cpu_price_per_vcpu_hour = 0.04048
    memory_price_per_gb_hour = 0.004445
    hours_per_month = 730

    for resource in resources:
        metadata = resource.get("metadata") or {}
        launch_type = str(metadata.get("launchType") or "").upper()
        if not launch_type:
            compat = [str(v).upper() for v in (metadata.get("taskRequiresCompatibilities") or [])]
            if "FARGATE" in compat:
                launch_type = "FARGATE"
        try:
            task_cpu = int(metadata.get("taskCpu") or 0)
            task_memory = int(metadata.get("taskMemory") or 0)
            desired_count = int(metadata.get("desiredCount") or 0)
        except (TypeError, ValueError):
            task_cpu = 0
            task_memory = 0
            desired_count = 0

        if launch_type == "FARGATE" and task_cpu > 0 and task_memory > 0:
            per_task_hourly = (task_cpu / 1024.0) * cpu_price_per_vcpu_hour + (task_memory / 1024.0) * memory_price_per_gb_hour
        else:
            per_task_hourly = 0.0
        monthly_cost = per_task_hourly * max(desired_count, 0) * hours_per_month
        baseline_note = None
        if monthly_cost <= 0:
            if check_id == "ecs_services_desired_count_zero":
                has_lb = bool(metadata.get("loadBalancers"))
                has_registry = bool(metadata.get("serviceRegistries"))
                monthly_cost = 18.0 if has_lb else (6.0 if has_registry else 2.0)
                baseline_note = "Estimated cleanup savings for residual service artifacts (LB/TG/logging/service discovery)."
            elif check_id == "ecs_idle_clusters_no_active_services_tasks":
                try:
                    registered_instances = int(metadata.get("registeredContainerInstancesCount") or 0)
                except (TypeError, ValueError):
                    registered_instances = 0
                monthly_cost = (registered_instances * 35.0) if registered_instances > 0 else 1.0
                baseline_note = "Estimated cleanup/operational savings for idle cluster management overhead."
        savings_monthly = monthly_cost * savings_ratio

        resource["pricing"] = {
            "price_per_unit": round(per_task_hourly, 6),
            "unit": "Task-Hrs",
            "currency": "USD",
            "source": "estimate",
            "note": "Estimated using ECS Fargate on-demand rates.",
        }
        metadata["pricing"] = resource["pricing"]
        metadata["estimated_monthly_cost"] = round(monthly_cost, 4)
        metadata["potential_savings_monthly"] = round(savings_monthly, 4)
        metadata["potential_savings_yearly"] = round(savings_monthly * 12, 4)
        if baseline_note:
            metadata["pricing_note"] = baseline_note

