"""Glue tables without partitions check."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry


def _list_glue_databases(glue_client) -> List[str]:
    names: List[str] = []
    paginator = glue_client.get_paginator("get_databases")
    for page in paginator.paginate():
        for db in page.get("DatabaseList", []):
            name = db.get("Name")
            if name:
                names.append(name)
    return names


def _list_glue_tables(glue_client, database_name: str) -> List[Dict[str, Any]]:
    tables: List[Dict[str, Any]] = []
    paginator = glue_client.get_paginator("get_tables")
    for page in paginator.paginate(DatabaseName=database_name):
        tables.extend(page.get("TableList", []))
    return tables


def check_glue_tables_without_partitions(
    aws_adapter,
    include_table_types: Optional[List[str]] = None,
    exclude_databases: Optional[List[str]] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Identify Glue tables that have no partition keys defined.
    """
    glue = aws_adapter.session.client("glue", region_name=region)
    include_types = {t.upper() for t in (include_table_types or ["EXTERNAL_TABLE"])}
    excluded_dbs: Set[str] = {d.lower() for d in (exclude_databases or [])}

    flagged: List[Dict[str, Any]] = []
    for db_name in _list_glue_databases(glue):
        if db_name.lower() in excluded_dbs:
            continue

        try:
            tables = _list_glue_tables(glue, db_name)
        except Exception as exc:
            print(f"Error listing Glue tables for database {db_name}: {exc}")
            continue

        for table in tables:
            try:
                table_name = table.get("Name")
                if not table_name:
                    continue

                table_type = str(table.get("TableType") or "").upper()
                if include_types and table_type not in include_types:
                    continue

                partition_keys = table.get("PartitionKeys") or []
                if len(partition_keys) > 0:
                    continue

                resource_id = f"{db_name}.{table_name}"
                flagged.append(
                    {
                        "resource_id": resource_id,
                        "resource_type": "glue_table",
                        "resource_name": table_name,
                        "region": region,
                        "state": "active",
                        "metadata": {
                            "database_name": db_name,
                            "table_name": table_name,
                            "table_type": table_type or None,
                            "partition_key_count": 0,
                            "recommended_action": "add_partitions",
                            "check_reason": create_check_reason(
                                "missing_policy",
                                {
                                    "policy": "partition_keys",
                                    "resource": "glue_table",
                                    "database": db_name,
                                    "table": table_name,
                                },
                            ),
                        },
                    }
                )
            except Exception as exc:
                print(f"Error evaluating Glue table {db_name}.{table.get('Name')}: {exc}")
                continue

    return flagged


check_registry.register(
    CheckMetadata(
        check_id="glue_tables_without_partitions",
        name="Glue Tables Without Partitions",
        description="Identifies Glue tables without partition keys",
        resource_type="glue_table",
        check_function=check_glue_tables_without_partitions,
        default_action="add_partitions",
        parameters={
            "include_table_types": ["EXTERNAL_TABLE"],
            "exclude_databases": [],
            "region": None,
        },
    )
)
