"""MCP server entry point for MaxOps."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from maxops_mcp.client import MaxOpsClient, resource_types

mcp = FastMCP("MaxOps MCP")


@mcp.tool()
def list_resource_types() -> list[str]:
    """List MaxOps resource types supported by the MCP server."""
    return resource_types()


@mcp.tool()
async def list_checks(resource_type: str | None = None) -> list[dict[str, Any]]:
    """List MaxOps optimization checks, optionally filtered by resource type."""
    async with MaxOpsClient() as client:
        return await client.list_checks(resource_type)


@mcp.tool()
async def get_latest_check_results() -> dict[str, Any]:
    """Return latest MaxOps check results keyed by check id."""
    async with MaxOpsClient() as client:
        return await client.latest_check_results()


@mcp.tool()
async def get_inventory(resource_type: str, snooze_state: str = "all", limit: int = 100) -> dict[str, Any]:
    """Return inventory resources for a resource type, optionally filtered by snooze state."""
    async with MaxOpsClient() as client:
        return await client.get_inventory(resource_type, snooze_state, limit)


@mcp.tool()
async def query_inventory(
    resource_type: str,
    q: str | None = None,
    account_id: str | None = None,
    region: str | None = None,
    state: str | None = None,
    check_id: str | None = None,
    severity: str | None = None,
    finding_type: str | None = None,
    tag_key: str | None = None,
    tag_value: str | None = None,
    tag_value_mode: str = "equals",
    metadata_key: str | None = None,
    metadata_value: str | None = None,
    snooze_state: str = "all",
    min_savings_yearly: float | None = None,
    max_savings_yearly: float | None = None,
    min_monthly_cost: float | None = None,
    max_monthly_cost: float | None = None,
    sort_by: str = "potential_savings_yearly",
    sort_order: str = "desc",
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """Query inventory with server-side filtering/sorting/pagination in one backend call."""
    async with MaxOpsClient() as client:
        return await client.query_inventory(
            resource_type=resource_type,
            q=q,
            account_id=account_id,
            region=region,
            state=state,
            check_id=check_id,
            severity=severity,
            finding_type=finding_type,
            tag_key=tag_key,
            tag_value=tag_value,
            tag_value_mode=tag_value_mode,
            metadata_key=metadata_key,
            metadata_value=metadata_value,
            snooze_state=snooze_state,
            min_savings_yearly=min_savings_yearly,
            max_savings_yearly=max_savings_yearly,
            min_monthly_cost=min_monthly_cost,
            max_monthly_cost=max_monthly_cost,
            sort_by=sort_by,
            sort_order=sort_order,
            offset=offset,
            limit=limit,
        )


@mcp.tool()
async def query_check_results(
    resource_type: str | None = None,
    status: str | None = None,
    check_id: str | None = None,
    q: str | None = None,
    min_resources_found: int | None = None,
    min_potential_savings_yearly: float | None = None,
    sort_by: str = "potential_savings_yearly",
    sort_order: str = "desc",
    offset: int = 0,
    limit: int = 100,
    include_parameters: bool = False,
    include_checks_without_results: bool = True,
) -> dict[str, Any]:
    """Query latest check results with metadata in one backend call."""
    async with MaxOpsClient() as client:
        return await client.query_check_results(
            resource_type=resource_type,
            status=status,
            check_id=check_id,
            q=q,
            min_resources_found=min_resources_found,
            min_potential_savings_yearly=min_potential_savings_yearly,
            sort_by=sort_by,
            sort_order=sort_order,
            offset=offset,
            limit=limit,
            include_parameters=include_parameters,
            include_checks_without_results=include_checks_without_results,
        )


@mcp.tool()
async def get_tag_cost_savings_summary(
    tag_key: str,
    tag_value: str,
    tag_value_mode: str = "equals",
    resource_type: str | None = None,
    include_details: bool = True,
    details_limit: int = 200,
) -> dict[str, Any]:
    """Fast aggregate totals for monthly cost and potential savings by tag filter."""
    async with MaxOpsClient() as client:
        return await client.get_tag_cost_savings_summary(
            tag_key=tag_key,
            tag_value=tag_value,
            tag_value_mode=tag_value_mode,
            resource_type=resource_type,
            include_details=include_details,
            details_limit=details_limit,
        )


@mcp.tool()
async def snooze_resources(resources: list[dict[str, Any]], snoozed_until: str, reason: str) -> dict[str, Any]:
    """Snooze one or more resources until an ISO datetime. Requires write mode."""
    async with MaxOpsClient() as client:
        return await client.snooze_resources(resources, snoozed_until, reason)


@mcp.tool()
async def remove_snooze(resources: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    """Remove active snoozes from one or more resources. Requires a mandatory audit comment."""
    async with MaxOpsClient() as client:
        return await client.remove_snooze(resources, reason)


@mcp.tool()
async def run_check(check_id: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run a MaxOps check through the backend test-check endpoint. Requires write mode."""
    async with MaxOpsClient() as client:
        return await client.run_check(check_id, parameters)


