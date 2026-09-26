"""DuckDB reader for local CUR aggregate Parquet cache."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from pricing.cur.cache import discover_cached_months
from pricing.cur.datasets import DATASETS, DEFAULT_CACHE_ROOT
from pricing.cur.months import BillingMonth


FILTER_COLUMNS = {
    "account_id": "line_item_usage_account_id",
    "service": "service_code",
    "region": "product_region_code",
    "resource_id": "line_item_resource_id",
}

RESOURCE_COST_DATASET = "resource_monthly"
RESOURCE_COST_METRIC = "net_amortized_cost"


class CurCacheMissingError(FileNotFoundError):
    pass


def _json_safe_records(frame) -> list[dict[str, Any]]:
    """Rows as plain dicts, with SQL NULLs represented as None.

    DuckDB surfaces a NULL numeric as NaN, and NaN/±Inf are not valid JSON —
    `json.dumps` raises rather than emitting them. CUR rows are full of nullable
    numeric columns, so without this a single null anywhere fails the whole
    response with a 500.
    """
    import math

    records = frame.to_dict(orient="records")
    for record in records:
        for key, value in record.items():
            if isinstance(value, float) and not math.isfinite(value):
                record[key] = None
    return records


def parquet_glob(cache_root: Path, dataset: str) -> str:
    if dataset not in DATASETS:
        raise ValueError(f"Unknown CUR dataset: {dataset}")
    return (cache_root / dataset / "year=*" / "month=*" / "*.parquet").as_posix()


def build_reader_sql(
    dataset: str,
    *,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    filters: Optional[Dict[str, str]] = None,
    limit: int = 500,
    offset: int = 0,
) -> tuple[str, list[Any]]:
    definition = DATASETS.get(dataset)
    if not definition:
        raise ValueError(f"Unknown CUR dataset: {dataset}")

    sql = [
        "SELECT *",
        # Historical CUR months can predate additive columns such as
        # ``pricing_unit``.  Name-based union keeps those months readable and
        # supplies NULL for a column that did not exist in the older file.
        "FROM read_parquet(?, hive_partitioning=true, union_by_name=true)",
        "WHERE 1=1",
    ]
    params: list[Any] = [parquet_glob(cache_root, dataset)]

    if start_date:
        sql.append(f"AND {definition.date_column} >= ?")
        params.append(start_date)
    if end_date:
        sql.append(f"AND {definition.date_column} < ?")
        params.append(end_date)

    for api_name, value in (filters or {}).items():
        if value is None:
            continue
        column = FILTER_COLUMNS.get(api_name)
        if not column:
            raise ValueError(f"Unsupported CUR filter: {api_name}")
        if column not in definition.dimensions:
            raise ValueError(f"Filter '{api_name}' is not valid for dataset '{dataset}'")
        sql.append(f"AND {column} = ?")
        params.append(value)

    sql.append(f"ORDER BY {definition.date_column} DESC")
    sql.append("LIMIT ? OFFSET ?")
    params.extend([limit, offset])
    return "\n".join(sql), params


def query_dataset(
    dataset: str,
    *,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    filters: Optional[Dict[str, str]] = None,
    limit: int = 500,
    offset: int = 0,
) -> list[dict[str, Any]]:
    dataset_root = cache_root / dataset
    if not dataset_root.exists() or not any(dataset_root.glob("year=*/month=*/*.parquet")):
        raise CurCacheMissingError(f"No local cache exists for dataset '{dataset}'")

    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("duckdb is required to query CUR cache") from exc

    sql, params = build_reader_sql(
        dataset,
        cache_root=cache_root,
        start_date=start_date,
        end_date=end_date,
        filters=filters,
        limit=limit,
        offset=offset,
    )
    with duckdb.connect(database=":memory:") as connection:
        frame = connection.execute(sql, params).fetchdf()
    return _json_safe_records(frame)


def latest_cached_month(
    cache_root: Path = DEFAULT_CACHE_ROOT,
    dataset: str = RESOURCE_COST_DATASET,
) -> Optional[BillingMonth]:
    """Most recent billing month present in the local cache for a dataset."""
    months = discover_cached_months(cache_root).get(dataset) or {}
    return max(months) if months else None


def month_parquet_glob(cache_root: Path, dataset: str, month: BillingMonth) -> str:
    year_part, month_part = month.path_parts()
    return (cache_root / dataset / year_part / month_part / "*.parquet").as_posix()


def resource_monthly_costs(
    *,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    billing_month: Optional[BillingMonth] = None,
    account_id: Optional[str] = None,
) -> tuple[Optional[BillingMonth], Dict[str, float]]:
    """Total net amortized cost per resource for a single billing month.

    A resource appears on many CUR rows in a month — one per usage type,
    operation and line-item type — so the rows are summed per resource here
    rather than by the caller.

    Returns the month actually used together with a
    ``{line_item_resource_id: monthly_cost}`` map. If the local cache holds no
    ``resource_monthly`` data, returns ``(None, {})`` so callers can fall back
    to list pricing instead of failing.
    """
    month = billing_month or latest_cached_month(cache_root, RESOURCE_COST_DATASET)
    if month is None:
        return None, {}

    pattern = month_parquet_glob(cache_root, RESOURCE_COST_DATASET, month)
    if not any(Path(pattern).parent.glob("*.parquet")):
        return None, {}

    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("duckdb is required to query CUR cache") from exc

    sql = [
        "SELECT line_item_resource_id AS resource_id,",
        f"       SUM(COALESCE({RESOURCE_COST_METRIC}, 0.0)) AS monthly_cost",
        "FROM read_parquet(?, hive_partitioning=true)",
        "WHERE line_item_resource_id IS NOT NULL",
        "  AND line_item_resource_id <> ''",
    ]
    params: list[Any] = [pattern]
    if account_id:
        sql.append(f"AND {FILTER_COLUMNS['account_id']} = ?")
        params.append(account_id)
    sql.append("GROUP BY 1")

    with duckdb.connect(database=":memory:") as connection:
        rows = connection.execute("\n".join(sql), params).fetchall()

    return month, {str(resource_id): float(cost or 0.0) for resource_id, cost in rows}


def cache_status(cache_root: Path = DEFAULT_CACHE_ROOT, datasets: Optional[Iterable[str]] = None) -> dict[str, Any]:
    cached = discover_cached_months(cache_root)
    selected = list(datasets or DATASETS)
    status = {}
    for dataset in selected:
        months = cached.get(dataset, {})
        status[dataset] = {
            "months": [
                {
                    "month": str(month),
                    "path": str(item.path),
                    "cache_refreshed_at": item.refreshed_at,
                }
                for month, item in sorted(months.items())
            ],
            "month_count": len(months),
        }
    return status
