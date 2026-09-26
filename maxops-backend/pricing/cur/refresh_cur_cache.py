#!/usr/bin/env python3
"""Refresh local Parquet CUR aggregate cache from Athena."""

from __future__ import annotations

import argparse
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from pricing.cur.athena import AthenaConfig, create_session, download_athena_csv, parse_s3_uri, run_athena_query
from pricing.cur.cache import RefreshTask, plan_refresh_tasks
from pricing.cur.datasets import (
    DATASETS,
    DEFAULT_ATHENA_RESULTS_S3,
    DEFAULT_CACHE_ROOT,
    DEFAULT_DATABASE,
    DEFAULT_RAW_TABLE,
    DEFAULT_REGION,
    DEFAULT_S3_CACHE_ROOT,
)
from pricing.cur.months import BillingMonth
from pricing.cur.sql import render_dataset_query, render_unload_query


def write_parquet(df: pd.DataFrame, task: RefreshTask, refreshed_at: datetime) -> bool:
    if df.empty:
        return False

    output = task.output_path
    output.parent.mkdir(parents=True, exist_ok=True)
    enriched = df.copy()
    # First day of the month as an ISO date, not "YYYY-MM". `billing_month` is
    # the date column the monthly datasets are filtered on, and those filters
    # compare against full dates — "2026-09" sorts before "2026-09-01", so the
    # short form silently excludes every row from every monthly report.
    enriched["billing_month"] = task.month.start_date().isoformat()
    enriched["dataset"] = task.dataset
    enriched["cache_refreshed_at"] = refreshed_at.isoformat()
    enriched.to_parquet(output, index=False, engine="pyarrow")
    return True


def _s3_prefix(root: str, task: RefreshTask) -> str:
    year_part, month_part = task.month.path_parts()
    return f"{root.rstrip('/')}/{task.dataset}/{year_part}/{month_part}/"


def _staging_prefix(root: str, task: RefreshTask, run_id: str) -> str:
    year_part, month_part = task.month.path_parts()
    return f"{root.rstrip('/')}/_staging/{run_id}/{task.dataset}/{year_part}/{month_part}/"


def _list_s3_keys(session, prefix: str, region: str) -> list[str]:
    bucket, key_prefix = parse_s3_uri(prefix)
    s3 = session.client("s3", region_name=region)
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=key_prefix):
        keys.extend(item["Key"] for item in page.get("Contents", []))
    return keys


def _delete_s3_keys(session, bucket: str, keys: list[str], region: str) -> int:
    if not keys:
        return 0
    s3 = session.client("s3", region_name=region)
    deleted = 0
    for index in range(0, len(keys), 1000):
        batch = keys[index : index + 1000]
        response = s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
        )
        deleted += len(batch) - len(response.get("Errors", []))
    return deleted


def _delete_s3_prefix(session, prefix: str, region: str) -> int:
    bucket, _ = parse_s3_uri(prefix)
    return _delete_s3_keys(session, bucket, _list_s3_keys(session, prefix, region), region)


def _copy_s3_prefix(
    session,
    source_prefix: str,
    destination_prefix: str,
    region: str,
    destination_name_prefix: str = "",
) -> int:
    source_bucket, source_key_prefix = parse_s3_uri(source_prefix)
    destination_bucket, destination_key_prefix = parse_s3_uri(destination_prefix)
    s3 = session.client("s3", region_name=region)
    copied = 0
    for source_key in _list_s3_keys(session, source_prefix, region):
        relative_key = source_key[len(source_key_prefix) :].lstrip("/")
        if not relative_key:
            continue
        destination_name = f"{destination_name_prefix}{relative_key.replace('/', '-')}"
        if not destination_name.endswith(".parquet"):
            destination_name = f"{destination_name}.parquet"
        destination_key = f"{destination_key_prefix.rstrip('/')}/{destination_name}"
        s3.copy_object(
            Bucket=destination_bucket,
            Key=destination_key,
            CopySource={"Bucket": source_bucket, "Key": source_key},
        )
        copied += 1
    return copied


def _parquet_keys(session, prefix: str, region: str) -> list[str]:
    keys = []
    for key in _list_s3_keys(session, prefix, region):
        name = Path(key).name
        if not name or name.startswith("_") or "manifest" in name.lower() or name.endswith(".metadata"):
            continue
        if key.endswith(".parquet") or "." not in name:
            keys.append(key)
    return keys


