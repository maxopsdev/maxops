"""Kinesis Data Streams inactive streams check."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _list_stream_names(kinesis_client) -> List[str]:
    names: List[str] = []
    paginator = kinesis_client.get_paginator("list_streams")
    for page in paginator.paginate():
        names.extend(page.get("StreamNames", []))
    return names


def _sum_metric(
    cloudwatch_client,
    stream_name: str,
    metric_name: str,
    start_date: datetime,
    end_date: datetime,
    period_seconds: int,
) -> float:
    response = cloudwatch_client.get_metric_statistics(
        Namespace="AWS/Kinesis",
        MetricName=metric_name,
        Dimensions=[{"Name": "StreamName", "Value": stream_name}],
        StartTime=start_date,
        EndTime=end_date,
        Period=period_seconds,
        Statistics=["Sum"],
    )
    datapoints = response.get("Datapoints", [])
    return sum(_to_float(dp.get("Sum")) for dp in datapoints)


def check_kinesis_inactive_streams(
    aws_adapter,
    lookback_days: int = 14,
    max_incoming_bytes_total: float = 0.0,
    max_incoming_records_total: float = 0.0,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Identify active Kinesis streams with little/no traffic over a lookback window.
    """
    if lookback_days <= 0:
        return []

    kinesis = aws_adapter.session.client("kinesis", region_name=region)
    cloudwatch = aws_adapter.session.client("cloudwatch", region_name=region)
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=lookback_days)

    flagged: List[Dict[str, Any]] = []
    for stream_name in _list_stream_names(kinesis):
        try:
            summary = kinesis.describe_stream_summary(StreamName=stream_name).get(
                "StreamDescriptionSummary", {}
            )
            stream_status = str(summary.get("StreamStatus") or "").upper()
            if stream_status != "ACTIVE":
                continue

            incoming_bytes = _sum_metric(
                cloudwatch, stream_name, "IncomingBytes", start_date, end_date, 3600
            )
            incoming_records = _sum_metric(
                cloudwatch, stream_name, "IncomingRecords", start_date, end_date, 3600
            )

            if (
                incoming_bytes <= max_incoming_bytes_total
                and incoming_records <= max_incoming_records_total
            ):
                flagged.append(
                    {
                        "resource_id": stream_name,
                        "resource_type": "kinesis_stream",
                        "resource_name": stream_name,
                        "region": region,
                        "state": stream_status,
                        "metadata": {
                            "lookback_days": lookback_days,
                            "incoming_bytes_total": int(incoming_bytes),
                            "incoming_records_total": int(incoming_records),
                            "max_incoming_bytes_total": max_incoming_bytes_total,
                            "max_incoming_records_total": max_incoming_records_total,
                            "recommended_action": "delete_or_downsize_stream",
                            "check_reason": create_check_reason(
                                "unused",
                                {
                                    "reason": "No significant incoming traffic in lookback window"
                                },
                            ),
                        },
                    }
                )
        except Exception as exc:
            print(f"Error checking Kinesis stream {stream_name} inactivity: {exc}")
            continue

    return flagged


check_registry.register(
    CheckMetadata(
        check_id="kinesis_inactive_streams",
        name="Kinesis Inactive Streams",
        description="Identifies Kinesis streams with no/near-zero traffic over a lookback window",
        resource_type="kinesis_stream",
        check_function=check_kinesis_inactive_streams,
        default_action="delete_or_downsize_stream",
        parameters={
            "lookback_days": 14,
            "max_incoming_bytes_total": 0.0,
            "max_incoming_records_total": 0.0,
            "region": None,
        },
    )
)
