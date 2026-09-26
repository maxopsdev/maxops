from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from pricing.cur.cache import discover_cached_months, parquet_path, plan_refresh_tasks
from pricing.cur.datasets import (
    DATASETS,
    DEFAULT_ATHENA_RESULTS_S3,
    DEFAULT_CUR_BUCKET_ARN,
    DEFAULT_S3_CACHE_ROOT,
    athena_results_from_bucket_arn,
    bucket_name_from_arn,
)
from pricing.cur.months import BillingMonth
from pricing.cur.reader import build_reader_sql, query_dataset
from pricing.cur.refresh_cur_cache import refresh_tasks, write_parquet
from pricing.cur.sql import render_dataset_query, render_unload_query


def test_no_bucket_is_assumed_unless_configured():
    # No default bucket: MaxOps derives maxops-cur-report-<account id> once the
    # account is known. A hardcoded fallback would point every install at one
    # bucket that exists in nobody else's account.
    assert DEFAULT_CUR_BUCKET_ARN is None
    assert DEFAULT_ATHENA_RESULTS_S3 is None

    # The derivation helpers still work when given a bucket.
    assert bucket_name_from_arn("arn:aws:s3:::my-bucket") == "my-bucket"
    assert (
        athena_results_from_bucket_arn("arn:aws:s3:::my-bucket")
        == "s3://my-bucket/athena-results/"
    )


def test_cur_datasets_include_daily_and_monthly_report_families():
    assert set(DATASETS) == {
        "service_daily",
        "service_monthly",
        "usage_type_daily",
        "usage_type_monthly",
        "resource_daily",
        "resource_monthly",
    }
    assert DATASETS["service_monthly"].date_column == "billing_month"
    assert DATASETS["usage_type_monthly"].date_column == "billing_month"
    assert DATASETS["service_monthly"].query_file == "service_monthly.sql"
    assert DATASETS["usage_type_monthly"].query_file == "usage_type_monthly.sql"


def test_render_dataset_query_filters_one_month_and_uses_safe_identifier_quotes():
    query = render_dataset_query(
        "service_daily",
        database="maxops_cur",
        raw_table="curv2_raw",
        month=BillingMonth(2026, 5),
    )

    assert 'FROM "maxops_cur"."curv2_raw"' in query
    assert "TIMESTAMP '2026-05-01 00:00:00'" in query
    assert "TIMESTAMP '2026-06-01 00:00:00'" in query
    assert "WHERE CAST(line_item_usage_start_date AS date)" not in query
    assert "GROUP BY 1, 2, 3, 4, 5, 6" in query
    assert "TRY_CAST(line_item_unblended_cost AS double)" in query


def test_render_dataset_query_includes_only_net_amortized_costs():
    for dataset in DATASETS:
        query = render_dataset_query(
            dataset,
            database="maxops_cur",
            raw_table="curv2_raw",
            month=BillingMonth(2026, 5),
        )

        assert "AS net_amortized_cost" in query
        assert "AS amortized_cost" not in query
        assert "line_item_line_item_type IN ('DiscountedUsage', 'DiscountUsage')" in query
        assert "TRY_CAST(reservation_net_effective_cost AS double)" in query
        assert "TRY_CAST(reservation_effective_cost AS double)" in query
        assert "line_item_line_item_type = 'SavingsPlanCoveredUsage'" in query
        assert "TRY_CAST(savings_plan_net_savings_plan_effective_cost AS double)" in query
        assert "TRY_CAST(savings_plan_savings_plan_effective_cost AS double)" in query
        assert "line_item_line_item_type IN ('SavingsPlanNegation', 'SavingsPlanUpfrontFee')" in query
        assert "TRY_CAST(line_item_net_unblended_cost AS double)" in query
        assert "TRY_CAST(line_item_unblended_cost AS double)" in query


def test_render_unload_query_writes_aggregate_select_as_snappy_parquet():
    select_query = render_dataset_query(
        "service_monthly",
        database="maxops_cur",
        raw_table="curv2_raw",
        month=BillingMonth(2026, 5),
    )

    unload_query = render_unload_query(
        select_query,
        "s3://maxops-cur-report-123456789012/cur-aggregates/service_monthly/year=2026/month=05/",
    )

    assert unload_query.startswith("UNLOAD (\nSELECT")
    assert 'FROM "maxops_cur"."curv2_raw"' in unload_query
    assert "TO 's3://maxops-cur-report-123456789012/cur-aggregates/service_monthly/year=2026/month=05/'" in unload_query
    assert "WITH (format = 'PARQUET', compression = 'SNAPPY')" in unload_query


