"""Customer-facing explanations for mandatory review warnings."""

WARNING_MESSAGES = {
    "GPU_UNUSED_FULL_WINDOW": (
        "No GPU device was observed busy for the full decision window. Removing "
        "GPU capability is conditional; confirm that a scheduled or infrequent "
        "accelerator job is not absent from this observation."
    ),
    "GPU_DEVICE_DEMAND_USES_WINDOW_MAXIMUM": (
        "GPU device demand uses the maximum simultaneous busy-device count in "
        "the observation window, protecting scheduled and bursty multi-GPU jobs."
    ),
    "GPU_OBSERVATION_WINDOW_TOO_SHORT": (
        "GPU telemetry covers less than the policy minimum observation window. "
        "A scheduled job may be missing, so this recommendation is conditional."
    ),
    "NETWORK_SUSTAINED_ABOVE_BASELINE": (
        "Sustained network demand (p99) exceeds the target's guaranteed baseline "
        "bandwidth. The instance would rely on EC2 burst capacity, which is "
        "time-limited and not guaranteed, so network may throttle under sustained "
        "load. Review network behavior before applying this recommendation."
    ),
    "NETWORK_BASELINE_ASSUMED": (
        "No published sustained baseline is available for this target, so the "
        "baseline was estimated from the instance's size and peak bandwidth. Treat "
        "the network comparison as approximate and validate before applying this "
        "recommendation."
    ),
    "MEMORY_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED": (
        "Usable instance memory telemetry was unavailable. The recommendation retains at "
        "least the current memory capacity instead of estimating a smaller requirement."
    ),
    "NON_DEFAULT_NETWORK_BANDWIDTH_WEIGHTING_REQUIRES_REVIEW": (
        "The instance uses non-default EC2 bandwidth weighting. Published instance-type "
        "baseline values might not represent its effective network and EBS allocation, so "
        "review the configured weighting before resizing."
    ),
    "INSTANCE_STORE_USAGE_UNKNOWN": (
        "The current instance has local instance-store capacity, or its use could not be "
        "proven absent. Review local mounts and data durability before resizing."
    ),
    "HIGH_NETWORK_USAGE_REVIEW_REQUIRED": (
        "The workload shows material network demand relative to the target's documented capacity. "
        "EC2 network performance may use baseline and burst behavior, and aggregated CloudWatch "
        "metrics may not capture short microbursts. Review detailed network and ENA allowance "
        "metrics before applying this recommendation."
    ),
    "HIGH_EBS_USAGE_REVIEW_REQUIRED": (
        "The workload shows material EBS IOPS or throughput demand relative to the target's "
        "documented capacity. Actual EBS performance depends on volume configuration, I/O size, "
        "access pattern, instance limits, and burst behavior. Review EBS queue, latency, "
        "throttling, and exceeded metrics before applying this recommendation."
    ),
    "RESOURCE_BASELINE_CAPACITY_UNKNOWN": (
        "A reliable sustained baseline was not available for this target. The recommendation "
        "passed known compatibility checks but requires performance validation."
    ),
    "NETWORK_BURST_CAPACITY_REQUIRES_REVIEW": (
        "The target is documented with burst or 'up to' network performance. Available telemetry "
        "does not prove that sustained or short-duration network demand will remain within the "
        "target's baseline capacity. Review network behavior before applying this recommendation."
    ),
    "NETWORK_USAGE_REVIEW_REQUIRED": (
        "Observed network demand is material relative to the target's sustained baseline. "
        "Review network traffic and headroom before applying the change."
    ),
    "NETWORK_CAPABILITY_UNKNOWN": (
        "The target's relative network capability is unknown. Validate network performance "
        "before applying this recommendation."
    ),
    "NETWORK_CAPABILITY_REDUCTION": (
        "The target has a lower documented network capability class than the current instance. "
        "Confirm the workload can tolerate the reduction."
    ),
    "NETWORK_CAPACITY_APPROXIMATE": (
        "Current and target network capabilities are only available as qualitative classes. "
        "The comparison is approximate and requires review."
    ),
    "NETWORK_ALLOWANCE_EXCEEDED": (
        "Bandwidth allowance-exceeded events were observed. Review ENA metrics and choose a "
        "target with clearly greater network capacity."
    ),
    "PPS_ALLOWANCE_EXCEEDED": (
        "Packet-per-second allowance-exceeded events were observed. Review packet-rate demand "
        "and the target's PPS capability."
    ),
    "CONNTRACK_ALLOWANCE_EXCEEDED": (
        "Connection-tracking allowance-exceeded events were observed. Review connection "
        "tracking demand before resizing."
    ),
    "NETWORK_ALLOWANCE_METRICS_MISSING": (
        "One or more ENA allowance metrics were unavailable. Their absence does not prove that "
        "the workload remained within its network allowances."
    ),
    "NETWORK_ALLOWANCE_METRICS_REQUIRED": (
        "The active policy requires complete network allowance telemetry before a recommendation "
        "can be actionable."
    ),
    "EBS_USAGE_REVIEW_REQUIRED": (
        "Observed EBS demand is material relative to the target baseline. Review IOPS and "
        "throughput headroom before applying the change."
    ),
    "EBS_BURST_CAPACITY_REQUIRES_REVIEW": (
        "The target's EBS performance depends on burst or temporary maximum capacity. Validate "
        "sustained storage performance before resizing."
    ),
    "EBS_BASELINE_CAPABILITY_UNKNOWN": (
        "A reliable sustained EBS baseline is unavailable for the target. Known compatibility "
        "checks passed, but storage performance still requires validation."
    ),
    "EBS_THROTTLING_OR_EXCEEDED": (
        "EBS throttling or instance EBS exceeded events were observed. Select clearly greater "
        "capacity or investigate the storage workload before resizing."
    ),
    "EBS_QUEUE_REVIEW_REQUIRED": (
        "High EBS queue depth was observed. Review queueing, I/O size, and access patterns before "
        "applying the recommendation."
    ),
    "EBS_LATENCY_REVIEW_REQUIRED": (
        "High EBS latency was observed. Review volume and instance-level storage behavior before "
        "applying the recommendation."
    ),
    "EBS_BURST_BALANCE_DEPLETED": (
        "An EBS burst balance fell below the safe threshold. Validate sustained baseline "
        "performance before resizing."
    ),
    "EBS_EXCEEDED_METRICS_MISSING": (
        "The active policy requires EBS exceeded-check telemetry, but one or more metrics were "
        "unavailable."
    ),
    "EBS_DIRECTIONAL_METRICS_INCOMPLETE": (
        "Read and write EBS series did not contain matching timestamps. Observed demand was "
        "retained as a lower bound, but complete combined demand could not be proven."
    ),
}
