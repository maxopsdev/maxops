"""Offline SageMaker adapter parsing tests backed by botocore service shapes."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable

import botocore.session
import pytest

from app.adapters.aws.adapter import AWSAdapter
from app.checks.sagemaker.endpoint_gpu_underutilized import (
    check_sagemaker_endpoint_gpu_underutilized,
)
from app.checks.sagemaker.notebook_no_auto_stop import DEFAULT_AUTO_STOP_PATTERNS
from app.checks.sagemaker.common import lifecycle_auto_stop_match
from tests.payload_helpers import (
    StaticApplicationAutoScalingClient,
    StaticCloudWatchClient,
    StaticSageMakerClient,
    StaticSession,
)


def _shape_has(
    service: str, operation: str, dotted_path: str, expected_type: str
) -> None:
    """Assert one output member path and its botocore type.

    List members use ``[]`` in the path.  Keeping traversal here means adding
    another adapter field requires one readable table entry rather than a new
    block of botocore model boilerplate.
    """
    model = botocore.session.get_session().get_service_model(service)
    shape = model.operation_model(operation).output_shape
    assert shape is not None, f"{service}.{operation} has no output shape"
    for component in dotted_path.split("."):
        is_list_member = component.endswith("[]")
        component = component[:-2] if is_list_member else component
        assert shape.type_name == "structure", dotted_path
        assert component in shape.members, dotted_path
        shape = shape.members[component]
        if is_list_member:
            assert shape.type_name == "list", dotted_path
            shape = shape.member
    assert shape.type_name == expected_type, dotted_path


def test_sagemaker_adapter_fields_match_botocore_service_models() -> None:
    """Validate every AWS response member consumed by the SageMaker adapter."""
    fields = {
        "sagemaker": {
            "ListNotebookInstances": {
                "NotebookInstances": "list",
                "NotebookInstances[].NotebookInstanceName": "string",
                "NotebookInstances[].NotebookInstanceStatus": "string",
                "NotebookInstances[].InstanceType": "string",
                "NotebookInstances[].CreationTime": "timestamp",
            },
            "DescribeNotebookInstance": {
                "NotebookInstanceStatus": "string",
                "NotebookInstanceLifecycleConfigName": "string",
                "VolumeSizeInGB": "integer",
                "PlatformIdentifier": "string",
                "DirectInternetAccess": "string",
                "LastModifiedTime": "timestamp",
                "CreationTime": "timestamp",
                "InstanceType": "string",
            },
            "DescribeNotebookInstanceLifecycleConfig": {
                "OnStart": "list",
                "OnStart[].Content": "string",
            },
            "ListEndpoints": {
                "Endpoints": "list",
                "Endpoints[].EndpointName": "string",
                "Endpoints[].EndpointStatus": "string",
                "Endpoints[].CreationTime": "timestamp",
            },
            "DescribeEndpoint": {
                "EndpointConfigName": "string",
                "EndpointStatus": "string",
                "CreationTime": "timestamp",
                "LastModifiedTime": "timestamp",
                "ProductionVariants": "list",
                "ProductionVariants[].VariantName": "string",
                "ProductionVariants[].CurrentInstanceCount": "integer",
            },
            "DescribeEndpointConfig": {
                "EndpointConfigName": "string",
                "ProductionVariants": "list",
                "ProductionVariants[].VariantName": "string",
                "ProductionVariants[].InstanceType": "string",
                "ProductionVariants[].InitialInstanceCount": "integer",
                "ProductionVariants[].ServerlessConfig": "structure",
                "ProductionVariants[].ServerlessConfig.MemorySizeInMB": "integer",
            },
            "ListTrainingJobs": {
                "TrainingJobSummaries": "list",
                "TrainingJobSummaries[].TrainingJobName": "string",
                "TrainingJobSummaries[].TrainingJobStatus": "string",
                "TrainingJobSummaries[].CreationTime": "timestamp",
            },
            "DescribeTrainingJob": {
                "TrainingJobStatus": "string",
                "ResourceConfig": "structure",
                "ResourceConfig.InstanceType": "string",
                "ResourceConfig.InstanceCount": "integer",
                "EnableManagedSpotTraining": "boolean",
                "CheckpointConfig": "structure",
                "CheckpointConfig.S3Uri": "string",
                "CheckpointConfig.LocalPath": "string",
                "StoppingCondition": "structure",
                "StoppingCondition.MaxWaitTimeInSeconds": "integer",
                "TrainingTimeInSeconds": "integer",
                "BillableTimeInSeconds": "integer",
                "CreationTime": "timestamp",
            },
        },
        "application-autoscaling": {
            "DescribeScalingPolicies": {
                "ScalingPolicies": "list",
                "ScalingPolicies[].PolicyName": "string",
            }
        },
        "cloudwatch": {
            "GetMetricData": {
                "MetricDataResults": "list",
                "MetricDataResults[].Id": "string",
                "MetricDataResults[].Timestamps": "list",
                "MetricDataResults[].Values": "list",
                "NextToken": "string",
            },
            "ListMetrics": {
                "Metrics": "list",
                "Metrics[].Namespace": "string",
                "Metrics[].MetricName": "string",
                "Metrics[].Dimensions": "list",
                "Metrics[].Dimensions[].Name": "string",
                "Metrics[].Dimensions[].Value": "string",
                "NextToken": "string",
            },
        },
    }
    for service, operations in fields.items():
        for operation, operation_fields in operations.items():
            for path, expected_type in operation_fields.items():
                _shape_has(service, operation, path, expected_type)


def _adapter(
    sagemaker_responses: Dict[str, Dict[str, Any]],
    *,
    scaling_policies: Iterable[Dict[str, Any]] = (),
    metric_data_responses: Dict[tuple[str, str], Dict[str, Any]] | None = None,
    list_metrics_response: Dict[str, Any] | None = None,
) -> AWSAdapter:
    """Build an AWSAdapter using only static service clients."""
    cloudwatch = StaticCloudWatchClient(
        metric_data_responses or {},
        list_metrics_response=list_metrics_response or {"Metrics": []},
    )
    adapter = AWSAdapter.__new__(AWSAdapter)
    adapter.session = StaticSession(
        {
            "sagemaker": StaticSageMakerClient(sagemaker_responses),
            "application-autoscaling": StaticApplicationAutoScalingClient(
                {"describe_scaling_policies": {"ScalingPolicies": list(scaling_policies)}}
            ),
            "cloudwatch": cloudwatch,
        }
    )
    adapter.cloudwatch_client = cloudwatch
    adapter._default_region = "us-east-1"
    adapter._ec2_metric_discovery_cache = {}
    return adapter


def _notebook_responses() -> Dict[str, Dict[str, Any]]:
    """Return a shape-valid notebook and lifecycle response set."""
    timestamp = datetime(2026, 9, 1, tzinfo=timezone.utc)
    content = base64.b64encode(b"#!/bin/bash\n# autostop after idle\n").decode()
    return {
        "list_notebook_instances": {
            "NotebookInstances": [
                {
                    "NotebookInstanceName": "notebook-a",
                    "NotebookInstanceStatus": "InService",
                    "InstanceType": "ml.g5.xlarge",
                    "CreationTime": timestamp,
                }
            ]
        },
        "describe_notebook_instance:notebook-a": {
            "NotebookInstanceName": "notebook-a",
            "NotebookInstanceStatus": "InService",
            "NotebookInstanceLifecycleConfigName": "lifecycle-a",
            "VolumeSizeInGB": 20,
            "PlatformIdentifier": "notebook-al2-v3",
            "DirectInternetAccess": "Enabled",
            "LastModifiedTime": timestamp,
            "CreationTime": timestamp,
            "InstanceType": "ml.g5.xlarge",
        },
        "describe_notebook_instance_lifecycle_config:lifecycle-a": {
            "NotebookInstanceLifecycleConfigName": "lifecycle-a",
            "OnStart": [{"Content": content}],
            "OnCreate": [],
        },
    }


def _endpoint_responses() -> Dict[str, Dict[str, Any]]:
    """Return one serverless and one four-GPU real-time endpoint variant."""
    timestamp = datetime(2026, 8, 1, tzinfo=timezone.utc)
    return {
        "list_endpoints": {
            "Endpoints": [
                {
                    "EndpointName": "endpoint-a",
                    "EndpointStatus": "InService",
                    "CreationTime": timestamp,
                }
            ]
        },
        "describe_endpoint:endpoint-a": {
            "EndpointName": "endpoint-a",
            "EndpointConfigName": "endpoint-config-a",
            "EndpointStatus": "InService",
            "CreationTime": timestamp,
            "LastModifiedTime": timestamp,
            "ProductionVariants": [
                {"VariantName": "Serverless", "CurrentInstanceCount": 0},
                {"VariantName": "RealTime", "CurrentInstanceCount": 2},
            ],
        },
        "describe_endpoint_config:endpoint-config-a": {
            "EndpointConfigName": "endpoint-config-a",
            "ProductionVariants": [
                {
                    "VariantName": "Serverless",
                    "ServerlessConfig": {"MemorySizeInMB": 2048, "MaxConcurrency": 10},
                },
                {
                    "VariantName": "RealTime",
                    "InstanceType": "ml.g5.12xlarge",
                    "InitialInstanceCount": 2,
                },
            ],
        },
    }


def test_adapter_builds_notebook_endpoint_and_training_shapes() -> None:
    """Exercise discovery fields consumed by notebook, endpoint, and training checks."""
    notebook_adapter = _adapter(_notebook_responses())
    notebooks = notebook_adapter._get_sagemaker_notebooks(region="us-east-1")
    notebook = notebooks[0]
    assert notebook["resource_type"] == "sagemaker_notebook"
    assert notebook["status"] == "InService"
    assert notebook["region"] == "us-east-1"
    assert notebook["metadata"]["lifecycle_config_name"] == "lifecycle-a"
    assert notebook["metadata"]["lifecycle_on_start"][0]["Content"]
    assert notebook["metadata"]["lifecycle_config_has_auto_stop"] is True
    assert lifecycle_auto_stop_match(
        notebook["metadata"]["lifecycle_on_start"][0]["Content"],
        DEFAULT_AUTO_STOP_PATTERNS,
    ) == "autostop"
    assert notebook["metadata"]["gpu_device_count"] == 1

    endpoint_adapter = _adapter(_endpoint_responses())
    endpoints = endpoint_adapter._get_sagemaker_endpoints(region="us-east-1")
    endpoint = endpoints[0]
    assert endpoint["resource_id"] == "endpoint-a"
    assert endpoint["metadata"]["endpoint_status"] == "InService"
    assert endpoint["created_at"] == datetime(2026, 8, 1, tzinfo=timezone.utc)
    variants = {item["variant_name"]: item for item in endpoint["metadata"]["variants"]}
    assert variants["Serverless"]["serverless"] is True
    assert variants["Serverless"]["gpu_device_count"] is None
    assert variants["RealTime"]["instance_type"] == "ml.g5.12xlarge"
    assert variants["RealTime"]["current_instance_count"] == 2
    assert variants["RealTime"]["initial_instance_count"] == 2
    assert variants["RealTime"]["gpu_device_count"] == 4
    assert variants["RealTime"]["vcpus"] == 48
    assert variants["RealTime"]["autoscaling_policy_present"] is False

    policy_adapter = _adapter(
        _endpoint_responses(),
        scaling_policies=[{"PolicyName": "target-tracking"}],
    )
    policy_variant = policy_adapter._get_sagemaker_endpoints("", "us-east-1")[0]["variants"][1]
    assert policy_variant["autoscaling_policy_present"] is True

    training_name = "daily-train-2026-09-01-0001"
    training_adapter = _adapter(
        {
            "list_training_jobs": {
                "TrainingJobSummaries": [
                    {
                        "TrainingJobName": training_name,
                        "TrainingJobStatus": "Completed",
                        "CreationTime": datetime(2026, 9, 1, tzinfo=timezone.utc),
                    }
                ]
            },
            f"describe_training_job:{training_name}": {
                "TrainingJobName": training_name,
                "TrainingJobStatus": "Completed",
                "CreationTime": datetime(2026, 9, 1, tzinfo=timezone.utc),
                "ResourceConfig": {"InstanceType": "ml.m5.large", "InstanceCount": 2},
                "EnableManagedSpotTraining": True,
                "CheckpointConfig": {"S3Uri": "s3://bucket/checkpoints", "LocalPath": "/opt/ml/checkpoints"},
                "StoppingCondition": {"MaxWaitTimeInSeconds": 3600},
                "TrainingTimeInSeconds": 900,
                "BillableTimeInSeconds": 600,
            },
        }
    )
    jobs = training_adapter._get_sagemaker_training_jobs(
        {"job_history_days": 30}, "us-east-1"
    )
    job = jobs[0]
    assert job["resource_type"] == "sagemaker_training_job"
    assert job["metadata"]["enable_managed_spot_training"] is True
    assert job["metadata"]["checkpoint_config_present"] is True
    assert job["aws_payload"]["EnableManagedSpotTraining"] is True
    assert job["aws_payload"]["CheckpointConfig"]["S3Uri"] == "s3://bucket/checkpoints"
    assert job["aws_payload"]["CheckpointConfig"]["LocalPath"] == "/opt/ml/checkpoints"
    assert job["metadata"]["job_family"] == "daily-train"
    assert training_adapter._sagemaker_training_job_family(training_name) == "daily-train"


def _metric_response(values_by_id: Dict[str, list[float]]) -> Dict[str, Any]:
    """Build a shape-valid GetMetricData response for the requested IDs."""
    timestamps = [
        datetime(2026, 9, 1, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 1, 0, 1, tzinfo=timezone.utc),
    ]
    return {
        "MetricDataResults": [
            {"Id": query_id, "Timestamps": timestamps, "Values": values}
            for query_id, values in values_by_id.items()
        ]
    }


def test_adapter_utilization_preserves_sentinels_and_gpu_mean_of_sum() -> None:
    """Exercise CloudWatch shaping and reach mean-of-sum GPU evidence via adapter calls."""
    endpoint_values: Dict[str, list[float]] = {}
    aliases = (
        "invocations",
        "invocations_per_instance",
        "cpuutilization",
        "memoryutilization",
        "gpuutilization",
        "gpumemoryutilization",
        "gpuutilization_normalized",
    )
    for alias in aliases:
        values = [1.0, 1.0]
        if alias == "gpuutilization":
            values = [8.0, 8.0]
        elif alias == "gpumemoryutilization":
            values = [8.0, 8.0]
        elif alias == "gpuutilization_normalized":
            # Leave the optional normalized metric absent so the check must
            # normalize the raw summed series by the inventory device count.
            values = []
        for suffix in ("average", "maximum"):
            endpoint_values[f"{alias}_{suffix}"] = values

    training_values = {
        f"training_{alias}_{suffix}": [1.0, 2.0]
        for alias in ("cpuutilization", "memoryutilization", "gpuutilization", "gpumemoryutilization")
        for suffix in ("average", "maximum")
    }
    adapter = _adapter(
        _endpoint_responses(),
        metric_data_responses={
            ("AWS/SageMaker", "endpoint-a"): _metric_response(endpoint_values),
            ("/aws/sagemaker/TrainingJobs", "training-a/algo-1"): _metric_response(training_values),
        },
        # No AcceleratorId discovery means the native summed GPU series takes
        # the documented mean-of-sum fallback after device-count normalization.
        list_metrics_response={"Metrics": []},
    )
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    end = start + timedelta(minutes=5)
    endpoint_utilization = adapter._get_sagemaker_endpoint_utilization(
        "endpoint-a", start, end, "us-east-1"
    )
    realtime = endpoint_utilization["variants"]["RealTime"]
    assert realtime["gpuutilization"]["metric_status"] == "usable"
    assert realtime["gpuutilization"]["metric_history"]["average"] == [8.0, 8.0]
    assert realtime["gpuutilization"]["metric_summary"]["sample_count"] == 2

    training_utilization = adapter._get_sagemaker_training_utilization(
        "training-a", start, end, "us-east-1"
    )
    assert training_utilization["metric_history"]["cpuutilization"]["average"] == [1.0, 2.0]
    assert training_utilization["cpuutilization_metric_status"] == "usable"

    notebook_utilization = adapter._get_sagemaker_notebook_utilization(
        "notebook-a", start, end, "us-east-1"
    )
    assert notebook_utilization["cpu_metric_status"] == "unavailable"
    assert notebook_utilization["cpu_metric_unavailable_reason"] == "no_candidates"
    assert notebook_utilization["gpu_metric_unavailable_reason"] == "no_candidates"

    endpoint = adapter._get_sagemaker_endpoints("", "us-east-1")[0]
    adapter.get_resources = lambda resource_type, filters=None, region=None: [endpoint]
    findings = check_sagemaker_endpoint_gpu_underutilized(
        adapter,
        lookback_days=1,
        gpu_threshold=5.0,
        gpu_memory_threshold=5.0,
        region="us-east-1",
    )
    assert len(findings) == 1
    evidence = findings[0]["metadata"]
    assert evidence["gpu_p95_per_device"] == 2.0
    assert evidence["gpu_memory_p95_per_device"] == 2.0
    assert evidence["gpu_aggregation"] == "mean_of_sum"
