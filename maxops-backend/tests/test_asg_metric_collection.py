from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
from types import MethodType

from app.adapters.aws.adapter import AWSAdapter
from app.services.scan_service import (
    _asg_activity_signals,
    _asg_stable_members_proven,
)
from app.services.iam_onboarding_service import READ_ONLY_SCAN_POLICY
from app.models.inventory import AsgInventory, MaxOpsInventory
from rightsizers.asg.asg_rightsizer.models import ASGCapacityPolicy
from rightsizers.asg.asg_rightsizer.normalization import normalize_asg_metrics


NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


class FakeCloudWatch:
    def __init__(self):
        self.requests = []

    def get_metric_data(self, **request):
        self.requests.append(request)
        results = []
        for query in request["MetricDataQueries"]:
            query_id = query["Id"]
            value = {
                "cpu_percent": 20.0,
                "desired_capacity": 10.0,
                "in_service_instances": 10.0,
                "memory_percent": 30.0,
            }.get(query_id, 1.0)
            results.append(
                {"Id": query_id, "Timestamps": [NOW], "Values": [value]}
            )
        return {"MetricDataResults": results}


class FakeAutoScaling:
    def __init__(self):
        self.group_pages = 0

    def describe_auto_scaling_groups(self, **request):
        self.group_pages += 1
        if not request.get("NextToken"):
            return {
                "AutoScalingGroups": [self._group("asg-one", "i-1")],
                "NextToken": "page-2",
            }
        return {"AutoScalingGroups": [self._group("asg-two", "i-2")]}

    @staticmethod
    def _group(name, instance_id):
        return {
            "AutoScalingGroupName": name,
            "MinSize": 2,
            "DesiredCapacity": 3,
            "MaxSize": 6,
            "AvailabilityZones": ["us-east-1a", "us-east-1b"],
            "LaunchTemplate": {"LaunchTemplateId": f"lt-{name}", "Version": "1"},
            "Instances": [
                {
                    "InstanceId": instance_id,
                    "InstanceType": "m6i.large",
                    "LifecycleState": "InService",
                    "ProtectedFromScaleIn": False,
                }
            ],
            "Tags": [],
        }

    def describe_scaling_activities(self, **request):
        if request["AutoScalingGroupName"] == "asg-one":
            raise RuntimeError("one group activity lookup failed")
        return {"Activities": []}

    def describe_scheduled_actions(self, **request):
        return {"ScheduledUpdateGroupActions": []}

    def describe_policies(self, **request):
        return {
            "ScalingPolicies": [
                {
                    "PolicyType": "TargetTrackingScaling",
                    "TargetTrackingConfiguration": {
                        "PredefinedMetricSpecification": {
                            "PredefinedMetricType": "ASGAverageCPUUtilization"
                        }
                    },
                }
            ]
        }

    def describe_warm_pool(self, **request):
        if request["AutoScalingGroupName"] == "asg-one":
            return {
                "Instances": [],
                "WarmPoolConfiguration": {
                    "MinSize": 0,
                    "PoolState": "Stopped",
                },
            }
        return {"Instances": []}

    def describe_instance_refreshes(self, **request):
        return {"InstanceRefreshes": []}


class FakeEC2:
    def describe_instances(self, **request):
        return {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": instance_id,
                            "InstanceType": "m6i.large",
                            "PlatformDetails": "Linux/UNIX",
                            "LaunchTime": NOW - timedelta(days=90),
                        }
                        for instance_id in request["InstanceIds"]
                    ]
                }
            ]
        }

    def describe_launch_template_versions(self, **request):
        return {
            "LaunchTemplateVersions": [
                {"LaunchTemplateData": {"InstanceType": "m6i.large"}}
            ]
        }


class FakeSession:
    def __init__(self):
        self.autoscaling = FakeAutoScaling()
        self.ec2 = FakeEC2()

    def client(self, service_name, **kwargs):
        return self.autoscaling if service_name == "autoscaling" else self.ec2


class FakeTrendCloudWatch:
    def __init__(self):
        self.requests = []

    def get_metric_data(self, **request):
        self.requests.append(request)
        if not request.get("NextToken"):
            results = []
            for query in request["MetricDataQueries"]:
                query_id = query["Id"]
                if "_bucket_" in query_id:
                    days = int(query_id.split("_bucket_", 1)[1].split("_", 1)[0])
                    timestamp = NOW - timedelta(days=days)
                else:
                    timestamp = NOW - timedelta(days=1)
                results.append(
                    {"Id": query_id, "Timestamps": [timestamp], "Values": [42.0]}
                )
            return {"MetricDataResults": results, "NextToken": "next"}
        return {"MetricDataResults": []}


