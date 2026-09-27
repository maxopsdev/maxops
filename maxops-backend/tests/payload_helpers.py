"""Helpers for payload-backed offline tests."""

from __future__ import annotations

import json
import re
from importlib import import_module
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple

from botocore.exceptions import ClientError


REPO_ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_ROOT = REPO_ROOT / "tests" / "payloads"
GENERATOR_ROOT = REPO_ROOT / "tests_generator"


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def get_scenario_dir(service: str, check_id: str, scenario: str) -> Path:
    return PAYLOAD_ROOT / service / check_id / scenario


def load_scenario_payloads(service: str, check_id: str, scenario: str) -> Dict[str, Dict[str, Any]]:
    scenario_dir = get_scenario_dir(service, check_id, scenario)
    payloads: Dict[str, Dict[str, Any]] = {}
    for path in sorted(scenario_dir.glob("*.json")):
        payloads[path.name] = load_json(path)
    return payloads


def load_generator_config(service: str, filename: str) -> Dict[str, Any]:
    return load_json(GENERATOR_ROOT / service / filename)


def load_resource_config(service: str) -> Dict[str, Any]:
    return load_generator_config(service, "resource_config.json")


def load_check_manifest(service: str) -> Dict[str, Any]:
    return load_generator_config(service, "checks.json")


def load_action_manifest(service: str) -> Dict[str, Any]:
    return load_generator_config(service, "actions.json")


def load_action_payloads(service: str, action_key: str, scenario: str) -> Dict[str, Dict[str, Any]]:
    scenario_dir = PAYLOAD_ROOT / service / "actions" / action_key / scenario
    return {
        path.name: load_json(path)
        for path in sorted(scenario_dir.glob("*.json"))
    }


def load_payload_check_map() -> Dict[str, Dict[str, Any]]:
    return load_json(GENERATOR_ROOT / "payload_check_map.json")


def import_mapped_check_function(entry: Dict[str, Any]):
    module = import_module(entry["check_module"])
    check_function = getattr(module, entry["check_function"])
    if not callable(check_function):
        raise TypeError(f"{entry['check_module']}.{entry['check_function']} is not callable")
    return check_function


def replace_dynamic_values(obj: Any, replacements: Dict[str, str]) -> Any:
    """Recursively replace actual capture-time values with placeholders."""
    if isinstance(obj, dict):
        return {key: replace_dynamic_values(value, replacements) for key, value in obj.items()}
    if isinstance(obj, list):
        return [replace_dynamic_values(item, replacements) for item in obj]
    if isinstance(obj, str):
        updated = obj
        for actual, placeholder in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
            updated = updated.replace(actual, placeholder)
        return updated
    return obj


def collect_strings(obj: Any) -> List[str]:
    values: List[str] = []
    if isinstance(obj, dict):
        for value in obj.values():
            values.extend(collect_strings(value))
    elif isinstance(obj, list):
        for value in obj:
            values.extend(collect_strings(value))
    elif isinstance(obj, str):
        values.append(obj)
    return values


def find_real_aws_identifiers(obj: Any) -> List[str]:
    """Return AWS-style identifiers that should not appear in sanitized fixtures."""
    patterns = [
        re.compile(r"\bi-[0-9a-f]{8,17}\b"),
        re.compile(r"\bvpc-[0-9a-f]{8,17}\b"),
        re.compile(r"\bsubnet-[0-9a-f]{8,17}\b"),
        re.compile(r"\bsg-[0-9a-f]{8,17}\b"),
        re.compile(r"\bami-[0-9a-f]{8,17}\b"),
        re.compile(r"\bmaxops-payload-dynamodb-[a-z0-9-]+-[0-9a-f]{6}-\d+\b"),
    ]
    matches: List[str] = []
    for value in collect_strings(obj):
        for pattern in patterns:
            matches.extend(pattern.findall(value))
    return sorted(set(matches))


def find_forbidden_strings(obj: Any, forbidden_values: Iterable[str]) -> List[str]:
    """Return configured forbidden strings that still appear anywhere in the payload object."""
    string_values = collect_strings(obj)
    leaks: List[str] = []
    for forbidden in forbidden_values:
        if forbidden and any(forbidden in value for value in string_values):
            leaks.append(forbidden)
    return sorted(set(leaks))


