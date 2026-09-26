"""Athena datasource non-parquet file-type check."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.athena._datasource_sampling import (
    collect_tables,
    is_parquet_by_metadata,
    parse_s3_uri,
    sample_parquet_ratio,
    sample_table_files,
    table_resource_id,
)


def check_athena_datasources_non_parquet_file_type(
    aws_adapter,
    min_parquet_ratio: float = 0.8,
    max_files_per_source: int = 10,
    max_partition_locations: int = 200,
    include_table_types: Optional[List[str]] = None,
    exclude_databases: Optional[List[str]] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Flag datasource tables that are not Parquet (metadata + sampled filenames).
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

        samples = sample_table_files(
            s3, glue, table, max_files_per_source=max_files_per_source, max_partition_locations=max_partition_locations
        )
        parquet_ratio = sample_parquet_ratio(samples)
        parquet_meta = is_parquet_by_metadata(table)

        non_parquet = (parquet_meta is False) or (
            parquet_meta is None and parquet_ratio < min_parquet_ratio
        )
        if not non_parquet:
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
                    "sampled_parquet_ratio": round(parquet_ratio, 3),
                    "min_parquet_ratio": min_parquet_ratio,
                    "sampled_file_count": len(samples),
                    "max_files_per_source": max_files_per_source,
                    "recommended_action": "convert_to_parquet",
                    "check_reason": create_check_reason(
                        "unused",
                        {
                            "reason": (
                                "Datasource is not confidently Parquet based on metadata/sample"
                            )
                        },
                    ),
                },
            }
        )

    return flagged


check_registry.register(
    CheckMetadata(
        check_id="athena_datasources_non_parquet_file_type",
        name="Athena Datasources Non-Parquet File Type",
        description="Identifies Athena datasource tables that are not Parquet",
        resource_type="glue_table",
        check_function=check_athena_datasources_non_parquet_file_type,
        default_action="convert_to_parquet",
        parameters={
            "min_parquet_ratio": 0.8,
            "max_files_per_source": 10,
            "max_partition_locations": 200,
            "include_table_types": ["EXTERNAL_TABLE"],
            "exclude_databases": [],
            "region": None,
        },
    )
)
