"""SQL rendering helpers for CUR aggregates."""

from __future__ import annotations

from pricing.cur.datasets import DATASETS
from pricing.cur.months import BillingMonth


def quote_identifier(value: str) -> str:
    if not value.replace("_", "").isalnum():
        raise ValueError(f"Unsafe Athena identifier: {value}")
    return f'"{value}"'


def render_dataset_query(
    dataset: str,
    *,
    database: str,
    raw_table: str,
    month: BillingMonth,
) -> str:
    definition = DATASETS.get(dataset)
    if not definition:
        raise ValueError(f"Unknown CUR dataset: {dataset}")

    template = definition.query_path.read_text(encoding="utf-8")
    return template.format(
        database=quote_identifier(database),
        raw_table=quote_identifier(raw_table),
        month_start=month.start_date().isoformat(),
        month_end=month.end_date().isoformat(),
    )


def render_unload_query(select_query: str, destination: str) -> str:
    if "'" in destination:
        raise ValueError(f"Unsafe Athena UNLOAD destination: {destination}")
    return "\n".join(
        [
            "UNLOAD (",
            select_query,
            ")",
            f"TO '{destination.rstrip('/')}/'",
            "WITH (format = 'PARQUET', compression = 'SNAPPY')",
        ]
    )
