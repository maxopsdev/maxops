"""Build the regional S3 optimizer seed from the AWS Pricing API.

This is an offline-maintenance command.  Runtime optimization never calls
the Pricing API; the command is deliberately explicit about the profile and
regions it is allowed to query.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from app.pricing.s3_price_map import CANONICAL_KEYS

try:
    from staging_pricing.s3_pricing_db import write_region
except ModuleNotFoundError:
    from s3_pricing_db import write_region

try:
    from staging_pricing.import_vantage_pricing import REGION_NAMES, TARGET_REGIONS
except ModuleNotFoundError:
    from import_vantage_pricing import REGION_NAMES, TARGET_REGIONS


DEFAULT_DATABASE = Path(__file__).resolve().parents[1] / "maxops_pricing.db"
PRICING_ENDPOINT_REGION = "us-east-1"
SOURCE_URL = "aws-pricing-api"
FILTER_TABLE_PATH = Path(__file__).with_name("s3_price_key_filters.json")
REGION_PREFIXES = {
    "us-east-1": "",
    "us-east-2": "USE2-",
    "us-west-1": "USW1-",
    "us-west-2": "USW2-",
    "ca-central-1": "CAN1-",
    "eu-west-1": "EU-",  # AWS quirk: Ireland uses the legacy "EU-" prefix, not "EUW1-"
    "eu-west-2": "EUW2-",
    "eu-west-3": "EUW3-",
    "eu-central-1": "EUC1-",
    "eu-north-1": "EUN1-",
    "ap-south-1": "APS3-",
    "ap-southeast-1": "APS1-",
    "ap-southeast-2": "APS2-",
    "ap-northeast-1": "APN1-",
    "sa-east-1": "SAE1-",
}
OUTPUT_UNITS = {
    "GB-Mo": "GB-month",
    "GB": "GB",
    "Requests": "requests/1000",
    "Objects": "objects/1000",
}


def _load_filter_table() -> dict[str, dict[str, Any]]:
    """Load the verified selectors, excluding the JSON metadata header."""

    data = json.loads(FILTER_TABLE_PATH.read_text(encoding="utf-8"))
    table = {key: value for key, value in data.items() if not key.startswith("_")}
    if set(table) != set(CANONICAL_KEYS):
        raise ValueError("verified S3 filter table does not cover CANONICAL_KEYS exactly")
    return table


KEY_FILTER_TABLE = _load_filter_table()


def _product(raw: Any) -> dict[str, Any]:
    """Decode a Pricing API product string or mapping."""

    return json.loads(raw) if isinstance(raw, str) else dict(raw)


def _attributes(product: Mapping[str, Any]) -> Mapping[str, str]:
    """Return product attributes, or an empty mapping for malformed input."""

    return (product.get("product") or {}).get("attributes") or {}


def _strip_region_prefix(value: str, region: str) -> str:
    """Strip the AWS region prefix present on non-us-east-1 usage types."""

    prefix = REGION_PREFIXES.get(region, "")
    return value[len(prefix):] if prefix and value.startswith(prefix) else value


def _matches(product: Mapping[str, Any], spec: Mapping[str, Any], region: str) -> bool:
    """Match only the verified usage and billing attributes, never productFamily."""

    attributes = _attributes(product)
    if attributes.get("servicecode") != spec.get("service_code"):
        return False
    if _strip_region_prefix(attributes.get("usagetype", ""), region) != spec.get("usagetype"):
        return False
    for field in ("operation", "group", "feeCode"):
        expected = spec.get(field)
        if expected is not None and attributes.get(field, "") != expected:
            return False
    return True


def filters_for_spec(spec: Mapping[str, Any], region: str = "us-east-1") -> list[dict[str, str]]:
    """Build API filters, restoring the region prefix absent from the table."""

    fields = ("usagetype", "operation", "group", "feeCode")
    return [
        {
            "Type": "TERM_MATCH",
            "Field": field,
            "Value": (
                REGION_PREFIXES.get(region, "") + str(spec[field])
                if field == "usagetype"
                else str(spec[field])
            ),
        }
        for field in fields
        if spec.get(field) is not None
    ]


def discover_products(products: Iterable[Any], region: str) -> list[dict[str, Any]]:
    """Decode a product dump; region normalization happens during matching."""

    return [_product(product) for product in products]


def _dimensions(product: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return USD OnDemand dimensions sorted so beginRange zero is first."""

    dimensions = []
    for term in (product.get("terms") or {}).get("OnDemand", {}).values():
        for dimension in (term.get("priceDimensions") or {}).values():
            if dimension.get("pricePerUnit", {}).get("USD") is not None:
                dimensions.append(dimension)
    return sorted(dimensions, key=lambda item: int(item.get("beginRange", "0")))


def _output_unit(spec: Mapping[str, Any]) -> str:
    """Return the engine unit after applying the verified API multiplier."""

    return OUTPUT_UNITS[spec["unit_in"]]


