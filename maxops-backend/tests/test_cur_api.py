import pytest
from fastapi import HTTPException

import app.api.routes.cur as cur_routes
from app.api.routes.cur import (
    default_date_window,
    get_cur_cache_status,
    get_cur_dataset,
    get_resource_cost_by_id,
    get_resource_cost_report,
    get_service_cost_report,
    get_usage_type_cost_report,
    list_cur_datasets,
)
from pricing.cur.reader import CurCacheMissingError


def test_cur_datasets_endpoint_lists_cur_datasets():
    response = list_cur_datasets()

    dataset_names = {dataset["name"] for dataset in response["datasets"]}
    assert dataset_names == {
        "service_daily",
        "service_monthly",
        "usage_type_daily",
        "usage_type_monthly",
        "resource_daily",
        "resource_monthly",
    }


def test_cur_status_endpoint_is_separate_and_read_only(tmp_path):
    response = get_cur_cache_status(cache_root=str(tmp_path))

    assert response["cache_root"] == str(tmp_path)
    assert response["datasets"]["service_daily"]["month_count"] == 0


def test_cur_dataset_endpoint_rejects_unknown_dataset():
    with pytest.raises(HTTPException) as exc_info:
        get_cur_dataset("not_a_dataset")

    assert exc_info.value.status_code == 400
    assert "Unknown CUR dataset" in exc_info.value.detail


def test_daily_default_date_window_is_yesterday_to_today():
    now = cur_routes.datetime(2026, 5, 26, 12, tzinfo=cur_routes.timezone.utc)

    assert default_date_window("daily", now) == ("2026-05-25", "2026-05-26")


def test_monthly_default_date_window_is_current_month_to_next_month():
    now = cur_routes.datetime(2026, 5, 26, 12, tzinfo=cur_routes.timezone.utc)

    assert default_date_window("monthly", now) == ("2026-05-01", "2026-06-01")


def test_service_report_resolves_daily_dataset_and_totals_returned_rows(monkeypatch):
    calls = []

    def fake_query_dataset(*args, **kwargs):
        calls.append((args, kwargs))
        return [
            {"service_code": "AmazonEC2", "net_amortized_cost": 1.25},
            {"service_code": "AmazonS3", "net_amortized_cost": 2.75},
        ]

    monkeypatch.setattr(cur_routes, "default_date_window", lambda grain: ("2026-05-25", "2026-05-26"))
    monkeypatch.setattr(cur_routes, "query_dataset", fake_query_dataset)

    response = get_service_cost_report(account_id="123456789012", service="AmazonEC2", region="us-east-1")

    assert calls[0][0] == ("service_daily",)
    assert calls[0][1]["start_date"] == "2026-05-25"
    assert calls[0][1]["end_date"] == "2026-05-26"
    assert calls[0][1]["filters"] == {
        "account_id": "123456789012",
        "service": "AmazonEC2",
        "region": "us-east-1",
    }
    assert response["report"] == "services"
    assert response["dataset"] == "service_daily"
    assert response["grain"] == "daily"
    assert response["cost_metric"] == "net_amortized_cost"
    assert response["total_net_amortized_cost"] == 4.0
    assert response["row_count"] == 2


def test_service_report_resolves_monthly_dataset(monkeypatch):
    calls = []

    def fake_query_dataset(*args, **kwargs):
        calls.append((args, kwargs))
        return []

    monkeypatch.setattr(cur_routes, "default_date_window", lambda grain: ("2026-05-01", "2026-06-01"))
    monkeypatch.setattr(cur_routes, "query_dataset", fake_query_dataset)

    response = get_service_cost_report(grain="monthly")

    assert calls[0][0] == ("service_monthly",)
    assert response["dataset"] == "service_monthly"
    assert response["start_date"] == "2026-05-01"
    assert response["end_date"] == "2026-06-01"


def test_usage_type_report_resolves_monthly_dataset(monkeypatch):
    calls = []

    def fake_query_dataset(*args, **kwargs):
        calls.append((args, kwargs))
        return [{"net_amortized_cost": None}]

    monkeypatch.setattr(cur_routes, "query_dataset", fake_query_dataset)

    response = get_usage_type_cost_report(
        grain="monthly",
        start_date="2026-05-01",
        end_date="2026-06-01",
        service="AmazonEC2",
    )

    assert calls[0][0] == ("usage_type_monthly",)
    assert calls[0][1]["filters"] == {"service": "AmazonEC2"}
    assert response["report"] == "usage_types"
    assert response["total_net_amortized_cost"] == 0.0


def test_resource_report_accepts_resource_id_filter(monkeypatch):
    calls = []

    def fake_query_dataset(*args, **kwargs):
        calls.append((args, kwargs))
        return [{"line_item_resource_id": "i-123", "net_amortized_cost": 3.5}]

    monkeypatch.setattr(cur_routes, "query_dataset", fake_query_dataset)

    response = get_resource_cost_report(
        grain="monthly",
        start_date="2026-05-01",
        end_date="2026-06-01",
        resource_id="i-123",
    )

    assert calls[0][0] == ("resource_monthly",)
    assert calls[0][1]["filters"] == {"resource_id": "i-123"}
    assert response["dataset"] == "resource_monthly"
    assert response["total_net_amortized_cost"] == 3.5


def test_resource_cost_path_injects_resource_id(monkeypatch):
    calls = []

    def fake_query_dataset(*args, **kwargs):
        calls.append((args, kwargs))
        return [{"line_item_resource_id": "vol-123", "net_amortized_cost": "4.25"}]

    monkeypatch.setattr(cur_routes, "query_dataset", fake_query_dataset)

    response = get_resource_cost_by_id(
        "vol-123",
        start_date="2026-05-25",
        end_date="2026-05-26",
        region="us-east-1",
    )

    assert calls[0][0] == ("resource_daily",)
    assert calls[0][1]["filters"] == {"region": "us-east-1", "resource_id": "vol-123"}
    assert response["filters"]["resource_id"] == "vol-123"
    assert response["total_net_amortized_cost"] == 4.25


def test_report_missing_cache_returns_404(monkeypatch):
    def fake_query_dataset(*args, **kwargs):
        raise CurCacheMissingError("No local cache exists")

    monkeypatch.setattr(cur_routes, "query_dataset", fake_query_dataset)

    with pytest.raises(HTTPException) as exc_info:
        get_resource_cost_report(start_date="2026-05-25", end_date="2026-05-26")

    assert exc_info.value.status_code == 404


def test_report_invalid_filter_returns_400(monkeypatch):
    def fake_query_dataset(*args, **kwargs):
        raise ValueError("Filter 'resource_id' is not valid")

    monkeypatch.setattr(cur_routes, "query_dataset", fake_query_dataset)

    with pytest.raises(HTTPException) as exc_info:
        get_service_cost_report(start_date="2026-05-25", end_date="2026-05-26")

    assert exc_info.value.status_code == 400
