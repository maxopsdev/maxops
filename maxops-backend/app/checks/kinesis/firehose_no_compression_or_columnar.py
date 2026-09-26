"""Kinesis Firehose check for compression/columnar optimization."""
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


def check_kinesis_firehose_no_compression_or_columnar(
    aws_adapter,
    include_statuses: Optional[List[str]] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Identify Firehose streams writing to S3 without compression and without
    record-format conversion (Parquet/ORC conversion path).
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

                compression = str(cfg.get("CompressionFormat") or "").upper()
                data_format_cfg = cfg.get("DataFormatConversionConfiguration") or {}
                format_conversion_enabled = bool(data_format_cfg.get("Enabled"))

                no_compression = compression in {"", "UNCOMPRESSED"}
                no_columnar_conversion = not format_conversion_enabled
                if not (no_compression and no_columnar_conversion):
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
                            "compression_format": compression or None,
                            "format_conversion_enabled": format_conversion_enabled,
                            "recommended_action": "enable_compression_or_columnar_conversion",
                            "check_reason": create_check_reason(
                                "missing_policy",
                                {
                                    "policy": "compression_or_columnar_conversion",
                                    "stream": stream_name,
                                },
                            ),
                        },
                    }
                )
        except Exception as exc:
            print(f"Error checking Firehose stream {stream_name} compression/config: {exc}")
            continue

    return flagged


check_registry.register(
    CheckMetadata(
        check_id="kinesis_firehose_no_compression_or_columnar",
        name="Kinesis Firehose No Compression/Columnar",
        description="Identifies Firehose S3 destinations without compression and columnar conversion",
        resource_type="firehose_delivery_stream",
        check_function=check_kinesis_firehose_no_compression_or_columnar,
        default_action="enable_compression_or_columnar_conversion",
        parameters={
            "include_statuses": ["ACTIVE"],
            "region": None,
        },
    )
)
