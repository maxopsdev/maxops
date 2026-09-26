"""Clean up ElastiCache resources used for payload capture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests_generator.elasticache.elasticache_resource_creation import (
    CONFIG_PATH,
    ElastiCachePayloadResourceManager,
    _load_json,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean up ElastiCache payload-capture resources.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to resource_config.json")
    parser.add_argument("--state", type=Path, default=None, help="Optional path to the capture state file")
    args = parser.parse_args()

    manager = ElastiCachePayloadResourceManager(config_path=args.config)
    state = None
    if args.state is not None:
        state_path = args.state if args.state.is_absolute() else REPO_ROOT / args.state
        state = _load_json(state_path)

    summary = manager.cleanup_resources(state=state)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
