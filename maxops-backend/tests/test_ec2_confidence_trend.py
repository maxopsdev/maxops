from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.aws.adapter import AWSAdapter
from app.api.routes.recommendations import get_ec2_rightsize_confidence_trend
from app.database import Base
from app.models.inventory import Ec2Inventory
from app.services.ec2_rightsizer import EC2Rightsizer
from app.services.ec2_trend_cache import EC2TrendCache, ec2_trend_cache


class CloudWatchClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def get_metric_data(self, **request):
        self.requests.append(request)
        return self.responses.pop(0)


class AdapterSession:
    def __init__(self, client):
        self.client_value = client

    def client(self, service, **kwargs):
        assert service == "cloudwatch"
        return self.client_value


def _adapter(client):
    adapter = object.__new__(AWSAdapter)
    adapter.session = AdapterSession(client)
    adapter.cloudwatch_client = client
    return adapter


def test_cpu_confidence_buckets_use_trailing_daily_max_and_complete_percentiles():
    recent = datetime(2026, 7, 1, tzinfo=timezone.utc)
    older = datetime(2026, 5, 1, tzinfo=timezone.utc)
    client = CloudWatchClient(
        [
            {
                "MetricDataResults": [
                    {
                        "Id": "daily_maximum",
                        "Timestamps": [older, recent],
                        "Values": [99, 97],
                    },
                    {"Id": "daily_p95", "Timestamps": [recent], "Values": [20]},
                    {
                        "Id": "bucket_30_p95",
                        "Timestamps": [older, recent],
                        "Values": [12, 88],
                    },
                ]
            }
        ]
    )
    end = datetime(2026, 7, 12, tzinfo=timezone.utc)
    result = _adapter(client).get_ec2_confidence_trend(
        "i-1", end - timedelta(days=455), end, region="us-east-1"
    )
    queries = client.requests[0]["MetricDataQueries"]
    assert len(queries) == 15
    assert "bucket_30_maximum" not in {query["Id"] for query in queries}
    assert {query["MetricStat"]["Metric"]["MetricName"] for query in queries} == {
        "CPUUtilization"
    }
    assert {query["MetricStat"]["Period"] for query in queries} == {
        86400,
        30 * 86400,
        90 * 86400,
        120 * 86400,
        180 * 86400,
        365 * 86400,
        455 * 86400,
    }
    assert result["daily"][-1] == {
        "timestamp": recent.isoformat(),
        "maximum": 97.0,
        "p99": None,
        "p95": 20.0,
    }
    assert result["buckets"]["30d"] == {
        "maximum": 97.0,
        "p99": None,
        "p95": 12.0,
    }
    assert result["buckets"]["90d"]["maximum"] == 99.0


def test_cpu_confidence_trend_handles_pagination_and_empty_data():
    client = CloudWatchClient(
        [{"MetricDataResults": [], "NextToken": "next"}, {"MetricDataResults": []}]
    )
    end = datetime(2026, 7, 12, tzinfo=timezone.utc)
    result = _adapter(client).get_ec2_confidence_trend(
        "i-1", end - timedelta(days=455), end, region="us-east-1"
    )
    assert client.requests[1]["NextToken"] == "next"
    assert result["daily"] == []
    assert all(
        all(value is None for value in bucket.values())
        for bucket in result["buckets"].values()
    )


