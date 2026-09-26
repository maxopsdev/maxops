"""Shared helpers for OpenSearch checks."""
from __future__ import annotations

import datetime as dt
import json
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_os_client(aws_adapter, region: Optional[str]):
    try:
        return aws_adapter.session.client("opensearch", region_name=region)
    except Exception:
        return aws_adapter.session.client("es", region_name=region)


def list_domains(client) -> List[str]:
    response = client.list_domain_names()
    return [d.get("DomainName") for d in response.get("DomainNames", []) if d.get("DomainName")]


def describe_domain(client, domain_name: str) -> Dict[str, Any]:
    response = client.describe_domain(DomainName=domain_name)
    return response.get("DomainStatus", {})


def metric_stat(
    cloudwatch_client,
    domain_name: str,
    metric_name: str,
    start_date: dt.datetime,
    end_date: dt.datetime,
    stat: str = "Average",
    period_seconds: int = 3600,
) -> Optional[float]:
    response = cloudwatch_client.get_metric_statistics(
        Namespace="AWS/ES",
        MetricName=metric_name,
        Dimensions=[{"Name": "DomainName", "Value": domain_name}],
        StartTime=start_date,
        EndTime=end_date,
        Period=period_seconds,
        Statistics=[stat],
    )
    datapoints = response.get("Datapoints", [])
    if not datapoints:
        return None
    values = [to_float(dp.get(stat)) for dp in datapoints if dp.get(stat) is not None]
    if not values:
        return None
    if stat == "Sum":
        return sum(values)
    if stat == "Maximum":
        return max(values)
    if stat == "Minimum":
        return min(values)
    return sum(values) / len(values)


def get_domain_endpoint(domain_status: Dict[str, Any]) -> Optional[str]:
    endpoint = domain_status.get("Endpoint")
    if endpoint:
        return str(endpoint)
    endpoints = domain_status.get("Endpoints") or {}
    if isinstance(endpoints, dict):
        for _k, v in endpoints.items():
            if v:
                return str(v)
    return None


def signed_get_json(
    aws_adapter,
    region: str,
    endpoint: str,
    path_with_query: str,
    timeout_seconds: int = 8,
) -> Optional[Any]:
    # Best-effort signed request to domain APIs (_cat, _plugins, etc.).
    try:
        creds = aws_adapter.session.get_credentials()
        if creds is None:
            return None
        frozen = creds.get_frozen_credentials()
        url = f"https://{endpoint}{path_with_query}"
        req = AWSRequest(method="GET", url=url, data=None, headers={"host": endpoint})
        SigV4Auth(frozen, "es", region).add_auth(req)
        headers = dict(req.headers.items())
        http_req = urllib.request.Request(url=url, method="GET", headers=headers)
        with urllib.request.urlopen(http_req, timeout=timeout_seconds) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body)
    except Exception:
        return None


def parse_size_gib(size_text: Any) -> Optional[float]:
    s = str(size_text or "").strip().lower()
    if not s:
        return None
    # Handles values like "1.2gb", "532mb", "1024b", "0"
    try:
        if s.endswith("gb"):
            return float(s[:-2])
        if s.endswith("gib"):
            return float(s[:-3])
        if s.endswith("mb"):
            return float(s[:-2]) / 1024.0
        if s.endswith("mib"):
            return float(s[:-3]) / 1024.0
        if s.endswith("kb"):
            return float(s[:-2]) / (1024.0 * 1024.0)
        if s.endswith("b"):
            return float(s[:-1]) / (1024.0 * 1024.0 * 1024.0)
        return float(s) / (1024.0 * 1024.0 * 1024.0)
    except ValueError:
        return None


def parse_generation(instance_type: str) -> Tuple[Optional[str], Optional[int], bool]:
    # e.g. m5.large, r6g.xlarge, i3.large
    if not instance_type:
        return None, None, False
    left = str(instance_type).split(".", 1)[0].lower()
    if not left:
        return None, None, False
    family = left[0]
    digits = ""
    idx = 1
    while idx < len(left) and left[idx].isdigit():
        digits += left[idx]
        idx += 1
    generation = int(digits) if digits else None
    graviton = "g" in left[idx:]
    return family, generation, graviton

