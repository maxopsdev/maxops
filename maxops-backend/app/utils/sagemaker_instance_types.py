"""SageMaker GPU instance types that are not always present in the EC2 catalog.

SageMaker continues to accept several older GPU types after they stopped being
returned by ``DescribeInstanceTypes``.  Keep this table explicit: a family
letter is not sufficient evidence that a SageMaker type has a GPU.
"""

from __future__ import annotations

from typing import Dict

from app.utils.ec2_gpu_info import EC2GpuInfo


def _gpu(
    instance_type: str,
    device_count: int,
    model: str,
    memory_mib: int,
) -> EC2GpuInfo:
    """Build one immutable seeded GPU capability record."""
    return EC2GpuInfo(
        instance_type=instance_type,
        device_count=device_count,
        fractional=False,
        total_memory_mib=memory_mib,
        manufacturer="NVIDIA",
        model=model,
    )


# The 20 GPU types in the SageMaker Studio Classic notebook table.  Values are
# addressable devices, not CUDA cores or the summed CloudWatch percentage.
SAGEMAKER_GPU_INSTANCE_TYPES: Dict[str, EC2GpuInfo] = {
    "p2.xlarge": _gpu("p2.xlarge", 1, "K80", 12 * 1024),
    "p2.8xlarge": _gpu("p2.8xlarge", 8, "K80", 96 * 1024),
    "p2.16xlarge": _gpu("p2.16xlarge", 16, "K80", 192 * 1024),
    "p3.2xlarge": _gpu("p3.2xlarge", 1, "V100", 16 * 1024),
    "p3.8xlarge": _gpu("p3.8xlarge", 4, "V100", 64 * 1024),
    "p3.16xlarge": _gpu("p3.16xlarge", 8, "V100", 128 * 1024),
    "p4d.24xlarge": _gpu("p4d.24xlarge", 8, "A100", 320 * 1024),
    "p4de.24xlarge": _gpu("p4de.24xlarge", 8, "A100", 640 * 1024),
    "p5.48xlarge": _gpu("p5.48xlarge", 8, "H100", 640 * 1024),
    "g4dn.xlarge": _gpu("g4dn.xlarge", 1, "T4", 16 * 1024),
    "g4dn.2xlarge": _gpu("g4dn.2xlarge", 1, "T4", 16 * 1024),
    "g4dn.4xlarge": _gpu("g4dn.4xlarge", 1, "T4", 16 * 1024),
    "g4dn.8xlarge": _gpu("g4dn.8xlarge", 1, "T4", 16 * 1024),
    "g4dn.12xlarge": _gpu("g4dn.12xlarge", 4, "T4", 64 * 1024),
    "g5.xlarge": _gpu("g5.xlarge", 1, "A10G", 24 * 1024),
    "g5.2xlarge": _gpu("g5.2xlarge", 1, "A10G", 24 * 1024),
    "g5.4xlarge": _gpu("g5.4xlarge", 1, "A10G", 24 * 1024),
    "g5.8xlarge": _gpu("g5.8xlarge", 1, "A10G", 24 * 1024),
    "g5.12xlarge": _gpu("g5.12xlarge", 4, "A10G", 96 * 1024),
    "g5.16xlarge": _gpu("g5.16xlarge", 1, "A10G", 24 * 1024),
}


def seeded_gpu_info(instance_type: str) -> EC2GpuInfo | None:
    """Return seeded GPU data for a bare EC2-style type, or ``None``."""
    return SAGEMAKER_GPU_INSTANCE_TYPES.get(str(instance_type or "").strip())