def test_refresh_tasks_s3_unload_dry_run_uses_staging_and_stable_prefixes():
    task = type(
        "Task",
        (),
        {
            "dataset": "service_monthly",
            "month": BillingMonth(2026, 5),
            "reason": "missing",
            "output_path": Path("data/cur_cache/service_monthly/year=2026/month=05/part.parquet"),
        },
    )()

    results = refresh_tasks(
        [task],
        profile=None,
        region="us-east-1",
        database="maxops_cur",
        raw_table="curv2_raw",
        # s3-unload needs an explicit bucket: there is no default one.
        athena_results_s3="s3://maxops-cur-report-123456789012/athena-results/",
        workgroup="primary",
        mode="s3-unload",
        s3_cache_root="s3://maxops-cur-report-123456789012/cur-aggregates",
        dry_run=True,
    )

    result = results[0]
    assert result["mode"] == "s3-unload"
    assert result["s3_output_prefix"] == (
        "s3://maxops-cur-report-123456789012/cur-aggregates/service_monthly/year=2026/month=05/"
    )
    assert "/_staging/" in result["staging_prefix"]
    assert result["local_output_dir"] == "data/cur_cache/service_monthly/year=2026/month=05"
    assert result["local_staging_dir"].startswith("data/cur_cache/_staging/")
    assert result["local_staging_dir"].endswith("/service_monthly/year=2026/month=05")
    assert result["unload_query"].startswith("UNLOAD (\nSELECT")
    assert "WITH (format = 'PARQUET', compression = 'SNAPPY')" in result["unload_query"]


def test_empty_cache_plans_current_plus_previous_12_months_for_each_dataset(tmp_path):
    tasks = plan_refresh_tasks(
        cache_root=tmp_path,
        now=datetime(2026, 5, 25, tzinfo=timezone.utc),
    )

    assert len(tasks) == len(DATASETS) * 13
    assert {str(task.month) for task in tasks} == {
        "2025-05",
        "2025-06",
        "2025-07",
        "2025-08",
        "2025-09",
        "2025-10",
        "2025-11",
        "2025-12",
        "2026-01",
        "2026-02",
        "2026-03",
        "2026-04",
        "2026-05",
    }
    assert {task.reason for task in tasks} == {"missing"}


def test_existing_cache_backfills_missing_months_and_refreshes_current(tmp_path):
    parquet_path(tmp_path, "service_daily", BillingMonth(2026, 1)).parent.mkdir(parents=True)
    parquet_path(tmp_path, "service_daily", BillingMonth(2026, 1)).write_text("stub", encoding="utf-8")
    parquet_path(tmp_path, "service_daily", BillingMonth(2026, 3)).parent.mkdir(parents=True)
    parquet_path(tmp_path, "service_daily", BillingMonth(2026, 3)).write_text("stub", encoding="utf-8")
    parquet_path(tmp_path, "service_daily", BillingMonth(2026, 5)).parent.mkdir(parents=True)
    parquet_path(tmp_path, "service_daily", BillingMonth(2026, 5)).write_text("stub", encoding="utf-8")

    tasks = plan_refresh_tasks(
        cache_root=tmp_path,
        datasets=["service_daily"],
        now=datetime(2026, 5, 25, tzinfo=timezone.utc),
    )

    assert [(str(task.month), task.reason) for task in tasks] == [
        ("2026-02", "missing"),
        ("2026-04", "missing"),
        ("2026-05", "current_month"),
    ]


def test_closed_months_are_skipped_outside_previous_month_grace(tmp_path):
    for month in [BillingMonth(2026, 1), BillingMonth(2026, 2), BillingMonth(2026, 3)]:
        path = parquet_path(tmp_path, "service_daily", month)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stub", encoding="utf-8")

    tasks = plan_refresh_tasks(
        cache_root=tmp_path,
        datasets=["service_daily"],
        end_month=BillingMonth(2026, 3),
        now=datetime(2026, 5, 25, tzinfo=timezone.utc),
    )

    assert tasks == []


def test_discover_cached_months_reads_hive_style_paths(tmp_path):
    path = parquet_path(tmp_path, "resource_daily", BillingMonth(2026, 5)).with_name("run-output.parquet")
    path.parent.mkdir(parents=True)
    path.write_text("stub", encoding="utf-8")

    discovered = discover_cached_months(tmp_path)

    assert BillingMonth(2026, 5) in discovered["resource_daily"]
    assert discovered["resource_daily"][BillingMonth(2026, 5)].path == path


def test_build_reader_sql_uses_duckdb_parquet_glob_and_filters():
    sql, params = build_reader_sql(
        "resource_daily",
        cache_root=Path("data/cur_cache"),
        start_date="2026-05-01",
        end_date="2026-06-01",
        filters={"account_id": "123456789012", "region": "us-east-1", "resource_id": "i-123"},
        limit=25,
        offset=50,
    )

    assert "read_parquet(?, hive_partitioning=true, union_by_name=true)" in sql
    assert "line_item_usage_account_id = ?" in sql
    assert "product_region_code = ?" in sql
    assert "line_item_resource_id = ?" in sql
    assert params == [
        "data/cur_cache/resource_daily/year=*/month=*/*.parquet",
        "2026-05-01",
        "2026-06-01",
        "123456789012",
        "us-east-1",
        "i-123",
        25,
        50,
    ]


