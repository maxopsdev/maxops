"""Pure EC2 instance-type string helpers."""

from __future__ import annotations

import re


def instance_family(instance_type: str) -> str:
    return instance_type.partition(".")[0]


def family_class(instance_type: str) -> str:
    family = instance_family(instance_type)
    match = re.match(r"[a-z]+", family.lower())
    return match.group(0) if match else family.lower()
