"""EBS gp2 Volumes Convertible to gp3 Check."""
from typing import List, Dict, Any, Optional

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


def check_ebs_gp2_volumes_convertible_to_gp3(
    aws_adapter,
    min_size_gb: int = 1,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Flag gp2 EBS volumes that can be migrated to gp3 for lower cost."""
    volumes = aws_adapter.get_resources("ebs", region=region)
    flagged: List[Dict[str, Any]] = []

    for volume in volumes:
        metadata = volume.get("metadata", {})
        volume_type = (
            metadata.get("volume_type")
            or metadata.get("VolumeType")
            or ""
        )
        if str(volume_type).lower() != "gp2":
            continue

        raw_size = (
            metadata.get("size")
            or metadata.get("Size")
            or metadata.get("size_gb")
        )
        try:
            size_gb = float(raw_size)
        except (TypeError, ValueError):
            continue

        if size_gb < min_size_gb:
            continue

        # Simplified estimate (US-East): gp2 $0.10/GB-month, gp3 $0.08/GB-month.
        current_monthly_cost = size_gb * 0.10
        optimized_monthly_cost = size_gb * 0.08
        potential_savings_monthly = max(0.0, current_monthly_cost - optimized_monthly_cost)

        md = volume.setdefault("metadata", {})
        md["current_volume_type"] = "gp2"
        md["recommended_volume_type"] = "gp3"
        md["size_gb"] = round(size_gb, 2)
        md["current_monthly_cost"] = round(current_monthly_cost, 4)
        md["optimized_monthly_cost"] = round(optimized_monthly_cost, 4)
        md["potential_savings_monthly"] = round(potential_savings_monthly, 4)
        md["potential_savings_yearly"] = round(potential_savings_monthly * 12, 2)
        md["recommended_action"] = "modify_volume_type_to_gp3"
        md["check_reason"] = create_check_reason(
            "gp2_convertible_to_gp3",
            {
                "volume_type": "gp2",
                "recommended_type": "gp3",
                "size_gb": round(size_gb, 2),
            },
        )
        flagged.append(volume)

    return flagged


check_registry.register(CheckMetadata(
    check_id="ebs_gp2_volumes_convertible_to_gp3",
    name="EBS gp2 Volumes Convertible to gp3",
    description="Identifies gp2 EBS volumes that can be moved to gp3 to reduce cost",
    resource_type="ebs",
    check_function=check_ebs_gp2_volumes_convertible_to_gp3,
    default_action="migrate_ebs_gp2_to_gp3",
    parameters={
        "min_size_gb": 1,
        "region": None,
    },
))
