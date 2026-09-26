"""Local CUR cache path and refresh planning helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from pricing.cur.datasets import DATASETS, DEFAULT_CACHE_ROOT
from pricing.cur.months import BillingMonth, month_range


PART_FILE = "part.parquet"


@dataclass(frozen=True)
class CachedMonth:
    dataset: str
    month: BillingMonth
    path: Path
    refreshed_at: Optional[str]


@dataclass(frozen=True)
class RefreshTask:
    dataset: str
    month: BillingMonth
    reason: str
    output_path: Path


def dataset_path(cache_root: Path, dataset: str) -> Path:
    return cache_root / dataset


def parquet_path(cache_root: Path, dataset: str, month: BillingMonth) -> Path:
    year_part, month_part = month.path_parts()
    return dataset_path(cache_root, dataset) / year_part / month_part / PART_FILE


def _read_refreshed_at(path: Path) -> Optional[str]:
    try:
        import pyarrow.parquet as pq

        table = pq.read_table(path, columns=["cache_refreshed_at"])
        values = table.column("cache_refreshed_at").to_pylist()
        return str(values[0]) if values else None
    except Exception:
        if path.exists():
            return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        return None


def discover_cached_months(cache_root: Path = DEFAULT_CACHE_ROOT) -> Dict[str, Dict[BillingMonth, CachedMonth]]:
    discovered: Dict[str, Dict[BillingMonth, CachedMonth]] = {name: {} for name in DATASETS}
    for dataset in DATASETS:
        root = dataset_path(cache_root, dataset)
        if not root.exists():
            continue
        for path in root.glob("year=*/month=*/*.parquet"):
            try:
                year = int(path.parent.parent.name.split("=", 1)[1])
                month = int(path.parent.name.split("=", 1)[1])
                billing_month = BillingMonth(year, month)
            except (IndexError, ValueError):
                continue
            discovered[dataset][billing_month] = CachedMonth(
                dataset=dataset,
                month=billing_month,
                path=path,
                refreshed_at=_read_refreshed_at(path),
            )
    return discovered


def _selected_datasets(values: Optional[Iterable[str]]) -> List[str]:
    if not values:
        return list(DATASETS)
    selected = list(values)
    unknown = sorted(set(selected) - set(DATASETS))
    if unknown:
        raise ValueError(f"Unknown CUR datasets: {', '.join(unknown)}")
    return selected


def plan_refresh_tasks(
    *,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    datasets: Optional[Iterable[str]] = None,
    now: Optional[datetime] = None,
    start_month: Optional[BillingMonth] = None,
    end_month: Optional[BillingMonth] = None,
    force: bool = False,
    refresh_previous_days: int = 3,
) -> List[RefreshTask]:
    now = now or datetime.now(timezone.utc)
    actual_current_month = BillingMonth.current(now)
    end_month = end_month or BillingMonth.current(now)
    cached = discover_cached_months(cache_root)
    tasks: List[RefreshTask] = []

    for dataset in _selected_datasets(datasets):
        dataset_cached = cached.get(dataset, {})
        if start_month:
            first_month = start_month
        elif dataset_cached:
            first_month = min(dataset_cached)
        else:
            first_month = end_month.add(-12)

        planned: Set[BillingMonth] = set()
        for month in month_range(first_month, end_month):
            cached_month = month in dataset_cached
            reason = None
            if force:
                reason = "force"
            elif not cached_month:
                reason = "missing"
            elif month == actual_current_month:
                reason = "current_month"
            elif month.is_previous_month_grace(now, refresh_previous_days):
                reason = "previous_month_grace"

            if reason and month not in planned:
                planned.add(month)
                tasks.append(
                    RefreshTask(
                        dataset=dataset,
                        month=month,
                        reason=reason,
                        output_path=parquet_path(cache_root, dataset, month),
                    )
                )
    return tasks