@mcp.tool()
async def list_rightsizer_resource_types() -> dict[str, Any]:
    """List Rightsizer hub resource types (ec2, asg, ecs, rds, s3) with resource counts and policy."""
    async with MaxOpsClient() as client:
        return await client.list_rightsizer_resource_types()


@mcp.tool()
async def list_rightsizer_resources(
    resource_type: str,
    q: str | None = None,
    region: str | None = None,
    state: str | None = None,
    status: str | None = None,
    current_type: str | None = None,
    target_type: str | None = None,
) -> Any:
    """List Rightsizer hub resources (current vs. target sizing status) for a resource type."""
    async with MaxOpsClient() as client:
        return await client.list_rightsizer_resources(
            resource_type,
            q=q,
            region=region,
            state=state,
            status=status,
            current_type=current_type,
            target_type=target_type,
        )


@mcp.tool()
async def get_rightsizer_resource_detail(resource_type: str, inventory_id: int) -> Any:
    """Get Rightsizer hub evidence/detail for one resource."""
    async with MaxOpsClient() as client:
        return await client.get_rightsizer_resource_detail(resource_type, inventory_id)


@mcp.tool()
async def get_s3_optimizer_detail(inventory_id: int) -> Any:
    """Get the stored S3 storage-class optimizer result for one bucket."""
    async with MaxOpsClient() as client:
        return await client.get_s3_optimizer_detail(inventory_id)


@mcp.tool()
async def list_s3_optimizer_buckets(
    limit: int = 100,
    offset: int = 0,
    region: str | None = None,
) -> Any:
    """List stored S3 optimizer bucket summaries."""
    async with MaxOpsClient() as client:
        return await client.list_s3_optimizer_buckets(
            limit=limit,
            offset=offset,
            region=region,
        )


@mcp.tool()
async def list_rightsize_recommendations(
    resource_type: str,
    account_id: str | None = None,
    region: str | None = None,
    state: str | None = None,
    engine: str | None = None,
    classification: str | None = None,
    min_monthly_savings: float = 0.0,
    candidate_limit: int | None = None,
    network_medium_ratio: float | None = None,
    network_high_ratio: float | None = None,
    ebs_medium_ratio: float | None = None,
    ebs_high_ratio: float | None = None,
    memory_medium_ratio: float | None = None,
    memory_high_ratio: float | None = None,
    allow_unknown_instance_store_usage: bool | None = None,
) -> Any:
    """List rightsizing recommendations for one of: ec2, asg, elasticache, rds.

    Evaluates persisted inventory + scan telemetry against local pricing; makes no
    AWS calls and no changes. `engine` applies to rds only; `classification` applies
    to asg/rds only; `network_*`/`ebs_*` ratios tune ec2 warning thresholds;
    `network_*`/`memory_*` ratios tune elasticache thresholds. Unsupported
    parameters for the given resource_type are ignored.
    """
    async with MaxOpsClient() as client:
        return await client.list_rightsize_recommendations(
            resource_type,
            account_id=account_id,
            region=region,
            state=state,
            engine=engine,
            classification=classification,
            min_monthly_savings=min_monthly_savings,
            candidate_limit=candidate_limit,
            network_medium_ratio=network_medium_ratio,
            network_high_ratio=network_high_ratio,
            ebs_medium_ratio=ebs_medium_ratio,
            ebs_high_ratio=ebs_high_ratio,
            memory_medium_ratio=memory_medium_ratio,
            memory_high_ratio=memory_high_ratio,
            allow_unknown_instance_store_usage=allow_unknown_instance_store_usage,
        )


