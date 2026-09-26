"""Pure EC2-shape candidate primitives shared by EC2 and ASG rightsizers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
from collections.abc import Callable
from typing import Any, Iterable, Protocol

from rightsizers.common.instance_types import family_class


class InstanceShape(Protocol):
    instance_type: str
    vcpus: float | None
    memory_mib: float | None
    coremark: float | None
    architectures: tuple[str, ...]
    gpu_device_count: int | None
    gpu_fractional: bool
    gpu_model: str | None
    gpu_memory_mib_per_device: int | None


@dataclass(frozen=True)
class EC2CandidatePolicy:
    gated_family_classes: tuple[str, ...] = (
        "t",
        "p",
        "g",
        "inf",
        "trn",
        "dl",
        "f",
        "vt",
        "hpc",
        "u",
    )
    memory_preview_enabled: bool = True
    graviton_preview_enabled: bool = True


@dataclass(frozen=True)
class CPUCapacityBasis:
    kind: str
    current_units: float
    target_units: float


class CPUCapacityKind:
    COREMARK = "coremark"
    VCPU_FALLBACK = "vcpu_fallback"


def gpu_target_allowed(
    current: InstanceShape,
    target: InstanceShape,
    required_devices: int,
    required_vram_mib: float,
) -> tuple[bool, str | None, bool]:
    """Evaluate same-model GPU capability for a GPU source.

    Returns ``(allowed, rejection_code, conditional)``. Non-GPU sources return
    ``(True, None, False)`` so existing CPU-only behavior is unchanged.
    """
    if current.gpu_device_count is None:
        return True, None, False
    if target.gpu_device_count is None:
        return (
            (True, "GPU_UNUSED_FULL_WINDOW", True)
            if required_devices == 0
            else (False, "GPU_DEVICE_COUNT_REQUIREMENT_NOT_MET", False)
        )
    if target.gpu_model != current.gpu_model:
        return False, "GPU_MODEL_MISMATCH", False
    if target.gpu_device_count < required_devices:
        return False, "GPU_DEVICE_COUNT_REQUIREMENT_NOT_MET", False
    if (
        target.gpu_memory_mib_per_device is None
        or target.gpu_memory_mib_per_device < required_vram_mib
    ):
        return False, "GPU_VRAM_REQUIREMENT_NOT_MET", False
    return True, None, False


def candidate_policy_evidence(policy: EC2CandidatePolicy) -> dict[str, Any]:
    """Expose stable policy evidence while keeping preview switches internal."""
    return {"gated_family_classes": policy.gated_family_classes}


def exact_savings(
    current_cost: float | Decimal, target_cost: float | Decimal
) -> Decimal:
    return Decimal(str(current_cost)) - Decimal(str(target_cost))


def savings_is_eligible(
    current_cost: float | Decimal,
    target_cost: float | Decimal,
    minimum: float | Decimal,
) -> bool:
    savings = exact_savings(current_cost, target_cost)
    return savings >= Decimal("0.01") and savings >= Decimal(str(minimum))


def architecture_overlaps(current: InstanceShape, target: InstanceShape) -> bool:
    return bool(
        current.architectures
        and target.architectures
        and set(current.architectures) & set(target.architectures)
    )


def family_target_allowed(
    current_type: str,
    target_type: str,
    policy: EC2CandidatePolicy,
) -> bool:
    target_class = family_class(target_type)
    return not (
        target_class in set(policy.gated_family_classes)
        and family_class(current_type) != target_class
    )


def comparable_cpu_capacity(
    current: InstanceShape, target: InstanceShape
) -> CPUCapacityBasis | None:
    if (
        architecture_overlaps(current, target)
        and current.coremark is not None
        and current.coremark > 0
        and target.coremark is not None
        and target.coremark > 0
    ):
        return CPUCapacityBasis(
            CPUCapacityKind.COREMARK, current.coremark, target.coremark
        )
    if (
        current.vcpus is not None
        and current.vcpus > 0
        and target.vcpus is not None
        and target.vcpus > 0
    ):
        return CPUCapacityBasis(
            CPUCapacityKind.VCPU_FALLBACK, current.vcpus, target.vcpus
        )
    return None


def performance_evidence(
    current: InstanceShape, target: InstanceShape
) -> tuple[float | None, float | None]:
    if (
        not architecture_overlaps(current, target)
        or not current.coremark
        or target.coremark is None
    ):
        return None, None
    ratio = target.coremark / current.coremark
    return ratio, (target.coremark - current.coremark) / current.coremark * 100.0


def retains_raw_capacity(
    current: InstanceShape,
    target: InstanceShape,
    *,
    current_count: int = 1,
    target_count: int = 1,
) -> bool:
    cpu_retained, memory_retained = raw_capacity_retention(
        current,
        target,
        current_count=current_count,
        target_count=target_count,
    )
    return cpu_retained and memory_retained


def raw_capacity_retention(
    current: InstanceShape,
    target: InstanceShape,
    *,
    current_count: int = 1,
    target_count: int = 1,
) -> tuple[bool, bool]:
    """Return raw aggregate CPU and memory retention independently."""
    cpu_retained = bool(
        current.vcpus is not None
        and target.vcpus is not None
        and target.vcpus * target_count >= current.vcpus * current_count
    )
    memory_retained = bool(
        current.memory_mib is not None
        and target.memory_mib is not None
        and target.memory_mib * target_count >= current.memory_mib * current_count
    )
    return cpu_retained, memory_retained


def limit_preserving_balanced(
    candidates: list[dict[str, Any]],
    limit: int,
    tier_ratios: Iterable[tuple[str, float]],
    *,
    utilization_key: str = "projected_util",
    savings_key: str = "monthly_savings",
    type_key: str = "target_instance_type",
    selection_key: Callable[[dict[str, Any]], Any] | None = None,
    display_sort_key: Callable[[dict[str, Any]], Any] | None = None,
) -> list[dict[str, Any]]:
    if len(candidates) <= limit:
        return candidates
    balanced_ratio = next(
        (ratio for name, ratio in tier_ratios if name == "balanced"), None
    )
    if balanced_ratio is None:
        raise ValueError("tier ratios must define balanced")
    choose = selection_key or (
        lambda candidate: (
            -float(candidate[savings_key]),
            str(candidate[type_key]),
        )
    )
    display = display_sort_key or (
        lambda candidate: (-float(candidate[savings_key]), str(candidate[type_key]))
    )
    balanced = min(
        (
            candidate
            for candidate in candidates
            if candidate.get(utilization_key) is not None
            and float(candidate[utilization_key]) <= balanced_ratio
        ),
        key=choose,
        default=None,
    )
    limited = candidates[:limit]
    if balanced is not None and balanced not in limited:
        limited = [
            balanced,
            *[item for item in candidates if item is not balanced][: max(0, limit - 1)],
        ]
        limited.sort(key=display)
    return limited


def coremark_catalog_coverage(entries: Iterable[InstanceShape]) -> dict[str, Any]:
    eligible = {
        entry.instance_type: entry
        for entry in entries
        if not entry.instance_type.endswith(".metal")
        and entry.vcpus is not None
        and entry.vcpus > 0
    }
    covered = [
        entry
        for entry in eligible.values()
        if entry.coremark is not None
        and math.isfinite(entry.coremark)
        and entry.coremark > 0
    ]
    denominator = len(eligible)
    numerator = len(covered)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "ratio": numerator / denominator if denominator else None,
    }
