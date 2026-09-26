"""Clean up EBS resources created for payload capture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tests_generator.ebs.ebs_resource_creation import CONFIG_PATH, EBSPayloadResourceManager


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean up EBS payload-capture resources.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to resource_config.json")
    args = parser.parse_args()

    manager = EBSPayloadResourceManager(config_path=args.config)
    summary = manager.cleanup_resources()
    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
