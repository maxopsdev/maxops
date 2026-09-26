from datetime import datetime, timedelta, timezone

import pytest

from app.adapters.aws.adapter import (
    AWSAdapter,
    _valid_non_negative_value,
    is_ec2_gpu_utilization_metric_name,
    is_ec2_gpu_memory_used_metric_name,
    is_ec2_memory_metric_name,
)
from app.checks.ec2.gpu_underutilized import check_ec2_gpu_underutilized
from app.checks.ec2.idle_instances import check_ec2_idle_instances
from app.checks.ec2.gpu_utils import busy_device_count
from app.utils.ec2_gpu_info import gpu_info_for_instance_type
from app.services.scan_service import EC2GpuTelemetryPolicy, _gpu_demand_from_device_series
from app.services.scan_service import _enrich_ec2_rightsizing_metrics


START = datetime(2026, 7, 1, tzinfo=timezone.utc)
END = datetime(2026, 7, 8, tzinfo=timezone.utc)


def _adapter(client):
    adapter = object.__new__(AWSAdapter)
    adapter._default_region = "us-east-1"
    adapter._use_simulator = False
    adapter.cloudwatch_client = client
    return adapter


def _gpu_metric(instance_id="i-gpu", index="0", name="nvidia_smi_utilization_gpu"):
    return {
        "Namespace": "CWAgent",
        "MetricName": name,
        "Dimensions": [
            {"Name": "InstanceId", "Value": instance_id},
            {"Name": "index", "Value": index},
            {"Name": "host", "Value": "host"},
        ],
    }


class GPUCloudWatchClient:
    def __init__(self, values=None, metrics=None, list_error=None):
        self.values = values or {str(index): [1.0] for index in range(4)}
        self.metrics = metrics if metrics is not None else [
            _gpu_metric(index=str(index)) for index in range(4)
        ]
        self.list_error = list_error
        self.list_calls = 0
        self.metric_requests = []

    def list_metrics(self, **request):
        self.list_calls += 1
        if self.list_error:
            raise self.list_error
        return {"Metrics": self.metrics}

    def get_metric_data(self, **request):
        self.metric_requests.append(request)
        timestamp = datetime(2026, 7, 7, tzinfo=timezone.utc)
        results = []
        for query in request["MetricDataQueries"]:
            query_id = query["Id"]
            if query_id.startswith("gpu_"):
                position = int(query_id.split("_")[1])
                device_index = str(position)
                values = self.values.get(device_index, [])
                timestamps = [
                    timestamp + timedelta(minutes=index)
                    for index in range(len(values))
                ]
                results.append({"Id": query_id, "Timestamps": timestamps, "Values": values})
            elif query_id.startswith("cpu_"):
                results.append({"Id": query_id, "Timestamps": [timestamp], "Values": [1.0]})
            else:
                results.append({"Id": query_id, "Timestamps": [], "Values": []})
        return {"MetricDataResults": results}


class RaisingGPUCloudWatchClient(GPUCloudWatchClient):
    def get_metric_data(self, **request):
        raise RuntimeError("permission denied")


def _utilization(cpu=1.0, gpu_p95=1.0, gpu_max=1.0, status="usable"):
    return {
        "cpuutilization": cpu,
        "networkin": 1.0,
        "networkout": 1.0,
        "memoryutilization": 1.0,
        "memory_metric_status": "usable",
        "gpu_metric_status": status,
        "gpu_metric_source": {"namespace": "CWAgent", "metric_name": "nvidia_smi_utilization_gpu"},
        "gpu_metric_unavailable_reason": None if status == "usable" else "no_candidates",
        "metric_summary": {
            "cpuutilization": {
                "maximum": 1.0,
                "p90": 1.0,
                "p95": 1.0,
                "p99": 1.0,
            },
            "gpuutilization": {
                "average": gpu_p95,
                "maximum": gpu_max,
                "p95": gpu_p95,
            },
        },
        "metric_history": {
            "cpuutilization": {"maximum": [1.0]},
            "gpuutilization": {"maximum": [gpu_max]},
        },
    }


