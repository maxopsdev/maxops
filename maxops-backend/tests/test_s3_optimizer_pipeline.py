"""Unit tests for the S3 optimizer AWS collection and scan bridge."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import boto3
from botocore.stub import ANY, Stubber
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.adapters.aws.s3_optimizer_collect import (
    discover_storage_types,
    fetch_daily_storage_metrics,
    get_s3_bucket_lifecycle_typed,
    list_s3_bucket_intelligent_tiering_configurations,
)
from app.database import Base
from app.models.inventory import S3Inventory
from app.checks.s3.bucket_low_access import check_s3_bucket_low_access
from app.checks.s3.bucket_unused import check_s3_bucket_unused
from app.services.s3_bucket_source import coverage_by_bucket, derive_signals
from app.services import s3_optimizer_enrichment
from app.services.s3_optimizer import evaluate_bucket
from app.services.s3_optimizer_enrichment import OPTIMIZER_METADATA_KEYS
from app.services.scan_service import CachedAWSAdapter, _store_inventory_resource


class FakeS3:
    """Small typed-API fake used by adapter collection tests."""

    def __init__(self, lifecycle=None, intelligent=None):
        self.lifecycle = lifecycle
        self.intelligent = intelligent or {"IntelligentTieringConfigurationList": []}

    def get_bucket_lifecycle_configuration(self, **_kwargs):
        if isinstance(self.lifecycle, Exception):
            raise self.lifecycle
        return self.lifecycle or {"Rules": []}

    def list_bucket_intelligent_tiering_configurations(self, **_kwargs):
        return self.intelligent


def _optimizer_metadata():
    """Return five distinct optimizer-owned values for persistence tests."""

    return {
        "s3_optimizer": {"telemetry_status": "usable", "marker": "result"},
        "storage_class_breakdown": {"STANDARD": {"bytes": 1}},
        "lifecycle_transitions": [],
        "intelligent_tiering_config": [],
        "s3_optimizer_version": {"engine": "v1", "marker": "version"},
    }


def test_overlay_store_is_shared_across_regional_cached_adapters():
    """An overlay written through one adapter is visible to another adapter."""

    class Delegate:
        def get_resources(self, *_args, **_kwargs):
            return [{"resource_id": "bucket-east-2", "resource_type": "s3", "metadata": {}}]

    overlay_store = {}
    adapter_a = CachedAWSAdapter(Delegate(), overlay_store)
    adapter_b = CachedAWSAdapter(Delegate(), overlay_store)
    adapter_a.get_resources("s3", region="us-east-1")
    adapter_b.get_resources("s3", region="us-east-2")

    adapter_a.overlay_resource_metadata("s3", "bucket-east-2", "s3_optimizer", {"telemetry_status": "usable"})

    resources = adapter_b.get_resources("s3", region="us-east-2")
    assert resources[0]["metadata"]["s3_optimizer"] == {"telemetry_status": "usable"}


def test_s3_inventory_update_preserves_optimizer_metadata_when_resource_omits_it():
    """An ordinary S3 inventory update retains existing optimizer-owned keys."""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    metadata = _optimizer_metadata()
    initial = {
        "resource_id": "bucket-east-2",
        "resource_type": "s3",
        "region": "us-east-2",
        "metadata": {**metadata, "estimated_monthly_cost": 0.0},
    }
    _store_inventory_resource(
        session,
        "s3",
        initial,
        "111111111111",
        datetime(2026, 9, 21),
        s3_optimizer_metadata=metadata,
    )
    session.flush()

    update = {
        "resource_id": "bucket-east-2",
        "resource_type": "s3",
        "region": "us-east-2",
        "metadata": {"check_field": "preserve-me", "estimated_monthly_cost": 0.0},
    }
    _store_inventory_resource(
        session,
        "s3",
        update,
        "111111111111",
        datetime(2026, 9, 21),
    )
    session.flush()

    row = session.query(S3Inventory).filter_by(resource_id="bucket-east-2").one()
    assert set(OPTIMIZER_METADATA_KEYS) <= set(row.metadata_json)
    assert row.metadata_json["check_field"] == "preserve-me"
    assert row.metadata_json["s3_optimizer_version"] == metadata["s3_optimizer_version"]


def test_check_flagged_s3_resource_does_not_wipe_optimizer_metadata():
    """The policies-phase persistence call preserves keys absent from a finding copy."""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    metadata = _optimizer_metadata()
    initial = {
        "resource_id": "bucket-east-2",
        "resource_type": "s3",
        "region": "us-east-2",
        "metadata": {**metadata, "estimated_monthly_cost": 0.0},
    }
    _store_inventory_resource(
        session,
        "s3",
        initial,
        "111111111111",
        datetime(2026, 9, 21),
        s3_optimizer_metadata=metadata,
    )
    session.flush()

    optimizer_result = {
        "telemetry_status": "usable",
        "coverage": {"cur_days_covered": 90},
        "current": {
            "storage_class_breakdown": {
                "STANDARD": {"bytes": 2**30, "objects": 100}
            },
            "monthly_storage_cost": 1.0,
        },
        "signals": {
            "monthly_data_read_requests": 0,
            "monthly_data_write_requests": 0,
            "monthly_list_requests": 0,
            "window_totals": {"restore_requests": {}},
        },
    }
    flagged_resource = {
        "resource_id": "bucket-east-2",
        "resource_type": "s3",
        "region": "us-east-2",
        "metadata": {"s3_optimizer": optimizer_result, "estimated_monthly_cost": 0.0},
    }
    finding = check_s3_bucket_unused(
        SimpleNamespace(get_resources=lambda *_args, **_kwargs: [flagged_resource]),
        window_days=90,
    )[0]
    assert "storage_class_breakdown" not in finding["metadata"]

    _store_inventory_resource(
        session,
        "s3",
        finding,
        "111111111111",
        datetime(2026, 9, 21),
    )
    session.flush()

    row = session.query(S3Inventory).filter_by(resource_id="bucket-east-2").one()
    assert set(OPTIMIZER_METADATA_KEYS) <= set(row.metadata_json)


def _session():
    """Create a botocore session with fake credentials and no network calls."""

    return boto3.Session(
        aws_access_key_id="test", aws_secret_access_key="test", region_name="us-east-1"
    )


def test_cloudwatch_discovery_paginates_and_caches_and_empty_series_stays_absent():
    """The regional sweep is cached and lagged metrics are not zero-filled."""

    session = _session()
    client = session.client("cloudwatch", region_name="us-east-1")
    with Stubber(client) as stubber:
        stubber.add_response(
            "list_metrics",
            {"Metrics": [{"Namespace": "AWS/S3", "MetricName": "BucketSizeBytes", "Dimensions": [{"Name": "BucketName", "Value": "bucket-a"}, {"Name": "StorageType", "Value": "StandardStorage"}]}], "NextToken": "next"},
            {"Namespace": "AWS/S3", "MetricName": "BucketSizeBytes"},
        )
        stubber.add_response(
            "list_metrics",
            {"Metrics": [{"Namespace": "AWS/S3", "MetricName": "BucketSizeBytes", "Dimensions": [{"Name": "BucketName", "Value": "bucket-a"}, {"Name": "StorageType", "Value": "GlacierStorage"}]}]},
            {"Namespace": "AWS/S3", "MetricName": "BucketSizeBytes", "NextToken": "next"},
        )
        stubber.add_response(
            "get_metric_data",
            {"MetricDataResults": [{"Id": "size_0", "Timestamps": [], "Values": []}, {"Id": "size_1", "Timestamps": [], "Values": []}, {"Id": "number_of_objects", "Timestamps": [], "Values": []}]},
            {"MetricDataQueries": ANY, "StartTime": ANY, "EndTime": ANY, "ScanBy": "TimestampAscending"},
        )
        adapter = SimpleNamespace(session=SimpleNamespace(client=lambda _name, **_kwargs: client))
        adapter._s3_optimizer_storage_types = {}
        assert discover_storage_types(adapter, "us-east-1") == {"bucket-a": {"StandardStorage", "GlacierStorage"}}
        assert discover_storage_types(adapter, "us-east-1") == {"bucket-a": {"StandardStorage", "GlacierStorage"}}
        metrics = fetch_daily_storage_metrics(adapter, "bucket-a", {"StandardStorage", "GlacierStorage"}, region="us-east-1")
        assert metrics == {"metrics": {"bucket-a": {}}}
        stubber.assert_no_pending_responses()


def test_typed_lifecycle_and_intelligent_tiering_shapes_preserve_statuses():
    """Only NoSuchLifecycleConfiguration is treated as confirmed absence."""

    adapter = SimpleNamespace(s3_client=FakeS3({"Rules": [{"ID": "r1", "Status": "Enabled"}]}))
    assert get_s3_bucket_lifecycle_typed(adapter, "bucket")["status"] == "usable"
    adapter.s3_client = FakeS3(Exception("AccessDenied"))
    denied = get_s3_bucket_lifecycle_typed(adapter, "bucket")
    assert denied["status"] == "error"
    adapter.s3_client = FakeS3(
        intelligent={
            "IntelligentTieringConfigurationList": [
                {"Id": "it", "Status": "Enabled", "Filter": {"And": {"Prefix": "logs/", "Tags": [{"Key": "env", "Value": "prod"}]}}, "Tierings": [{"AccessTier": "ARCHIVE_ACCESS", "Days": 90}]}
            ]
        }
    )
    parsed = list_s3_bucket_intelligent_tiering_configurations(adapter, "bucket")
    assert parsed["configurations"] == [{"id": "it", "status": "Enabled", "prefix": "logs/", "tags": {"env": "prod"}, "tierings": [{"access_tier": "ARCHIVE_ACCESS", "days": 90}]}]


class FakeOptimizerAdapter:
    """Adapter fake exposing only the S3 optimizer calls."""

    def __init__(self, failing_bucket=None):
        self.failing_bucket = failing_bucket
        self.overlays = []

    def discover_storage_types(self, _region):
        return {"bucket-a": {"StandardStorage"}, "bucket-b": {"StandardStorage"}}

    def fetch_daily_storage_metrics(self, bucket, _types, days=90, **_kwargs):
        if bucket == self.failing_bucket:
            raise RuntimeError("metric failure")
        return {"metrics": {bucket: {}}}

    def get_s3_bucket_lifecycle_typed(self, _bucket, **_kwargs):
        return {"status": "absent", "rules": [], "error": None}

    def list_s3_bucket_intelligent_tiering_configurations(self, _bucket, **_kwargs):
        return {"status": "usable", "configurations": [], "error": None}

    def overlay_resource_metadata(self, resource_type, resource_id, key, value):
        self.overlays.append((resource_type, resource_id, key, value))


def test_enrichment_resolves_once_and_fail_soft_per_bucket(monkeypatch, tmp_path):
    """Stored unknown results and overlays survive one bucket's AWS failure."""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    rows = [
        {"period": "2026-09-20", "bucket": "bucket-a", "region": "us-east-1", "usage_type": "TimedStorage-ByteHrs", "operation": "StandardStorage", "usage_amount": 1, "unblended_cost": 0.01},
        {"period": "2026-09-20", "bucket": "bucket-b", "region": "us-east-1", "usage_type": "TimedStorage-ByteHrs", "operation": "StandardStorage", "usage_amount": 1, "unblended_cost": 0.01},
    ]
    monkeypatch.setattr(s3_optimizer_enrichment, "load_bucket_rows", lambda *_args: rows)
    calls = []
    monkeypatch.setattr(s3_optimizer_enrichment, "resolve", lambda *args: calls.append(args) or {"resolved": {}, "unresolved": [], "price_map_window": {"start": "2026-09-20", "end": "2026-09-21"}, "seed_as_of": None})
    adapter = FakeOptimizerAdapter(failing_bucket="bucket-b")
    buckets = [{"resource_id": "bucket-a", "region": "us-east-1", "metadata": {}}, {"resource_id": "bucket-b", "region": "us-east-1", "metadata": {}}]
    s3_optimizer_enrichment.enrich_s3_buckets(session, adapter, buckets, "us-east-1", tmp_path, {"start_date": "2026-09-20", "end_date": "2026-09-21", "window_days": 1})
    assert len(calls) == 1
    assert buckets[0]["metadata"]["s3_optimizer"]["telemetry_status"] == "unknown"
    assert "error" in buckets[1]["metadata"]["s3_optimizer"]
    assert {item[2] for item in adapter.overlays} == {"s3_optimizer", "storage_class_breakdown", "lifecycle_transitions", "intelligent_tiering_config"}


