"""Shared EC2 instance-type helpers."""
from __future__ import annotations

from typing import Optional, Tuple


GRAVITON_FAMILY_MAP = {
    "t2": "t4g",
    "t3": "t4g",
    "t3a": "t4g",
    "m5": "m6g",
    "m5a": "m6g",
    "m5n": "m6g",
    "m4": "m6g",
    "c5": "c6g",
    "c5a": "c6g",
    "c5n": "c6g",
    "c4": "c6g",
    "r5": "r6g",
    "r5a": "r6g",
    "r5b": "r6g",
    "r4": "r6g",
}

def split_instance_type(instance_type: str) -> Tuple[str, str]:
    """Split an EC2 instance type into family and size suffix."""
    family, _, size = (instance_type or "").partition(".")
    return family, size


def is_graviton_instance_type(instance_type: str) -> bool:
    """Return whether the instance type already belongs to a Graviton family."""
    family, _ = split_instance_type(instance_type)
    return family.endswith("g") and not family.endswith("ng")


def recommended_graviton_instance_type(instance_type: str) -> Optional[str]:
    """Return the like-for-like Graviton replacement when known."""
    family, size = split_instance_type(instance_type)
    target_family = GRAVITON_FAMILY_MAP.get(family)
    if not target_family or not size:
        return None
    return f"{target_family}.{size}"
