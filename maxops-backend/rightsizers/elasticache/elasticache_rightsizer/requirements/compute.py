def projected_engine_cpu(engine_cpu_p99: float | None) -> float | None:
    return engine_cpu_p99


def projected_host_cpu(
    host_cpu_p99: float | None, current_vcpus: int | None, target_vcpus: int | None
) -> float | None:
    if host_cpu_p99 is None or not current_vcpus or not target_vcpus:
        return None
    return float(host_cpu_p99) * float(current_vcpus) / float(target_vcpus)