def test_confidence_trend_adds_selected_memory_source_and_never_network_or_average():
    timestamp = datetime(2026, 7, 1, tzinfo=timezone.utc)

    class Client:
        requests = []

        def get_metric_data(self, **request):
            self.requests.append(request)
            results = []
            for query in request["MetricDataQueries"]:
                values = []
                timestamps = []
                if query["Id"] in {"daily_maximum", "memory_daily_maximum"}:
                    timestamps = [timestamp]
                    values = [20.0 if query["Id"] == "daily_maximum" else 91.0]
                results.append(
                    {"Id": query["Id"], "Timestamps": timestamps, "Values": values}
                )
            return {"MetricDataResults": results}

    client = Client()
    memory_source = {
        "namespace": "Custom/Host",
        "metric_name": "MemoryUtilization",
        "dimensions": [
            {"Name": "InstanceId", "Value": "i-1"},
            {"Name": "ImageId", "Value": "ami-1"},
        ],
    }
    end = datetime(2026, 7, 12, tzinfo=timezone.utc)
    result = _adapter(client).get_ec2_confidence_trend(
        "i-1",
        end - timedelta(days=455),
        end,
        region="us-east-1",
        memory_source=memory_source,
    )
    queries = client.requests[0]["MetricDataQueries"]
    assert len(queries) == 30
    assert {query["MetricStat"]["Metric"]["MetricName"] for query in queries} == {
        "CPUUtilization",
        "MemoryUtilization",
    }
    assert "Average" not in {query["MetricStat"]["Stat"] for query in queries}
    assert all(
        "Network" not in query["MetricStat"]["Metric"]["MetricName"]
        for query in queries
    )
    memory_queries = [query for query in queries if query["Id"].startswith("memory_")]
    assert all(
        query["MetricStat"]["Metric"]["Dimensions"] == memory_source["dimensions"]
        for query in memory_queries
    )
    assert result["memory"]["daily"][0]["maximum"] == 91.0
    assert result["memory"]["source"] == memory_source


def test_decision_metric_query_set_period_and_pagination_remain_unchanged():
    client = CloudWatchClient(
        [{"MetricDataResults": [], "NextToken": "next"}, {"MetricDataResults": []}]
    )
    end = datetime(2026, 7, 12, tzinfo=timezone.utc)
    _adapter(client).get_ec2_rightsizing_metrics(
        "i-1", end - timedelta(days=60), end, region="us-east-1"
    )
    queries = client.requests[0]["MetricDataQueries"]
    assert {query["Id"] for query in queries} == {
        "cpu_percent",
        "network_in_bytes",
        "network_out_bytes",
        "network_packets_in",
        "network_packets_out",
        "ebs_read_operations",
        "ebs_write_operations",
        "ebs_read_bytes",
        "ebs_write_bytes",
        "instance_ebs_iops_exceeded",
        "instance_ebs_throughput_exceeded",
        "ebs_io_balance_percent",
        "ebs_byte_balance_percent",
        "memory_percent",
        "bw_in_allowance_exceeded",
        "bw_out_allowance_exceeded",
        "pps_allowance_exceeded",
        "conntrack_allowance_exceeded",
    }
    assert all(query["MetricStat"]["Period"] == 300 for query in queries)
    by_id = {query["Id"]: query for query in queries}
    assert by_id["bw_in_allowance_exceeded"]["MetricStat"]["Metric"]["MetricName"] == "ethtool_bw_in_allowance_exceeded"
    assert by_id["bw_out_allowance_exceeded"]["MetricStat"]["Metric"]["MetricName"] == "ethtool_bw_out_allowance_exceeded"
    assert by_id["pps_allowance_exceeded"]["MetricStat"]["Metric"]["MetricName"] == "ethtool_pps_allowance_exceeded"
    assert by_id["conntrack_allowance_exceeded"]["MetricStat"]["Metric"]["MetricName"] == "ethtool_conntrack_allowance_exceeded"
    assert client.requests[1]["MetricDataQueries"] == queries
    assert client.requests[1]["NextToken"] == "next"


def _db(rows=1):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    for index in range(1, rows + 1):
        session.add(
            Ec2Inventory(
                inventory_id=index,
                resource_id=f"i-{index}",
                resource_type="ec2",
                account_id=f"account-{index}",
                region="us-east-1",
                state="running",
                instance_type="m5.large",
                metadata_json={"instance_store_present": False},
            )
        )
    session.commit()
    return session