def _local_staging_dir(task: RefreshTask, run_id: str) -> Path:
    return task.output_path.parents[3] / "_staging" / run_id / task.dataset / task.output_path.parent.parent.name / task.output_path.parent.name


def _download_s3_prefix(session, source_prefix: str, destination_dir: Path, region: str) -> int:
    bucket, key_prefix = parse_s3_uri(source_prefix)
    s3 = session.client("s3", region_name=region)
    if destination_dir.exists():
        shutil.rmtree(destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    for source_key in _parquet_keys(session, source_prefix, region):
        relative_key = source_key[len(key_prefix) :].lstrip("/")
        if not relative_key:
            continue
        destination_name = relative_key.replace("/", "-")
        if not destination_name.endswith(".parquet"):
            destination_name = f"{destination_name}.parquet"
        destination = destination_dir / destination_name
        s3.download_file(bucket, source_key, str(destination))
        downloaded += 1
    return downloaded


def _promote_local_cache(task: RefreshTask, staging_dir: Path) -> int:
    parquet_files = sorted(staging_dir.glob("*.parquet"))
    if not parquet_files:
        raise RuntimeError(f"No local Parquet files downloaded to {staging_dir}")

    stable_dir = task.output_path.parent
    backup_dir = stable_dir.with_name(f".{stable_dir.name}.previous-{uuid.uuid4().hex}")
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    if stable_dir.exists():
        stable_dir.rename(backup_dir)
    stable_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir.rename(stable_dir)
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    return len(parquet_files)


def refresh_task_to_s3_unload(
    session,
    task: RefreshTask,
    *,
    select_query: str,
    config: AthenaConfig,
    s3_cache_root: str,
    run_id: str,
    download_local: bool = True,
) -> dict:
    stable_prefix = _s3_prefix(s3_cache_root, task)
    staging_prefix = _staging_prefix(s3_cache_root, task, run_id)
    old_stable_keys = _list_s3_keys(session, stable_prefix, config.region)

    # Athena UNLOAD requires an empty destination. The run-scoped prefix should
    # be new, but remove anything there from a retried run before submitting.
    _delete_s3_prefix(session, staging_prefix, config.region)

    unload_query = render_unload_query(select_query, staging_prefix)
    athena_output = run_athena_query(session, unload_query, config)
    parquet_keys = _parquet_keys(session, staging_prefix, config.region)
    if not parquet_keys:
        raise RuntimeError(f"Athena UNLOAD wrote no Parquet files to {staging_prefix}")

    copied_objects = _copy_s3_prefix(
        session,
        staging_prefix,
        stable_prefix,
        config.region,
        destination_name_prefix=f"{run_id}-",
    )
    stable_bucket, _ = parse_s3_uri(stable_prefix)
    deleted_old_objects = _delete_s3_keys(session, stable_bucket, old_stable_keys, config.region)
    deleted_staging_objects = _delete_s3_prefix(session, staging_prefix, config.region)
    local_files = 0
    local_staging_dir = None
    if download_local:
        local_staging_dir = _local_staging_dir(task, run_id)
        _download_s3_prefix(session, stable_prefix, local_staging_dir, config.region)
        local_files = _promote_local_cache(task, local_staging_dir)
        local_staging_root = task.output_path.parents[3] / "_staging" / run_id
        if local_staging_root.exists():
            shutil.rmtree(local_staging_root)

    return {
        "s3_output_prefix": stable_prefix,
        "staging_prefix": staging_prefix,
        "local_output_dir": task.output_path.parent.as_posix() if download_local else None,
        "local_staging_dir": local_staging_dir.as_posix() if local_staging_dir else None,
        "athena_output": athena_output,
        "parquet_files": len(parquet_keys),
        "copied_objects": copied_objects,
        "local_files": local_files,
        "deleted_old_objects": deleted_old_objects,
        "deleted_staging_objects": deleted_staging_objects,
    }


def refresh_tasks(
    tasks: Iterable[RefreshTask],
    *,
    profile: Optional[str],
    region: str,
    database: str,
    raw_table: str,
    athena_results_s3: str,
    workgroup: str,
    mode: str = "local",
    s3_cache_root: Optional[str] = DEFAULT_S3_CACHE_ROOT,
    download_local: bool = True,
    dry_run: bool = False,
) -> list[dict]:
    if mode == "s3-unload" and not s3_cache_root:
        raise ValueError(
            "s3-unload mode needs an S3 cache root. Pass --s3-cache-root, or set "
            "MAXOPS_CUR_BUCKET_ARN to derive it."
        )

    session = None if dry_run else create_session(profile)
    results = []
    refreshed_at = datetime.now(timezone.utc)
    run_id = f"{refreshed_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex}"
    config = AthenaConfig(
        database=database,
        output_location=athena_results_s3,
        workgroup=workgroup,
        region=region,
        profile=profile,
    )

    for task in tasks:
        query = render_dataset_query(task.dataset, database=database, raw_table=raw_table, month=task.month)
        stable_prefix = _s3_prefix(s3_cache_root, task)
        staging_prefix = _staging_prefix(s3_cache_root, task, run_id)
        local_staging_dir = _local_staging_dir(task, run_id)
        if dry_run:
            results.append(
                {
                    "dataset": task.dataset,
                    "month": str(task.month),
                    "reason": task.reason,
                    "mode": mode,
                    "output_path": str(task.output_path),
                    "s3_output_prefix": stable_prefix if mode == "s3-unload" else None,
                    "staging_prefix": staging_prefix if mode == "s3-unload" else None,
                    "local_output_dir": (
                        task.output_path.parent.as_posix()
                        if mode == "s3-unload" and download_local
                        else None
                    ),
                    "local_staging_dir": (
                        local_staging_dir.as_posix()
                        if mode == "s3-unload" and download_local
                        else None
                    ),
                    "query": query,
                    "unload_query": render_unload_query(query, staging_prefix) if mode == "s3-unload" else None,
                    "dry_run": True,
                }
            )
            continue

        if mode == "s3-unload":
            unload_result = refresh_task_to_s3_unload(
                session,
                task,
                select_query=query,
                config=config,
                s3_cache_root=s3_cache_root,
                run_id=run_id,
                download_local=download_local,
            )
            results.append(
                {
                    "dataset": task.dataset,
                    "month": str(task.month),
                    "reason": task.reason,
                    "mode": mode,
                    **unload_result,
                }
            )
            continue

        output_location = run_athena_query(session, query, config)
        dataframe = download_athena_csv(session, output_location, region)
        wrote_file = write_parquet(dataframe, task, refreshed_at)
        results.append(
            {
                "dataset": task.dataset,
                "month": str(task.month),
                "reason": task.reason,
                "mode": mode,
                "output_path": str(task.output_path),
                "athena_output": output_location,
                "rows": int(len(dataframe)),
                "wrote_file": wrote_file,
            }
        )
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh local CUR aggregate Parquet cache.")
    parser.add_argument("--profile", help="AWS CLI profile to use.")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--raw-table", default=DEFAULT_RAW_TABLE)
    parser.add_argument("--athena-results-s3", default=DEFAULT_ATHENA_RESULTS_S3)
    parser.add_argument("--workgroup", default="primary")
    parser.add_argument("--mode", choices=["local", "s3-unload"], default="local")
    parser.add_argument("--s3-cache-root", default=DEFAULT_S3_CACHE_ROOT)
    parser.add_argument("--no-download-local", action="store_true", help="For s3-unload mode, skip downloading promoted Parquet files into the local DuckDB cache.")
    parser.add_argument("--dataset", action="append", choices=sorted(DATASETS), help="Dataset to refresh. Repeatable.")
    parser.add_argument("--cache-root", default=str(DEFAULT_CACHE_ROOT))
    parser.add_argument("--start-month", help="Earliest month to consider, formatted YYYY-MM.")
    parser.add_argument("--end-month", help="Latest month to consider, formatted YYYY-MM.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--refresh-previous-days", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tasks = plan_refresh_tasks(
        cache_root=Path(args.cache_root),
        datasets=args.dataset,
        start_month=BillingMonth.parse(args.start_month) if args.start_month else None,
        end_month=BillingMonth.parse(args.end_month) if args.end_month else None,
        force=args.force,
        refresh_previous_days=args.refresh_previous_days,
    )
    results = refresh_tasks(
        tasks,
        profile=args.profile,
        region=args.region,
        database=args.database,
        raw_table=args.raw_table,
        athena_results_s3=args.athena_results_s3,
        workgroup=args.workgroup,
        mode=args.mode,
        s3_cache_root=args.s3_cache_root,
        download_local=not args.no_download_local,
        dry_run=args.dry_run,
    )
    print(json.dumps({"planned": len(tasks), "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
