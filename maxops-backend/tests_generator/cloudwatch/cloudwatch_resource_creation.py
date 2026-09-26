"""Create and clean up CloudWatch payload-capture resources."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError
from tests_generator.capture_gate import require_capture_gate

CONFIG_PATH = Path(__file__).with_name("resource_config.json")
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class CloudWatchPayloadResourceManager:
    def __init__(self, config_path: Optional[Path] = None, *, apply: bool = False):
        require_capture_gate(apply)
        self.config_path = config_path or CONFIG_PATH
        self.config = _load_json(self.config_path)
        self.state_path = self.config_path.parents[2] / self.config["state_file"]
        profile = self.config.get("profile")
        self.region = self.config["region"]
        self.session = (
            boto3.Session(profile_name=profile, region_name=self.region)
            if profile
            else boto3.Session(region_name=self.region)
        )
        self.cloudwatch = self.session.client("cloudwatch", region_name=self.region)
        self.logs = self.session.client("logs", region_name=self.region)
        self.sts = self.session.client("sts")
        self.timestamp = int(time.time())

    def _name(self, placeholder: str) -> str:
        return f"{placeholder}-{self.timestamp}"

    def create_resources(self) -> Dict[str, Any]:
        placeholders = self.config["placeholder_values"]
        account_id = self.sts.get_caller_identity()["Account"]
        aliases = {
            "log_group_no_retention": "LOG_GROUP_NO_RETENTION",
            "log_group_retained": "LOG_GROUP_RETAINED",
            "log_group_delete_target": "LOG_GROUP_DELETE_TARGET",
            "no_action_alarm": "NO_ACTION_ALARM",
            "noisy_alarm": "NOISY_ALARM",
            "duplicate_keeper_alarm": "DUPLICATE_KEEPER_ALARM",
            "duplicate_delete_alarm": "DUPLICATE_DELETE_ALARM",
            "actionful_alarm": "ACTIONFUL_ALARM",
        }
        resources: Dict[str, Dict[str, Any]] = {}
        placeholder_map = {placeholders["ACCOUNT_ID"]: account_id}
        state = {
            "service": "cloudwatch",
            "region": self.region,
            "account_id": account_id,
            "resources": resources,
            "placeholder_map": placeholder_map,
        }
        try:
            for alias in ("log_group_no_retention", "log_group_retained", "log_group_delete_target"):
                key = aliases[alias]
                name = self._name(placeholders[key])
                self.logs.create_log_group(
                    logGroupName=name,
                    tags={"maxops_payload_capture": "true"},
                )
                if alias == "log_group_retained":
                    self.logs.put_retention_policy(logGroupName=name, retentionInDays=30)
                resources[alias] = {"resource_id": name, "placeholder_key": key, "kind": "log_group"}
                placeholder_map[placeholders[key]] = name

            action_arn = f"arn:aws:sns:{self.region}:{account_id}:maxops-payload-placeholder"
            for alias in (
                "no_action_alarm",
                "noisy_alarm",
                "duplicate_keeper_alarm",
                "duplicate_delete_alarm",
                "actionful_alarm",
            ):
                key = aliases[alias]
                name = self._name(placeholders[key])
                kwargs = {
                    "AlarmName": name,
                    "Namespace": "MaxOps/Payload",
                    "MetricName": "CaptureMetric",
                    "Statistic": "Average",
                    "Dimensions": [{"Name": "Profile", "Value": "duplicate" if alias.startswith("duplicate") else alias}],
                    "Period": 60,
                    "EvaluationPeriods": 2,
                    "Threshold": 80.0,
                    "ComparisonOperator": "GreaterThanThreshold",
                    "TreatMissingData": "missing",
                }
                if alias in {"noisy_alarm", "actionful_alarm"}:
                    kwargs["AlarmActions"] = [action_arn]
                self.cloudwatch.put_metric_alarm(**kwargs)
                resources[alias] = {"resource_id": name, "placeholder_key": key, "kind": "alarm"}
                placeholder_map[placeholders[key]] = name

            noisy_name = resources["noisy_alarm"]["resource_id"]
            self.cloudwatch.set_alarm_state(
                AlarmName=noisy_name,
                StateValue="OK",
                StateReason="MaxOps payload baseline",
            )
            self.cloudwatch.set_alarm_state(
                AlarmName=noisy_name,
                StateValue="ALARM",
                StateReason="MaxOps payload trigger",
            )
        except Exception:
            self.cleanup_resources(state)
            raise
        _write_json(self.state_path, state)
        return state

    def cleanup_resources(self, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = state or _load_json(self.state_path)
        errors = []
        targets = []
        alarm_names = []
        for resource in state.get("resources", {}).values():
            resource_id = resource["resource_id"]
            targets.append(resource_id)
            if resource["kind"] == "alarm":
                alarm_names.append(resource_id)
            else:
                try:
                    self.logs.delete_log_group(logGroupName=resource_id)
                except ClientError as exc:
                    if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
                        errors.append({"resource_name": resource_id, "operation": "delete_log_group", "message": str(exc)})
        if alarm_names:
            try:
                self.cloudwatch.delete_alarms(AlarmNames=alarm_names)
            except ClientError as exc:
                errors.append({"resources": alarm_names, "operation": "delete_alarms", "message": str(exc)})
        return {
            "service": "cloudwatch",
            "cleanup_attempted": True,
            "cleanup_status": "success" if not errors else "partial_failure",
            "resource_counts": {"targeted": len(targets), "succeeded": len(targets) - len(errors), "failed": len(errors)},
            "resources_targeted": targets,
            "errors": errors,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", default="create", choices=["create", "cleanup"])
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    require_capture_gate(args.apply)
    manager = CloudWatchPayloadResourceManager(args.config, apply=args.apply)
    result = manager.create_resources() if args.command == "create" else manager.cleanup_resources()
    print(json.dumps(result, indent=2, sort_keys=True))
    if result.get("errors"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
