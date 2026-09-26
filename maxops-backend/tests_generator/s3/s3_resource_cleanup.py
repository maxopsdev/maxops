"""Clean up S3 resources used for payload capture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from botocore.exceptions import ClientError

from tests_generator.s3.s3_resource_creation import CONFIG_PATH, S3PayloadResourceManager, _load_json


def _configured_bucket_prefixes(config: Dict[str, Any]) -> List[str]:
    prefixes: List[str] = []
    for definition in config.get("bucket_definitions", {}).values():
        placeholder_key = definition.get("name_placeholder_key")
        if not placeholder_key:
            continue
        placeholder_value = config.get("placeholder_values", {}).get(placeholder_key)
        if placeholder_value:
            prefixes.append(placeholder_value.lower())
    return prefixes


def _is_capture_bucket(manager: S3PayloadResourceManager, bucket_name: str, prefixes: List[str]) -> bool:
    lowered = bucket_name.lower()
    if any(lowered.startswith(prefix) for prefix in prefixes):
        return True

    try:
        tag_response = manager.s3.get_bucket_tagging(Bucket=bucket_name)
    except ClientError:
        return False

    tags = {item.get("Key"): item.get("Value") for item in tag_response.get("TagSet", [])}
    return tags.get("maxops_payload_capture") == "true"


def _build_fallback_state(manager: S3PayloadResourceManager) -> Dict[str, Any]:
    config = manager.config
    prefixes = _configured_bucket_prefixes(config)
    resources: Dict[str, Dict[str, Any]] = {}

    buckets = manager.s3.list_buckets().get("Buckets", [])
    for bucket in buckets:
        bucket_name = bucket.get("Name")
        if not bucket_name or not _is_capture_bucket(manager, bucket_name, prefixes):
            continue
        resources[bucket_name] = {
            "bucket_name": bucket_name,
            "placeholder_key": None,
            "placeholder_value": None,
            "versioning_enabled": False,
        }

    role_prefix = config.get("placeholder_values", {}).get("REPLICATION_ROLE_NAME", "")
    supporting_resources: Dict[str, Any] = {}
    if role_prefix:
        roles = manager.iam.list_roles(PathPrefix="/").get("Roles", [])
        matching_roles = [role for role in roles if role.get("RoleName", "").startswith(role_prefix)]
        if matching_roles:
            supporting_resources["replication_role_name"] = matching_roles[0]["RoleName"]

    return {
        "service": "s3",
        "region": manager.region,
        "resources": resources,
        "supporting_resources": supporting_resources,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Clean up S3 payload-capture resources. "
            "If the state file is missing or incomplete, the script will also look for stranded capture buckets."
        )
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to resource_config.json")
    parser.add_argument("--state", type=Path, default=None, help="Optional path to the capture state file")
    args = parser.parse_args()

    manager = S3PayloadResourceManager(config_path=args.config)
    state = None
    if args.state is not None:
        state_path = args.state if args.state.is_absolute() else REPO_ROOT / args.state
        state = _load_json(state_path)
    elif manager.state_path.exists():
        state = _load_json(manager.state_path)

    fallback_state = _build_fallback_state(manager)
    if state is None:
        state = fallback_state
    else:
        for key, resource in fallback_state.get("resources", {}).items():
            state.setdefault("resources", {}).setdefault(key, resource)
        if not state.get("supporting_resources", {}).get("replication_role_name"):
            state.setdefault("supporting_resources", {}).update(fallback_state.get("supporting_resources", {}))

    summary = manager.cleanup_resources(state=state)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