class TrendAdapter:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def get_ec2_confidence_trend(
        self, instance_id, start_date, end_date, region=None, memory_source=None
    ):
        self.calls.append((instance_id, start_date, end_date, region, memory_source))
        if self.fail:
            raise RuntimeError("cloudwatch unavailable")
        result = {"daily": [], "buckets": {}}
        if memory_source:
            result["memory"] = {
                "source": memory_source,
                "daily": [],
                "buckets": {},
                "unit": "Percent",
                "headline_stat": "maximum",
            }
        return result


def test_confidence_service_caches_success_and_isolates_keys():
    ec2_trend_cache.clear()
    adapter = TrendAdapter()
    service = EC2Rightsizer(_db(rows=2))
    first = service.get_confidence_trend(1, adapter=adapter)
    second = service.get_confidence_trend(1, adapter=adapter)
    other = service.get_confidence_trend(2, adapter=adapter)
    assert first["cached"] is False
    assert second["cached"] is True
    assert other["cached"] is False
    assert len(adapter.calls) == 2
    assert first["metric"] == "CPUUtilization"
    assert first["headline_stat"] == "maximum"
    assert first["bucket_semantics"] == {
        "maximum": "trailing_window_from_daily_maximum",
        "percentiles": "latest_complete_epoch_aligned_window",
    }


def test_confidence_service_passes_persisted_memory_source():
    ec2_trend_cache.clear()
    db = _db()
    row = db.query(Ec2Inventory).filter(Ec2Inventory.inventory_id == 1).one()
    source = {
        "namespace": "Custom/Host",
        "metric_name": "MemoryUtilization",
        "dimensions": [{"Name": "InstanceId", "Value": "i-1"}],
    }
    row.metadata_json = {"instance_store_present": False, "memory_metric_source": source}
    db.commit()
    adapter = TrendAdapter()
    result = EC2Rightsizer(db).get_confidence_trend(1, adapter=adapter)
    assert adapter.calls[0][-1] == source
    assert result["memory"]["source"] == source


def test_confidence_failures_are_not_cached():
    ec2_trend_cache.clear()
    adapter = TrendAdapter(fail=True)
    service = EC2Rightsizer(_db())
    with pytest.raises(RuntimeError):
        service.get_confidence_trend(1, adapter=adapter)
    with pytest.raises(RuntimeError):
        service.get_confidence_trend(1, adapter=adapter)
    assert len(adapter.calls) == 2


def test_thread_safe_cache_expires_and_evicts_at_capacity():
    now = [0.0]
    cache = EC2TrendCache(ttl_seconds=3600, capacity=1000, clock=lambda: now[0])
    cache.set("expiring", {"value": 1})
    now[0] = 3600.0
    assert cache.get("expiring") is None
    for index in range(1001):
        cache.set(index, {"value": index})
    assert cache.get(0) is None
    assert cache.get(1000) == {"value": 1000}


def test_confidence_route_returns_404_and_maps_only_aws_failures_to_502(monkeypatch):
    with pytest.raises(HTTPException) as missing:
        get_ec2_rightsize_confidence_trend(999, db=_db())
    assert missing.value.status_code == 404

    def aws_fail(self, inventory_id, adapter=None, end_date=None):
        raise ClientError(
            {"Error": {"Code": "Throttling", "Message": "slow down"}},
            "GetMetricData",
        )

    monkeypatch.setattr(EC2Rightsizer, "get_confidence_trend", aws_fail)
    with pytest.raises(HTTPException) as unavailable:
        get_ec2_rightsize_confidence_trend(1, db=_db())
    assert unavailable.value.status_code == 502

    def programming_error(self, inventory_id, adapter=None, end_date=None):
        raise KeyError("unexpected response shape")

    monkeypatch.setattr(EC2Rightsizer, "get_confidence_trend", programming_error)
    with pytest.raises(KeyError):
        get_ec2_rightsize_confidence_trend(1, db=_db())