def parse_price_product(product: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    """Parse one matched product using its first pricing tier."""

    sku = (product.get("product") or {}).get("sku")
    dimensions = _dimensions(product)
    if not sku or not dimensions:
        raise ValueError("Pricing product has no SKU or USD price dimension")
    dimension = dimensions[0]
    parsed = {
        "status": "resolved",
        "price": float(dimension["pricePerUnit"]["USD"]) * spec["multiply"],
        "unit": _output_unit(spec),
        "sku": sku,
        "source_url": SOURCE_URL,
        "attributes_json": json.dumps(_attributes(product), sort_keys=True, separators=(",", ":")),
    }
    if spec["unit_in"] == "GB-Mo":
        parsed["tier"] = "first"
    return parsed


def resolve_product(products: Iterable[Any], key: str, spec: Mapping[str, Any], region: str) -> tuple[Optional[dict[str, Any]], Optional[list[str]]]:
    """Resolve one key, returning a price or candidate SKUs for unresolved data."""

    if spec["status"] == "not_applicable":
        return {"status": "not_applicable", "price": None, "unit": _output_unit(spec)}, None
    candidates = [product for product in products if _matches(product, spec, region)]
    candidate_skus = [str((product.get("product") or {}).get("sku")) for product in candidates]
    if len(candidates) != 1:
        return None, candidate_skus
    try:
        return parse_price_product(candidates[0], spec), None
    except ValueError:
        return None, candidate_skus


def resolve_products(
    products: Iterable[Any],
    filter_table: Mapping[str, Mapping[str, Any]],
    region: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """Resolve a verified filter table against offline product fixtures."""

    prices: dict[str, dict[str, Any]] = {}
    unresolved: dict[str, list[str]] = {}
    for key, spec in filter_table.items():
        price, candidates = resolve_product(products, key, spec, region)
        if price is not None:
            prices[key] = price
        elif candidates is not None:
            unresolved[key] = candidates
    return prices, unresolved


def merge_region(seed: Mapping[str, Any], region: str, prices: Mapping[str, Any], unresolved: Mapping[str, Any], *, today: Optional[str] = None) -> tuple[dict[str, Any], list[str]]:
    """Merge API prices without dropping hand-entered keys or unresolved rows."""

    merged = json.loads(json.dumps(seed))
    region_seed = merged.setdefault(region, {})
    diffs: list[str] = []
    stamp = today or date.today().isoformat()
    for key, value in prices.items():
        old = region_seed.get(key)
        if old and old.get("price") not in (None, 0):
            relative = abs(float(value["price"]) - float(old["price"])) / abs(float(old["price"]))
            if relative <= 0.01:
                continue
            diffs.append(f"{region} {key}: {old['price']} -> {value['price']} ({relative:.2%})")
        region_seed[key] = {**value, "as_of": stamp}
    if unresolved:
        region_seed["unresolved"] = dict(unresolved)
    return merged, diffs


def _get_products(
    client: Any,
    service_code: str,
    filters: Optional[list[dict[str, str]]] = None,
) -> list[dict[str, Any]]:
    """Read all pages from one service-code-specific GetProducts query."""

    products = []
    token = None
    while True:
        request = {"ServiceCode": service_code, "MaxResults": 100}
        if filters:
            request["Filters"] = filters
        if token:
            request["NextToken"] = token
        response = client.get_products(**request)
        products.extend(_product(raw) for raw in response.get("PriceList", []))
        token = response.get("NextToken")
        if not token:
            return products


def build_region(client: Any, region: str) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """Discover and resolve one region using only GetProducts."""

    prices: dict[str, dict[str, Any]] = {}
    unresolved: dict[str, list[str]] = {}
    product_cache: dict[tuple[str, tuple[tuple[str, str], ...]], list[dict[str, Any]]] = {}
    for key, spec in KEY_FILTER_TABLE.items():
        if spec["status"] == "not_applicable":
            prices[key] = {"status": "not_applicable", "price": None, "unit": _output_unit(spec)}
            continue
        filters = filters_for_spec(spec, region)
        cache_key = (spec["service_code"], tuple((item["Field"], item["Value"]) for item in filters))
        if cache_key not in product_cache:
            product_cache[cache_key] = _get_products(client, spec["service_code"], filters)
        products = product_cache[cache_key]
        price, candidates = resolve_product(products, key, spec, region)
        if price is not None:
            prices[key] = price
        elif candidates is not None:
            unresolved[key] = candidates
    return prices, unresolved


def import_region(
    client: Any,
    database: Path,
    region: str,
    *,
    as_of: Optional[str] = None,
) -> dict[str, list[str]]:
    """Resolve and replace one database region, returning unresolved keys."""

    prices, unresolved = build_region(client, region)
    write_region(database, region, prices, as_of=as_of or date.today().isoformat())
    return unresolved


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse the maintenance command arguments."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", action="append", default=None)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument(
        "--seed-json",
        type=Path,
        help="Optional bootstrap JSON output; only us-east-1 is written.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Run the explicit regional seed rebuild and return a process code."""

    args = parse_args(argv)
    import boto3

    client = boto3.Session(profile_name=args.profile).client("pricing", region_name=PRICING_ENDPOINT_REGION)
    regions = args.region or TARGET_REGIONS
    seed: dict[str, dict[str, Any]] = {}
    for region in regions:
        prices, unresolved = build_region(client, region)
        write_region(args.database.resolve(), region, prices, as_of=date.today().isoformat())
        print(f"{region}: unresolved={len(unresolved)}")
        for key, candidates in sorted(unresolved.items()):
            print(f"UNRESOLVED {region} {key}: {candidates}")
        if region == "us-east-1":
            seed[region] = {
                key: {
                    field: value[field]
                    for field in ("price", "unit", "tier", "sku", "status", "source_url")
                    if field in value
                }
                | {"as_of": date.today().isoformat()}
                for key, value in prices.items()
            }
    if args.seed_json:
        args.seed_json.write_text(json.dumps(seed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
