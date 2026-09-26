"""CUR-backed pricing: the bridge from the Parquet cost cache into pricing."""

import pandas as pd
import pytest

from app.services.cur_cost_service import CurCostLookup, normalize_resource_id
from pricing.cur.cache import parquet_path
from pricing.cur.months import BillingMonth
from pricing.cur.reader import resource_monthly_costs

pytestmark = [pytest.mark.unit]


def write_resource_month(cache_root, month, rows):
    """Write a resource_monthly Parquet partition the reader can pick up."""
    path = parquet_path(cache_root, "resource_monthly", month)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def row(resource_id, cost, *, account_id="123456789012", usage_type="BoxUsage"):
    return {
        "billing_month": f"{month_str(BillingMonth(2026, 5))}-01",
        "line_item_usage_account_id": account_id,
        "line_item_resource_id": resource_id,
        "line_item_usage_type": usage_type,
        "net_amortized_cost": cost,
    }


def month_str(month):
    return str(month)


# --------------------------------------------------------------- normalization


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("i-0abc123", "i-0abc123"),
        ("vol-0def456", "vol-0def456"),
        ("my-bucket", "my-bucket"),
        ("arn:aws:rds:us-east-1:123456789012:db:mydb", "mydb"),
        ("arn:aws:dynamodb:us-east-1:123456789012:table/mytable", "mytable"),
        ("arn:aws:elasticache:us-east-1:123456789012:cluster:mycache", "mycache"),
        ("arn:aws:lambda:us-east-1:123456789012:function:myfn", "myfn"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_resource_id_reduces_arns_to_bare_identifier(raw, expected):
    assert normalize_resource_id(raw) == expected


# -------------------------------------------------------------- cost map load


def test_resource_monthly_costs_sums_every_row_for_a_resource(tmp_path):
    write_resource_month(
        tmp_path,
        BillingMonth(2026, 5),
        [
            row("i-0abc123", 10.0, usage_type="BoxUsage"),
            row("i-0abc123", 2.50, usage_type="EBSOptimized"),
            row("i-0abc123", 0.25, usage_type="DataTransfer"),
            row("i-0def456", 7.0),
        ],
    )

    month, costs = resource_monthly_costs(cache_root=tmp_path)

    assert str(month) == "2026-05"
    assert costs["i-0abc123"] == pytest.approx(12.75)
    assert costs["i-0def456"] == pytest.approx(7.0)


def test_resource_monthly_costs_uses_latest_month_present(tmp_path):
    write_resource_month(tmp_path, BillingMonth(2026, 3), [row("i-0abc123", 5.0)])
    write_resource_month(tmp_path, BillingMonth(2026, 5), [row("i-0abc123", 9.0)])

    month, costs = resource_monthly_costs(cache_root=tmp_path)

    assert str(month) == "2026-05"
    assert costs["i-0abc123"] == pytest.approx(9.0)


def test_resource_monthly_costs_filters_by_account(tmp_path):
    write_resource_month(
        tmp_path,
        BillingMonth(2026, 5),
        [
            row("i-0abc123", 10.0, account_id="111111111111"),
            row("i-0def456", 4.0, account_id="222222222222"),
        ],
    )

    _, costs = resource_monthly_costs(cache_root=tmp_path, account_id="222222222222")

    assert costs == {"i-0def456": pytest.approx(4.0)}


def test_missing_cache_returns_empty_rather_than_raising(tmp_path):
    month, costs = resource_monthly_costs(cache_root=tmp_path / "nothing-here")

    assert month is None
    assert costs == {}


# ------------------------------------------------------------------- lookup


@pytest.fixture
def cur_enabled(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "cur_pricing_enabled", True)
    return settings


def test_lookup_returns_billed_cost_and_billing_month(tmp_path, cur_enabled):
    write_resource_month(tmp_path, BillingMonth(2026, 5), [row("i-0abc123", 12.75)])
    lookup = CurCostLookup(cache_root=tmp_path)

    assert lookup.get("i-0abc123") == (pytest.approx(12.75), "2026-05")


def test_lookup_matches_inventory_id_against_a_cur_arn(tmp_path, cur_enabled):
    write_resource_month(
        tmp_path,
        BillingMonth(2026, 5),
        [row("arn:aws:rds:us-east-1:123456789012:db:mydb", 88.0)],
    )
    lookup = CurCostLookup(cache_root=tmp_path)

    # Inventory stores the bare identifier; CUR recorded the ARN.
    cost, month = lookup.get("mydb")
    assert cost == pytest.approx(88.0)
    assert month == "2026-05"


def test_lookup_prefers_an_exact_match_over_a_normalized_one(tmp_path, cur_enabled):
    write_resource_month(
        tmp_path,
        BillingMonth(2026, 5),
        [
            row("mydb", 1.0),
            row("arn:aws:rds:us-east-1:123456789012:db:mydb", 88.0),
        ],
    )
    lookup = CurCostLookup(cache_root=tmp_path)

    assert lookup.get("mydb")[0] == pytest.approx(1.0)


def test_ambiguous_normalized_id_yields_no_price_rather_than_a_guess(tmp_path, cur_enabled):
    # Two different services whose ARNs share a trailing segment. Charging one
    # resource's bill to the other would be worse than returning nothing.
    write_resource_month(
        tmp_path,
        BillingMonth(2026, 5),
        [
            row("arn:aws:rds:us-east-1:123456789012:db:shared", 50.0),
            row("arn:aws:elasticache:us-east-1:123456789012:cluster:shared", 20.0),
        ],
    )
    lookup = CurCostLookup(cache_root=tmp_path)

    assert lookup.get("shared") is None


def test_unknown_resource_returns_none(tmp_path, cur_enabled):
    write_resource_month(tmp_path, BillingMonth(2026, 5), [row("i-0abc123", 12.75)])
    lookup = CurCostLookup(cache_root=tmp_path)

    assert lookup.get("i-not-in-the-bill") is None


def test_lookup_is_inert_when_the_feature_is_disabled(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "cur_pricing_enabled", False)
    write_resource_month(tmp_path, BillingMonth(2026, 5), [row("i-0abc123", 12.75)])
    lookup = CurCostLookup(cache_root=tmp_path)

    assert lookup.get("i-0abc123") is None


def test_broken_cache_falls_back_instead_of_raising(tmp_path, cur_enabled):
    path = parquet_path(tmp_path, "resource_monthly", BillingMonth(2026, 5))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("this is not parquet", encoding="utf-8")

    lookup = CurCostLookup(cache_root=tmp_path)

    assert lookup.get("i-0abc123") is None


def test_map_is_loaded_once_and_reused_within_the_refresh_window(tmp_path, cur_enabled, monkeypatch):
    write_resource_month(tmp_path, BillingMonth(2026, 5), [row("i-0abc123", 12.75)])
    lookup = CurCostLookup(cache_root=tmp_path, refresh_seconds=3600)

    calls = []
    original = lookup._load

    def counting_load():
        calls.append(1)
        original()

    monkeypatch.setattr(lookup, "_load", counting_load)

    for _ in range(5):
        lookup.get("i-0abc123")

    assert len(calls) == 1


# ------------------------------------------------- wiring into the price chain


class _StubStreetPricing:
    """Stands in for the static list-price database."""

    def get_price_for_resource(self, resource):
        return {
            "price_per_unit": 999.0,
            "unit": "month",
            "currency": "USD",
            "source": "street_pricing",
            "parameters": {},
        }


def _pricing_service(monkeypatch, cache_root):
    from app.services import aws_pricing_cache as module

    service = module.AwsPricingCacheService.__new__(module.AwsPricingCacheService)
    service.pricing_client = object()
    service.street_pricing_service = _StubStreetPricing()
    monkeypatch.setattr(module, "cur_cost_lookup", CurCostLookup(cache_root=cache_root))
    return service


def _db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.pricing import PricingCache

    engine = create_engine("sqlite:///:memory:")
    PricingCache.__table__.create(bind=engine)
    return sessionmaker(bind=engine)()


def test_billed_cost_takes_precedence_over_list_pricing(tmp_path, cur_enabled, monkeypatch):
    write_resource_month(tmp_path, BillingMonth(2026, 5), [row("i-0abc123", 12.75)])
    service = _pricing_service(monkeypatch, tmp_path)

    price = service.get_price_for_resource(
        _db_session(),
        {"resource_type": "ec2", "region": "us-east-1", "resource_id": "i-0abc123"},
    )

    assert price["source"] == "cur"
    assert price["price_per_unit"] == pytest.approx(12.75)
    assert price["parameters"]["billing_month"] == "2026-05"
    assert price["parameters"]["metric"] == "net_amortized_cost"


def test_resource_absent_from_the_bill_falls_back_to_list_pricing(tmp_path, cur_enabled, monkeypatch):
    write_resource_month(tmp_path, BillingMonth(2026, 5), [row("i-0abc123", 12.75)])
    service = _pricing_service(monkeypatch, tmp_path)

    price = service.get_price_for_resource(
        _db_session(),
        {"resource_type": "ec2", "region": "us-east-1", "resource_id": "i-brand-new"},
    )

    assert price["source"] == "street_pricing"


def test_explicit_metadata_cost_still_wins_over_the_cache(tmp_path, cur_enabled, monkeypatch):
    write_resource_month(tmp_path, BillingMonth(2026, 5), [row("i-0abc123", 12.75)])
    service = _pricing_service(monkeypatch, tmp_path)

    price = service.get_price_for_resource(
        _db_session(),
        {
            "resource_type": "ec2",
            "region": "us-east-1",
            "resource_id": "i-0abc123",
            "metadata": {"cur_monthly_cost": 5.0},
        },
    )

    assert price["source"] == "cur"
    assert price["price_per_unit"] == pytest.approx(5.0)
