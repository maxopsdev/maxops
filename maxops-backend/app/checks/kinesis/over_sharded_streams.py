"""Kinesis Data Streams over-sharded check."""
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


def check_kinesis_over_sharded_streams(
    aws_adapter,
    lookback_days: int = 14,
    max_incoming_bytes_per_shard_per_sec: float = 1024 * 1024,  # 1 MB/s
    max_incoming_records_per_shard_per_sec: float = 100.0,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Identify provisioned streams with consistently low utilization per shard.
    """
    if lookback_days <= 0:
        return []

    kinesis = aws_adapter.session.client("kinesis", region_name=region)
    cloudwatch = aws_adapter.session.client("cloudwatch", region_name=region)
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=lookback_days)
    window_seconds = max(1.0, lookback_days * 24 * 3600.0)

    flagged: List[Dict[str, Any]] = []
    for stream_name in _list_stream_names(kinesis):
        try:
            summary = kinesis.describe_stream_summary(StreamName=stream_name).get(
                "StreamDescriptionSummary", {}
            )
            stream_status = str(summary.get("StreamStatus") or "").upper()
            if stream_status != "ACTIVE":
                continue

            stream_mode = str(
                (summary.get("StreamModeDetails") or {}).get("StreamMode") or "PROVISIONED"
            ).upper()
            if stream_mode != "PROVISIONED":
                continue

            shard_count = int(summary.get("OpenShardCount") or 0)
            if shard_count <= 0:
                continue

            incoming_bytes = _sum_metric(
                cloudwatch, stream_name, "IncomingBytes", start_date, end_date, 3600
            )
            incoming_records = _sum_metric(
                cloudwatch, stream_name, "IncomingRecords", start_date, end_date, 3600
            )

            bytes_per_shard_per_sec = incoming_bytes / window_seconds / shard_count
            records_per_shard_per_sec = incoming_records / window_seconds / shard_count

            if (
                bytes_per_shard_per_sec <= max_incoming_bytes_per_shard_per_sec
                and records_per_shard_per_sec <= max_incoming_records_per_shard_per_sec
            ):
                flagged.append(
                    {
                        "resource_id": stream_name,
                        "resource_type": "kinesis_stream",
                        "resource_name": stream_name,
                        "region": region,
                        "state": stream_status,
                        "metadata": {
                            "stream_mode": stream_mode,
                            "open_shard_count": shard_count,
                            "lookback_days": lookback_days,
                            "incoming_bytes_total": int(incoming_bytes),
                            "incoming_records_total": int(incoming_records),
                            "incoming_bytes_per_shard_per_sec": round(bytes_per_shard_per_sec, 2),
                            "incoming_records_per_shard_per_sec": round(records_per_shard_per_sec, 2),
                            "max_incoming_bytes_per_shard_per_sec": max_incoming_bytes_per_shard_per_sec,
                            "max_incoming_records_per_shard_per_sec": max_incoming_records_per_shard_per_sec,
                            "recommended_action": "reduce_shards_or_switch_to_on_demand",
                            "check_reason": create_check_reason(
                                "unused",
                                {
                                    "reason": (
                                        "Provisioned stream has low incoming bytes/records per shard"
                                    )
                                },
                            ),
                        },
                    }
                )
        except Exception as exc:
            print(f"Error checking Kinesis stream {stream_name} for over-sharding: {exc}")
            continue

    return flagged


check_registry.register(
    CheckMetadata(
        check_id="kinesis_over_sharded_streams",
        name="Kinesis Over-Sharded Streams",
        description="Identifies provisioned Kinesis streams with low utilization per shard",
        resource_type="kinesis_stream",
        check_function=check_kinesis_over_sharded_streams,
        default_action="reduce_shards_or_switch_to_on_demand",
        parameters={
            "lookback_days": 14,
            "max_incoming_bytes_per_shard_per_sec": 1024 * 1024,
            "max_incoming_records_per_shard_per_sec": 100.0,
            "region": None,
        },
    )
)
