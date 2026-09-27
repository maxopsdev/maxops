from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np

from app.adapters.aws.adapter import AWSAdapter, derive_ec2_metric_period
from app.checks.ec2.idle_instances import check_ec2_idle_instances
from app.services.scan_service import CachedAWSAdapter, _execute_check_across_settings


def _resource(resource_id="i-test"):
    return {
        "resource_id": resource_id,
        "resource_type": "ec2",
        "region": "us-east-1",
        "state": "running",
        "instance_type": "m5.xlarge",
        "metadata": {},
    }


class CheckAdapter:
    def __init__(self, utilization):
        self.utilization = utilization

    def get_resources(self, resource_type, filters=None, region=None):
        return [_resource()]

    def get_resource_utilization(self, resource_id, resource_type, *args, **kwargs):
        return self.utilization


def test_empty_or_missing_idle_cpu_history_is_not_flagged():
    for metric_history in ({}, {"cpuutilization": {"maximum": []}}):
        result = check_ec2_idle_instances(
            CheckAdapter(
                {
                    "cpuutilization": 1.0,
                    "metric_history": metric_history,
                }
            )
        )
        assert result == []


def test_scan_skips_unknown_policy_check(caplog):
    # ec2_rightsize_candidate was removed when the EC2 rightsizer replaced it,
    # so saved policies in existing databases still name it. Keep a retired id
    # here: swapping in a live check inverts the test without failing it.
    result = _execute_check_across_settings(
        "ec2_rightsize_candidate",
        {},
        SimpleNamespace(account="123456789012", regions=["us-east-1"]),
        "ec2",
        {},
        None,
    )

    assert result == []
    assert "Skipping unknown policy check ec2_rightsize_candidate" in caplog.text


class MetricClient:
    def __init__(self):
        self.requests = []

    def list_metrics(self, **request):
        return {"Metrics": []}

    def get_metric_data(self, **request):
        self.requests.append(request)
        return {"MetricDataResults": []}


def _adapter(client):
    adapter = object.__new__(AWSAdapter)
    adapter._default_region = "us-east-1"
    adapter.cloudwatch_client = client
    return adapter


def test_ec2_telemetry_paths_use_one_period_and_never_exceed_window():
    client = MetricClient()
    adapter = _adapter(client)
    end = datetime(2026, 7, 13, tzinfo=timezone.utc)
    start = end - timedelta(days=14)

    utilization = adapter.get_resource_utilization("i-test", "ec2", start, end)
    adapter.get_ec2_rightsizing_metrics("i-test", start, end)

    periods = {
        query["MetricStat"]["Period"]
        for request in client.requests
        for query in request["MetricDataQueries"]
    }
    assert periods == {300}
    assert utilization["memory_metric_status"] == "unavailable"
    assert derive_ec2_metric_period(end - timedelta(seconds=120), end) == 60
    assert derive_ec2_metric_period(end - timedelta(seconds=120), end) <= 120


class SeriesMetricClient:
    def __init__(self, values=None, maximum=None, pages=None):
        self.values = list(values or [])
        self.maximum = list(maximum if maximum is not None else self.values)
        self.pages = pages
        self.requests = []

    def list_metrics(self, **request):
        return {"Metrics": []}

    def get_metric_data(self, **request):
        self.requests.append(request)
        query_ids = {query["Id"] for query in request["MetricDataQueries"]}
        if any(query_id.startswith("memory_") for query_id in query_ids):
            return {"MetricDataResults": []}
        if self.pages is not None:
            return self.pages[1] if request.get("NextToken") else self.pages[0]

        timestamp_start = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)
        timestamps = [
            timestamp_start + timedelta(minutes=index)
            for index in range(len(self.values))
        ]
        results = []
        for query in request["MetricDataQueries"]:
            if query["Id"] == "cpu_average":
                values = self.values
            elif query["Id"] == "cpu_maximum":
                values = self.maximum
            else:
                values = []
            results.append({"Id": query["Id"], "Timestamps": timestamps, "Values": values})
        return {"MetricDataResults": results}


def test_nightly_spike_keeps_window_percentiles_low_and_flags_idle():
    client = SeriesMetricClient(values=[12.0] + [2.0] * 99)
    utilization = _adapter(client).get_resource_utilization(
        "i-test",
        "ec2",
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 8, tzinfo=timezone.utc),
    )

    assert utilization["metric_summary"]["cpuutilization"]["p90"] == 2.0
    result = check_ec2_idle_instances(CheckAdapter(utilization))

    assert len(result) == 1
    assert result[0]["metadata"]["cpu_p90"] == 2.0


