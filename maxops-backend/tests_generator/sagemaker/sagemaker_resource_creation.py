"""Create the small, cost-gated SageMaker fixture set used by payload capture."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import boto3
from tests_generator.capture_gate import require_capture_gate


CONFIG_PATH = Path(__file__).with_name("resource_config.json")


def _load_json(path: Path) -> Dict[str, Any]:
    """Load one generator JSON document."""
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Write one generator state document with stable formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class SageMakerPayloadResourceManager:
    """Create and clean up named SageMaker payload-capture resources.

    Clients are created only after the two explicit capture gates pass. The
    checked-in config supplies names and cost estimates; operators supply the
    execution role and algorithm/model settings in that config or environment.
    """

    def __init__(self, config_path: Optional[Path] = None, *, apply: bool = False):
        """Load config and create AWS clients only for an approved run."""
        require_capture_gate(apply, resource_plan="SageMaker payload capture")
        self.config_path = config_path or CONFIG_PATH
        self.config = _load_json(self.config_path)
        region = self.config["region"]
        profile = self.config.get("profile")
        self.session = boto3.Session(profile_name=profile, region_name=region) if profile else boto3.Session(region_name=region)
        self.region = region
        self.sagemaker = self.session.client("sagemaker", region_name=region)
        self.timestamp = int(time.time())
        self.state_path = self.config_path.parents[2] / self.config["state_file"]

    def _required(self, key: str) -> str:
        """Read an operator setting and explain how to supply a missing value."""
        value = self.config.get(key) or os.environ.get(f"SAGEMAKER_CAPTURE_{key.upper()}")
        if not value:
            raise RuntimeError(
                f"SageMaker capture requires {key!r} in resource_config.json "
                f"or SAGEMAKER_CAPTURE_{key.upper()}"
            )
        return str(value)

    def _name(self, alias: str) -> str:
        """Return a unique AWS name for a configured capture alias."""
        return f"{self.config['placeholder_values'].get(alias.upper(), alias)}-{self.timestamp}"

    def _tags(self, alias: str) -> list[Dict[str, str]]:
        """Build standard capture tags."""
        return [
            {"Key": "maxops_payload_capture", "Value": "true"},
            {"Key": "maxops_payload_alias", "Value": alias},
        ]

    def create_resources(self) -> Dict[str, Any]:
        """Create configured resources and persist names for capture/cleanup."""
        role_arn = self._required("execution_role_arn")
        state: Dict[str, Any] = {
            "service": "sagemaker",
            "region": self.region,
            "created_at_epoch": self.timestamp,
            "resources": {},
            "lifecycle_configs": [],
            "models": [],
            "endpoint_configs": [],
            "placeholder_map": {},
        }
        try:
            for alias, definition in self.config.get("resources", {}).items():
                kind = definition.get("kind")
                if kind == "notebook":
                    name = self._name(alias)
                    lifecycle_name = None
                    if alias == "notebook_cpu_autostop":
                        lifecycle_name = f"{name}-lifecycle"
                        self.sagemaker.create_notebook_instance_lifecycle_config(
                            NotebookInstanceLifecycleConfigName=lifecycle_name,
                            OnStart=[{"Content": self.config.get("autostop_script_base64", "")}],
                        )
                        state["lifecycle_configs"].append(lifecycle_name)
                    request: Dict[str, Any] = {
                        "NotebookInstanceName": name,
                        "InstanceType": definition["instance_type"],
                        "RoleArn": role_arn,
                        "Tags": self._tags(alias),
                    }
                    if lifecycle_name:
                        request["LifecycleConfigName"] = lifecycle_name
                    self.sagemaker.create_notebook_instance(**request)
                    state["resources"][alias] = {"kind": kind, "name": name}
                    state["placeholder_map"][self.config["placeholder_values"][alias.upper()]] = name
                elif kind == "endpoint":
                    name = self._name(alias)
                    model_name = f"{name}-model"
                    endpoint_config_name = f"{name}-config"
                    container = {"Image": self._required("inference_image_uri")}
                    if self.config.get("model_data_url"):
                        container["ModelDataUrl"] = self.config["model_data_url"]
                    self.sagemaker.create_model(
                        ModelName=model_name,
                        PrimaryContainer=container,
                        ExecutionRoleArn=role_arn,
                        Tags=self._tags(alias),
                    )
                    self.sagemaker.create_endpoint_config(
                        EndpointConfigName=endpoint_config_name,
                        ProductionVariants=[{
                            "VariantName": "AllTraffic",
                            "ModelName": model_name,
                            "InitialInstanceCount": int(definition.get("instance_count", 1)),
                            "InstanceType": definition["instance_type"],
                            "InitialVariantWeight": 1.0,
                        }],
                        Tags=self._tags(alias),
                    )
                    self.sagemaker.create_endpoint(
                        EndpointName=name,
                        EndpointConfigName=endpoint_config_name,
                        Tags=self._tags(alias),
                    )
                    state["models"].append(model_name)
                    state["endpoint_configs"].append(endpoint_config_name)
                    state["resources"][alias] = {"kind": kind, "name": name}
                    state["placeholder_map"][self.config["placeholder_values"][alias.upper()]] = name
                elif kind == "training_job":
                    family = self.config["placeholder_values"]["TRAINING_FAMILY"]
                    jobs = []
                    for run in range(int(definition.get("runs", 4))):
                        name = f"{family}-{self.timestamp + run:010d}"
                        request = {
                            "TrainingJobName": name,
                            "AlgorithmSpecification": {
                                "TrainingImage": self._required("training_image_uri"),
                                "TrainingInputMode": "File",
                            },
                            "RoleArn": role_arn,
                            "InputDataConfig": [],
                            "OutputDataConfig": {"S3OutputPath": self._required("training_output_s3_uri")},
                            "ResourceConfig": {
                                "InstanceType": definition["instance_type"],
                                "InstanceCount": int(definition.get("instance_count", 1)),
                                "VolumeSizeInGB": 30,
                            },
                            "StoppingCondition": {"MaxRuntimeInSeconds": 600},
                            "Tags": self._tags(alias),
                        }
                        self.sagemaker.create_training_job(**request)
                        jobs.append(name)
                    state["resources"][alias] = {"kind": kind, "names": jobs}
                    state["placeholder_map"][self.config["placeholder_values"][alias.upper()]] = family
                else:
                    raise ValueError(f"Unsupported SageMaker capture resource kind: {kind}")
        except Exception:
            self.cleanup_resources(state)
            raise
        _write_json(self.state_path, state)
        return state

    def cleanup_resources(self, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Delete endpoints before their configs/models and remove notebooks."""
        state = state or _load_json(self.state_path)
        errors = []
        targeted = 0

        def call(operation: str, **kwargs: Any) -> None:
            """Run one cleanup call and retain unexpected errors."""
            nonlocal targeted
            targeted += 1
            try:
                getattr(self.sagemaker, operation)(**kwargs)
            except Exception as exc:
                code = getattr(exc, "response", {}).get("Error", {}).get("Code")
                if code not in {"ValidationException", "ResourceNotFound"}:
                    errors.append({"operation": operation, "message": str(exc)})

        for resource in state.get("resources", {}).values():
            if resource.get("kind") == "endpoint":
                call("delete_endpoint", EndpointName=resource["name"])
        for name in state.get("endpoint_configs", []):
            call("delete_endpoint_config", EndpointConfigName=name)
        for name in state.get("models", []):
            call("delete_model", ModelName=name)
        for resource in state.get("resources", {}).values():
            if resource.get("kind") == "notebook":
                name = resource["name"]
                call("stop_notebook_instance", NotebookInstanceName=name)
                call("delete_notebook_instance", NotebookInstanceName=name)
        for name in state.get("lifecycle_configs", []):
            call("delete_notebook_instance_lifecycle_config", NotebookInstanceLifecycleConfigName=name)
        return {
            "service": "sagemaker",
            "cleanup_attempted": True,
            "cleanup_status": "failure" if errors else "success",
            "resource_counts": {"targeted": targeted, "failed": len(errors)},
            "errors": errors,
        }


def main() -> None:
    """Print the plan and require the two capture gates before AWS calls."""
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["create", "cleanup"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args()
    config = _load_json(args.config)
    print(f"SageMaker capture plan: {config['resources']}")
    print(f"Estimated maximum hourly cost: ${config['estimated_hourly_cost_usd']:.2f}")
    manager = SageMakerPayloadResourceManager(args.config, apply=args.apply)
    if args.command == "create":
        print(json.dumps(manager.create_resources(), indent=2, sort_keys=True))
    else:
        summary = manager.cleanup_resources()
        print(json.dumps(summary, indent=2, sort_keys=True))
        if summary["errors"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