class FakePagedSeriesCloudWatch:
    def get_metric_data(self, **request):
        if not request.get("NextToken"):
            return {
                "MetricDataResults": [
                    {
                        "Id": "cpu_percent",
                        "Timestamps": [NOW, NOW - timedelta(minutes=5)],
                        "Values": [30.0, 10.0],
                    }
                ],
                "NextToken": "next",
            }
        return {
            "MetricDataResults": [
                {
                    "Id": "cpu_percent",
                    "Timestamps": [NOW - timedelta(minutes=5)],
                    "Values": [20.0],
                }
            ]
        }


def adapter_with_client(client):
    adapter = object.__new__(AWSAdapter)
    adapter._cloudwatch_client_for_region = MethodType(lambda self, region: client, adapter)
    adapter._asg_memory_metric_candidates = MethodType(
        lambda self, name, cw: [
            {
                "source": {
                    "kind": "group",
                    "namespace": "CWAgent",
                    "metric_name": "mem_used_percent",
                    "dimensions": [{"Name": "AutoScalingGroupName", "Value": name}],
                },
                "fallback": False,
            }
        ],
        adapter,
    )
    return adapter


def test_exact_metric_query_allowlist_and_statistics():
    client = FakeCloudWatch()
    adapter = adapter_with_client(client)
    result = adapter.get_asg_rightsizing_metrics(
        "asg-test", NOW - timedelta(days=60), NOW, region="us-east-1"
    )
    names = {
        query["MetricStat"]["Metric"]["MetricName"]
        for request in client.requests
        for query in request["MetricDataQueries"]
    }
    assert names == {
        "CPUUtilization",
        "GroupDesiredCapacity",
        "GroupInServiceInstances",
        "mem_used_percent",
    }
    definitions = {
        query["MetricStat"]["Metric"]["MetricName"]: query["MetricStat"]["Stat"]
        for request in client.requests
        for query in request["MetricDataQueries"]
    }
    assert definitions["CPUUtilization"] == "Average"
    assert definitions["GroupDesiredCapacity"] == "Maximum"
    assert definitions["GroupInServiceInstances"] == "Maximum"
    assert all(
        query["MetricStat"]["Metric"]["Dimensions"]
        == [{"Name": "AutoScalingGroupName", "Value": "asg-test"}]
        for request in client.requests
        for query in request["MetricDataQueries"]
    )
    assert result["metrics"]["memory_percent"]["source"]["kind"] == "group"


def test_metric_pages_merge_sort_and_deduplicate_deterministically():
    query = {"Id": "cpu_percent", "MetricStat": {}, "ReturnData": True}
    result = AWSAdapter._query_metric_series(
        FakePagedSeriesCloudWatch(), [query], NOW - timedelta(days=1), NOW
    )["cpu_percent"]
    assert result["timestamps"] == [
        (NOW - timedelta(minutes=5)).isoformat(),
        NOW.isoformat(),
    ]
    assert result["values"] == [20.0, 30.0]


def test_normalization_pairs_exact_timestamps_and_never_fills():
    timestamps = [NOW - timedelta(minutes=5 * index) for index in range(4)]
    raw = {
        "metrics": {
            "cpu_percent": {"timestamps": timestamps, "values": [10, 20, 30, 40]},
            "memory_percent": {"timestamps": timestamps[:3], "values": [20, 30, 40], "source": {"kind": "group"}},
            "desired_capacity": {"timestamps": timestamps, "values": [4, 4, 4, 4]},
            "in_service_instances": {"timestamps": [timestamps[0], timestamps[2], timestamps[3]], "values": [4, 4, 4]},
        }
    }
    windows, telemetry, demand = normalize_asg_metrics(
        raw, NOW, ASGCapacityPolicy(), include_demand=True
    )
    summary = windows["14d"]["normalized"]["required_capacity"]["balanced"]
    assert summary["sample_count"] == 3
    assert summary["p99_record"]["memory_used_instances"] is None
    assert summary["p99_record"]["binding_dimension"] == "cpu"
    assert telemetry["cpu_percent"]["pairing_ratio"] == 0.75
    assert telemetry["memory_percent"]["pairing_ratio"] == 2 / 3
    assert demand["normalization_version"] == "asg-v2-target-independent-demand"
    assert demand["period_seconds"] == 300
    assert [point["timestamp"] for point in demand["points"]] == sorted(
        timestamp.isoformat()
        for timestamp in (timestamps[0], timestamps[2], timestamps[3])
    )
    assert all(
        point["memory_used_instance_equivalents"] is None
        for point in demand["points"]
    )