class CheckAdapter:
    def __init__(self, instance_type, utilization):
        self.instance = {
            "resource_id": "i-gpu",
            "resource_type": "ec2",
            "region": "us-east-1",
            "state": "running",
            "instance_type": instance_type,
            "metadata": {},
        }
        self.utilization = utilization

    def get_resources(self, resource_type, filters=None, region=None):
        return [self.instance]

    def get_resource_utilization(self, *args, **kwargs):
        return self.utilization


def test_memory_matcher_excludes_gpu_memory_but_keeps_ram_aliases():
    assert not is_ec2_memory_metric_name("nvidia_smi_utilization_memory")
    assert not is_ec2_memory_metric_name("nvidia_smi_memory_used")
    assert is_ec2_memory_metric_name("mem_used_percent")
    assert is_ec2_memory_metric_name("MemoryUtilization")


def test_gpu_info_comes_from_catalog_and_handles_fractional_slices():
    assert gpu_info_for_instance_type("g4dn.12xlarge").device_count == 4
    fractional = gpu_info_for_instance_type("g6f.large")
    assert fractional.device_count == 1
    assert fractional.fractional is True
    assert gpu_info_for_instance_type("m5.large") is None
    assert gpu_info_for_instance_type("not-a-type") is None


def test_gpu_matcher_accepts_aliases_and_rejects_gpu_memory():
    for name in (
        "nvidia_smi_utilization_gpu",
        "utilization_gpu",
        "gpu_utilization",
        "GPUUtilization",
        "gpu_util",
    ):
        assert is_ec2_gpu_utilization_metric_name(name)
    for name in ("nvidia_smi_utilization_memory", "GPUMemoryUtilization", "nvidia_smi_memory_used"):
        assert not is_ec2_gpu_utilization_metric_name(name)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("nvidia_smi_memory_used", True),
        ("GPUMemoryUsed", True),
        ("memory_used_gpu", True),
        ("nvidia_smi_memory_total", False),
        ("GPUMemoryTotal", False),
        ("nvidia_smi_utilization_memory", False),
    ],
)
def test_gpu_memory_matcher_accept_reject_table(name, expected):
    assert is_ec2_gpu_memory_used_metric_name(name) is expected


def test_gpu_alignment_drops_timestamp_missing_from_one_device():
    device_series = {
        "0": {
            "utilization_gpu": {"timestamps": ["2026-07-01T00:00:00+00:00", "2026-07-01T00:05:00+00:00"], "values": [1, 90]},
            "memory_used": {"timestamps": ["2026-07-01T00:00:00+00:00", "2026-07-01T00:05:00+00:00"], "values": [0, 2_000]},
        },
        "1": {
            "utilization_gpu": {"timestamps": ["2026-07-01T00:00:00+00:00"], "values": [1]},
            "memory_used": {"timestamps": ["2026-07-01T00:00:00+00:00"], "values": [0]},
        },
    }
    required, _vram, _utilization, evidence = _gpu_demand_from_device_series(
        device_series, START, EC2GpuTelemetryPolicy()
    )
    assert required == {"2026-07-01T00:00:00+00:00": 0}
    assert evidence["aligned_sample_count"] == 1
    assert evidence["dropped_sample_count"] == 1


def test_gpu_required_devices_uses_window_maximum_for_nightly_eight_gpu_job():
    timestamps = [
        (START + timedelta(minutes=index)).isoformat() for index in range(100)
    ]
    devices = {
        str(index): {
            "utilization_gpu": {
                "timestamps": timestamps,
                "values": [90 if timestamp == timestamps[-1] else 0 for timestamp in timestamps],
            },
            "memory_used": {"timestamps": timestamps, "values": [0] * 100},
        }
        for index in range(8)
    }
    required, _vram, _utilization, _evidence = _gpu_demand_from_device_series(
        devices, START, EC2GpuTelemetryPolicy()
    )
    assert max(required.values()) == 8


def test_gpu_memory_used_validation_preserves_mib_values_not_percent_limits():
    assert _valid_non_negative_value(2_048) == 2_048
    assert _valid_non_negative_value(101) == 101
    assert _valid_non_negative_value(-1) is None
    assert _valid_non_negative_value(float("inf")) is None


