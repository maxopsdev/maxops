"""Package maxops_pricing.db into release artifacts for OSS distribution."""
from __future__ import annotations

import argparse
from pathlib import Path

try:
    from staging_pricing.import_vantage_pricing import TARGET_REGIONS
    from staging_pricing.release_utils import (
        DEFAULT_ASSET_NAME,
        DEFAULT_METADATA_NAME,
        DEFAULT_SHA256_NAME,
        build_db_metadata,
        compress_xz,
        sha256_file,
        write_metadata,
        write_sha256_file,
    )
except ModuleNotFoundError:
    from import_vantage_pricing import TARGET_REGIONS
    from release_utils import (
        DEFAULT_ASSET_NAME,
        DEFAULT_METADATA_NAME,
        DEFAULT_SHA256_NAME,
        build_db_metadata,
        compress_xz,
        sha256_file,
        write_metadata,
        write_sha256_file,
    )


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Package pricing DB release artifacts."
    )
    parser.add_argument(
        "--db-path",
        default=str(_backend_root() / "maxops_pricing.db"),
        help="Path to the SQLite pricing database to package.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(_backend_root() / "pricing_artifacts"),
        help="Directory to write packaged release artifacts.",
    )
    parser.add_argument(
        "--asset-name",
        default=DEFAULT_ASSET_NAME,
        help="Compressed asset file name.",
    )
    parser.add_argument(
        "--preset",
        type=int,
        default=6,
        help="XZ compression preset (0-9).",
    )
    return parser.parse_args()


def require_ec2_capability_catalog(metadata: dict[str, object]) -> None:
    tables = metadata.get("tables")
    specs = tables.get("ec2_instance_specs") if isinstance(tables, dict) else None
    spec_count = specs.get("row_count") if isinstance(specs, dict) else 0
    if not spec_count:
        raise SystemExit(
            "EC2 capability catalog is empty. Run "
            "staging_pricing/enrich_ec2_instance_specs.py before packaging."
        )


def require_rds_rightsizer_catalog(metadata: dict[str, object]) -> None:
    tables = metadata.get("tables")
    tables = tables if isinstance(tables, dict) else {}
    required = (
        "rds_instance_specs",
        "rds_rightsizer_class_prices",
        "rds_rightsizer_storage_prices",
    )
    missing = [
        name
        for name in required
        if not isinstance(tables.get(name), dict) or not tables[name].get("row_count")
    ]
    if missing:
        raise SystemExit(
            "RDS rightsizer catalog is incomplete (missing: "
            + ", ".join(missing)
            + "). Run staging_pricing/import_rds_rightsizer_catalog.py before packaging."
        )
    specs = tables["rds_instance_specs"]
    if specs.get("missing_priced_instance_type_count"):
        raise SystemExit("RDS capability coverage is incomplete for priced instance classes.")


def require_s3_street_pricing(metadata: dict[str, object]) -> None:
    """Require the release database to contain the S3 street-pricing table."""

    tables = metadata.get("tables")
    tables = tables if isinstance(tables, dict) else {}
    table = tables.get("street_pricing_s3")
    if not isinstance(table, dict) or not table.get("row_count"):
        raise SystemExit(
            "S3 street pricing is empty. Run "
            "staging_pricing/import_s3_pricing.py before packaging."
        )


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        raise SystemExit(f"Pricing DB not found: {db_path}")

    metadata = build_db_metadata(db_path, TARGET_REGIONS)
    require_ec2_capability_catalog(metadata)
    require_rds_rightsizer_catalog(metadata)
    require_s3_street_pricing(metadata)

    asset_path = out_dir / args.asset_name
    sha256_path = out_dir / DEFAULT_SHA256_NAME
    metadata_path = out_dir / DEFAULT_METADATA_NAME

    compress_xz(db_path, asset_path, preset=args.preset)
    digest = sha256_file(asset_path)
    write_sha256_file(sha256_path, digest, asset_path.name)
    write_metadata(metadata_path, metadata)

    print("Packaged pricing release artifacts:")
    print(f"  compressed: {asset_path}")
    print(f"  sha256:     {sha256_path}")
    print(f"  metadata:   {metadata_path}")
    print(f"  digest:     {digest}")


if __name__ == "__main__":
    main()
