"""Offline tests for the verified S3 Pricing API importer."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.pricing import s3_price_map
from app.pricing.s3_price_map import CANONICAL_KEYS, resolve
from staging_pricing.import_s3_pricing import (
    FILTER_TABLE_PATH,
    KEY_FILTER_TABLE,
    _matches,
    filters_for_spec,
    import_region,
    merge_region,
    resolve_product,
)


FIXTURE_DIR = Path(__file__).parent / "payloads" / "s3_optimizer" / "pricing_api"


def _products(region: str) -> list[dict]:
    """Load both verified service-code dumps for one region."""

    names = (f"s3_products_{region}.json", f"s3gda_products_{region}.json")
    products = []
    for name in names:
        products.extend(json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8")))
    return products


def test_filter_table_covers_every_canonical_key_and_status_counts():
    """The verified mapping covers all 79 keys without unresolved rows."""

    assert set(KEY_FILTER_TABLE) == set(CANONICAL_KEYS)
    assert sum(spec["status"] == "resolved" for spec in KEY_FILTER_TABLE.values()) == 67
    assert sum(spec["status"] == "not_applicable" for spec in KEY_FILTER_TABLE.values()) == 12
    assert FILTER_TABLE_PATH.is_file()


def test_api_filter_restores_region_usage_prefix():
    """The stored suffix is expanded to the actual us-east-2 API value."""

    spec = KEY_FILTER_TABLE["storage.STANDARD.gb_month"]
    assert filters_for_spec(spec, "us-east-2")[0]["Value"] == "USE2-TimedStorage-ByteHrs"


def test_fixture_resolver_has_exactly_one_product_for_each_resolved_key():
    """Every resolved selector matches exactly one product in both regions."""

    for region in ("us-east-1", "us-east-2"):
        products = _products(region)
        for key, spec in KEY_FILTER_TABLE.items():
            if spec["status"] != "resolved":
                continue
            matches = [product for product in products if _matches(product, spec, region)]
            assert len(matches) == 1, (region, key, [p["product"]["sku"] for p in matches])
            result, candidates = resolve_product(products, key, spec, region)
            assert candidates is None
            assert result is not None


def test_fixture_prices_and_skus_equal_verified_us_east_1_mapping():
    """Derived us-east-1 prices and SKUs equal the supplied verified mapping."""

    products = _products("us-east-1")
    for key, spec in KEY_FILTER_TABLE.items():
        if spec["status"] != "resolved":
            continue
        result, candidates = resolve_product(products, key, spec, "us-east-1")
        assert candidates is None
        assert result is not None
        assert result["sku"] == spec["sku_us_east_1"], key
        assert result["price"] == pytest.approx(spec["price_us_east_1"]), key


def test_not_applicable_has_no_price_and_is_pricing_unavailable(monkeypatch, tmp_path: Path):
    """Not-sold canonical keys never become zero-priced runtime entries."""

    key = "retrieval.STANDARD_IA.standard.gb"
    spec = KEY_FILTER_TABLE[key]
    result, candidates = resolve_product([], key, spec, "us-east-1")
    assert candidates is None
    assert result == {"status": "not_applicable", "price": None, "unit": "GB"}
    monkeypatch.setattr(s3_price_map, "_load_seed", lambda: {"us-east-1": {key: result}})
    resolved = resolve([], "us-east-1", {}, database_path=tmp_path / "missing.db")
    assert key in resolved["unresolved"]
    assert key not in resolved["resolved"]


class FixturePricingClient:
    """Serve the recorded product dumps as two-page API responses."""

    def __init__(self, products_by_service: dict[str, list[dict]]):
        self.products_by_service = products_by_service
        self.calls = []

    def get_products(self, **request):
        self.calls.append(request)
        products = self.products_by_service[request["ServiceCode"]]
        if request.get("NextToken"):
            return {"PriceList": [json.dumps(product) for product in products[1:]]}
        return {"PriceList": [json.dumps(products[0])], "NextToken": "page-2"}


def test_importer_writes_all_rows_and_replaces_only_selected_region(tmp_path: Path):
    """Fixture import writes 67 prices plus 12 not-applicable rows."""

    database = tmp_path / "maxops_pricing.db"
    client = FixturePricingClient(
        {
            "AmazonS3": json.loads((FIXTURE_DIR / "s3_products_us-east-1.json").read_text()),
            "AmazonS3GlacierDeepArchive": json.loads(
                (FIXTURE_DIR / "s3gda_products_us-east-1.json").read_text()
            ),
        }
    )
    assert import_region(client, database, "us-east-1", as_of="2026-09-22") == {}
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM street_pricing_s3").fetchone()[0] == 79
        assert connection.execute(
            "SELECT COUNT(*) FROM street_pricing_s3 WHERE status = 'not_applicable'"
        ).fetchone()[0] == 12
        connection.execute(
            "INSERT INTO street_pricing_s3 "
            "(region_code, region_name, canonical_key, price_usd, unit, as_of, attributes_json, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("us-east-2", "Ohio", "keep", 9.0, "GB", "old", "{}", "resolved"),
        )
        connection.commit()

    assert import_region(client, database, "us-east-1", as_of="2026-09-23") == {}
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM street_pricing_s3 WHERE region_code = 'us-east-1'"
        ).fetchone()[0] == 79
        assert connection.execute(
            "SELECT price_usd FROM street_pricing_s3 "
            "WHERE region_code = 'us-east-2' AND canonical_key = 'keep'"
        ).fetchone() == (9.0,)
    assert {call["ServiceCode"] for call in client.calls} == {
        "AmazonS3",
        "AmazonS3GlacierDeepArchive",
    }


def test_merge_preserves_existing_keys_and_records_unresolved():
    """The retained bootstrap merge helper never deletes an existing key."""

    seed = {"us-east-1": {"keep": {"price": 1.0, "source_url": "hand"}}}
    merged, diffs = merge_region(
        seed,
        "us-east-2",
        {"new": {"price": 2.0, "unit": "GB", "sku": "SKU"}},
        {"missing": ["SKU-A", "SKU-B"]},
        today="2026-09-22",
    )
    assert merged["us-east-1"]["keep"]["source_url"] == "hand"
    assert merged["us-east-2"]["new"]["price"] == 2.0
    assert merged["us-east-2"]["unresolved"] == {"missing": ["SKU-A", "SKU-B"]}
    assert diffs == []


def test_merge_printable_diff_is_recorded_for_material_change():
    """Material bootstrap changes remain visible to the maintenance command."""

    merged, diffs = merge_region(
        {"us-east-1": {"key": {"price": 1.0, "unit": "GB", "source_url": "hand"}}},
        "us-east-1",
        {"key": {"price": 1.02, "unit": "GB", "sku": "SKU"}},
        {},
        today="2026-09-22",
    )
    assert diffs == ["us-east-1 key: 1.0 -> 1.02 (2.00%)"]
    assert merged["us-east-1"]["key"]["sku"] == "SKU"