def test_scan_enrichment_persists_gpu_demand_summaries_and_alignment_evidence():
    now = END
    timestamps = [
        (now - timedelta(minutes=10)).isoformat(),
        (now - timedelta(minutes=5)).isoformat(),
    ]

    class Adapter:
        def get_ec2_rightsizing_metrics(self, *args, **kwargs):
            return {
                "period_seconds": 300,
                "gpu_metric_status": "usable",
                "gpu_metric_source": {"metric_name": "nvidia_smi_utilization_gpu"},
                "gpu_memory_metric_source": {"metric_name": "nvidia_smi_memory_used"},
                "gpu_device_count_observed": 8,
                "metrics": {
                    "gpu_devices": {
                        "0": {
                            "utilization_gpu": {"timestamps": timestamps, "values": [0, 90]},
                            "memory_used": {"timestamps": timestamps, "values": [0, 2048]},
                        },
                        "1": {
                            "utilization_gpu": {"timestamps": timestamps, "values": [0, 0]},
                            "memory_used": {"timestamps": timestamps, "values": [0, 0]},
                        },
                        **{
                            str(index): {
                                "utilization_gpu": {"timestamps": timestamps, "values": [0, 0]},
                                "memory_used": {"timestamps": timestamps, "values": [0, 0]},
                            }
                            for index in range(2, 6)
                        },
                        **{
                            str(index): {
                                "utilization_gpu": {"timestamps": timestamps, "values": [0, 0]},
                            }
                            for index in range(6, 8)
                        },
                    }
                },
            }

    resource = {"resource_id": "i-gpu", "metadata": {}}
    _enrich_ec2_rightsizing_metrics(Adapter(), resource, "us-east-1", now)
    window = resource["metadata"]["rightsizing_metrics"]["60d"]
    assert window["normalized"]["gpu_required_devices"] == {
        "maximum": 1,
        "sample_count": 2,
    }
    assert window["normalized"]["gpu_vram_used_mib"]["maximum"] == 2048
    assert resource["metadata"]["gpu_device_count_observed"] == 6
    assert (
        window["aggregation_evidence"]["gpu_devices"][
            "gpu_device_count_discovered"
        ]
        == 8
    )
    assert window["aggregation_evidence"]["gpu_devices"]["device_count_observed"] == 6
    assert window["aggregation_evidence"]["gpu_devices"]["semantics"] == (
        "all_devices_reported"
    )
    assert resource["metadata"]["gpu_metric_status"] == "usable"


def test_discovery_groups_gpu_sources_by_device_and_shares_memory_sweep():
    client = GPUCloudWatchClient(
        metrics=[
            *[_gpu_metric(index=str(index)) for index in range(4)]
        ]
        + [_gpu_metric(name="nvidia_smi_utilization_memory")]
        + [
            {
                "Namespace": "CWAgent",
                "MetricName": "mem_used_percent",
                "Dimensions": [{"Name": "InstanceId", "Value": "i-gpu"}],
            }
        ]
    )
    adapter = _adapter(client)
    candidates = adapter._ec2_gpu_metric_candidates("i-gpu", client, "us-east-1")
    assert len(candidates) == 4
    assert {candidate["device_index"] for candidate in candidates} == {"0", "1", "2", "3"}
    assert all(candidate["source"]["metric_name"] != "nvidia_smi_utilization_memory" for candidate in candidates)
    adapter._ec2_memory_metric_candidates("i-gpu", client, "us-east-1")
    assert client.list_calls == 1
    adapter._ec2_gpu_metric_candidates("i-gpu", client, "us-east-1")
    assert client.list_calls == 1


