"""Kinesis Firehose check for small S3 file buffering configuration."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry


def _list_delivery_stream_names(firehose_client) -> List[str]:
    # ListDeliveryStreams has no botocore paginator config -- Firehose paginates
    # this one manually via HasMoreDeliveryStreams/ExclusiveStartDeliveryStreamName
    # instead of a NextToken.
    names: List[str] = []
    has_more = True
    while has_more:
        kwargs: Dict[str, Any] = {}
        if names:
            kwargs["ExclusiveStartDeliveryStreamName"] = names[-1]
        response = firehose_client.list_delivery_streams(**kwargs)
        names.extend(response.get("DeliveryStreamNames", []))
        has_more = bool(response.get("HasMoreDeliveryStreams", False))
    return names


def _is_s3_destination(dest: Dict[str, Any]) -> bool:
    return "ExtendedS3DestinationDescription" in dest or "S3DestinationDescription" in dest


def _extract_destination_config(dest: Dict[str, Any]) -> Dict[str, Any]:
    return dest.get("ExtendedS3DestinationDescription") or dest.get("S3DestinationDescription") or {}


def check_kinesis_firehose_small_files_to_s3(
    aws_adapter,
    min_buffer_size_mb: int = 128,
    min_buffer_interval_seconds: int = 300,
    include_statuses: Optional[List[str]] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Identify Firehose S3 destinations likely to emit small files due to
    low buffering size/interval settings.
    """
    firehose = aws_adapter.session.client("firehose", region_name=region)
    statuses = {s.upper() for s in (include_statuses or ["ACTIVE"])}

    flagged: List[Dict[str, Any]] = []
    for stream_name in _list_delivery_stream_names(firehose):
        try:
            desc = firehose.describe_delivery_stream(
                DeliveryStreamName=stream_name
            ).get("DeliveryStreamDescription", {})
            status = str(desc.get("DeliveryStreamStatus") or "").upper()
            if statuses and status not in statuses:
                continue

            destinations = desc.get("Destinations", [])
            for idx, dest in enumerate(destinations):
                if not _is_s3_destination(dest):
                    continue
                cfg = _extract_destination_config(dest)
                buffering = cfg.get("BufferingHints") or {}
                size_mb = int(buffering.get("SizeInMBs") or 0)
                interval_sec = int(buffering.get("IntervalInSeconds") or 0)

                if size_mb >= min_buffer_size_mb and interval_sec >= min_buffer_interval_seconds:
                    continue

                bucket_arn = cfg.get("BucketARN")
                flagged.append(
                    {
                        "resource_id": stream_name,
                        "resource_type": "firehose_delivery_stream",
                        "resource_name": stream_name,
                        "region": region,
                        "state": status,
                        "metadata": {
                            "destination_index": idx,
                            "bucket_arn": bucket_arn,
                            "buffer_size_mb": size_mb,
                            "buffer_interval_seconds": interval_sec,
                            "min_buffer_size_mb": min_buffer_size_mb,
                            "min_buffer_interval_seconds": min_buffer_interval_seconds,
                            "recommended_action": "increase_buffering_to_reduce_small_files",
                            "check_reason": create_check_reason(
                                "underutilized",
                                {
                                    "resource": "firehose_delivery_stream",
                                    "stream": stream_name,
                                    "buffer_size_mb": size_mb,
                                    "buffer_interval_seconds": interval_sec,
                                },
                            ),
                        },
                    }
                )
        except Exception as exc:
            print(f"Error checking Firehose stream {stream_name} buffering: {exc}")
            continue

    return flagged


check_registry.register(
    CheckMetadata(
        check_id="kinesis_firehose_small_files_to_s3",
        name="Kinesis Firehose Small Files To S3",
        description="Identifies Firehose S3 destinations likely producing small output files",
        resource_type="firehose_delivery_stream",
        check_function=check_kinesis_firehose_small_files_to_s3,
        default_action="increase_buffering_to_reduce_small_files",
        parameters={
            "min_buffer_size_mb": 128,
            "min_buffer_interval_seconds": 300,
            "include_statuses": ["ACTIVE"],
            "region": None,
        },
    )
)