def test_v1_required_capacity_exact_tier_boundary_is_inclusive():
    raw = {
        "metrics": {
            "cpu_percent": {"timestamps": [NOW], "values": [70.0]},
            "memory_percent": {
                "timestamps": [NOW],
                "values": [70.0],
                "source": {"kind": "group"},
            },
            "desired_capacity": {"timestamps": [NOW], "values": [15]},
            "in_service_instances": {"timestamps": [NOW], "values": [15]},
        }
    }
    windows, _telemetry = normalize_asg_metrics(
        raw,
        NOW,
        ASGCapacityPolicy(minimum_observed_days=0),
    )
    record = windows["14d"]["normalized"]["required_capacity"]["balanced"][
        "p99_record"
    ]
    assert record["required"] == 15
    assert record["binding_dimension"] == "cpu_and_memory"


def test_usable_memory_does_not_erase_cpu_spike_without_memory_timestamp():
    timestamps = [NOW - timedelta(minutes=5 * index) for index in range(10)]
    spike = timestamps[0]
    raw = {
        "metrics": {
            "cpu_percent": {
                "timestamps": timestamps,
                "values": [100 if timestamp == spike else 10 for timestamp in timestamps],
            },
            "memory_percent": {
                "timestamps": timestamps[1:],
                "values": [20] * 9,
                "source": {"kind": "group"},
            },
            "desired_capacity": {"timestamps": timestamps, "values": [10] * 10},
            "in_service_instances": {
                "timestamps": timestamps,
                "values": [10] * 10,
            },
        }
    }
    policy = ASGCapacityPolicy(minimum_observed_days=0)
    windows, telemetry, demand = normalize_asg_metrics(
        raw, NOW, policy, include_demand=True
    )
    balanced = windows["14d"]["normalized"]["required_capacity"]["balanced"]
    assert telemetry["memory_percent"]["status"] == "usable"
    assert balanced["sample_count"] == 10
    assert balanced["p99"] == 15
    assert balanced["p99_record"]["timestamp"] == spike.isoformat()
    assert balanced["p99_record"]["binding_dimension"] == "cpu"
    assert balanced["p99_record"]["memory_used_instances"] is None
    demand_spike = next(
        point for point in demand["points"] if point["timestamp"] == spike.isoformat()
    )
    assert demand_spike == {
        "timestamp": spike.isoformat(),
        "cpu_used_instance_equivalents": 10.0,
        "memory_used_instance_equivalents": None,
    }


def test_usable_memory_spike_survives_missing_cpu_timestamp():
    timestamps = [NOW - timedelta(minutes=5 * index) for index in range(10)]
    spike = timestamps[0]
    raw = {
        "metrics": {
            "cpu_percent": {
                "timestamps": timestamps[1:],
                "values": [10] * 9,
            },
            "memory_percent": {
                "timestamps": timestamps,
                "values": [100 if timestamp == spike else 20 for timestamp in timestamps],
                "source": {"kind": "group"},
            },
            "desired_capacity": {"timestamps": timestamps, "values": [10] * 10},
            "in_service_instances": {
                "timestamps": timestamps,
                "values": [10] * 10,
            },
        }
    }
    windows, telemetry, demand = normalize_asg_metrics(
        raw,
        NOW,
        ASGCapacityPolicy(minimum_observed_days=0),
        include_demand=True,
    )
    balanced = windows["14d"]["normalized"]["required_capacity"]["balanced"]
    assert telemetry["memory_percent"]["status"] == "usable"
    assert balanced["sample_count"] == 10
    assert balanced["p99"] == 15
    assert balanced["p99_record"]["timestamp"] == spike.isoformat()
    assert balanced["p99_record"]["binding_dimension"] == "memory"
    assert balanced["p99_record"]["cpu_used_instances"] is None
    demand_spike = next(
        point for point in demand["points"] if point["timestamp"] == spike.isoformat()
    )
    assert demand_spike == {
        "timestamp": spike.isoformat(),
        "cpu_used_instance_equivalents": None,
        "memory_used_instance_equivalents": 10.0,
    }


