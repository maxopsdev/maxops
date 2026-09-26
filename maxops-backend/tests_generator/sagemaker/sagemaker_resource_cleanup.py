"""Cost-gated SageMaker payload cleanup entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests_generator.sagemaker.sagemaker_resource_creation import (
    CONFIG_PATH,
    SageMakerPayloadResourceManager,
    _load_json,
)


def main() -> None:
    """Clean up a previously captured resource state behind the same gate."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--state", type=Path, default=None)
    args = parser.parse_args()
    manager = SageMakerPayloadResourceManager(args.config, apply=args.apply)
    state_path = args.state or manager.state_path
    state = _load_json(state_path)
    summary = manager.cleanup_resources(state)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
