"""Athena datasource low average file-size check."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.athena._datasource_sampling import (
    collect_tables,
    is_parquet_by_metadata,
    parse_s3_uri,
    sample_table_files,
    table_resource_id,
)


def check_athena_datasources_low_avg_file_size(
    aws_adapter,
    min_avg_file_size_mb: float = 128.0,
    max_files_per_source: int = 10,
    max_partition_locations: int = 200,
    parquet_only: bool = True,
    include_table_types: Optional[List[str]] = None,
    exclude_databases: Optional[List[str]] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Flag datasource tables whose sampled average file size is below threshold.
    """
    glue = aws_adapter.session.client("glue", region_name=region)
    s3 = aws_adapter.session.client("s3", region_name=region)
    include_types = {t.upper() for t in (include_table_types or ["EXTERNAL_TABLE"])}

    flagged: List[Dict[str, Any]] = []
    for table in collect_tables(glue, exclude_databases):
        db_name = table.get("DatabaseName")
        table_name = table.get("Name")
        if not db_name or not table_name:
            continue

        table_type = str(table.get("TableType") or "").upper()
        if include_types and table_type not in include_types:
            continue

        table_location = (table.get("StorageDescriptor") or {}).get("Location")
        if not parse_s3_uri(table_location):
            continue

        parquet_meta = is_parquet_by_metadata(table)
        if parquet_only and parquet_meta is False:
            continue

        samples = sample_table_files(
            s3, glue, table, max_files_per_source=max_files_per_source, max_partition_locations=max_partition_locations
        )
        if not samples:
            continue

        total_bytes = sum(int(obj.get("Size") or 0) for obj in samples)
        avg_mb = (total_bytes / len(samples)) / (1024 * 1024)
        small_count = sum(1 for obj in samples if int(obj.get("Size") or 0) < 128 * 1024 * 1024)
        small_ratio = small_count / len(samples)

        if avg_mb >= min_avg_file_size_mb:
            continue

        flagged.append(
            {
                "resource_id": table_resource_id(db_name, table_name),
                "resource_type": "glue_table",
                "resource_name": table_name,
                "region": region,
                "state": "active",
                "metadata": {
                    "database_name": db_name,
                    "table_name": table_name,
                    "table_type": table_type or None,
                    "table_location": table_location,
                    "parquet_by_metadata": parquet_meta,
                    "sampled_file_count": len(samples),
                    "max_files_per_source": max_files_per_source,
                    "avg_file_size_mb": round(avg_mb, 2),
                    "min_avg_file_size_mb": min_avg_file_size_mb,
                    "small_file_ratio_lt_128mb": round(small_ratio, 3),
                    "recommended_action": "compact_files",
                    "check_reason": create_check_reason(
                        "unused",
                        {
                            "reason": (
                                f"Sampled average file size {round(avg_mb, 2)}MB "
                                f"is below threshold {min_avg_file_size_mb}MB"
                            )
                        },
                    ),
                },
            }
        )

    return flagged


check_registry.register(
    CheckMetadata(
        check_id="athena_datasources_low_avg_file_size",
        name="Athena Datasources Low Avg File Size",
        description="Identifies Athena datasource tables with small sampled average file size",
        resource_type="glue_table",
        check_function=check_athena_datasources_low_avg_file_size,
        default_action="compact_files",
        parameters={
            "min_avg_file_size_mb": 128.0,
            "max_files_per_source": 10,
            "max_partition_locations": 200,
            "parquet_only": True,
            "include_table_types": ["EXTERNAL_TABLE"],
            "exclude_databases": [],
            "region": None,
        },
    )
)
