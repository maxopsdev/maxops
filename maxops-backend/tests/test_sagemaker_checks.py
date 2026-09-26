"""Offline unit coverage for SageMaker V1 checks."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.actions import action_registry
from app.actions.base import ActionExecutionContext
from app.checks.sagemaker.common import normalize_sagemaker_gpu_value, resolve_sagemaker_instance_type
from app.checks.sagemaker.endpoint_gpu_underutilized import check_sagemaker_endpoint_gpu_underutilized
from app.checks.sagemaker.endpoint_idle import check_sagemaker_endpoint_idle
from app.checks.sagemaker.endpoint_overprovisioned import (
    check_sagemaker_endpoint_overprovisioned,
    endpoint_target_instance_count,
)
from app.checks.sagemaker.notebook_idle import check_sagemaker_notebook_idle
from app.checks.sagemaker.notebook_no_auto_stop import check_sagemaker_notebook_no_auto_stop
from app.checks.sagemaker.training_no_managed_spot import check_sagemaker_training_no_managed_spot
from app.schemas.check import CheckActionRequest


OLD = (datetime.now(timezone.utc) - timedelta(days=29)).isoformat()


class StubAdapter:
    """Small no-AWS adapter used by all SageMaker logic tests."""

    def __init__(self, resources, utilization=None, lifecycle=None):
        self.resources = resources
        self.utilization = utilization or {}
        self.lifecycle = lifecycle

    def get_resources(self, resource_type, filters=None, region=None):
        return [resource for resource in self.resources if resource["resource_type"] == resource_type]

    def get_resource_utilization(self, resource_id, resource_type, *args, **kwargs):
        return self.utilization.get(resource_id, {})

    def get_sagemaker_lifecycle_config(self, name, region=None):
        if isinstance(self.lifecycle, Exception):
            raise self.lifecycle
        return self.lifecycle or {}


def endpoint(name="endpoint", instance_count=1, instance_type="ml.m5.large", created_at=OLD):
    return {
        "resource_id": name,
        "resource_type": "sagemaker_endpoint",
        "status": "InService",
        "created_at": created_at,
        "metadata": {"endpoint_status": "InService"},
        "variants": [{"variant_name": "AllTraffic", "instance_type": instance_type, "instance_count": instance_count, "autoscaling_policy_present": False}],
    }


def notebook(name="notebook", instance_type="ml.t3.medium"):
    return {"resource_id": name, "resource_type": "sagemaker_notebook", "status": "InService", "instance_type": instance_type, "metadata": {"notebook_status": "InService", "instance_type": instance_type}}


def test_sagemaker_instance_resolution_and_summed_gpu_normalization():
    assert resolve_sagemaker_instance_type("ml.g5.xlarge")["is_gpu"] is True
    assert resolve_sagemaker_instance_type("ml.p3.2xlarge")["gpu_count"] == 1
    assert resolve_sagemaker_instance_type("ml.m5.large")["is_gpu"] is False
    assert resolve_sagemaker_instance_type("ml.zz9.large")["known"] is False
    assert normalize_sagemaker_gpu_value(240, 8) == 30
    assert normalize_sagemaker_gpu_value(240, None) is None


def test_endpoint_idle_requires_old_endpoint_and_usable_zero_metric():
    resource = endpoint()
    util = {"endpoint": {"variants": {"AllTraffic": {"invocations": {"metric_status": "usable", "total": 0, "sample_count": 14}}}}}
    adapter = StubAdapter([resource], {"endpoint": util["endpoint"]})
    assert len(check_sagemaker_endpoint_idle(adapter)) == 1
    young = endpoint(created_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    assert check_sagemaker_endpoint_idle(StubAdapter([young], {"endpoint": util["endpoint"]})) == []


def test_endpoint_serverless_variant_is_not_idle_candidate():
    resource = endpoint()
    resource["variants"][0]["serverless"] = True
    assert check_sagemaker_endpoint_idle(StubAdapter([resource], {"endpoint": {}})) == []


def test_overprovisioned_scaling_policy_and_formula():
    resource = endpoint(instance_count=4)
    util = {"variants": {"AllTraffic": {"invocations": {"metric_status": "usable", "total": 10}, "cpuutilization": {"metric_status": "usable", "metric_summary": {"p95": 10, "sample_count": 14}}}}}
    assert endpoint_target_instance_count(4, 10) == 1
    assert check_sagemaker_endpoint_overprovisioned(StubAdapter([resource], {"endpoint": util}))
    resource["variants"][0]["autoscaling_policy_present"] = True
    assert check_sagemaker_endpoint_overprovisioned(StubAdapter([resource], {"endpoint": util})) == []


def test_gpu_underutilized_requires_memory_gate_and_telemetry():
    resource = endpoint(instance_type="ml.g5.xlarge")
    util = {"variants": {"AllTraffic": {"invocations": {"metric_status": "usable", "total": 10}, "gpuutilization": {"metric_status": "usable", "metric_summary": {"p95": 2}}, "gpumemoryutilization": {"metric_status": "usable", "metric_summary": {"p95": 50}}}}}
    assert check_sagemaker_endpoint_gpu_underutilized(StubAdapter([resource], {"endpoint": util})) == []
    util["variants"]["AllTraffic"]["gpumemoryutilization"]["metric_summary"]["p95"] = 2
    assert len(check_sagemaker_endpoint_gpu_underutilized(StubAdapter([resource], {"endpoint": util}))) == 1
    util["variants"]["AllTraffic"]["gpuutilization"]["metric_status"] = "unavailable"
    assert check_sagemaker_endpoint_gpu_underutilized(StubAdapter([resource], {"endpoint": util})) == []


def test_no_auto_stop_flags_missing_config_and_accepts_autostop_script():
    missing = notebook()
    assert len(check_sagemaker_notebook_no_auto_stop(StubAdapter([missing]))) == 1
    encoded = base64.b64encode(b"# autostop notebook").decode()
    attached = notebook()
    attached["metadata"]["lifecycle_config_name"] = "auto"
    attached["metadata"]["lifecycle_on_start"] = [{"Content": encoded}]
    assert check_sagemaker_notebook_no_auto_stop(StubAdapter([attached])) == []
    assert check_sagemaker_notebook_no_auto_stop(StubAdapter([attached], lifecycle=RuntimeError("denied"))) == []


def test_gpu_notebook_without_agent_gpu_data_is_never_flagged_idle():
    gpu = notebook(instance_type="ml.g5.xlarge")
    util = {"cpu_metric_status": "usable", "metric_summary": {"cpuutilization": {"p95": 1, "maximum": 3}}, "gpu_metric_status": "unavailable", "gpu_metric_unavailable_reason": "no_candidates"}
    assert check_sagemaker_notebook_idle(StubAdapter([gpu], {"gpu": util})) == []


def test_cpu_notebook_idle_is_flagged():
    cpu = notebook()
    util = {"cpu_metric_status": "usable", "metric_summary": {"cpuutilization": {"p95": 1, "maximum": 3}}}
    assert len(check_sagemaker_notebook_idle(StubAdapter([cpu], {"notebook": util}))) == 1


def test_training_spot_groups_timestamped_family_and_requires_three_runs():
    def run(timestamp, spot=False):
        return {"resource_id": f"train-{timestamp}", "resource_type": "sagemaker_training_job", "status": "Completed", "metadata": {"training_job_status": "Completed", "creation_time": OLD, "enable_managed_spot_training": spot, "instance_type": "ml.m5.large", "instance_count": 1, "training_time_seconds": 60, "checkpoint_config_present": False}}
    three = [run("2026-09-01-1200"), run("2026-09-02-1200"), run("2026-09-03-1200")]
    assert len(check_sagemaker_training_no_managed_spot(StubAdapter(three))) == 1
    assert check_sagemaker_training_no_managed_spot(StubAdapter(three[:2])) == []
    assert check_sagemaker_training_no_managed_spot(StubAdapter(three[:2] + [run("2026-09-04-1200", True)])) == []


def test_sagemaker_actions_are_registered_and_do_not_need_check_object():
    assert action_registry.get_action("stop_notebook") is not None
    assert action_registry.get_action("delete_endpoint") is not None
