"""Shared helpers for Athena datasource sampling checks."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple


def parse_s3_uri(uri: Optional[str]) -> Optional[Tuple[str, str]]:
    if not uri:
        return None
    s = str(uri).strip()
    if not s.lower().startswith("s3://"):
        return None
    path = s[5:]
    if "/" in path:
        bucket, prefix = path.split("/", 1)
    else:
        bucket, prefix = path, ""
    return bucket.strip(), prefix.strip()


def list_glue_databases(glue_client) -> List[str]:
    names: List[str] = []
    paginator = glue_client.get_paginator("get_databases")
    for page in paginator.paginate():
        for db in page.get("DatabaseList", []):
            name = db.get("Name")
            if name:
                names.append(name)
    return names


def list_glue_tables(glue_client, database_name: str) -> List[Dict[str, Any]]:
    tables: List[Dict[str, Any]] = []
    paginator = glue_client.get_paginator("get_tables")
    for page in paginator.paginate(DatabaseName=database_name):
        tables.extend(page.get("TableList", []))
    return tables


def list_partition_locations(
    glue_client,
    database_name: str,
    table_name: str,
    max_partition_locations: int,
) -> List[str]:
    locations: List[str] = []
    try:
        paginator = glue_client.get_paginator("get_partitions")
        for page in paginator.paginate(DatabaseName=database_name, TableName=table_name):
            for part in page.get("Partitions", []):
                location = (part.get("StorageDescriptor") or {}).get("Location")
                if location:
                    locations.append(location)
                    if len(locations) >= max_partition_locations:
                        return locations
    except Exception:
        return []
    return locations


def sample_objects_from_prefix(
    s3_client,
    bucket: str,
    prefix: str,
    limit: int,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    token: Optional[str] = None

    while len(out) < limit:
        params: Dict[str, Any] = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            params["ContinuationToken"] = token
        response = s3_client.list_objects_v2(**params)
        for obj in response.get("Contents", []):
            key = obj.get("Key")
            size = obj.get("Size")
            if not key or key.endswith("/") or size is None:
                continue
            out.append({"Key": key, "Size": size})
            if len(out) >= limit:
                break

        if len(out) >= limit or not response.get("IsTruncated"):
            break
        token = response.get("NextContinuationToken")
        if not token:
            break

    return out


def sample_table_files(
    s3_client,
    glue_client,
    table: Dict[str, Any],
    max_files_per_source: int,
    max_partition_locations: int,
) -> List[Dict[str, Any]]:
    sd = table.get("StorageDescriptor") or {}
    locations: List[str] = []

    table_location = sd.get("Location")
    if table_location:
        locations.append(table_location)

    db_name = table.get("DatabaseName")
    table_name = table.get("Name")
    if db_name and table_name:
        part_locations = list_partition_locations(
            glue_client, db_name, table_name, max_partition_locations
        )
        for loc in part_locations:
            if loc not in locations:
                locations.append(loc)

    samples: List[Dict[str, Any]] = []
    seen_keys: Set[str] = set()

    for loc in locations:
        if len(samples) >= max_files_per_source:
            break
        parsed = parse_s3_uri(loc)
        if not parsed:
            continue
        bucket, prefix = parsed
        needed = max_files_per_source - len(samples)
        objs = sample_objects_from_prefix(s3_client, bucket, prefix, min(needed, 3))
        for obj in objs:
            key = obj.get("Key")
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            samples.append({"Bucket": bucket, "Key": key, "Size": obj.get("Size", 0)})
            if len(samples) >= max_files_per_source:
                break

    return samples


def is_parquet_by_metadata(table: Dict[str, Any]) -> Optional[bool]:
    sd = table.get("StorageDescriptor") or {}
    input_format = str(sd.get("InputFormat") or "").lower()
    serde = str((sd.get("SerdeInfo") or {}).get("SerializationLibrary") or "").lower()
    if not input_format and not serde:
        return None
    if "parquet" in input_format or "parquet" in serde:
        return True
    return False


def sample_parquet_ratio(samples: List[Dict[str, Any]]) -> float:
    if not samples:
        return 0.0
    parquet_count = 0
    for obj in samples:
        key = str(obj.get("Key") or "").lower()
        if key.endswith(".parquet") or ".parquet." in key:
            parquet_count += 1
    return parquet_count / max(1, len(samples))


def table_resource_id(db_name: str, table_name: str) -> str:
    return f"{db_name}.{table_name}"


def collect_tables(glue_client, exclude_databases: Optional[List[str]]) -> List[Dict[str, Any]]:
    excluded = {d.lower() for d in (exclude_databases or [])}
    all_tables: List[Dict[str, Any]] = []
    for db_name in list_glue_databases(glue_client):
        if db_name.lower() in excluded:
            continue
        try:
            tables = list_glue_tables(glue_client, db_name)
            for t in tables:
                t["DatabaseName"] = db_name
            all_tables.extend(tables)
        except Exception as exc:
            print(f"Error listing Glue tables for database {db_name}: {exc}")
            continue
    return all_tables