def test_write_parquet_and_query_dataset_round_trip_with_duckdb(tmp_path):
    month = BillingMonth(2026, 5)
    task = type(
        "Task",
        (),
        {
            "dataset": "service_daily",
            "month": month,
            "output_path": parquet_path(tmp_path, "service_daily", month),
        },
    )()
    frame = pd.DataFrame(
        [
            {
                "usage_date": "2026-05-15",
                "bill_payer_account_id": "123456789012",
                "line_item_usage_account_id": "123456789012",
                "service_code": "AmazonEC2",
                "product_region_code": "us-east-1",
                "product_location": "US East (N. Virginia)",
                "usage_amount": 10.0,
                "unblended_cost": 2.5,
            }
        ]
    )

    assert write_parquet(frame, task, datetime(2026, 5, 25, tzinfo=timezone.utc))

    rows = query_dataset(
        "service_daily",
        cache_root=tmp_path,
        start_date="2026-05-01",
        end_date="2026-06-01",
        filters={"account_id": "123456789012", "service": "AmazonEC2", "region": "us-east-1"},
    )

    assert len(rows) == 1
    assert rows[0]["service_code"] == "AmazonEC2"
    assert rows[0]["dataset"] == "service_daily"
    # Full ISO date so the monthly date filters match it (see the
    # billing_month regression test below).
    assert rows[0]["billing_month"] == "2026-05-01"


def test_billing_month_is_written_as_a_full_iso_date(tmp_path):
    """The monthly datasets are filtered on billing_month against full dates.

    Writing "2026-09" instead of "2026-09-01" makes every monthly report return
    nothing, because "2026-09" < "2026-09-01" as a string comparison.
    """
    from pricing.cur.cache import RefreshTask
    from pricing.cur.reader import build_reader_sql

    task = RefreshTask(
        dataset="resource_monthly",
        month=BillingMonth(2026, 9),
        reason="test",
        output_path=parquet_path(tmp_path, "resource_monthly", BillingMonth(2026, 9)),
    )
    frame = pd.DataFrame([{"line_item_resource_id": "i-1", "net_amortized_cost": 1.0}])

    write_parquet(frame, task, datetime(2026, 9, 7, tzinfo=timezone.utc))

    written = pd.read_parquet(task.output_path)
    assert written["billing_month"].iloc[0] == "2026-09-01"

    # And it falls inside the window a monthly report actually asks for.
    _, params = build_reader_sql(
        "resource_monthly",
        cache_root=tmp_path,
        start_date="2026-09-01",
        end_date="2026-10-01",
    )
    assert params[1] <= written["billing_month"].iloc[0] < params[2]


def test_null_numerics_come_back_as_none_not_nan(tmp_path):
    """DuckDB returns SQL NULL numerics as NaN, which json.dumps rejects.

    A single null in a CUR row would otherwise fail the whole API response
    with a 500 rather than returning the row.
    """
    import json

    from pricing.cur.reader import query_dataset

    path = parquet_path(tmp_path, "resource_monthly", BillingMonth(2026, 9))
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "billing_month": "2026-09-01",
                "line_item_resource_id": "i-1",
                "net_amortized_cost": 1.5,
                "reservation_effective_cost": None,
            }
        ]
    ).to_parquet(path, index=False)

    rows = query_dataset("resource_monthly", cache_root=tmp_path)

    assert rows[0]["reservation_effective_cost"] is None
    # The whole point: the result has to survive serialisation.
    assert json.dumps(rows)


def test_query_dataset_reads_mixed_schema_months_by_column_name(tmp_path):
    """A pre-pricing-unit month contributes NULL beside a new-schema month."""

    import pyarrow as pa
    import pyarrow.parquet as pq

    root = tmp_path / "resource_daily"
    old_dir = root / "year=2026" / "month=05"
    new_dir = root / "year=2026" / "month=06"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    base = {
        "usage_date": ["2026-05-15"],
        "bill_payer_account_id": ["account-a"],
        "line_item_usage_account_id": ["account-a"],
        "line_item_resource_id": ["bucket-a"],
        "service_code": ["AmazonS3"],
        "product_region_code": ["us-east-1"],
        "product_location": ["US East (N. Virginia)"],
        "line_item_usage_type": ["TimedStorage-ByteHrs"],
        "line_item_operation": ["StandardStorage"],
        "line_item_line_item_type": ["Usage"],
        "usage_amount": [1.0],
        "unblended_cost": [0.023],
        "net_amortized_cost": [0.023],
    }
    pq.write_table(pa.Table.from_pydict(base), old_dir / "old.parquet")
    newer = dict(base)
    newer["usage_date"] = ["2026-06-15"]
    newer["pricing_unit"] = ["GB-Mo"]
    pq.write_table(pa.Table.from_pydict(newer), new_dir / "new.parquet")

    rows = query_dataset(
        "resource_daily",
        cache_root=tmp_path,
        start_date="2026-05-01",
        end_date="2026-07-01",
        limit=10,
    )

    assert {row["pricing_unit"] for row in rows if pd.isna(row["pricing_unit"])}
    assert {row["pricing_unit"] for row in rows if not pd.isna(row["pricing_unit"])} == {"GB-Mo"}