def test_first_and_second_scan_persist_all_optimizer_metadata(monkeypatch, tmp_path):
    """Create and update both retain the complete five-key optimizer shape."""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    rows = [
        {
            "period": "2026-09-20",
            "bucket": "bucket-a",
            "region": "us-east-1",
            "usage_type": "TimedStorage-ByteHrs",
            "operation": "StandardStorage",
            "usage_amount": 1,
            "unblended_cost": 0.01,
        }
    ]
    monkeypatch.setattr(s3_optimizer_enrichment, "load_bucket_rows", lambda *_args: rows)
    price_map = {
        "resolved": {},
        "unresolved": [],
        "price_map_window": {"start": "2026-09-20", "end": "2026-09-21"},
        "seed_as_of": None,
    }
    monkeypatch.setattr(s3_optimizer_enrichment, "resolve", lambda *_args: dict(price_map))
    adapter = FakeOptimizerAdapter()
    first_resource = {"resource_id": "bucket-a", "region": "us-east-1", "metadata": {}}

    first_output = s3_optimizer_enrichment.enrich_s3_buckets(
        session,
        adapter,
        [first_resource],
        "us-east-1",
        tmp_path,
        {"start_date": "2026-09-20", "end_date": "2026-09-21", "window_days": 1},
    )
    first_resource["metadata"]["estimated_monthly_cost"] = 0.0
    _store_inventory_resource(
        session,
        "s3",
        first_resource,
        "111111111111",
        datetime(2026, 9, 21),
        s3_price_map=first_output,
        s3_optimizer_metadata=first_output["metadata_by_bucket"]["bucket-a"],
    )
    session.commit()

    first_row = session.query(S3Inventory).filter_by(resource_id="bucket-a").one()
    keys = {
        "s3_optimizer",
        "storage_class_breakdown",
        "lifecycle_transitions",
        "intelligent_tiering_config",
        "s3_optimizer_version",
    }
    assert keys <= set(first_row.metadata_json)
    assert first_row.metadata_json["s3_optimizer"]["inventory_id"] == first_row.inventory_id

    second_resource = {"resource_id": "bucket-a", "region": "us-east-1", "metadata": {}}
    second_output = s3_optimizer_enrichment.enrich_s3_buckets(
        session,
        adapter,
        [second_resource],
        "us-east-1",
        tmp_path,
        {"start_date": "2026-09-20", "end_date": "2026-09-21", "window_days": 1},
    )
    second_metadata = second_output["metadata_by_bucket"]["bucket-a"]
    second_metadata["s3_optimizer_version"]["engine"] = "v2"
    second_resource["metadata"]["estimated_monthly_cost"] = 0.0
    _store_inventory_resource(
        session,
        "s3",
        second_resource,
        "111111111111",
        datetime(2026, 9, 21),
        s3_price_map=second_output,
        s3_optimizer_metadata=second_metadata,
    )
    session.commit()

    second_row = session.query(S3Inventory).filter_by(resource_id="bucket-a").one()
    assert second_row.inventory_id == first_row.inventory_id
    assert keys <= set(second_row.metadata_json)
    assert second_row.metadata_json["s3_optimizer_version"]["engine"] == "v2"