def test_normalization_filters_nonfinite_and_out_of_range_values():
    raw = {
        "metrics": {
            "cpu_percent": {"timestamps": [NOW] * 4, "values": [-1, 20, math.inf, 101]},
            "memory_percent": {"timestamps": [], "values": []},
            "desired_capacity": {"timestamps": [NOW], "values": [4]},
            "in_service_instances": {"timestamps": [NOW], "values": [4]},
        }
    }
    windows, telemetry = normalize_asg_metrics(raw, NOW, ASGCapacityPolicy())
    assert telemetry["cpu_percent"]["present"] is True
    assert windows["14d"]["normalized"]["cpu_percent"]["sample_count"] == 1


def test_percentage_and_in_service_numeric_boundaries():
    timestamps = [NOW - timedelta(minutes=index * 5) for index in range(4)]
    raw = {
        "metrics": {
            "cpu_percent": {
                "timestamps": timestamps,
                "values": [0, 100, -0.0001, 100.0001],
            },
            "memory_percent": {"timestamps": [], "values": []},
            "desired_capacity": {"timestamps": timestamps, "values": [1] * 4},
            "in_service_instances": {
                "timestamps": timestamps,
                "values": [1, 1, 0, 0],
            },
        }
    }
    windows, _ = normalize_asg_metrics(raw, NOW, ASGCapacityPolicy())
    normalized = windows["14d"]["normalized"]
    assert normalized["cpu_percent"]["sample_count"] == 2
    assert normalized["cpu_percent"]["maximum"] == 100
    assert normalized["in_service_instances"]["sample_count"] == 2


def test_current_state_activity_failure_is_cleared_by_later_success():
    failed = {
        "StartTime": NOW - timedelta(hours=2),
        "Description": "Launching a new EC2 instance",
        "StatusCode": "Failed",
        "StatusMessage": "Insufficient capacity",
    }
    success = {
        "StartTime": NOW - timedelta(hours=1),
        "Description": "Launching a new EC2 instance",
        "StatusCode": "Successful",
    }
    base = {"desired_capacity": 1, "in_service_instance_ids": ["i-1"]}
    signals = _asg_activity_signals({**base, "scaling_activities": [failed]})
    assert signals["scaling_failure_seen"] is True
    assert signals["capacity_shortage_seen"] is True
    signals = _asg_activity_signals({**base, "scaling_activities": [failed, success]})
    assert signals["scaling_failure_seen"] is False
    assert signals["capacity_shortage_seen"] is False


def test_stable_member_proof_requires_constant_history_and_old_members():
    start = NOW - timedelta(days=60)
    metadata = {
        "in_service_instance_ids": ["i-1"],
        "member_instances": [
            {"InstanceId": "i-1", "LaunchTime": start - timedelta(days=1)}
        ],
        "scaling_activities": [],
    }
    timestamps = [start + timedelta(minutes=5 * index) for index in range(17280)]
    raw = {
        "period_seconds": 300,
        "metrics": {
            "desired_capacity": {"timestamps": timestamps, "values": [1] * len(timestamps)},
            "in_service_instances": {"timestamps": timestamps, "values": [1] * len(timestamps)},
        }
    }
    policy = ASGCapacityPolicy()
    assert _asg_stable_members_proven(metadata, raw, start, policy) is True
    metadata["scaling_activities"] = [
        {
            "StartTime": NOW - timedelta(days=1),
            "Description": "Adding a new EC2 instance to the group",
            "StatusCode": "Successful",
        }
    ]
    assert _asg_stable_members_proven(metadata, raw, start, policy) is False
    metadata["scaling_activities"] = []
    raw["metrics"]["desired_capacity"]["values"][-1] = 2
    assert _asg_stable_members_proven(metadata, raw, start, policy) is False


def test_stable_member_proof_uses_capacity_policy_window_and_ratio():
    policy = ASGCapacityPolicy(lookback_days=(1,), minimum_pairing_ratio=0.5)
    start = NOW - timedelta(days=1)
    metadata = {
        "in_service_instance_ids": ["i-1"],
        "member_instances": [
            {"InstanceId": "i-1", "LaunchTime": start - timedelta(days=1)}
        ],
        "scaling_activities": [],
    }
    required_samples = int(86400 / 300 * 0.5)
    timestamps = [
        start + timedelta(minutes=5 * index) for index in range(required_samples)
    ]
    raw = {
        "period_seconds": 300,
        "metrics": {
            "desired_capacity": {
                "timestamps": timestamps,
                "values": [1] * required_samples,
            },
            "in_service_instances": {
                "timestamps": timestamps,
                "values": [1] * required_samples,
            },
        },
    }
    assert _asg_stable_members_proven(metadata, raw, start, policy) is True
    raw["metrics"]["desired_capacity"]["timestamps"] = timestamps[:-1]
    raw["metrics"]["desired_capacity"]["values"] = [1] * (required_samples - 1)
    assert _asg_stable_members_proven(metadata, raw, start, policy) is False