@mcp.tool()
async def get_rightsize_recommendation(
    resource_type: str,
    inventory_id: int,
    min_monthly_savings: float = 0.0,
    candidate_limit: int | None = None,
    network_medium_ratio: float | None = None,
    network_high_ratio: float | None = None,
    ebs_medium_ratio: float | None = None,
    ebs_high_ratio: float | None = None,
    memory_medium_ratio: float | None = None,
    memory_high_ratio: float | None = None,
    allow_unknown_instance_store_usage: bool | None = None,
) -> Any:
    """Get a single rightsizing recommendation for ec2, asg, elasticache, or rds by inventory_id."""
    async with MaxOpsClient() as client:
        return await client.get_rightsize_recommendation(
            resource_type,
            inventory_id,
            min_monthly_savings=min_monthly_savings,
            candidate_limit=candidate_limit,
            network_medium_ratio=network_medium_ratio,
            network_high_ratio=network_high_ratio,
            ebs_medium_ratio=ebs_medium_ratio,
            ebs_high_ratio=ebs_high_ratio,
            memory_medium_ratio=memory_medium_ratio,
            memory_high_ratio=memory_high_ratio,
            allow_unknown_instance_store_usage=allow_unknown_instance_store_usage,
        )


@mcp.tool()
async def get_rightsize_confidence_trend(resource_type: str, inventory_id: int) -> Any:
    """Get the CloudWatch-derived confidence trend backing a rightsizing recommendation."""
    async with MaxOpsClient() as client:
        return await client.get_rightsize_confidence_trend(resource_type, inventory_id)


@mcp.resource("maxops://resource-types")
def resource_types_resource() -> str:
    """Supported MaxOps resource types."""
    return json.dumps(resource_types(), indent=2)


@mcp.resource("maxops://dashboard/summary")
async def dashboard_summary_resource() -> str:
    """Latest MaxOps check summary."""
    async with MaxOpsClient() as client:
        return json.dumps(await client.latest_check_results(), indent=2)


@mcp.resource("maxops://inventory/{resource_type}")
async def inventory_resource(resource_type: str) -> str:
    """Inventory for a MaxOps resource type."""
    async with MaxOpsClient() as client:
        return json.dumps(await client.get_inventory(resource_type, "all", 200), indent=2)


@mcp.resource("maxops://snoozes/{resource_type}")
async def snoozes_resource(resource_type: str) -> str:
    """Active snoozes for a MaxOps resource type."""
    async with MaxOpsClient() as client:
        return json.dumps(await client.get_inventory(resource_type, "snoozed", 200), indent=2)


@mcp.resource("maxops://rightsizer/{resource_type}")
async def rightsizer_resource(resource_type: str) -> str:
    """Rightsizer hub resources for a MaxOps resource type."""
    async with MaxOpsClient() as client:
        return json.dumps(await client.list_rightsizer_resources(resource_type), indent=2)


@mcp.prompt()
def analyze_snoozed_resources(resource_type: str = "ec2") -> str:
    """Prompt for analyzing snoozed resources in MaxOps."""
    return (
        f"Use maxops://snoozes/{resource_type} and the latest check results to summarize "
        "which resources are snoozed, when the snoozes expire, what savings are currently excluded, "
        "and which snoozes should be reviewed."
    )


def main() -> None:
    """Run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
