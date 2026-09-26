from __future__ import annotations

from datetime import datetime, timezone

from app.adapters.aws.adapter import AWSAdapter, is_ec2_memory_metric_name
from rightsizers.asg.asg_rightsizer.normalization import aggregate_stable_member_memory


class FakeListMetrics:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def list_metrics(self, **request):
        self.calls.append(request)
        return self.pages.pop(0)


def bare_adapter():
    return object.__new__(AWSAdapter)


def test_group_memory_discovery_paginates_filters_and_ranks():
    client = FakeListMetrics(
        [
            {
                "Metrics": [
                    {
                        "Namespace": "Custom",
                        "MetricName": "MemoryUtilization",
                        "Dimensions": [{"Name": "AutoScalingGroupName", "Value": "other"}],
                    }
                ],
                "NextToken": "next",
            },
            {
                "Metrics": [
                    {
                        "Namespace": "CWAgent",
                        "MetricName": "mem_used_percent",
                        "Dimensions": [{"Name": "AutoScalingGroupName", "Value": "asg-test"}],
                    },
                    {
                        "Namespace": "CWAgent",
                        "MetricName": "mem_available_bytes",
                        "Dimensions": [{"Name": "AutoScalingGroupName", "Value": "asg-test"}],
                    },
                ]
            },
        ]
    )
    candidates = bare_adapter()._asg_memory_metric_candidates("asg-test", client)
    assert len(client.calls) == 2
    assert all(
        call["Dimensions"]
        == [{"Name": "AutoScalingGroupName", "Value": "asg-test"}]
        for call in client.calls
    )
    assert candidates[0]["source"]["metric_name"] == "mem_used_percent"
    assert all(
        candidate["source"]["dimensions"][0]["Value"] == "asg-test"
        for candidate in candidates
    )


def test_asg_list_metrics_filter_or_cache_is_scope_bounded():
    client = FakeListMetrics(
        [
            {
                "Metrics": [
                    {
                        "Namespace": "CWAgent",
                        "MetricName": "mem_used_percent",
                        "Dimensions": [
                            {"Name": "AutoScalingGroupName", "Value": "asg-one"}
                        ],
                    }
                ]
            },
            {
                "Metrics": [
                    {
                        "Namespace": "CWAgent",
                        "MetricName": "mem_used_percent",
                        "Dimensions": [
                            {"Name": "AutoScalingGroupName", "Value": "asg-two"}
                        ],
                    }
                ]
            },
        ]
    )
    adapter = bare_adapter()

    one = adapter._asg_memory_metric_candidates("asg-one", client)
    two = adapter._asg_memory_metric_candidates("asg-two", client)

    assert len(client.calls) == 2
    assert [call["Dimensions"] for call in client.calls] == [
        [{"Name": "AutoScalingGroupName", "Value": "asg-one"}],
        [{"Name": "AutoScalingGroupName", "Value": "asg-two"}],
    ]
    assert one[0]["source"]["dimensions"][0]["Value"] == "asg-one"
    assert two[0]["source"]["dimensions"][0]["Value"] == "asg-two"


def test_stable_member_aggregate_requires_every_member_and_timestamp():
    source = lambda instance_id: {
        "namespace": "CWAgent",
        "metric_name": "mem_used_percent",
        "dimensions": [{"Name": "InstanceId", "Value": instance_id}],
    }
    payload = {
        "members": {
            "i-1": {"timestamps": ["2026-07-01T00:00:00+00:00", "2026-07-01T00:05:00+00:00"], "values": [20, 40], "source": source("i-1")},
            "i-2": {"timestamps": ["2026-07-01T00:00:00+00:00"], "values": [40], "source": source("i-2")},
        }
    }
    result = aggregate_stable_member_memory(payload, ["i-1", "i-2"])
    assert result["values"] == [30]
    assert result["source"]["kind"] == "stable_member_aggregate"
    assert aggregate_stable_member_memory(payload, ["i-1", "i-2", "i-3"]) is None


def test_stable_member_aggregate_rejects_incompatible_metric_names():
    payload = {
        "members": {
            "i-1": {"timestamps": ["2026-07-01T00:00:00+00:00"], "values": [20], "source": {"namespace": "CWAgent", "metric_name": "mem_used_percent"}},
            "i-2": {"timestamps": ["2026-07-01T00:00:00+00:00"], "values": [20], "source": {"namespace": "Custom", "metric_name": "MemoryUtilization"}},
        }
    }
    assert aggregate_stable_member_memory(payload, ["i-1", "i-2"]) is None


def test_shared_memory_name_filter_retains_ec2_contract():
    assert is_ec2_memory_metric_name("mem_used_percent") is True
    assert is_ec2_memory_metric_name("MemoryUtilization") is True
    assert is_ec2_memory_metric_name("mem_available_bytes") is False