def test_bucket_b_result_persists_signals_for_unused_and_low_access_checks():
    """The fixture's fully covered write-once bucket reaches both checks."""

    fixture_dir = Path(__file__).parent / "payloads/s3_optimizer"
    cur_payload = json.loads((fixture_dir / "cur_fixture.json").read_text(encoding="utf-8"))
    cloudwatch = json.loads((fixture_dir / "cloudwatch_fixture.json").read_text(encoding="utf-8"))
    rows = [row for row in cur_payload["rows"] if row["bucket"] == "bucket-b"]
    coverage = coverage_by_bucket(rows, "2026-07-01", "2026-09-21")["bucket-b"]
    signals = derive_signals(rows, coverage)
    signals["resource_id"] = "bucket-b"
    prices = {
        "resolved": json.loads(
            (Path(__file__).parents[1] / "pricing/s3_price_seed.json").read_text(encoding="utf-8")
        )["us-east-1"]
    }
    result = evaluate_bucket(
        signals,
        prices,
        cloudwatch,
        {"resource_id": "bucket-b", "region": "us-east-1"},
        {},
    )
    def adapter():
        """Return a fresh resource because checks annotate their findings."""

        return SimpleNamespace(
            get_resources=lambda *_args, **_kwargs: [
                {"resource_id": "bucket-b", "metadata": {"s3_optimizer": result}}
            ]
        )

    unused = check_s3_bucket_unused(adapter(), window_days=coverage.count)
    low_access = check_s3_bucket_low_access(adapter())

    assert result["signals"]["monthly_data_read_requests"] == 0
    assert result["signals"]["monthly_data_write_requests"] == 0
    assert result["signals"]["monthly_list_requests"] == 0
    assert unused and unused[0]["metadata"]["recommended_action"] == "s3_review_unused_bucket"
    assert low_access and low_access[0]["metadata"]["scenarios"]
