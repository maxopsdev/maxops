"""Capture or verify the frozen ASG V1 decision projection."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any

from tests.asg_rightsizer_helpers import metadata, requirement, run


GOLDEN_PATH = Path(__file__).with_name("v1_decision_projection_golden.json")


def decision_projection(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "classification": result.get("classification"),
        "capacity_policy": result.get("capacity_policy"),
        "targets": [
            (
                item["target_min_size"],
                item["target_desired_capacity"],
                item["target_max_size"],
            )
            for item in result.get("recommendations", [])
        ],
        "savings": [
            item["monthly_savings"] for item in result.get("recommendations", [])
        ],
        "recommendation_contract": [
            {
                "classification": item["classification"],
                "reason_codes": item["reason_codes"],
                "satisfied_tiers": item["satisfied_tiers"],
            }
            for item in result.get("recommendations", [])
        ],
        "tiers": {
            name: (
                option["target_min_size"],
                option["target_desired_capacity"],
            )
            if option
            else None
            for name, option in result.get("tiers", {}).items()
            if name != "default"
        },
        "tier_default": result.get("tiers", {}).get("default"),
        "previews": {
            item["kind"]: {
                "classification": item["classification"],
                "blockers": tuple(item["blockers"]),
                "targets": {
                    tier: (
                        option["target_min_size"],
                        option["target_desired_capacity"],
                        option["target_max_size"],
                    )
                    if option
                    else None
                    for tier, option in item["options"].items()
                    if tier != "default"
                },
                "default": item["options"]["default"],
            }
            for item in result.get("savings_previews", [])
        },
        "blocking_reasons": result.get("blocking_reasons", []),
        "deferred_reasons": result.get("deferred_reason_codes", []),
        "messages": result.get("messages", []),
    }


def _missing_memory() -> dict[str, Any]:
    telemetry = metadata()["telemetry_summary"]
    telemetry["memory_percent"] = {
        "present": False,
        "observed_days": 0.0,
        "thin_data": True,
        "pairing_ratio": None,
        "source": None,
        "status": "unavailable",
    }
    return {"telemetry_summary": telemetry}


def _desired_history_missing() -> dict[str, Any]:
    telemetry = metadata()["telemetry_summary"]
    telemetry["desired_capacity"]["present"] = False
    return {"telemetry_summary": telemetry}


def _cpu_missing() -> dict[str, Any]:
    telemetry = metadata()["telemetry_summary"]
    telemetry["cpu_percent"]["present"] = False
    return {"telemetry_summary": telemetry}


def _tier_collapse() -> dict[str, Any]:
    windows = deepcopy(metadata()["rightsizing_metrics"])
    for window in windows.values():
        window["normalized"]["required_capacity"] = {
            tier: {
                **requirement(5),
                "p50": 3,
                "p50_record": {
                    **requirement(3)["p50_record"],
                    "required": 3,
                },
            }
            for tier in ("conservative", "balanced", "aggressive")
        }
    return {"rightsizing_metrics": windows}


def capture() -> dict[str, Any]:
    cases = {
        "complete": {},
        "missing_memory": _missing_memory(),
        "desired_history_missing": _desired_history_missing(),
        "deferred_warm_pool": {"warm_pool_present": True},
        "insufficient_cpu": _cpu_missing(),
        "tier_collapse": _tier_collapse(),
        "operational_conditional": {
            "dynamic_policy_kinds": [],
            "dynamic_policy_metrics": [],
        },
    }
    return {
        name: decision_projection(run(metadata_override=override)[0])
        for name, override in cases.items()
    }


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--print", action="store_true", dest="print_payload")
    args = parser.parse_args()
    current = canonical_bytes(capture())
    if args.print_payload:
        sys.stdout.buffer.write(current)
        return 0
    if args.write:
        GOLDEN_PATH.write_bytes(current)
        return 0
    if not GOLDEN_PATH.is_file():
        raise SystemExit(
            f"{GOLDEN_PATH} is missing; capture it before helper extraction with --write"
        )
    if GOLDEN_PATH.read_bytes() != current:
        raise SystemExit("ASG V1 decision projection differs from the frozen golden")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