def test_window_percentiles_match_numpy_over_average_series():
    values = [1.0, 2.0, 3.0, 4.0, 20.0]
    client = SeriesMetricClient(values=values, maximum=[2.0, 4.0, 6.0, 8.0, 40.0])
    utilization = _adapter(client).get_resource_utilization(
        "i-test",
        "ec2",
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 8, tzinfo=timezone.utc),
    )
    summary = utilization["metric_summary"]["cpuutilization"]

    assert summary["average"] == float(np.mean(values))
    assert summary["maximum"] == 40.0
    for percentile in (90, 95, 99):
        assert summary[f"p{percentile}"] == float(np.percentile(values, percentile))


def test_empty_series_summary_is_unknown_and_check_does_not_flag_resource():
    utilization = _adapter(MetricClient()).get_resource_utilization(
        "i-test",
        "ec2",
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 8, tzinfo=timezone.utc),
    )
    for summary in utilization["metric_summary"].values():
        assert summary["sample_count"] == 0
        assert all(summary[key] is None for key in ("average", "maximum", "p90", "p95", "p99"))
    assert check_ec2_idle_instances(CheckAdapter(utilization)) == []


def test_ec2_utilization_requests_only_average_and_maximum_statistics():
    client = MetricClient()
    _adapter(client).get_resource_utilization(
        "i-test",
        "ec2",
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 8, tzinfo=timezone.utc),
    )

    assert len(client.requests[0]["MetricDataQueries"]) == 6
    assert {
        query["MetricStat"]["Stat"]
        for request in client.requests
        for query in request["MetricDataQueries"]
    } == {"Average", "Maximum"}


def test_ec2_utilization_paginates_and_preserves_both_pages():
    first_timestamp = datetime(2026, 7, 7, tzinfo=timezone.utc)
    second_timestamp = datetime(2026, 7, 6, tzinfo=timezone.utc)
    client = SeriesMetricClient(
        pages=[
            {
                "MetricDataResults": [
                    {"Id": "cpu_average", "Timestamps": [first_timestamp], "Values": [4.0]},
                    {"Id": "cpu_maximum", "Timestamps": [first_timestamp], "Values": [4.0]},
                ],
                "NextToken": "page-2",
            },
            {
                "MetricDataResults": [
                    {"Id": "cpu_average", "Timestamps": [second_timestamp], "Values": [2.0]},
                    {"Id": "cpu_maximum", "Timestamps": [second_timestamp], "Values": [2.0]},
                ]
            },
        ]
    )
    utilization = _adapter(client).get_resource_utilization(
        "i-test",
        "ec2",
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 8, tzinfo=timezone.utc),
    )

    history = utilization["metric_history"]["cpuutilization"]
    assert history["average"] == [4.0, 2.0]
    assert history["timestamps"] == [first_timestamp.isoformat(), second_timestamp.isoformat()]
    assert utilization["metric_summary"]["cpuutilization"]["sample_count"] == 2


def test_cached_resource_utilization_is_called_once_and_returns_isolated_copies():
    class Adapter:
        def __init__(self):
            self.calls = 0

        def get_resource_utilization(self, *args, **kwargs):
            self.calls += 1
            return {"metric_history": {"cpuutilization": {"maximum": [1.0]}}}

    adapter = Adapter()
    cached = CachedAWSAdapter(adapter)

    # Each check captures utcnow() once before its instance loop, so the two
    # EC2 checks' end timestamps are separated by however long the first check
    # ran. Inject that gap explicitly instead of reading the clock twice: a
    # same-instant test cannot reproduce the condition that matters. The gap
    # here also straddles wall-clock ten-minute boundaries.
    first_end = datetime(2026, 9, 13, 10, 8, 17, 123456)
    second_end = first_end + timedelta(minutes=47, seconds=31)

    first = cached.get_resource_utilization(
        "i-test",
        "ec2",
        first_end - timedelta(days=14),
        first_end,
        "us-east-1",
    )
    first["metric_history"]["cpuutilization"]["maximum"].append(99.0)
    second = cached.get_resource_utilization(
        "i-test",
        "ec2",
        second_end - timedelta(days=14),
        second_end,
        "us-east-1",
    )

    assert adapter.calls == 1
    assert second["metric_history"]["cpuutilization"]["maximum"] == [1.0]

    # The key must still discriminate: a genuinely different lookback window is
    # different data and must not be served from cache.
    cached.get_resource_utilization(
        "i-test",
        "ec2",
        second_end - timedelta(days=7),
        second_end,
        "us-east-1",
    )
    assert adapter.calls == 2