class StaticEC2Client:
    """Minimal EC2 client stub keyed by instance state filter or instance id."""

    def __init__(self, responses: Dict[str, Dict[str, Any]]):
        self._responses = responses

    def _inflate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        inflated = deepcopy(payload)
        for reservation in inflated.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                launch_time = instance.get("LaunchTime")
                if isinstance(launch_time, str):
                    instance["LaunchTime"] = datetime.fromisoformat(launch_time.replace("Z", "+00:00"))
        return inflated

    def describe_instances(
        self,
        Filters: Optional[List[Dict[str, Any]]] = None,
        InstanceIds: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if InstanceIds:
            key = f"instance_ids:{','.join(InstanceIds)}"
        else:
            state = None
            for item in Filters or []:
                if item.get("Name") == "instance-state-name":
                    values = item.get("Values") or []
                    state = values[0] if values else None
                    break
            key = f"state:{state or 'all'}"
        if key not in self._responses:
            raise AssertionError(f"Unexpected describe_instances key: {key}")
        return self._inflate(self._responses[key])

    def describe_volumes(self, VolumeIds: Optional[List[str]] = None, Filters: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        if "describe_volumes" not in self._responses:
            raise AssertionError("Unexpected EC2 request: describe_volumes")
        response = deepcopy(self._responses["describe_volumes"])
        if VolumeIds:
            wanted = set(VolumeIds)
            response["Volumes"] = [volume for volume in response.get("Volumes", []) if volume.get("VolumeId") in wanted]
        return response


class StaticCloudWatchClient:
    """Minimal CloudWatch client stub keyed by namespace and resource id."""

    def __init__(
        self,
        metric_data_responses: Dict[Tuple[str, str], Dict[str, Any]],
        metric_statistics_responses: Optional[Dict[Tuple[str, str, str], Dict[str, Any]]] = None,
        list_metrics_response: Optional[Dict[str, Any]] = None,
        default_metric_data_response: Optional[Dict[str, Any]] = None,
    ):
        self._metric_data_responses = metric_data_responses
        self._metric_statistics_responses = metric_statistics_responses or {}
        self._list_metrics_response = deepcopy(list_metrics_response or {"Metrics": []})
        self._default_metric_data_response = deepcopy(default_metric_data_response)

    def list_metrics(self, **kwargs: Any) -> Dict[str, Any]:
        """Replay the captured ListMetrics discovery response."""
        return deepcopy(self._list_metrics_response)

    def get_metric_statistics(self, **kwargs: Any) -> Dict[str, Any]:
        namespace = kwargs.get("Namespace", "__missing__")
        metric_name = kwargs.get("MetricName", "__missing__")
        dimensions = kwargs.get("Dimensions") or []
        resource_id = dimensions[0]["Value"] if dimensions else "__missing__"
        if namespace == "AWS/DynamoDB" and len(dimensions) > 1:
            by_name = {dimension.get("Name"): dimension.get("Value") for dimension in dimensions}
            table_name = by_name.get("TableName")
            index_name = by_name.get("GlobalSecondaryIndexName")
            if table_name and index_name:
                resource_id = f"{table_name}/{index_name}"
        key = (namespace, metric_name, resource_id)
        if key not in self._metric_statistics_responses:
            raise AssertionError(f"Unexpected CloudWatch get_metric_statistics request: {key}")
        return deepcopy(self._metric_statistics_responses[key])

    def get_metric_data(self, **kwargs: Any) -> Dict[str, Any]:
        queries = kwargs.get("MetricDataQueries") or []
        if not queries:
            raise AssertionError("Expected MetricDataQueries in get_metric_data request")
        first_query = queries[0]
        metric = first_query.get("MetricStat", {}).get("Metric", {})
        namespace = metric.get("Namespace", "__missing__")
        dimensions = metric.get("Dimensions") or []
        resource_id = dimensions[0]["Value"] if dimensions else "__missing__"
        key = (namespace, resource_id)
        if key not in self._metric_data_responses:
            if self._default_metric_data_response is not None:
                return deepcopy(self._default_metric_data_response)
            raise AssertionError(f"Unexpected CloudWatch get_metric_data request: {key}")
        return deepcopy(self._metric_data_responses[key])


class StaticCloudWatchInventoryClient:
    """CloudWatch inventory and alarm-history responses for check fixtures."""

    def __init__(
        self,
        alarms: Dict[str, Any],
        histories: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        self._alarms = deepcopy(alarms)
        self._histories = deepcopy(histories or {})

    def describe_alarms(self, **kwargs: Any) -> Dict[str, Any]:
        return deepcopy(self._alarms)

    def describe_alarm_history(self, AlarmName: str, **kwargs: Any) -> Dict[str, Any]:
        if AlarmName not in self._histories:
            raise AssertionError(f"Unexpected alarm history request: {AlarmName}")
        return deepcopy(self._histories[AlarmName])


class StaticLogsClient:
    def __init__(self, log_groups: Dict[str, Any]):
        self._log_groups = deepcopy(log_groups)

    def describe_log_groups(self, **kwargs: Any) -> Dict[str, Any]:
        return deepcopy(self._log_groups)


class StaticVPCClient:
    def __init__(self, vpcs: Dict[str, Any], endpoints: Dict[str, Any], flow_logs: Dict[str, Any]):
        self._vpcs = deepcopy(vpcs)
        self._endpoints = deepcopy(endpoints)
        self._flow_logs = deepcopy(flow_logs)

    def describe_vpcs(self, **kwargs: Any) -> Dict[str, Any]:
        return deepcopy(self._vpcs)

    def describe_vpc_endpoints(self, **kwargs: Any) -> Dict[str, Any]:
        return deepcopy(self._endpoints)

    def describe_flow_logs(self, **kwargs: Any) -> Dict[str, Any]:
        return deepcopy(self._flow_logs)


class StaticS3Client:
    """Minimal S3 client stub keyed by operation name and bucket."""

    def __init__(self, responses: Dict[str, Dict[str, Any]], default_region: str = "us-east-1"):
        self._responses = deepcopy(responses)
        self._default_region = default_region

    def _response(self, key: str, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if key in self._responses:
            return deepcopy(self._responses[key])
        if default is not None:
            return deepcopy(default)
        raise AssertionError(f"Unexpected S3 request: {key}")

    def list_buckets(self) -> Dict[str, Any]:
        return self._response("list_buckets")

    def get_bucket_location(self, Bucket: str) -> Dict[str, Any]:
        return self._response(
            f"get_bucket_location:{Bucket}",
            {"LocationConstraint": None if self._default_region == "us-east-1" else self._default_region},
        )

    def get_bucket_tagging(self, Bucket: str) -> Dict[str, Any]:
        return self._response(f"get_bucket_tagging:{Bucket}", {"TagSet": []})

    def get_bucket_lifecycle_configuration(self, Bucket: str) -> Dict[str, Any]:
        return self._response(f"get_bucket_lifecycle_configuration:{Bucket}", {})

    def get_bucket_versioning(self, Bucket: str) -> Dict[str, Any]:
        return self._response(f"get_bucket_versioning:{Bucket}", {})

    def get_bucket_logging(self, Bucket: str) -> Dict[str, Any]:
        return self._response(f"get_bucket_logging:{Bucket}", {})

    def get_bucket_replication(self, Bucket: str) -> Dict[str, Any]:
        return self._response(f"get_bucket_replication:{Bucket}", {})

    def list_bucket_inventory_configurations(self, Bucket: str) -> Dict[str, Any]:
        return self._response(f"list_bucket_inventory_configurations:{Bucket}", {})


class StaticRDSClient:
    """Minimal RDS client stub keyed by operation name and resource."""

    def __init__(self, responses: Dict[str, Dict[str, Any]]):
        self._responses = deepcopy(responses)

    def describe_db_instances(self, DBInstanceIdentifier: Optional[str] = None) -> Dict[str, Any]:
        if DBInstanceIdentifier:
            key = f"describe_db_instances:{DBInstanceIdentifier}"
            if key in self._responses:
                return deepcopy(self._responses[key])
        return deepcopy(self._responses["describe_db_instances"])

    def list_tags_for_resource(self, ResourceName: str) -> Dict[str, Any]:
        key = f"list_tags_for_resource:{ResourceName}"
        if key not in self._responses:
            raise AssertionError(f"Unexpected RDS request: {key}")
        return deepcopy(self._responses[key])


class StaticElastiCacheClient:
    """Minimal ElastiCache client stub keyed by operation name and resource."""

    def __init__(self, responses: Dict[str, Dict[str, Any]]):
        self._responses = deepcopy(responses)

    def describe_cache_clusters(
        self,
        CacheClusterId: Optional[str] = None,
        ShowCacheNodeInfo: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if CacheClusterId:
            key = f"describe_cache_clusters:{CacheClusterId}"
            if key in self._responses:
                return deepcopy(self._responses[key])
        if "describe_cache_clusters" not in self._responses:
            raise AssertionError("Unexpected ElastiCache request: describe_cache_clusters")
        return deepcopy(self._responses["describe_cache_clusters"])

    def describe_replication_groups(self, ReplicationGroupId: Optional[str] = None) -> Dict[str, Any]:
        if ReplicationGroupId:
            key = f"describe_replication_groups:{ReplicationGroupId}"
            if key in self._responses:
                return deepcopy(self._responses[key])
        if "describe_replication_groups" not in self._responses:
            raise AssertionError("Unexpected ElastiCache request: describe_replication_groups")
        return deepcopy(self._responses["describe_replication_groups"])


class StaticDynamoDBClient:
    """Minimal DynamoDB client stub keyed by operation name and resource."""

    def __init__(self, responses: Dict[str, Dict[str, Any]]):
        self._responses = deepcopy(responses)

    def list_tables(self) -> Dict[str, Any]:
        if "list_tables" not in self._responses:
            raise AssertionError("Unexpected DynamoDB request: list_tables")
        return deepcopy(self._responses["list_tables"])

    def describe_table(self, TableName: str) -> Dict[str, Any]:
        key = f"describe_table:{TableName}"
        if key not in self._responses:
            raise AssertionError(f"Unexpected DynamoDB request: {key}")
        return deepcopy(self._responses[key])

    def list_tags_of_resource(self, ResourceArn: str) -> Dict[str, Any]:
        key = f"list_tags_of_resource:{ResourceArn}"
        if key not in self._responses:
            raise AssertionError(f"Unexpected DynamoDB request: {key}")
        return deepcopy(self._responses[key])


class StaticSageMakerClient:
    """Offline SageMaker client replaying captured operation-shaped responses."""

    def __init__(self, responses: Dict[str, Dict[str, Any]]):
        self._responses = deepcopy(responses)

    def _response(self, operation: str, **identifiers: Any) -> Dict[str, Any]:
        suffix = next((str(value) for value in identifiers.values() if value), "")
        keys = [f"{operation}:{suffix}", operation]
        for key in keys:
            if key in self._responses:
                return deepcopy(self._responses[key])
        raise AssertionError(f"Unexpected SageMaker request: {operation} {identifiers}")

    def list_notebook_instances(self, **kwargs: Any) -> Dict[str, Any]:
        return self._response("list_notebook_instances")

    def describe_notebook_instance(self, NotebookInstanceName: str) -> Dict[str, Any]:
        return self._response("describe_notebook_instance", name=NotebookInstanceName)

    def describe_notebook_instance_lifecycle_config(self, NotebookInstanceLifecycleConfigName: str) -> Dict[str, Any]:
        return self._response("describe_notebook_instance_lifecycle_config", name=NotebookInstanceLifecycleConfigName)

    def list_endpoints(self, **kwargs: Any) -> Dict[str, Any]:
        return self._response("list_endpoints")

    def describe_endpoint(self, EndpointName: str) -> Dict[str, Any]:
        return self._response("describe_endpoint", name=EndpointName)

    def describe_endpoint_config(self, EndpointConfigName: str) -> Dict[str, Any]:
        return self._response("describe_endpoint_config", name=EndpointConfigName)

    def list_training_jobs(self, **kwargs: Any) -> Dict[str, Any]:
        return self._response("list_training_jobs")

    def describe_training_job(self, TrainingJobName: str) -> Dict[str, Any]:
        return self._response("describe_training_job", name=TrainingJobName)

    def stop_notebook_instance(self, NotebookInstanceName: str) -> Dict[str, Any]:
        return self._response("stop_notebook_instance", name=NotebookInstanceName)

    def delete_endpoint(self, EndpointName: str) -> Dict[str, Any]:
        return self._response("delete_endpoint", name=EndpointName)


class StaticApplicationAutoScalingClient:
    """Offline application-autoscaling response for endpoint discovery."""

    def __init__(self, responses: Dict[str, Dict[str, Any]]):
        self._responses = deepcopy(responses)

    def describe_scaling_policies(self, **kwargs: Any) -> Dict[str, Any]:
        return deepcopy(self._responses.get("describe_scaling_policies", {"ScalingPolicies": []}))


class StrictActionReplay:
    """Replay captured AWS action calls in order and assert exact kwargs."""

    def __init__(self, calls: List[Dict[str, Any]], payloads: Dict[str, Dict[str, Any]]):
        self._calls = deepcopy(calls)
        self._payloads = deepcopy(payloads)
        self._position = 0

    def client(self, service_name: str):
        return _StrictActionClient(service_name, self)

    def invoke(self, service_name: str, operation: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        if self._position >= len(self._calls):
            raise AssertionError(f"Unexpected extra AWS action call: {service_name}.{operation}({kwargs!r})")
        expected = self._calls[self._position]
        actual_identity = (service_name, operation)
        expected_identity = (expected["service"], expected["operation"])
        if actual_identity != expected_identity:
            raise AssertionError(
                f"AWS action call {self._position + 1} expected {expected_identity}, got {actual_identity}"
            )
        if not action_values_match(expected.get("kwargs", {}), kwargs):
            raise AssertionError(
                f"AWS action call {service_name}.{operation} kwargs mismatch: "
                f"expected {expected.get('kwargs', {})!r}, got {kwargs!r}"
            )
        self._position += 1
        response_file = expected["response_file"]
        if response_file not in self._payloads:
            raise AssertionError(f"Missing captured action response file: {response_file}")
        payload = deepcopy(self._payloads[response_file])
        if expected.get("raises_client_error"):
            raise ClientError(payload, operation)
        return payload

    def assert_complete(self) -> None:
        if self._position != len(self._calls):
            remaining = [
                f"{call['service']}.{call['operation']}"
                for call in self._calls[self._position:]
            ]
            raise AssertionError(f"Expected AWS action calls were not made: {remaining}")


class _StrictActionClient:
    def __init__(self, service_name: str, replay: StrictActionReplay):
        self._service_name = service_name
        self._replay = replay

    def __getattr__(self, operation: str):
        def invoke(**kwargs: Any) -> Dict[str, Any]:
            return self._replay.invoke(self._service_name, operation, kwargs)

        return invoke

    def get_waiter(self, waiter_name: str):
        return _NoOpWaiter()

    def get_paginator(self, operation_name: str):
        return _StrictActionPaginator(self._service_name, operation_name, self._replay)


class _NoOpWaiter:
    def wait(self, **kwargs: Any) -> None:
        return None


class _StrictActionPaginator:
    def __init__(self, service_name: str, operation: str, replay: StrictActionReplay):
        self._service_name = service_name
        self._operation = operation
        self._replay = replay

    def paginate(self, **kwargs: Any):
        payload = self._replay.invoke(self._service_name, self._operation, kwargs)
        return iter(payload.get("Pages", []))


class StaticActionSession:
    def __init__(self, replay: StrictActionReplay):
        self._replay = replay
        self._clients: Dict[str, _StrictActionClient] = {}

    def client(self, service_name: str, region_name: Optional[str] = None, endpoint_url: Optional[str] = None):
        if service_name not in self._clients:
            self._clients[service_name] = self._replay.client(service_name)
        return self._clients[service_name]


class StaticSession:
    """Session stub that serves per-service clients."""

    def __init__(self, clients: Dict[str, Any]):
        self._clients = dict(clients)

    def client(self, service_name: str, region_name: Optional[str] = None, endpoint_url: Optional[str] = None):
        if service_name not in self._clients:
            raise AssertionError(f"Unexpected service client request: {service_name}")
        return self._clients[service_name]


class FakeEC2CheckAdapter:
    """Small adapter implementation for pure check logic tests."""

    def __init__(self, resources_by_state: Dict[str, List[Dict[str, Any]]], utilization: Dict[str, Dict[str, Any]]):
        self._resources_by_state = deepcopy(resources_by_state)
        self._utilization = deepcopy(utilization)

    def get_resources(self, resource_type: str, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None):
        if resource_type != "ec2":
            raise AssertionError(f"Unsupported resource_type for fake adapter: {resource_type}")
        state = (filters or {}).get("state", "all")
        return deepcopy(self._resources_by_state.get(state, self._resources_by_state.get("all", [])))

    def get_resource_utilization(self, resource_id: str, resource_type: str, start_date, end_date, region: Optional[str] = None):
        if resource_type != "ec2":
            raise AssertionError(f"Unsupported resource_type for fake adapter: {resource_type}")
        return deepcopy(self._utilization.get(resource_id, {}))

    def get_resource_tags(self, resource_id: str, resource_type: str):
        return {}


def build_ec2_payload_adapter(check_id: str, scenario: str, default_region: str = "us-east-1"):
    """Build an AWSAdapter instance backed by saved EC2 payload fixtures."""
    from app.adapters.aws.adapter import AWSAdapter

    payloads = load_scenario_payloads("ec2", check_id, scenario)
    metadata = payloads.get("capture_metadata.json", {})
    describe_payload = payloads["describe_instances.json"]
    describe_call = next(
        (
            call
            for call in metadata.get("calls", [])
            if call.get("service") == "ec2" and call.get("operation") == "describe_instances"
        ),
        {},
    )
    state_values = (
        describe_call.get("kwargs", {})
        .get("Filters", [{}])[0]
        .get("Values", [])
    )
    state = state_values[0] if state_values else "all"
    ec2_client = StaticEC2Client({f"state:{state}": describe_payload})

    cloudwatch_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    instances = [
        instance
        for reservation in describe_payload.get("Reservations", [])
        for instance in reservation.get("Instances", [])
    ]
    if instances:
        instance_id = instances[0]["InstanceId"]
        if "get_metric_data_ec2.json" in payloads:
            cloudwatch_map[("AWS/EC2", instance_id)] = payloads["get_metric_data_ec2.json"]
        if "get_metric_data_memory.json" in payloads:
            cloudwatch_map[("CWAgent", instance_id)] = payloads["get_metric_data_memory.json"]

    cloudwatch_client = StaticCloudWatchClient(cloudwatch_map)
    session = StaticSession({"ec2": ec2_client, "cloudwatch": cloudwatch_client})

    adapter = AWSAdapter.__new__(AWSAdapter)
    adapter._default_region = default_region
    adapter.session = session
    adapter.ec2_client = ec2_client
    adapter.cloudwatch_client = cloudwatch_client
    return adapter


def build_sagemaker_payload_adapter(check_id: str, scenario: str, default_region: str = "us-east-1"):
    """Build an AWSAdapter backed by captured SageMaker payload files."""
    from app.adapters.aws.adapter import AWSAdapter

    payloads = load_scenario_payloads("sagemaker", check_id, scenario)
    if not payloads:
        raise FileNotFoundError(f"SageMaker payload fixtures are not captured: {check_id}/{scenario}")
    responses: Dict[str, Dict[str, Any]] = {}
    for filename, payload in payloads.items():
        if filename == "capture_metadata.json" or not filename.endswith(".json"):
            continue
        operation = filename.split("__", 1)[0].removesuffix(".json")
        responses.setdefault(operation, payload)
    sagemaker_client = StaticSageMakerClient(responses)
    autoscaling_client = StaticApplicationAutoScalingClient(responses)
    metric_payloads = [
        payload for filename, payload in payloads.items()
        if filename.startswith("get_metric_data") and filename.endswith(".json")
    ]
    list_metrics_payload = next(
        (payload for filename, payload in payloads.items() if filename.startswith("list_metrics") and filename.endswith(".json")),
        {"Metrics": []},
    )
    cloudwatch_client = StaticCloudWatchClient(
        {},
        list_metrics_response=list_metrics_payload,
        default_metric_data_response=metric_payloads[0] if metric_payloads else None,
    )
    session = StaticSession({
        "sagemaker": sagemaker_client,
        "application-autoscaling": autoscaling_client,
        "cloudwatch": cloudwatch_client,
    })
    adapter = AWSAdapter.__new__(AWSAdapter)
    adapter._default_region = default_region
    adapter.session = session
    adapter.cloudwatch_client = cloudwatch_client
    adapter._ec2_metric_discovery_cache = {}
    return adapter


def action_values_match(expected: Any, actual: Any) -> bool:
    """Compare captured values, allowing explicit dynamic fixture tokens."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(
            action_values_match(expected[key], actual[key])
            for key in expected
        )
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(
            action_values_match(expected_item, actual_item)
            for expected_item, actual_item in zip(expected, actual)
        )
    if isinstance(expected, str) and isinstance(actual, str) and "{{ANY_INT}}" in expected:
        if expected == actual:
            return True
        pattern = re.escape(expected).replace(re.escape("{{ANY_INT}}"), r"\d+")
        return re.fullmatch(pattern, actual) is not None
    return expected == actual


def build_action_payload_adapter(service: str, action_key: str, scenario: str = "success"):
    """Build an adapter-like object and strict replay for a captured action."""
    payloads = load_action_payloads(service, action_key, scenario)
    metadata = payloads.get("capture_metadata.json")
    if not metadata:
        raise FileNotFoundError(
            f"Missing {service} action metadata for {action_key}/{scenario}"
        )
    replay = StrictActionReplay(metadata.get("calls", []), payloads)
    adapter = SimpleNamespace(session=StaticActionSession(replay))
    return adapter, replay, metadata


def build_dynamodb_action_payload_adapter(action_key: str, scenario: str = "success"):
    """Backward-compatible DynamoDB action payload adapter."""
    return build_action_payload_adapter("dynamodb", action_key, scenario)


def build_s3_payload_adapter(check_id: str, scenario: str, default_region: str = "us-east-1"):
    """Build an AWSAdapter instance backed by saved S3 payload fixtures."""
    from app.adapters.aws.adapter import AWSAdapter

    payloads = load_scenario_payloads("s3", check_id, scenario)
    metadata = payloads.get("capture_metadata.json", {})
    bucket_placeholders = metadata.get("bucket_placeholders", {})

    responses: Dict[str, Dict[str, Any]] = {
        "list_buckets": payloads["list_buckets.json"],
    }
    for alias, bucket_name in bucket_placeholders.items():
        for operation in (
            "get_bucket_location",
            "get_bucket_tagging",
            "get_bucket_lifecycle_configuration",
            "get_bucket_versioning",
            "get_bucket_logging",
            "get_bucket_replication",
            "list_bucket_inventory_configurations",
        ):
            filename = f"{operation}__{alias}.json"
            if filename in payloads:
                responses[f"{operation}:{bucket_name}"] = payloads[filename]

    s3_client = StaticS3Client(responses, default_region=default_region)
    session = StaticSession({"s3": s3_client})

    adapter = AWSAdapter.__new__(AWSAdapter)
    adapter._default_region = default_region
    adapter.session = session
    adapter.ec2_client = None
    adapter.cloudwatch_client = None
    return adapter


def build_rds_payload_adapter(check_id: str, scenario: str, default_region: str = "us-east-1"):
    """Build an AWSAdapter instance backed by saved RDS payload fixtures."""
    from app.adapters.aws.adapter import AWSAdapter

    payloads = load_scenario_payloads("rds", check_id, scenario)
    describe_payload = payloads["describe_db_instances.json"]
    metadata = payloads.get("capture_metadata.json", {})
    db_placeholders = metadata.get("db_placeholders", {})

    rds_responses: Dict[str, Dict[str, Any]] = {
        "describe_db_instances": describe_payload,
    }
    metric_statistics_map: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    db_instances = describe_payload.get("DBInstances", [])
    by_identifier = {instance["DBInstanceIdentifier"]: instance for instance in db_instances}
    for alias, placeholder_identifier in db_placeholders.items():
        instance = by_identifier[placeholder_identifier]
        arn = instance["DBInstanceArn"]
        tags_filename = f"list_tags_for_resource__{alias}.json"
        if tags_filename in payloads:
            rds_responses[f"list_tags_for_resource:{arn}"] = payloads[tags_filename]
        for metric_name in ("CPUUtilization", "DatabaseConnections", "ReadIOPS", "WriteIOPS"):
            filename = f"get_metric_statistics__{alias}__{metric_name.lower()}.json"
            if filename in payloads:
                metric_statistics_map[("AWS/RDS", metric_name, placeholder_identifier)] = payloads[filename]

    rds_client = StaticRDSClient(rds_responses)
    cloudwatch_client = StaticCloudWatchClient({}, metric_statistics_map)
    session = StaticSession({"rds": rds_client, "cloudwatch": cloudwatch_client})

    adapter = AWSAdapter.__new__(AWSAdapter)
    adapter._default_region = default_region
    adapter.session = session
    adapter.ec2_client = None
    adapter.cloudwatch_client = cloudwatch_client
    return adapter


def build_payload_adapter(service: str, check_id: str, scenario: str, default_region: str = "us-east-1"):
    """Build the service-specific static AWSAdapter for a saved payload scenario."""
    builders = {
        "ec2": build_ec2_payload_adapter,
        "s3": build_s3_payload_adapter,
        "rds": build_rds_payload_adapter,
        "elasticache": build_elasticache_payload_adapter,
        "dynamodb": build_dynamodb_payload_adapter,
        "ebs": build_ebs_payload_adapter,
        "cloudwatch": build_cloudwatch_payload_adapter,
        "vpc": build_vpc_payload_adapter,
    }
    try:
        builder = builders[service]
    except KeyError as exc:
        raise ValueError(f"Unsupported payload-backed service: {service}") from exc
    return builder(check_id, scenario, default_region=default_region)


def build_cloudwatch_payload_adapter(
    check_id: str,
    scenario: str,
    default_region: str = "us-east-1",
):
    """Build an AWSAdapter backed by CloudWatch inventory payloads."""
    from app.adapters.aws.adapter import AWSAdapter

    payloads = load_scenario_payloads("cloudwatch", check_id, scenario)
    metadata = payloads["capture_metadata.json"]
    alarm_payload = payloads.get(
        "describe_alarms.json",
        {"MetricAlarms": [], "CompositeAlarms": []},
    )
    histories = {}
    for alias, resource_id in metadata.get("resource_placeholders", {}).items():
        filename = f"describe_alarm_history__{alias}.json"
        if filename in payloads:
            histories[resource_id] = payloads[filename]
    cloudwatch_client = StaticCloudWatchInventoryClient(alarm_payload, histories)
    logs_client = StaticLogsClient(
        payloads.get("describe_log_groups.json", {"logGroups": []})
    )
    session = StaticSession(
        {"cloudwatch": cloudwatch_client, "logs": logs_client}
    )
    adapter = AWSAdapter.__new__(AWSAdapter)
    adapter._default_region = default_region
    adapter.session = session
    adapter.cloudwatch_client = cloudwatch_client
    adapter.ec2_client = None
    return adapter


def build_vpc_payload_adapter(
    check_id: str,
    scenario: str,
    default_region: str = "us-east-1",
):
    """Build an AWSAdapter backed by VPC inventory payloads."""
    from app.adapters.aws.adapter import AWSAdapter

    payloads = load_scenario_payloads("vpc", check_id, scenario)
    ec2_client = StaticVPCClient(
        payloads.get("describe_vpcs.json", {"Vpcs": []}),
        payloads.get("describe_vpc_endpoints.json", {"VpcEndpoints": []}),
        payloads.get("describe_flow_logs.json", {"FlowLogs": []}),
    )
    session = StaticSession({"ec2": ec2_client})
    adapter = AWSAdapter.__new__(AWSAdapter)
    adapter._default_region = default_region
    adapter.session = session
    adapter.ec2_client = ec2_client
    adapter.cloudwatch_client = None
    return adapter


def build_elasticache_payload_adapter(check_id: str, scenario: str, default_region: str = "us-east-1"):
    """Build an AWSAdapter instance backed by saved ElastiCache payload fixtures."""
    from app.adapters.aws.adapter import AWSAdapter

    payloads = load_scenario_payloads("elasticache", check_id, scenario)
    metadata = payloads.get("capture_metadata.json", {})
    resource_placeholders = metadata.get("resource_placeholders", {})
    resource_types = metadata.get("resource_types", {})

    elasticache_responses: Dict[str, Dict[str, Any]] = {}
    if "describe_cache_clusters.json" in payloads:
        cluster_payload = payloads["describe_cache_clusters.json"]
        elasticache_responses["describe_cache_clusters"] = cluster_payload
        for cluster in cluster_payload.get("CacheClusters", []):
            cluster_id = cluster.get("CacheClusterId")
            if cluster_id:
                elasticache_responses[f"describe_cache_clusters:{cluster_id}"] = {
                    "CacheClusters": [deepcopy(cluster)],
                    "ResponseMetadata": deepcopy(
                        cluster_payload.get("ResponseMetadata", {})
                    ),
                }
    if "describe_replication_groups.json" in payloads:
        elasticache_responses["describe_replication_groups"] = payloads["describe_replication_groups.json"]

    metric_statistics_map: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for alias, resource_id in resource_placeholders.items():
        if resource_types.get(alias) == "elasticache_cluster" and "describe_cache_clusters.json" in payloads:
            cluster_payload = payloads["describe_cache_clusters.json"]
            clusters = [
                deepcopy(cluster)
                for cluster in cluster_payload.get("CacheClusters", [])
                if cluster.get("CacheClusterId") == resource_id
            ]
            if clusters:
                elasticache_responses[f"describe_cache_clusters:{resource_id}"] = {
                    "CacheClusters": clusters,
                    "ResponseMetadata": deepcopy(cluster_payload.get("ResponseMetadata", {})),
                }
        if resource_types.get(alias) == "elasticache_replication_group" and "describe_replication_groups.json" in payloads:
            rg_payload = payloads["describe_replication_groups.json"]
            groups = [
                deepcopy(group)
                for group in rg_payload.get("ReplicationGroups", [])
                if group.get("ReplicationGroupId") == resource_id
            ]
            if groups:
                elasticache_responses[f"describe_replication_groups:{resource_id}"] = {
                    "ReplicationGroups": groups,
                    "ResponseMetadata": deepcopy(rg_payload.get("ResponseMetadata", {})),
                }
        for metric_name in ("CurrItems", "KeyCount"):
            filename = f"get_metric_statistics__{alias}__{metric_name.lower()}.json"
            if filename in payloads:
                metric_statistics_map[("AWS/ElastiCache", metric_name, resource_id)] = payloads[filename]

    elasticache_client = StaticElastiCacheClient(elasticache_responses)
    cloudwatch_client = StaticCloudWatchClient({}, metric_statistics_map)
    session = StaticSession({"elasticache": elasticache_client, "cloudwatch": cloudwatch_client})

    adapter = AWSAdapter.__new__(AWSAdapter)
    adapter._default_region = default_region
    adapter.session = session
    adapter.ec2_client = None
    adapter.cloudwatch_client = cloudwatch_client
    return adapter


def build_dynamodb_payload_adapter(check_id: str, scenario: str, default_region: str = "us-east-1"):
    """Build an AWSAdapter instance backed by saved DynamoDB payload fixtures."""
    from app.adapters.aws.adapter import AWSAdapter

    payloads = load_scenario_payloads("dynamodb", check_id, scenario)
    metadata = payloads.get("capture_metadata.json", {})
    table_placeholders = metadata.get("table_placeholders", {})
    gsi_placeholders = metadata.get("gsi_placeholders", {})

    dynamodb_responses: Dict[str, Dict[str, Any]] = {
        "list_tables": payloads["list_tables.json"],
    }
    metric_statistics_map: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    table_arns: Dict[str, str] = {}

    for alias, table_name in table_placeholders.items():
        describe_filename = f"describe_table__{alias}.json"
        if describe_filename in payloads:
            describe_payload = payloads[describe_filename]
            dynamodb_responses[f"describe_table:{table_name}"] = describe_payload
            table_arn = describe_payload.get("Table", {}).get("TableArn")
            if table_arn:
                table_arns[alias] = table_arn
        tags_filename = f"list_tags_of_resource__{alias}.json"
        if tags_filename in payloads and alias in table_arns:
            dynamodb_responses[f"list_tags_of_resource:{table_arns[alias]}"] = payloads[tags_filename]
        for metric_name in (
            "ConsumedReadCapacityUnits",
            "ConsumedWriteCapacityUnits",
            "ProvisionedReadCapacityUnits",
            "ProvisionedWriteCapacityUnits",
            "ReadThrottleEvents",
            "WriteThrottleEvents",
        ):
            filename = f"get_metric_statistics__{alias}__{metric_name.lower()}.json"
            if filename in payloads:
                metric_statistics_map[("AWS/DynamoDB", metric_name, table_name)] = payloads[filename]

    for gsi_ref, gsi_id in gsi_placeholders.items():
        alias, index_name = gsi_ref.split(":", 1)
        for metric_name in ("ConsumedReadCapacityUnits", "ConsumedWriteCapacityUnits"):
            filename = f"get_metric_statistics__{alias}__{index_name}__{metric_name.lower()}.json"
            if filename in payloads:
                metric_statistics_map[("AWS/DynamoDB", metric_name, gsi_id)] = payloads[filename]

    dynamodb_client = StaticDynamoDBClient(dynamodb_responses)
    cloudwatch_client = StaticCloudWatchClient({}, metric_statistics_map)
    session = StaticSession({"dynamodb": dynamodb_client, "cloudwatch": cloudwatch_client})

    adapter = AWSAdapter.__new__(AWSAdapter)
    adapter._default_region = default_region
    adapter.session = session
    adapter.ec2_client = None
    adapter.cloudwatch_client = cloudwatch_client
    return adapter


def build_ebs_payload_adapter(check_id: str, scenario: str, default_region: str = "us-east-1"):
    """Build an AWSAdapter instance backed by saved EBS payload fixtures."""
    from app.adapters.aws.adapter import AWSAdapter

    payloads = load_scenario_payloads("ebs", check_id, scenario)
    metadata = payloads.get("capture_metadata.json", {})
    volume_placeholders = metadata.get("volume_placeholders", {})

    ec2_responses: Dict[str, Dict[str, Any]] = {
        "describe_volumes": payloads["describe_volumes.json"],
    }
    metric_statistics_map: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for alias, volume_id in volume_placeholders.items():
        for metric_name in ("VolumeReadOps", "VolumeWriteOps", "VolumeReadBytes", "VolumeWriteBytes"):
            filename = f"get_metric_statistics__{alias}__{metric_name.lower()}.json"
            if filename in payloads:
                metric_statistics_map[("AWS/EBS", metric_name, volume_id)] = payloads[filename]

    ec2_client = StaticEC2Client(ec2_responses)
    cloudwatch_client = StaticCloudWatchClient({}, metric_statistics_map)
    session = StaticSession({"ec2": ec2_client, "cloudwatch": cloudwatch_client})

    adapter = AWSAdapter.__new__(AWSAdapter)
    adapter._default_region = default_region
    adapter.session = session
    adapter.ec2_client = ec2_client
    adapter.cloudwatch_client = cloudwatch_client
    return adapter
