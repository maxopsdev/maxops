"""Shared helpers for ASG checks."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def list_asgs(autoscaling_client) -> List[Dict[str, Any]]:
    groups: List[Dict[str, Any]] = []
    paginator = autoscaling_client.get_paginator("describe_auto_scaling_groups")
    for page in paginator.paginate():
        groups.extend(page.get("AutoScalingGroups", []))
    return groups


def asg_instance_ids(asg: Dict[str, Any]) -> List[str]:
    ids: List[str] = []
    for inst in asg.get("Instances", []) or []:
        iid = inst.get("InstanceId")
        if iid:
            ids.append(iid)
    return ids


def describe_instances(ec2_client, instance_ids: Sequence[str]) -> List[Dict[str, Any]]:
    if not instance_ids:
        return []
    out: List[Dict[str, Any]] = []
    for i in range(0, len(instance_ids), 100):
        batch = list(instance_ids[i : i + 100])
        resp = ec2_client.describe_instances(InstanceIds=batch)
        for res in resp.get("Reservations", []):
            out.extend(res.get("Instances", []))
    return out


def sum_metric_for_dimension(
    cloudwatch_client,
    namespace: str,
    metric_name: str,
    dimensions: List[Dict[str, str]],
    start_date: datetime,
    end_date: datetime,
    period_seconds: int = 3600,
) -> float:
    response = cloudwatch_client.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=dimensions,
        StartTime=start_date,
        EndTime=end_date,
        Period=period_seconds,
        Statistics=["Sum"],
    )
    return sum(to_float(dp.get("Sum")) for dp in response.get("Datapoints", []))


def avg_metric_for_dimension(
    cloudwatch_client,
    namespace: str,
    metric_name: str,
    dimensions: List[Dict[str, str]],
    start_date: datetime,
    end_date: datetime,
    period_seconds: int = 3600,
) -> Optional[float]:
    response = cloudwatch_client.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=dimensions,
        StartTime=start_date,
        EndTime=end_date,
        Period=period_seconds,
        Statistics=["Average"],
    )
    datapoints = response.get("Datapoints", [])
    if not datapoints:
        return None
    values = [to_float(dp.get("Average")) for dp in datapoints if dp.get("Average") is not None]
    if not values:
        return None
    return sum(values) / len(values)


def avg_ec2_cpu_for_instances(
    cloudwatch_client,
    instance_ids: Sequence[str],
    start_date: datetime,
    end_date: datetime,
) -> Optional[float]:
    if not instance_ids:
        return None
    values: List[float] = []
    for iid in instance_ids:
        avg_cpu = avg_metric_for_dimension(
            cloudwatch_client,
            "AWS/EC2",
            "CPUUtilization",
            [{"Name": "InstanceId", "Value": iid}],
            start_date,
            end_date,
        )
        if avg_cpu is not None:
            values.append(avg_cpu)
    if not values:
        return None
    return sum(values) / len(values)


def total_ec2_network_bytes_for_instances(
    cloudwatch_client,
    instance_ids: Sequence[str],
    start_date: datetime,
    end_date: datetime,
) -> float:
    total = 0.0
    for iid in instance_ids:
        dims = [{"Name": "InstanceId", "Value": iid}]
        total += sum_metric_for_dimension(
            cloudwatch_client, "AWS/EC2", "NetworkIn", dims, start_date, end_date
        )
        total += sum_metric_for_dimension(
            cloudwatch_client, "AWS/EC2", "NetworkOut", dims, start_date, end_date
        )
    return total


def split_instance_lifecycle(instances: Sequence[Dict[str, Any]]) -> Tuple[int, int]:
    on_demand = 0
    spot = 0
    for inst in instances:
        lifecycle = str(inst.get("InstanceLifecycle") or "").lower()
        if lifecycle == "spot":
            spot += 1
        else:
            on_demand += 1
    return on_demand, spot


def parse_instance_type_family_generation(instance_type: str) -> Tuple[Optional[str], Optional[int], bool]:
    # Examples:
    # - m5.large -> family m, generation 5, graviton False
    # - m6g.large -> family m, generation 6, graviton True
    if not instance_type or "." not in instance_type:
        return None, None, False
    left = instance_type.split(".", 1)[0].lower()
    if not left:
        return None, None, False

    family = left[0]
    digits = ""
    for ch in left[1:]:
        if ch.isdigit():
            digits += ch
        else:
            break
    generation = int(digits) if digits else None
    graviton = "g" in left[len(family + digits) :]
    return family, generation, graviton
