"""AWS Pricing API cache service."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.pricing import PricingCache
from app.services.aws_credentials import create_runtime_boto3_session
from app.services.cur_cost_service import cur_cost_lookup
from app.services.street_pricing import StreetPricingService

logger = logging.getLogger("uvicorn.error")


REGION_LOCATION_MAP = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)",
    "ca-central-1": "Canada (Central)",
    "eu-west-1": "EU (Ireland)",
    "eu-west-2": "EU (London)",
    "eu-west-3": "EU (Paris)",
    "eu-central-1": "EU (Frankfurt)",
    "eu-north-1": "EU (Stockholm)",
    "ap-south-1": "Asia Pacific (Mumbai)",
    "ap-south-2": "Asia Pacific (Hyderabad)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-southeast-3": "Asia Pacific (Jakarta)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-northeast-2": "Asia Pacific (Seoul)",
    "ap-northeast-3": "Asia Pacific (Osaka)",
    "sa-east-1": "South America (Sao Paulo)",
}


class AwsPricingCacheService:
    """Fetch and cache AWS public pricing for resources."""

    def __init__(self) -> None:
        session = create_runtime_boto3_session(region_name="us-east-1")
        self.pricing_client = session.client("pricing", region_name="us-east-1")
        self.street_pricing_service = StreetPricingService()

    def get_prices_for_resources(
        self,
        db: Session,
        resources: List[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        results: Dict[str, Dict[str, Any]] = {}
        if not resources:
            return results

        for resource in resources:
            resource_id = resource.get("resource_id")
            if not resource_id:
                continue
            try:
                pricing = self.get_price_for_resource(db, resource)
                if pricing:
                    results[resource_id] = pricing
            except Exception as exc:
                logger.warning("Pricing lookup failed for %s: %s", resource_id, exc)
                continue
        return results

    def get_price_for_resource(
        self,
        db: Session,
        resource: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        resource_type = (resource.get("resource_type") or "").lower()
        region = resource.get("region") or settings.aws_region
        cur_pricing = self._get_cur_price_for_resource(db, resource, resource_type, region)
        if cur_pricing:
            return cur_pricing

        street_pricing = self.street_pricing_service.get_price_for_resource(resource)
        if street_pricing:
            return street_pricing

        params = self._build_pricing_parameters(resource_type, resource.get("metadata") or {}, region)
        if not params:
            return None
        params_hash = self._hash_params(params)

        cached = (
            db.query(PricingCache)
            .filter(
                PricingCache.resource_type == resource_type,
                PricingCache.region == region,
                PricingCache.parameters_hash == params_hash,
            )
            .first()
        )
        if cached:
            return {
                "price_per_unit": cached.price_per_unit,
                "unit": cached.unit,
                "currency": cached.currency,
                "source": cached.source,
                "parameters": cached.parameters_json,
            }

        price_per_unit, unit = self._fetch_price_from_aws(params)
        cache_entry = PricingCache(
            resource_type=resource_type,
            region=region,
            parameters_hash=params_hash,
            parameters_json=params,
            price_per_unit=price_per_unit,
            unit=unit,
            currency="USD",
            source="aws_pricing",
        )
        db.add(cache_entry)
        try:
            db.commit()
        except IntegrityError:
            # Another lookup inserted same cache key first (or existing failed lookup).
            # Reuse existing entry instead of failing request flow.
            db.rollback()
            existing = (
                db.query(PricingCache)
                .filter(
                    PricingCache.resource_type == resource_type,
                    PricingCache.region == region,
                    PricingCache.parameters_hash == params_hash,
                )
                .first()
            )
            if existing:
                return {
                    "price_per_unit": existing.price_per_unit,
                    "unit": existing.unit,
                    "currency": existing.currency,
                    "source": existing.source,
                    "parameters": existing.parameters_json,
                }
            raise
        return {
            "price_per_unit": price_per_unit,
            "unit": unit,
            "currency": "USD",
            "source": "aws_pricing",
            "parameters": params,
        }

    def _get_cur_price_for_resource(
        self,
        db: Session,
        resource: Dict[str, Any],
        resource_type: str,
        region: str,
    ) -> Optional[Dict[str, Any]]:
        metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
        embedded_pricing = metadata.get("pricing") or resource.get("pricing")
        if isinstance(embedded_pricing, dict) and str(embedded_pricing.get("source") or "").lower().startswith("cur"):
            return embedded_pricing

        explicit_cur_source = str(
            metadata.get("pricing_source")
            or metadata.get("cost_source")
            or metadata.get("source")
            or ""
        ).lower().startswith("cur")
        cur_cost_keys = ["cur_monthly_cost", "cur_estimated_monthly_cost"]
        if explicit_cur_source:
            cur_cost_keys.extend(["current_monthly_cost", "estimated_monthly_cost"])

        for key in cur_cost_keys:
            value = metadata.get(key)
            if value is None:
                continue
            try:
                monthly_cost = float(value)
            except (TypeError, ValueError):
                continue
            return {
                "price_per_unit": monthly_cost,
                "unit": "month",
                "currency": "USD",
                "source": "cur",
                "parameters": {"resource_id": resource.get("resource_id"), "field": key},
            }

        resource_id = str(resource.get("resource_id") or "")
        if not resource_id:
            return None

        # Actual billed cost from the local CUR aggregate cache, when an
        # operator has set one up. This is the authoritative source: it is what
        # the resource really cost, net of reservations and Savings Plans.
        billed = cur_cost_lookup.get(resource_id)
        if billed is not None:
            monthly_cost, billing_month = billed
            return {
                "price_per_unit": monthly_cost,
                "unit": "month",
                "currency": "USD",
                "source": "cur",
                "parameters": {
                    "resource_id": resource_id,
                    "billing_month": billing_month,
                    "metric": "net_amortized_cost",
                },
            }

        candidates = (
            db.query(PricingCache)
            .filter(
                PricingCache.resource_type == resource_type,
                PricingCache.region == region,
                PricingCache.source == "cur",
            )
            .order_by(PricingCache.updated_at.desc())
            .limit(50)
            .all()
        )
        for cached in candidates:
            params = cached.parameters_json if isinstance(cached.parameters_json, dict) else {}
            if str(params.get("resource_id") or "") != resource_id:
                continue
            return {
                "price_per_unit": cached.price_per_unit,
                "unit": cached.unit or "month",
                "currency": cached.currency,
                "source": cached.source,
                "parameters": cached.parameters_json,
            }

        return None

    def _build_pricing_parameters(
        self,
        resource_type: str,
        metadata: Dict[str, Any],
        region: str,
    ) -> Optional[Dict[str, Any]]:
        location = REGION_LOCATION_MAP.get(region)
        if not location:
            return None

        if resource_type in {"ec2", "ec2_instance"}:
            instance_type = metadata.get("instance_type") or metadata.get("InstanceType")
            if not instance_type:
                return None
            return {
                "service_code": "AmazonEC2",
                "filters": {
                    "instanceType": instance_type,
                    "location": location,
                    "operatingSystem": "Linux",
                    "tenancy": "Shared",
                    "preInstalledSw": "NA",
                    "capacitystatus": "Used",
                    "licenseModel": "No License required",
                },
                "unit_hint": "Hrs",
            }

        if resource_type in {"rds", "rds_instance"}:
            instance_class = metadata.get("instance_class") or metadata.get("DBInstanceClass")
            engine = metadata.get("engine") or metadata.get("Engine")
            if not instance_class or not engine:
                return None
            engine_normalized = str(engine).strip().lower()
            engine_candidates = [str(engine)]
            if engine_normalized in {"postgres", "postgresql"}:
                engine_candidates = ["PostgreSQL", "postgres", "PostgreSQL Community"]
            elif engine_normalized in {"mysql", "mariadb"}:
                engine_candidates = ["MySQL", "MariaDB", "mysql"]
            elif engine_normalized in {"aurora-postgresql", "aurora_postgresql"}:
                engine_candidates = ["Aurora PostgreSQL"]
            elif engine_normalized in {"aurora-mysql", "aurora_mysql"}:
                engine_candidates = ["Aurora MySQL"]

            base_filters = {
                "instanceType": instance_class,
                "databaseEngine": engine_candidates[0],
                "deploymentOption": "Single-AZ",
                "location": location,
                "productFamily": "Database Instance",
            }
            alternate_filters = []
            for candidate in engine_candidates[1:]:
                alternate_filters.append(
                    {
                        "instanceType": instance_class,
                        "databaseEngine": candidate,
                        "deploymentOption": "Single-AZ",
                        "location": location,
                        "productFamily": "Database Instance",
                    }
                )
            alternate_filters.extend(
                [
                    {
                        "instanceType": instance_class,
                        "databaseEngine": engine_candidates[0],
                        "location": location,
                        "productFamily": "Database Instance",
                    },
                    {
                        "instanceType": instance_class,
                        "location": location,
                        "productFamily": "Database Instance",
                    },
                ]
            )
            return {
                "service_code": "AmazonRDS",
                "filters": base_filters,
                "alternate_filters": alternate_filters,
                "unit_hint": "Hrs",
            }

        if resource_type in {"elasticache", "elasticache_cluster", "elasticache_replication_group"}:
            node_type = metadata.get("CacheNodeType")
            engine = metadata.get("Engine") or metadata.get("engine")
            if not node_type or not engine:
                return None
            engine_normalized = str(engine).strip().lower()
            engine_candidates = [str(engine)]
            if engine_normalized in {"redis", "redis oss", "redisos"}:
                engine_candidates = ["Redis", "Redis OSS"]
            elif engine_normalized == "valkey":
                engine_candidates = ["Valkey"]
            base_filters = {
                "cacheNodeType": node_type,
                "cacheEngine": engine_candidates[0],
                "location": location,
                "productFamily": "Cache Instance",
            }
            alternate_filters = []
            for candidate in engine_candidates[1:]:
                alternate_filters.append(
                    {
                        "cacheNodeType": node_type,
                        "cacheEngine": candidate,
                        "location": location,
                        "productFamily": "Cache Instance",
                    }
                )
            alternate_filters.extend(
                [
                    {
                        "instanceType": node_type,
                        "cacheEngine": engine_candidates[0],
                        "location": location,
                        "productFamily": "Cache Instance",
                    },
                    {
                        "instanceType": node_type,
                        "cacheEngine": engine_candidates[0],
                        "location": location,
                    },
                    {
                        "cacheNodeType": node_type,
                        "location": location,
                    },
                    {
                        "instanceType": node_type,
                        "location": location,
                    },
                ]
            )
            return {
                "service_code": "AmazonElastiCache",
                "filters": base_filters,
                "alternate_filters": alternate_filters,
                "unit_hint": "Hrs",
            }

        if resource_type in {"ebs", "ebs_volume"}:
            volume_type = (metadata.get("volume_type") or metadata.get("VolumeType") or "").lower()
            volume_map = {
                "gp2": "General Purpose",
                "gp3": "General Purpose SSD (gp3)",
                "io1": "Provisioned IOPS",
                "io2": "Provisioned IOPS SSD (io2)",
                "st1": "Throughput Optimized HDD",
                "sc1": "Cold HDD",
                "standard": "Magnetic",
            }
            volume_label = volume_map.get(volume_type)
            if not volume_label:
                return None
            base_filters = {
                "service_code": "AmazonEC2",
                "filters": {
                    "productFamily": "Storage",
                    "volumeType": volume_label,
                    "location": location,
                },
            }
            alternate_filters = [
                {
                    "productFamily": "Storage",
                    "volumeApiName": volume_type,
                    "location": location,
                },
                {
                    "productFamily": "Storage",
                    "volumeType": volume_label,
                    "storageMedia": "SSD-backed",
                    "location": location,
                },
                {
                    "productFamily": "Storage",
                    "volumeApiName": volume_type,
                    "storageMedia": "SSD-backed",
                    "location": location,
                },
                {
                    "productFamily": "Storage",
                    "volumeType": volume_label,
                    "location": location,
                },
            ]
            return {
                "service_code": "AmazonEC2",
                "filters": base_filters["filters"],
                "alternate_filters": alternate_filters,
                "unit_hint": "GB-Mo",
            }

        if resource_type in {"snapshot", "ebs_snapshot"}:
            # EBS Snapshots are priced per GB-month stored
            # They use incremental storage (only changed blocks are charged)
            return {
                "service_code": "AmazonEC2",
                "filters": {
                    "productFamily": "Storage Snapshot",
                    "location": location,
                },
                "alternate_filters": [
                    {
                        "productFamily": "Storage Snapshot",
                        "storageMedia": "Amazon S3",
                        "location": location,
                    },
                    {
                        "productFamily": "Storage Snapshot",
                        "usagetype": f"{region}:SnapshotUsage",
                        "location": location,
                    },
                ],
                "unit_hint": "GB-Mo",
            }

        if resource_type in {"efs", "efs_file_system"}:
            # EFS pricing is based on storage class (Standard or Infrequent Access)
            # Standard: ~$0.30/GB-month, IA: ~$0.025/GB-month
            # Throughput mode affects pricing but storage is the main component
            throughput_mode = (metadata.get("ThroughputMode") or metadata.get("throughput_mode") or "bursting").lower()
            
            # Try Standard storage first (most common)
            base_filters = {
                "productFamily": "Storage",
                "storageClass": "General Purpose",
                "location": location,
            }
            
            alternate_filters = [
                {
                    "productFamily": "Storage",
                    "storageClass": "General Purpose",
                    "location": location,
                },
                {
                    "productFamily": "Storage",
                    "usagetype": f"{region}:EFS:GeneralPurposeStorage",
                    "location": location,
                },
                {
                    "productFamily": "Storage",
                    "location": location,
                },
            ]
            
            # If provisioned throughput, add filters for throughput pricing
            if throughput_mode == "provisioned":
                alternate_filters.append({
                    "productFamily": "Provisioned Throughput",
                    "location": location,
                })
            
            return {
                "service_code": "AmazonEFS",
                "filters": base_filters,
                "alternate_filters": alternate_filters,
                "unit_hint": "GB-Mo",
            }

        if resource_type in {
            "sagemaker",
            "sagemaker_notebook",
            "sagemaker_endpoint",
            "sagemaker_training_job",
        }:
            instance_type = metadata.get("instance_type") or metadata.get("InstanceType")
            if not instance_type:
                return None
            component = str(
                metadata.get("pricing_component")
                or metadata.get("sagemaker_component")
                or {
                    "sagemaker_notebook": "Notebook",
                    "sagemaker_endpoint": "Hosting",
                    "sagemaker_training_job": "Training",
                }.get(resource_type, "Hosting")
            )
            # The public catalog has used both the SageMaker-prefixed and bare
            # type in releases. Try the contract shape first, then the safe
            # alternatives; never substitute EC2 pricing for an ML instance.
            return {
                "service_code": "AmazonSageMaker",
                "filters": {
                    "productFamily": "ML Instance",
                    "instanceType": instance_type,
                    "location": location,
                    "component": component,
                },
                "alternate_filters": [
                    {
                        "productFamily": "ML Instance",
                        "instanceType": str(instance_type).removeprefix("ml."),
                        "location": location,
                        "component": component,
                    },
                    {
                        "productFamily": "ML Instance",
                        "instanceType": instance_type,
                        "location": location,
                    },
                ],
                "unit_hint": "Hrs",
                "sagemaker_component": component,
            }

        return None

    def _fetch_price_from_aws(self, params: Dict[str, Any]) -> Tuple[Optional[float], Optional[str]]:
        service_code = params["service_code"]
        filter_sets = [params["filters"]] + params.get("alternate_filters", [])
        for filters in filter_sets:
            aws_filters = [
                {"Type": "TERM_MATCH", "Field": key, "Value": value}
                for key, value in filters.items()
            ]
            logger.info("Pricing lookup filters for %s: %s", service_code, filters)
            try:
                response = self.pricing_client.get_products(ServiceCode=service_code, Filters=aws_filters, MaxResults=1)
                price_list = response.get("PriceList", [])
                if not price_list:
                    logger.info("Pricing lookup returned 0 results for %s with filters: %s", service_code, filters)
                    continue
                raw = json.loads(price_list[0])
                terms = raw.get("terms", {}).get("OnDemand", {})
                if not terms:
                    logger.warning("No OnDemand terms found in pricing response for %s", service_code)
                    continue
                for term in terms.values():
                    price_dimensions = term.get("priceDimensions", {})
                    for dimension in price_dimensions.values():
                        price_per_unit = dimension.get("pricePerUnit", {}).get("USD")
                        unit = dimension.get("unit")
                        if price_per_unit:
                            try:
                                price_value = float(price_per_unit)
                                logger.info("Successfully fetched pricing for %s: $%s per %s", service_code, price_value, unit)
                                return price_value, unit
                            except (TypeError, ValueError) as e:
                                logger.warning("Failed to parse price_per_unit '%s' for %s: %s", price_per_unit, service_code, e)
                                return None, unit
            except Exception as e:
                logger.error("Error calling AWS Pricing API for %s with filters %s: %s", service_code, filters, e, exc_info=True)
                continue
        logger.warning(
            "Pricing lookup failed for %s with all filter sets: %s",
            service_code,
            filter_sets,
        )
        return None, params.get("unit_hint")

    def _hash_params(self, params: Dict[str, Any]) -> str:
        payload = json.dumps(params, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
