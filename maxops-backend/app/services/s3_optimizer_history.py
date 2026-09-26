"""CUR monthly history aggregation for S3 optimizer pattern classification."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from app.services.s3_bucket_source import normalize_bucket_name, normalize_usage_type


def build_monthly_history(rows: Iterable[Any]) -> dict[str, dict[str, list[Any]]]:
    """Build the 12-month pattern history from whole-calendar-month CUR rows."""

    grouped: dict[str, dict[str, dict[str, float]]] = {}
    for row in rows:
        if isinstance(row, Mapping):
            bucket = normalize_bucket_name(row.get("bucket", row.get("line_item_resource_id")))
            period = str(row.get("period") or "")[:7]
            usage_type = row.get("usage_type", row.get("line_item_usage_type"))
            operation = row.get("operation", row.get("line_item_operation"))
            amount = float(row.get("usage_amount") or 0.0)
        else:
            bucket = normalize_bucket_name(getattr(row, "bucket", None))
            period = str(getattr(row, "period", ""))[:7]
            usage_type = getattr(row, "usage_type", None)
            operation = getattr(row, "operation", None)
            amount = float(getattr(row, "usage_amount", 0.0) or 0.0)
        if not bucket or not period:
            continue
        classification = normalize_usage_type(usage_type, operation)
        month = grouped.setdefault(bucket, {}).setdefault(
            period,
            {
                "data_read": 0.0,
                "data_write": 0.0,
                "config": 0.0,
                "retrieval_gb": 0.0,
                "egress_gb": 0.0,
                "stored_gb": 0.0,
            },
        )
        if classification.category == "request":
            family = classification.family or "config"
            if family in {"data_read", "data_write", "config"}:
                month[family] += amount
        elif classification.category == "retrieval_gb":
            month["retrieval_gb"] += amount
        elif classification.category in {"data_transfer_other_gb", "data_transfer_out_gb"}:
            month["egress_gb"] += amount
        elif classification.category == "storage_gb_month":
            month["stored_gb"] += amount
    result: dict[str, dict[str, list[Any]]] = {}
    for bucket, months in grouped.items():
        ordered = sorted(months)[-12:]
        result[bucket] = {
            "months": ordered,
            "data_read": [months[month]["data_read"] for month in ordered],
            "data_write": [months[month]["data_write"] for month in ordered],
            "config": [months[month]["config"] for month in ordered],
            "retrieval_gb": [months[month]["retrieval_gb"] for month in ordered],
            "egress_gb": [months[month]["egress_gb"] for month in ordered],
            "stored_gb": [months[month]["stored_gb"] for month in ordered],
        }
    return result
