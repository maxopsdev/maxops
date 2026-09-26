#!/usr/bin/env python3
"""Scrub selected local S3 CUR rows into the offline optimizer fixture."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Direct execution puts this tools directory on sys.path, not the backend
# project root.  Add the root so the CLI works without an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.services.s3_bucket_source import load_bucket_rows


DEFAULT_OUTPUT = Path("tests/payloads/s3_optimizer/cur_fixture.json")


def scrub_rows(rows: list[dict[str, Any]], requested_buckets: list[str]) -> dict[str, Any]:
    """Replace every account/resource identifier with stable fixture placeholders."""

    placeholders = [f"bucket-{chr(ord('a') + index)}" for index in range(len(requested_buckets))]
    bucket_map = dict(zip(requested_buckets, placeholders))
    account_names = sorted(
        {
            str(value)
            for row in rows
            for key, value in row.items()
            if value is not None and ("account" in key.lower() or re.fullmatch(r"\d{12}", str(value)))
        }
    )
    account_map = {
        name: ("111111111111" if index == 0 else f"{index + 1:012d}")
        for index, name in enumerate(account_names)
    }
    scrubbed = []
    for row in rows:
        item = dict(row)
        item["bucket"] = bucket_map[item["bucket"]]
        item["line_item_resource_id"] = item["bucket"]
        for key, value in list(item.items()):
            if value is None:
                continue
            if "resource_id" in key.lower():
                item[key] = item["bucket"]
                continue
            text = str(value)
            if key == "account_id" or "account" in key.lower() or re.fullmatch(r"\d{12}", text):
                item[key] = account_map.get(text, "111111111111")
            elif re.search(r"\d{12}", text):
                item[key] = re.sub(r"\d{12}", "111111111111", text)
        scrubbed.append(item)
    return {
        "schema_version": 1,
        "grain": "daily",
        "buckets": [bucket_map[name] for name in requested_buckets],
        "rows": scrubbed,
    }


def parse_args() -> argparse.Namespace:
    """Parse the offline fixture scrubber arguments."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("buckets", nargs="+", help="one or more real bucket names in placeholder order")
    parser.add_argument("--cache-root", type=Path, default=Path("data/cur_cache"))
    parser.add_argument("--start", default="1900-01-01")
    parser.add_argument("--end", default="9999-12-31")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    """Read, scrub, and write the selected local rows; return a CLI status."""

    args = parse_args()
    requested = list(dict.fromkeys(args.buckets))
    rows = list(load_bucket_rows(args.cache_root, args.start, args.end, "daily"))
    selected = [row for row in rows if row.get("bucket") in requested]
    missing = [bucket for bucket in requested if bucket not in {row.get("bucket") for row in selected}]
    if missing:
        raise SystemExit(f"No S3 CUR rows found for bucket(s): {', '.join(missing)}")
    payload = scrub_rows(selected, requested)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(payload['rows'])} rows for {len(payload['buckets'])} buckets to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
