from datetime import datetime, timezone

from app.adapters.aws.adapter import AWSAdapter, is_ec2_memory_metric_name
from app.services.scan_service import _enrich_ec2_rightsizing_metrics


class MemoryCloudWatchClient:
    def __init__(self):
        self.list_requests = []
        self.metric_requests = []
        self.timestamp = datetime(2026, 7, 13, tzinfo=timezone.utc)

    def list_metrics(self, **request):
        self.list_requests.append(request)
        if "NextToken" not in request:
            return {
                "Metrics": [
                    {
                        "Namespace": "Custom/Host",
                        "MetricName": "MemoryUtilization",
                        "Dimensions": [
                            {"Name": "InstanceType", "Value": "m5.large"},
                            {"Name": "InstanceId", "Value": "i-custom"},
                        ],
                    },
                    {
                        "Namespace": "Custom/Host",
                        "MetricName": "memory_free_percent",
                        "Dimensions": [
                            {"Name": "InstanceId", "Value": "i-custom"},
                        ],
                    },
                ],
                "NextToken": "page-2",
            }
        return {
            "Metrics": [
                {
                    "Namespace": "Other/Host",
                    "MetricName": "mem_used_percent",
                    "Dimensions": [
                        {"Name": "InstanceId", "Value": "i-other"},
                    ],
                }
            ]
        }

    def get_metric_data(self, **request):
        self.metric_requests.append(request)
        results = []
        for query in request["MetricDataQueries"]:
            metric = query["MetricStat"]["Metric"]
            namespace = metric["Namespace"]
            metric_name = metric["MetricName"]
            result = {"Id": query["Id"], "Timestamps": [], "Values": []}
            if namespace == "AWS/EC2":
                result["Timestamps"] = [self.timestamp]
                result["Values"] = [1.0]
            elif metric_name == "MemoryUtilization" or query["Id"] == "memory_percent":
                result["Timestamps"] = [self.timestamp]
                result["Values"] = [42.0]
            results.append(result)
        return {"MetricDataResults": results}


def _adapter(client):
    adapter = object.__new__(AWSAdapter)
    adapter._default_region = "us-east-1"
    adapter.cloudwatch_client = client
    return adapter


def test_memory_name_matching_accepts_aliases_and_rejects_non_utilization_series():
    assert is_ec2_memory_metric_name("mem")
    assert is_ec2_memory_metric_name("memory")
    assert is_ec2_memory_metric_name("mem_used_percent")
    assert is_ec2_memory_metric_name("MemoryUtilization")
    assert is_ec2_memory_metric_name("systemMemoryPct")
    assert not is_ec2_memory_metric_name("memory_free_percent")
    assert not is_ec2_memory_metric_name("mem_used_bytes")
    assert not is_ec2_memory_metric_name("disk_utilization")


def test_regular_ec2_utilization_discovers_custom_namespace_and_full_dimensions():
    client = MemoryCloudWatchClient()
    utilization = _adapter(client).get_resource_utilization(
        "i-custom",
        "ec2",
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 13, tzinfo=timezone.utc),
    )

    assert utilization["memoryutilization"] == 42.0
    assert utilization["memory_metric_source"] == {
        "namespace": "Custom/Host",
        "metric_name": "MemoryUtilization",
        "dimensions": [
            {"Name": "InstanceId", "Value": "i-custom"},
            {"Name": "InstanceType", "Value": "m5.large"},
        ],
    }
    assert len(client.list_requests) == 2
    memory_request = client.metric_requests[-1]
    memory_metric = memory_request["MetricDataQueries"][0]["MetricStat"]["Metric"]
    assert memory_metric["Namespace"] == "Custom/Host"
    assert memory_metric["MetricName"] == "MemoryUtilization"
    assert memory_metric["Dimensions"] == utilization["memory_metric_source"]["dimensions"]


def test_rightsizing_metrics_maps_instance_and_paginates_discovery():
    client = MemoryCloudWatchClient()
    result = _adapter(client).get_ec2_rightsizing_metrics(
        "i-other",
        datetime(2026, 5, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 13, tzinfo=timezone.utc),
    )

    memory = result["metrics"]["memory_percent"]
    assert memory["values"] == [42.0]
    assert memory["source"]["namespace"] == "Other/Host"
    first_queries = client.metric_requests[0]["MetricDataQueries"]
    memory_query = next(query for query in first_queries if query["Id"] == "memory_percent")
    assert memory_query["MetricStat"]["Metric"]["Namespace"] == "Other/Host"
    assert memory_query["MetricStat"]["Metric"]["MetricName"] == "mem_used_percent"


def test_scan_enrichment_persists_actual_memory_average_history_and_source():
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)

    class Adapter:
        def get_ec2_rightsizing_metrics(self, *args, **kwargs):
            return {
                "period_seconds": 300,
                "metrics": {
                    "memory_percent": {
                        "timestamps": [now.isoformat(), "2026-07-12T00:00:00+00:00"],
                        "values": [20.0, 40.0],
                        "source": {
                            "namespace": "Custom/Host",
                            "metric_name": "MemoryUtilization",
                            "dimensions": [
                                {"Name": "InstanceId", "Value": "i-custom"}
                            ],
                        },
                    }
                },
            }

    resource = {"resource_id": "i-custom", "metadata": {}}
    _enrich_ec2_rightsizing_metrics(Adapter(), resource, "us-east-1", now)

    metadata = resource["metadata"]
    assert metadata["avg_memory_utilization"] == 30.0
    assert metadata["memory_metric_status"] == "usable"
    assert metadata["memory_metric_source"]["namespace"] == "Custom/Host"
    assert metadata["metric_history"]["memoryutilization"]["average"] == [20.0, 40.0]