def test_asg_discovery_paginates_resolves_launch_and_isolates_group_failures():
    adapter = object.__new__(AWSAdapter)
    adapter.session = FakeSession()
    resources = adapter.get_resources("asg", region="us-east-1")

    assert [item["resource_id"] for item in resources] == ["asg-one", "asg-two"]
    assert adapter.session.autoscaling.group_pages == 2
    assert all(item["instance_type"] == "m6i.large" for item in resources)
    assert all(
        item["metadata"]["dynamic_policy_metrics"] == ["ASGAverageCPUUtilization"]
        for item in resources
    )
    assert resources[0]["metadata"]["collection_errors"] == [
        {
            "operation": "describe_scaling_activities",
            "error": "one group activity lookup failed",
        }
    ]
    assert resources[0]["metadata"]["warm_pool_present"] is True
    assert resources[0]["metadata"]["warm_pool_configuration"] == {
        "MinSize": 0,
        "PoolState": "Stopped",
    }
    assert resources[0]["metadata"]["warm_pool_instances"] == []
    assert resources[1]["metadata"]["warm_pool_present"] is False
    assert resources[1]["metadata"]["collection_errors"] == []


def test_inventory_model_nullability_matches_existing_and_new_migrations():
    assert MaxOpsInventory.__table__.c.account_id.nullable is True
    assert MaxOpsInventory.__table__.c.region.nullable is True
    assert AsgInventory.__table__.c.account_id.nullable is False
    assert AsgInventory.__table__.c.region.nullable is False
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/20260715_000001_add_asg_inventory.py"
    ).read_text(encoding="utf-8")
    assert 'sa.Column("account_id", sa.String(length=100), nullable=False)' in migration
    assert 'sa.Column("region", sa.String(length=50), nullable=False)' in migration


def test_asg_discovery_permissions_are_present_in_read_only_policy():
    actions = {
        action
        for statement in READ_ONLY_SCAN_POLICY["Statement"]
        for action in statement["Action"]
    }
    assert {
        "autoscaling:DescribePolicies",
        "autoscaling:DescribeInstanceRefreshes",
        "autoscaling:DescribeLaunchConfigurations",
        "ec2:DescribeLaunchTemplateVersions",
    } <= actions


def test_launch_configuration_instance_type_resolution():
    class LaunchConfigurationClient:
        def describe_launch_configurations(self, **request):
            assert request == {"LaunchConfigurationNames": ["legacy-launch"]}
            return {"LaunchConfigurations": [{"InstanceType": "m6i.large"}]}

    resolved = object.__new__(AWSAdapter)._resolve_asg_launch_instance_type(
        LaunchConfigurationClient(),
        object(),
        {"LaunchConfigurationName": "legacy-launch"},
    )
    assert resolved == "m6i.large"


def test_trend_reuses_persisted_source_paginates_and_queries_only_cpu_memory():
    client = FakeTrendCloudWatch()
    adapter = object.__new__(AWSAdapter)
    adapter._cloudwatch_client_for_region = MethodType(
        lambda self, region: client, adapter
    )
    source = {
        "kind": "group",
        "namespace": "CWAgent",
        "metric_name": "mem_used_percent",
        "dimensions": [{"Name": "AutoScalingGroupName", "Value": "asg-test"}],
    }
    result = adapter.get_asg_confidence_trend(
        "asg-test",
        NOW - timedelta(days=455),
        NOW,
        region="us-east-1",
        memory_source=source,
    )
    metric_names = {
        query["MetricStat"]["Metric"]["MetricName"]
        for request in client.requests
        for query in request["MetricDataQueries"]
    }
    assert metric_names == {"CPUUtilization", "mem_used_percent"}
    assert len(client.requests) == 2
    assert result["daily"][0]["maximum"] == 42.0
    assert list(result["buckets"]) == [
        "30d",
        "90d",
        "120d",
        "180d",
        "365d",
        "455d",
    ]
    assert result["memory"]["source"] == source
