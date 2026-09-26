"""HTTP client for the existing MaxOps backend API."""

from __future__ import annotations

from typing import Any, Iterable

import httpx

from maxops_mcp.config import settings


RESOURCE_COLLECTIONS: dict[str, str] = {
    "ec2": "instances",
    "rds": "instances",
    "s3": "buckets",
    "dynamodb": "resources",
    "ebs": "volumes",
    "elasticache": "resources",
}

# The recommendations engine mounts one hardcoded route per resource type
# (/recommendations/{type}/rightsize...), unlike the hub's generic
# /rightsizer/resources/{resource_type} path, so the client must validate
# before building the URL rather than letting the backend 404.
RIGHTSIZE_RECOMMENDATION_TYPES: set[str] = {"ec2", "asg", "elasticache", "rds"}


class MaxOpsApiError(RuntimeError):
    """Raised when the MaxOps API returns an error response."""

    def __init__(self, message: str, *, status_code: int, response_text: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class MaxOpsClient:
    """Thin async client around MaxOps REST endpoints."""

    def __init__(self) -> None:
        headers = {"Accept": "application/json"}
        if settings.maxops_api_token:
            headers["Authorization"] = f"Bearer {settings.maxops_api_token}"
        self._client = httpx.AsyncClient(
            base_url=settings.maxops_api_base_url.rstrip("/"),
            headers=headers,
            timeout=settings.maxops_mcp_request_timeout,
        )

    async def __aenter__(self) -> "MaxOpsClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise MaxOpsApiError(
                f"MaxOps API {response.status_code}: {response.text}",
                status_code=response.status_code,
                response_text=response.text,
            )
        if response.content:
            return response.json()
        return None

    @staticmethod
    def _compact_params(params: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in params.items() if value is not None}

    @staticmethod
    def _is_all_resource_type(resource_type: str | None) -> bool:
        return str(resource_type or "").strip().lower() == "all"

    @staticmethod
    def _inventory_sort_value(resource: dict[str, Any], sort_by: str):
        if sort_by in {"monthly_cost", "potential_savings_yearly"}:
            key = "monthly_cost_estimate" if sort_by == "monthly_cost" else "potential_savings_yearly"
            return float(resource.get(key) or 0.0)
        if sort_by == "generated_at":
            return str(resource.get("generated_at") or "")
        if sort_by == "snoozed_until":
            snooze = resource.get("snooze") or {}
            return str(snooze.get("snoozed_until") or "")
        if sort_by == "check_id":
            maxops = resource.get("maxops") or {}
            return str(maxops.get("check_id") or "")
        if sort_by == "severity":
            maxops = resource.get("maxops") or {}
            return str(maxops.get("severity") or "")
        return str(resource.get(sort_by) or "")

    async def _query_inventory_single(
        self,
        *,
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
        self._validate_resource_type(resource_type)
        params = {
            "q": q,
            "account_id": account_id,
            "region": region,
            "state": state,
            "check_id": check_id,
            "severity": severity,
            "finding_type": finding_type,
            "tag_key": tag_key,
            "tag_value": tag_value,
            "tag_value_mode": tag_value_mode,
            "metadata_key": metadata_key,
            "metadata_value": metadata_value,
            "snooze_state": snooze_state,
            "min_savings_yearly": min_savings_yearly,
            "max_savings_yearly": max_savings_yearly,
            "min_monthly_cost": min_monthly_cost,
            "max_monthly_cost": max_monthly_cost,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "offset": max(offset, 0),
            "limit": max(limit, 1),
            "include_summary": True,
        }
        return await self._request("GET", f"/mcp/inventory/{resource_type}/query", params=self._compact_params(params))

    async def list_checks(self, resource_type: str | None = None) -> list[dict[str, Any]]:
        params = {"resource_type": resource_type} if resource_type else None
        return await self._request("GET", "/checks", params=params)

    async def latest_check_results(self) -> dict[str, Any]:
        try:
            payload = await self._request(
                "GET",
                "/mcp/checks/results/query",
                params=self._compact_params({
                    "limit": 500,
                    "offset": 0,
                    "include_parameters": False,
                    "include_checks_without_results": False,
                }),
            )
            items = payload.get("items") or []
            return {
                item["check_id"]: {
                    "check_id": item.get("check_id"),
                    "name": item.get("name"),
                    "description": item.get("description"),
                    "resource_type": item.get("resource_type"),
                    "status": item.get("status"),
                    "resources_found": item.get("resources_found", 0),
                    "potential_savings_yearly": item.get("potential_savings_yearly", 0.0),
                    "error": item.get("error"),
                    "execution_time": item.get("execution_time"),
                }
                for item in items
                if item.get("check_id")
            }
        except MaxOpsApiError as exc:
            if exc.status_code != 404:
                raise
            return await self._request("GET", "/checks/latest-results")

    async def inventory_overview(self, resource_type: str) -> dict[str, Any]:
        self._validate_resource_type(resource_type)
        return await self._request("GET", f"/inventory/{resource_type}/overview")

    async def get_inventory(
        self,
        resource_type: str,
        snooze_state: str = "all",
        limit: int = 100,
    ) -> dict[str, Any]:
        if self._is_all_resource_type(resource_type):
            merged = await self.query_inventory(
                resource_type="all",
                snooze_state=snooze_state,
                sort_by="potential_savings_yearly",
                sort_order="desc",
                offset=0,
                limit=max(limit, 1),
            )
            return {
                "resource_type": "all",
                "snooze_state": snooze_state,
                "total_matching": int(merged.get("total_matching", 0)),
                "returned": int(merged.get("returned", 0)),
                "summary": merged.get("summary", {}),
                "resources": merged.get("resources", []),
            }

        params = {
            "snooze_state": snooze_state,
            "limit": max(limit, 1),
            "offset": 0,
            "sort_by": "potential_savings_yearly",
            "sort_order": "desc",
            "include_summary": True,
        }
        try:
            payload = await self._request("GET", f"/mcp/inventory/{resource_type}/query", params=self._compact_params(params))
            return {
                "resource_type": resource_type,
                "snooze_state": snooze_state,
                "total_matching": int(payload.get("total_matching", 0)),
                "returned": int(payload.get("returned", 0)),
                "summary": payload.get("summary", {}),
                "resources": payload.get("resources", []),
            }
        except MaxOpsApiError as exc:
            if exc.status_code != 404:
                raise
            overview = await self.inventory_overview(resource_type)
            collection_key = RESOURCE_COLLECTIONS[resource_type]
            resources = list(overview.get(collection_key) or [])
            filtered = self._filter_snooze_state(resources, snooze_state)
            limited = filtered[: max(limit, 0)]
            return {
                "resource_type": resource_type,
                "snooze_state": snooze_state,
                "total_matching": len(filtered),
                "returned": len(limited),
                "summary": overview.get("summary", {}),
                "resources": limited,
            }

    async def query_inventory(
        self,
        *,
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
        if self._is_all_resource_type(resource_type):
            per_type_limit = max(offset + limit, 1)
            results = []
            for each_type in sorted(RESOURCE_COLLECTIONS):
                payload = await self._query_inventory_single(
                    resource_type=each_type,
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
                    offset=0,
                    limit=per_type_limit,
                )
                results.append(payload)

            all_resources: list[dict[str, Any]] = []
            total_matching = 0
            total_resources = 0
            snoozed_resources = 0
            total_monthly_cost_estimate = 0.0
            total_potential_savings_yearly = 0.0
            for payload in results:
                total_matching += int(payload.get("total_matching", 0))
                all_resources.extend(payload.get("resources") or [])
                summary = payload.get("summary") or {}
                total_resources += int(summary.get("total_resources", 0))
                snoozed_resources += int(summary.get("snoozed_resources", 0))
                total_monthly_cost_estimate += float(summary.get("total_monthly_cost_estimate", 0.0) or 0.0)
                total_potential_savings_yearly += float(summary.get("total_potential_savings_yearly", 0.0) or 0.0)

            reverse = sort_order == "desc"
            all_resources.sort(key=lambda item: self._inventory_sort_value(item, sort_by), reverse=reverse)
            sliced = all_resources[offset: offset + limit]

            return {
                "resource_type": "all",
                "query": {
                    "q": q,
                    "account_id": account_id,
                    "region": region,
                    "state": state,
                    "check_id": check_id,
                    "severity": severity,
                    "finding_type": finding_type,
                    "tag_key": tag_key,
                    "tag_value": tag_value,
                    "tag_value_mode": tag_value_mode,
                    "metadata_key": metadata_key,
                    "metadata_value": metadata_value,
                    "snooze_state": snooze_state,
                    "min_savings_yearly": min_savings_yearly,
                    "max_savings_yearly": max_savings_yearly,
                    "min_monthly_cost": min_monthly_cost,
                    "max_monthly_cost": max_monthly_cost,
                    "sort_by": sort_by,
                    "sort_order": sort_order,
                    "offset": offset,
                    "limit": limit,
                },
                "total_matching": total_matching,
                "returned": len(sliced),
                "summary": {
                    "total_resources": total_resources,
                    "snoozed_resources": snoozed_resources,
                    "not_snoozed_resources": max(total_resources - snoozed_resources, 0),
                    "total_potential_savings_yearly": round(total_potential_savings_yearly, 6),
                    "total_monthly_cost_estimate": round(total_monthly_cost_estimate, 6),
                },
                "resources": sliced,
            }

        return await self._query_inventory_single(
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

    async def query_check_results(
        self,
        *,
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
        params = {
            "resource_type": resource_type,
            "status": status,
            "check_id": check_id,
            "q": q,
            "min_resources_found": min_resources_found,
            "min_potential_savings_yearly": min_potential_savings_yearly,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "offset": max(offset, 0),
            "limit": max(limit, 1),
            "include_parameters": include_parameters,
            "include_checks_without_results": include_checks_without_results,
        }
        return await self._request("GET", "/mcp/checks/results/query", params=self._compact_params(params))

    async def get_tag_cost_savings_summary(
        self,
        *,
        tag_key: str,
        tag_value: str,
        tag_value_mode: str = "equals",
        resource_type: str | None = None,
        include_details: bool = True,
        details_limit: int = 200,
    ) -> dict[str, Any]:
        if resource_type is not None and not self._is_all_resource_type(resource_type):
            self._validate_resource_type(resource_type)
        effective_resource_type = None if self._is_all_resource_type(resource_type) else resource_type
        params = {
            "tag_key": tag_key,
            "tag_value": tag_value,
            "tag_value_mode": tag_value_mode,
            "resource_type": effective_resource_type,
            "include_details": include_details,
            "details_limit": max(details_limit, 1),
        }
        return await self._request("GET", "/mcp/tags/cost-savings-summary", params=self._compact_params(params))

    async def snooze_resources(
        self,
        resources: list[dict[str, Any]],
        snoozed_until: str,
        reason: str,
    ) -> dict[str, Any]:
        self._require_write_enabled()
        reason = reason.strip()
        if not reason:
            raise ValueError("Audit comment is required")
        payload = {
            "resources": self._normalize_resources(resources),
            "snoozed_until": snoozed_until,
            "reason": reason,
        }
        return await self._request("POST", "/inventory/resources/snooze", json=payload)

    async def remove_snooze(
        self,
        resources: list[dict[str, Any]],
        reason: str,
    ) -> dict[str, Any]:
        self._require_write_enabled()
        reason = reason.strip()
        if not reason:
            raise ValueError("Audit comment is required")
        payload = {
            "resources": self._normalize_resources(resources),
            "reason": reason,
        }
        return await self._request("POST", "/inventory/resources/snooze/remove", json=payload)

    async def run_check(self, check_id: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        self._require_write_enabled()
        payload = {"parameters": parameters or {}}
        return await self._request("POST", f"/checks/{check_id}/test", json=payload)

    async def list_rightsizer_resource_types(self) -> dict[str, Any]:
        return await self._request("GET", "/rightsizer/resource-types")

    async def list_rightsizer_resources(
        self,
        resource_type: str,
        *,
        q: str | None = None,
        region: str | None = None,
        state: str | None = None,
        status: str | None = None,
        current_type: str | None = None,
        target_type: str | None = None,
    ) -> Any:
        params = {
            "q": q,
            "region": region,
            "state": state,
            "status": status,
            "current_type": current_type,
            "target_type": target_type,
        }
        return await self._request(
            "GET", f"/rightsizer/resources/{resource_type}", params=self._compact_params(params)
        )

    async def get_rightsizer_resource_detail(self, resource_type: str, inventory_id: int) -> Any:
        return await self._request("GET", f"/rightsizer/resources/{resource_type}/{inventory_id}")

    async def get_s3_optimizer_detail(self, inventory_id: int) -> Any:
        """Fetch one stored S3 optimizer result."""
        return await self._request("GET", f"/s3-optimizer/{inventory_id}")

    async def list_s3_optimizer_buckets(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        region: str | None = None,
    ) -> Any:
        """List stored S3 optimizer bucket summaries."""
        params = {"limit": limit, "offset": offset, "region": region}
        return await self._request("GET", "/s3-optimizer", params=self._compact_params(params))

    async def list_rightsize_recommendations(
        self,
        resource_type: str,
        *,
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
        self._validate_rightsize_recommendation_type(resource_type)
        params = {
            "account_id": account_id,
            "region": region,
            "state": state,
            "engine": engine if resource_type == "rds" else None,
            "classification": classification if resource_type in {"asg", "rds"} else None,
            "min_monthly_savings": min_monthly_savings,
            "candidate_limit": candidate_limit,
            "network_medium_ratio": network_medium_ratio if resource_type in {"ec2", "elasticache"} else None,
            "network_high_ratio": network_high_ratio if resource_type in {"ec2", "elasticache"} else None,
            "ebs_medium_ratio": ebs_medium_ratio if resource_type == "ec2" else None,
            "ebs_high_ratio": ebs_high_ratio if resource_type == "ec2" else None,
            "memory_medium_ratio": memory_medium_ratio if resource_type == "elasticache" else None,
            "memory_high_ratio": memory_high_ratio if resource_type == "elasticache" else None,
            "allow_unknown_instance_store_usage": (
                allow_unknown_instance_store_usage if resource_type == "ec2" else None
            ),
        }
        return await self._request(
            "GET", f"/recommendations/{resource_type}/rightsize", params=self._compact_params(params)
        )

    async def get_rightsize_recommendation(
        self,
        resource_type: str,
        inventory_id: int,
        *,
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
        self._validate_rightsize_recommendation_type(resource_type)
        params = {
            "min_monthly_savings": min_monthly_savings,
            "candidate_limit": candidate_limit,
            "network_medium_ratio": network_medium_ratio if resource_type in {"ec2", "elasticache"} else None,
            "network_high_ratio": network_high_ratio if resource_type in {"ec2", "elasticache"} else None,
            "ebs_medium_ratio": ebs_medium_ratio if resource_type == "ec2" else None,
            "ebs_high_ratio": ebs_high_ratio if resource_type == "ec2" else None,
            "memory_medium_ratio": memory_medium_ratio if resource_type == "elasticache" else None,
            "memory_high_ratio": memory_high_ratio if resource_type == "elasticache" else None,
            "allow_unknown_instance_store_usage": (
                allow_unknown_instance_store_usage if resource_type == "ec2" else None
            ),
        }
        return await self._request(
            "GET",
            f"/recommendations/{resource_type}/rightsize/{inventory_id}",
            params=self._compact_params(params),
        )

    async def get_rightsize_confidence_trend(self, resource_type: str, inventory_id: int) -> Any:
        self._validate_rightsize_recommendation_type(resource_type)
        return await self._request(
            "GET", f"/recommendations/{resource_type}/rightsize/{inventory_id}/trend"
        )

    def _validate_rightsize_recommendation_type(self, resource_type: str) -> None:
        if resource_type not in RIGHTSIZE_RECOMMENDATION_TYPES:
            allowed = ", ".join(sorted(RIGHTSIZE_RECOMMENDATION_TYPES))
            raise ValueError(f"Unsupported resource_type '{resource_type}'. Allowed values: {allowed}")

    def _validate_resource_type(self, resource_type: str) -> None:
        if resource_type not in RESOURCE_COLLECTIONS:
            allowed = ", ".join(sorted(RESOURCE_COLLECTIONS))
            raise ValueError(f"Unsupported resource_type '{resource_type}'. Allowed values: {allowed}")

    def _require_write_enabled(self) -> None:
        if not settings.maxops_mcp_write_enabled:
            raise PermissionError("Write tools are disabled. Set MAXOPS_MCP_WRITE_ENABLED=true to enable them.")

    def _normalize_resources(self, resources: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for resource in resources:
            resource_id = str(resource.get("resource_id") or "").strip()
            resource_type = str(resource.get("resource_type") or "").strip()
            if not resource_id or not resource_type:
                raise ValueError("Each resource must include resource_id and resource_type")
            normalized.append(
                {
                    "resource_id": resource_id,
                    "resource_type": resource_type,
                    "resource_name": resource.get("resource_name"),
                    "account_id": resource.get("account_id"),
                    "region": resource.get("region"),
                }
            )
        if not normalized:
            raise ValueError("At least one resource is required")
        return normalized

    def _filter_snooze_state(self, resources: list[dict[str, Any]], snooze_state: str) -> list[dict[str, Any]]:
        if snooze_state == "all":
            return resources
        if snooze_state not in {"snoozed", "not_snoozed"}:
            raise ValueError("snooze_state must be one of: all, snoozed, not_snoozed")
        want_snoozed = snooze_state == "snoozed"
        return [
            resource
            for resource in resources
            if bool((resource.get("snooze") or {}).get("active")) == want_snoozed
        ]


def resource_types() -> list[str]:
    return ["all", *sorted(RESOURCE_COLLECTIONS)]
