"""Build the global EC2 instance-spec catalog from ``us-east-1``.

Instance-type specifications are treated as region-invariant product data;
regional prices remain in ``street_pricing_ec2``.  This release-time build step
uses only ``DescribeInstanceTypes`` in ``us-east-1`` and does not make
Availability Zone offering or launch-capacity claims.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3


SPEC_SOURCE_REGION = "us-east-1"
SPEC_SCHEMA_VERSION = 2


def _create_schema(connection: sqlite3.Connection, table: str) -> None:
    connection.execute(
        f"""
        CREATE TABLE {table} (
            instance_type TEXT NOT NULL,
            spec_json TEXT NOT NULL,
            source_region TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            generated_at TEXT NOT NULL,
            PRIMARY KEY (instance_type)
        )
        """
    )


def enrich_database(
    database_path: str | Path,
    *,
    session: Any | None = None,
) -> int:
    """Fetch global capabilities once from ``us-east-1`` and replace atomically."""
    session = session or boto3.Session()
    generated_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(database_path) as connection:
        priced_types = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT instance_type FROM street_pricing_ec2"
            )
        }

    # Finish every AWS call before opening the replacement transaction.  A
    # partial/failed fetch therefore leaves the prior derived table untouched.
    client = session.client("ec2", region_name=SPEC_SOURCE_REGION)
    specs: dict[str, dict[str, Any]] = {}
    next_token: str | None = None
    while True:
        request: dict[str, Any] = {"MaxResults": 100}
        if next_token:
            request["NextToken"] = next_token
        response = client.describe_instance_types(**request)
        for spec in response.get("InstanceTypes", []) or []:
            instance_type = str(spec.get("InstanceType") or "")
            if instance_type in priced_types:
                specs[instance_type] = spec
        next_token = response.get("NextToken")
        if not next_token:
            break

    rows = [
        (
            instance_type,
            json.dumps(spec, sort_keys=True, separators=(",", ":"), default=str),
            SPEC_SOURCE_REGION,
            SPEC_SCHEMA_VERSION,
            generated_at,
        )
        for instance_type, spec in sorted(specs.items())
    ]
    if priced_types and not rows:
        raise RuntimeError(
            "DescribeInstanceTypes returned no globally priced instance types; "
            "preserving the previous EC2 capability catalog"
        )
    with sqlite3.connect(database_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DROP TABLE IF EXISTS ec2_instance_specs_next")
            _create_schema(connection, "ec2_instance_specs_next")
            connection.executemany(
                """
                INSERT INTO ec2_instance_specs_next (
                    instance_type, spec_json, source_region, schema_version, generated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.execute("DROP TABLE IF EXISTS ec2_instance_specs")
            connection.execute(
                "ALTER TABLE ec2_instance_specs_next RENAME TO ec2_instance_specs"
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build the global EC2 instance-spec catalog from us-east-1. "
            "Regional pricing remains unchanged."
        )
    )
    parser.add_argument("--database", default="maxops_pricing.db")
    args = parser.parse_args()
    count = enrich_database(args.database)
    with sqlite3.connect(args.database) as connection:
        priced_count = connection.execute(
            "SELECT COUNT(DISTINCT instance_type) FROM street_pricing_ec2"
        ).fetchone()[0]
    print(
        f"Wrote {count} global EC2 instance capability rows from "
        f"{SPEC_SOURCE_REGION}; {max(priced_count - count, 0)} priced types "
        "remain coverage gaps"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
