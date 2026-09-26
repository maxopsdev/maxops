import json
import sqlite3
from pathlib import Path

import pytest

from app.pricing.s3_price_map import CANONICAL_KEYS, derive_prices_from_cur, resolve
from staging_pricing.import_vantage_pricing import TARGET_REGIONS


def rows_for_prices():
    return [
        {"region": "us-east-1", "usage_type": "TimedStorage-ByteHrs", "operation": "StandardStorage", "usage_amount": 1, "unblended_cost": 0.023, "net_amortized_cost": 0.001},
        {"region": "us-east-1", "usage_type": "TimedStorage-ByteHrs", "operation": "StandardStorage", "usage_amount": 1, "unblended_cost": 0, "net_amortized_cost": 0.001},
        {"region": "us-east-1", "usage_type": "Requests-Tier4", "operation": "S3-SIATransition", "usage_amount": 100, "unblended_cost": 0.001},
        {"region": "us-east-1", "usage_type": "Requests-Tier4", "operation": "S3-GIRTransition", "usage_amount": 100, "unblended_cost": 0.002},
        {"region": "us-east-1", "usage_type": "Requests-Tier3", "operation": "S3-GlacierTransition", "usage_amount": 100, "unblended_cost": 0.003},
        {"region": "us-east-1", "usage_type": "Requests-Tier3", "operation": "S3-GDATransition", "usage_amount": 100, "unblended_cost": 0.005},
        {"region": "us-east-1", "usage_type": "Retrieval-GIR", "operation": "GetObject", "usage_amount": 1, "unblended_cost": 0.03},
        {"region": "us-east-1", "usage_type": "Retrieval-SIA", "operation": "GetObject", "usage_amount": 1, "unblended_cost": 0.01},
        {"region": "us-east-1", "usage_type": "Retrieval-ZIA", "operation": "GetObject", "usage_amount": 1, "unblended_cost": 0.01},
        {"region": "us-east-1", "usage_type": "Requests-Tier1", "operation": "PutObject", "usage_amount": 1000, "unblended_cost": 0.005},
    ]


def test_derived_prices_match_verified_reference_rates_and_skip_free_rows():
    prices = derive_prices_from_cur(rows_for_prices(), "us-east-1")
    assert abs(prices["storage.STANDARD.gb_month"]["price"] - 0.023) < 1e-6
    assert abs(prices["transition.STANDARD_IA.per_1000"]["price"] - 0.01) < 1e-6
    assert abs(prices["transition.GLACIER_IR.per_1000"]["price"] - 0.02) < 1e-6
    assert abs(prices["transition.GLACIER.per_1000"]["price"] - 0.03) < 1e-6
    assert abs(prices["transition.DEEP_ARCHIVE.per_1000"]["price"] - 0.05) < 1e-6
    assert abs(prices["retrieval.GLACIER_IR.standard.gb"]["price"] - 0.03) < 1e-6
    assert abs(prices["retrieval.STANDARD_IA.standard.gb"]["price"] - 0.01) < 1e-6
    assert abs(prices["retrieval.ONEZONE_IA.standard.gb"]["price"] - 0.01) < 1e-6
    assert abs(prices["request.STANDARD.data_write.per_1000"]["price"] - 0.005) < 1e-6


def test_resolve_records_cur_source_and_seed_fallback(tmp_path):
    # Point at a database that does not exist so the street-pricing layer is
    # empty and the bootstrap seed is the only fallback; the shipped
    # maxops_pricing.db would otherwise answer first and mask this path.
    result = resolve(
        rows_for_prices(),
        "us-east-1",
        {"start": "2026-07-01", "end": "2026-09-21"},
        database_path=tmp_path / "missing.db",
    )
    assert result["resolved"]["storage.STANDARD.gb_month"]["price_source"] == "cur_observed"
    assert result["resolved"]["storage.DEEP_ARCHIVE.gb_month"]["price_source"] == "seed"
    assert result["price_map_window"] == {"start": "2026-07-01", "end": "2026-09-21"}
    assert result["seed_as_of"]
    assert "data_transfer_out.gb" in result["unresolved"]


def test_us_east_seed_covers_every_canonical_key():
    seed_path = Path(__file__).parents[1] / "pricing" / "s3_price_seed.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    assert set(CANONICAL_KEYS) <= set(seed["us-east-1"])


def test_shipped_s3_street_pricing_covers_target_regions_when_present():
    """The release DB, when populated, has every S3 key in every target region."""

    database = Path(__file__).parents[1] / "maxops_pricing.db"
    if not database.exists():
        pytest.skip("maxops_pricing.db is not shipped in this checkout")
    with sqlite3.connect(database) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'street_pricing_s3'"
        ).fetchone()
        if exists is None:
            pytest.skip("street_pricing_s3 is absent; run staging_pricing/import_s3_pricing.py")
        # A database packaged before the canonical-key schema still has the
        # table, just with the older columns. Runtime handles that by falling
        # back to the JSON seed, so the test skips rather than failing.
        columns = {row[1] for row in connection.execute("PRAGMA table_info(street_pricing_s3)")}
        if "canonical_key" not in columns:
            pytest.skip(
                "street_pricing_s3 predates the canonical-key schema; "
                "re-run staging_pricing/import_s3_pricing.py to repackage it"
            )
        for region in TARGET_REGIONS:
            keys = {
                row[0]
                for row in connection.execute(
                    "SELECT canonical_key FROM street_pricing_s3 WHERE region_code = ?",
                    (region,),
                )
            }
            assert set(CANONICAL_KEYS) <= keys, region


def test_region_none_rows_do_not_leak_into_regional_prices():
    rows = rows_for_prices()
    rows.append(
        {
            "region": None,
            "usage_type": "TimedStorage-ByteHrs",
            "operation": "StandardStorage",
            "usage_amount": 1,
            "unblended_cost": 1.0,
        }
    )
    prices = derive_prices_from_cur(rows, "us-east-1")
    assert abs(prices["storage.STANDARD.gb_month"]["price"] - 0.023) < 1e-6


def test_early_delete_gb_hours_are_converted_to_monthly_price():
    """The optimizer price key is monthly even though CUR bills GB-Hours."""

    prices = derive_prices_from_cur(
        [{"region": "us-east-1", "usage_type": "EarlyDelete-GDA", "operation": None, "usage_amount": 2, "unblended_cost": 0.01}],
        "us-east-1",
    )
    assert prices["early_delete.DEEP_ARCHIVE.gb"]["price"] == 0.01 / 2 * 730
    assert "early_delete.DEEP_ARCHIVE.gb" in prices
