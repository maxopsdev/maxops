"""Persisted, read-only RDS rightsizing recommendations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.aws.adapter import AWSAdapter
from app.models.inventory import RdsInventory
from app.services.ec2_trend_cache import ec2_trend_cache
from rightsizers.rds.rds_rightsizer import RdsRightsizerPolicy, evaluate_rds
from rightsizers.rds.rds_rightsizer.catalogs import RdsClassCatalog


CLASSIFICATION_ORDER = {
    "ACTIONABLE": 0,
    "CONDITIONAL": 1,
    "INSUFFICIENT_DATA": 2,
    "DEFERRED": 3,
    None: 4,
}


class RDSRightsizer:
    TREND_SCHEMA_VERSION = "rds-trend-v1"

    def __init__(
        self,
        db: Session,
        *,
        catalog: RdsClassCatalog | None = None,
        policy: RdsRightsizerPolicy | None = None,
    ) -> None:
        self.db = db
        self.catalog = catalog or RdsClassCatalog()
        self.policy = policy or RdsRightsizerPolicy()
        self._entries = self.catalog.list()

    def _price(
        self,
        region: str,
        instance_type: str,
        engine: str,
        multi_az: bool,
        license_model: str,
    ) -> float | None:
        return self.catalog.monthly_price(
            region,
            instance_type,
            engine,
            multi_az=multi_az,
            license_model=license_model,
        )

    @staticmethod
    def _inventory(row: RdsInventory) -> dict[str, Any]:
        return {
            "inventory_id": row.inventory_id,
            "resource_id": row.resource_id,
            "resource_name": row.resource_name,
            "account_id": row.account_id,
            "region": row.region,
            "state": row.state,
            "engine": row.engine,
            "db_instance_class": row.db_instance_class,
            "metadata": row.metadata_json if isinstance(row.metadata_json, dict) else {},
        }

    def _recommend(
        self,
        row: RdsInventory,
        min_monthly_savings: float,
        candidate_limit: int,
    ) -> dict[str, Any]:
        result = evaluate_rds(
            self._inventory(row),
            self._entries,
            self._price,
            policy=self.policy,
            min_monthly_savings=Decimal(str(min_monthly_savings)),
            candidate_limit=candidate_limit,
        )
        current_type = str(result.get("current", {}).get("db_instance_class") or "")
        current = self._entries.get(current_type)
        if current:
            result["current"].update(vcpus=current.vcpus, memory_gib=current.memory_gib)
        context = (row.metadata_json or {}).get("rightsizing_context") if isinstance(row.metadata_json, dict) else {}
        class_prices = context.get("class_prices") if isinstance(context, dict) and isinstance(context.get("class_prices"), dict) else {}
        persisted = class_prices.get(current_type)
        if isinstance(persisted, dict):
            persisted = persisted.get("monthly")
        try:
            current_cost = float(persisted) if persisted is not None else None
        except (TypeError, ValueError):
            current_cost = None
        result["current"]["monthly_cost"] = current_cost if current_cost is not None else self._price(
            str(row.region or ""),
            current_type,
            str(result.get("engine") or ""),
            bool(result.get("multi_az")),
            str(result.get("license_model") or ""),
        )
        return result

    @staticmethod
    def _headline(result: dict[str, Any]) -> dict[str, Any] | None:
        class_rec = next(
            (item for item in result.get("recommendations", []) if item.get("kind") == "DB_INSTANCE_CLASS_CHANGE"),
            None,
        )
        if class_rec:
            default = class_rec.get("tiers", {}).get("default")
            if default and isinstance(class_rec.get("tiers", {}).get(default), dict):
                return class_rec["tiers"][default]
            candidates = class_rec.get("candidates") or []
            if candidates:
                return candidates[0]
        return next(
            (item for item in result.get("recommendations", []) if item.get("kind") == "STORAGE_CONFIGURATION_CHANGE"),
            None,
        )

    @classmethod
    def _compact(cls, result: dict[str, Any]) -> dict[str, Any]:
        headline = cls._headline(result)
        class_rec = next(
            (item for item in result.get("recommendations", []) if item.get("kind") == "DB_INSTANCE_CLASS_CHANGE"),
            None,
        )
        storage = next(
            (item for item in result.get("recommendations", []) if item.get("kind") == "STORAGE_CONFIGURATION_CHANGE"),
            None,
        )
        class_headline = None
        if class_rec:
            default = class_rec.get("tiers", {}).get("default")
            class_headline = class_rec.get("tiers", {}).get(default) if default else (class_rec.get("candidates") or [None])[0]
        telemetry = result.get("telemetry_summary") or {}
        cpu = telemetry.get("cpu") or {}
        memory = telemetry.get("freeable_memory") or {}
        risk = (headline or {}).get("risk_assessment") or {}
        return {
            "inventory_id": result.get("inventory_id"),
            "resource_id": result.get("resource_id"),
            "resource_name": result.get("resource_name"),
            "account_id": result.get("account_id"),
            "region": result.get("region"),
            "state": result.get("state"),
            "engine": result.get("engine"),
            "engine_version": result.get("engine_version"),
            "deployment": "Read replica" if result.get("read_replica") else "Multi-AZ" if result.get("multi_az") else "Single-AZ",
            "current_db_instance_class": result.get("current", {}).get("db_instance_class"),
            "target_db_instance_class": (class_headline or {}).get("target_db_instance_class"),
            "classification": result.get("classification"),
            "evaluation_status": result.get("evaluation_status"),
            "instance_monthly_savings": (class_headline or {}).get("monthly_savings"),
            "storage_monthly_savings": (storage or {}).get("monthly_savings"),
            "storage_target": (storage or {}).get("target_storage"),
            "headline_monthly_savings": (headline or {}).get("monthly_savings"),
            "binding_dimension": (class_headline or {}).get("binding_dimension"),
            "overall_risk": risk.get("overall"),
            "database_load_status": result.get("database_load_attribution", {}).get("status"),
            "cpu_observed_days": cpu.get("observed_days"),
            "memory_observed_days": memory.get("observed_days"),
            "reason_codes": list(dict.fromkeys(
                code
                for values in [
                    *((result.get("evaluation_reason_codes") or {}).values()),
                    (headline or {}).get("reason_codes") or [],
                ]
                for code in values
            )),
            "generated_at": telemetry.get("generated_at"),
        }

    def list_recommendations(
        self,
        *,
        account_id: str | None = None,
        region: str | None = None,
        engine: str | None = None,
        state: str = "available",
        classification: str | None = None,
        min_monthly_savings: float = 0.0,
        candidate_limit: int = 10,
    ) -> list[dict[str, Any]]:
        query = self.db.query(RdsInventory)
        if account_id:
            query = query.filter(RdsInventory.account_id == account_id)
        if region:
            query = query.filter(RdsInventory.region == region)
        if engine:
            query = query.filter(RdsInventory.engine == engine)
        if state:
            query = query.filter(RdsInventory.state == state)
        results = [
            self._compact(self._recommend(row, min_monthly_savings, candidate_limit))
            for row in query.order_by(RdsInventory.inventory_id).all()
        ]
        if classification:
            expected = None if classification == "NONE" else classification
            results = [item for item in results if item.get("classification") == expected]
        return sorted(
            results,
            key=lambda item: (
                CLASSIFICATION_ORDER.get(item.get("classification"), 5),
                -(float(item["headline_monthly_savings"]) if item.get("headline_monthly_savings") is not None else -1),
                str(item.get("resource_name") or item.get("resource_id") or ""),
            ),
        )

    def get_recommendation(
        self,
        inventory_id: int,
        *,
        min_monthly_savings: float = 0.0,
        candidate_limit: int = 10,
    ) -> dict[str, Any] | None:
        row = self.db.query(RdsInventory).filter(RdsInventory.inventory_id == inventory_id).first()
        return self._recommend(row, min_monthly_savings, candidate_limit) if row else None

    def get_confidence_trend(
        self,
        inventory_id: int,
        *,
        adapter: AWSAdapter | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any] | None:
        row = self.db.query(RdsInventory).filter(RdsInventory.inventory_id == inventory_id).first()
        if row is None:
            return None
        key = (row.account_id or "", row.region or "", row.resource_id or "", self.TREND_SCHEMA_VERSION)
        cached = ec2_trend_cache.get(key)
        if cached:
            cached["cached"] = True
            return cached
        end = end_date or datetime.now(timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        aws = adapter or AWSAdapter(default_region=row.region or None)
        trend = aws.get_rds_confidence_trend(
            row.resource_id,
            end - timedelta(days=455),
            end,
            region=row.region or None,
        )
        generated = datetime.now(timezone.utc)
        response = {
            "inventory_id": row.inventory_id,
            "resource_id": row.resource_id,
            "region": row.region,
            **trend,
            "generated_at": generated.isoformat(),
            "expires_at": (generated + timedelta(hours=1)).isoformat(),
            "cached": False,
        }
        ec2_trend_cache.set(key, response)
        return response