def test_gpu_collection_uses_busiest_device_and_does_not_flag_busy_gpu_idle():
    client = GPUCloudWatchClient(values={"0": [1.0], "1": [1.0], "2": [90.0], "3": [1.0]})
    utilization = _adapter(client).get_resource_utilization("i-gpu", "ec2", START, END)
    gpu_history = utilization["metric_history"]["gpuutilization"]
    summary = utilization["metric_summary"]["gpuutilization"]
    assert gpu_history["maximum"] == [90.0]
    assert summary["p95"] == 90.0
    assert summary["average"] == pytest.approx(23.25)
    assert "mean_across_devices" not in summary
    assert "busy_device_count" not in summary
    assert len(utilization["metric_history"]["gpu_devices"]) == 4
    assert busy_device_count(utilization, 5.0) == 1
    assert busy_device_count(utilization, 95.0) == 0
    assert check_ec2_idle_instances(CheckAdapter("g4dn.12xlarge", utilization)) == []


def test_gpu_nightly_blip_uses_maximum_gate_and_flags_idle():
    client = GPUCloudWatchClient(
        values={str(index): [1.0] * 99 for index in range(4)}
    )
    client.values["2"][-1] = 12.0
    utilization = _adapter(client).get_resource_utilization("i-gpu", "ec2", START, END)
    assert utilization["metric_summary"]["gpuutilization"]["p95"] == pytest.approx(1.0)
    assert utilization["metric_summary"]["gpuutilization"]["maximum"] == 12.0
    assert len(check_ec2_idle_instances(CheckAdapter("g4dn.12xlarge", utilization))) == 1


def test_gpu_collection_with_no_candidates_does_not_query_gpu():
    client = GPUCloudWatchClient(metrics=[])
    utilization = _adapter(client).get_resource_utilization("i-gpu", "ec2", START, END)
    assert utilization["gpu_metric_status"] == "unavailable"
    assert utilization["gpu_metric_unavailable_reason"] == "no_candidates"
    assert not any(
        query["Id"].startswith("gpu_")
        for request in client.metric_requests
        for query in request["MetricDataQueries"]
    )


def test_list_metrics_failure_keeps_memory_fallback_and_discloses_gpu_failure():
    client = GPUCloudWatchClient(list_error=RuntimeError("denied"))
    utilization = _adapter(client).get_resource_utilization("i-gpu", "ec2", START, END)
    assert utilization["memory_metric_status"] == "unavailable"
    assert utilization["gpu_metric_status"] == "unavailable"
    assert utilization["gpu_metric_unavailable_reason"] == "discovery_unavailable"


def test_gpu_query_failure_is_distinct_from_an_empty_successful_query():
    failed = _adapter(RaisingGPUCloudWatchClient())._get_ec2_gpu_utilization(
        "i-gpu", START, END, RaisingGPUCloudWatchClient(), "us-east-1"
    )
    assert failed[3] == "query_failed"

    empty_client = GPUCloudWatchClient(
        values={str(index): [] for index in range(4)}
    )
    empty = _adapter(empty_client)._get_ec2_gpu_utilization(
        "i-gpu", START, END, empty_client, "us-east-1"
    )
    assert empty[3] == "no_datapoints"


def test_gpu_without_telemetry_is_never_flagged_idle_even_at_zero_cpu():
    result = check_ec2_idle_instances(
        CheckAdapter("g4dn.12xlarge", _utilization(cpu=0.0, status="unavailable"))
    )
    assert result == []


def test_non_gpu_idle_behavior_and_metadata_are_unchanged():
    result = check_ec2_idle_instances(CheckAdapter("m5.large", _utilization()))
    assert len(result) == 1
    assert "gpu_device_count" not in result[0]["metadata"]
    assert result[0]["metadata"]["avg_cpu_utilization"] == 1.0


def test_gpu_underutilized_and_idle_findings_are_mutually_exclusive():
    busy_cpu = _utilization(cpu=40.0)
    adapter = CheckAdapter("g4dn.12xlarge", busy_cpu)
    assert len(check_ec2_gpu_underutilized(adapter)) == 1
    assert check_ec2_idle_instances(adapter) == []
    assert adapter.instance["metadata"]["recommended_action"] == "rightsize"
    assert "recommended_instance_type" not in adapter.instance["metadata"]

    idle_cpu = _utilization(cpu=1.0)
    adapter = CheckAdapter("g4dn.12xlarge", idle_cpu)
    assert check_ec2_gpu_underutilized(adapter) == []
    assert len(check_ec2_idle_instances(adapter)) == 1
