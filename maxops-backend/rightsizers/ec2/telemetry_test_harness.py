"""Live EC2 telemetry fixture.  See ec2_rightsizer_telemetry_test_spec.md."""

from __future__ import annotations

from pathlib import Path

from rightsizers.common.telemetry_testing import (
    StateStore, ValidationFailure, add_reused_network, assert_complete_responses,
    assert_query_map, assert_state_identity, aws_tags, caller_identity,
    cleanup_instance_role, cloudwatch_agent_config, command_readiness,
    common_create_parser, common_state_parser, configure_cloudwatch_agent,
    finalize_cleanup, launch_managed_instance, latest_al2023_ami, make_run_id,
    metric_data_calls, new_session, print_result, production_adapter,
    require_apply, require_series, run_validation, select_offered_type,
    state_path_for, submit_host_workload, terminate_instance, wait_for_instance_profile,
    wait_for_ssm, create_instance_role, default_network,
)

FOLDER = Path(__file__).resolve().parent
DEFINITIONS = {
    "cpu_percent": ("AWS/EC2", "CPUUtilization", "Average"),
    "network_in_bytes": ("AWS/EC2", "NetworkIn", "Sum"),
    "network_out_bytes": ("AWS/EC2", "NetworkOut", "Sum"),
    "network_packets_in": ("AWS/EC2", "NetworkPacketsIn", "Sum"),
    "network_packets_out": ("AWS/EC2", "NetworkPacketsOut", "Sum"),
    "ebs_read_operations": ("AWS/EC2", "EBSReadOps", "Sum"),
    "ebs_write_operations": ("AWS/EC2", "EBSWriteOps", "Sum"),
    "ebs_read_bytes": ("AWS/EC2", "EBSReadBytes", "Sum"),
    "ebs_write_bytes": ("AWS/EC2", "EBSWriteBytes", "Sum"),
    "instance_ebs_iops_exceeded": ("AWS/EC2", "InstanceEBSIOPSExceededCheck", "Maximum"),
    "instance_ebs_throughput_exceeded": ("AWS/EC2", "InstanceEBSThroughputExceededCheck", "Maximum"),
    "ebs_io_balance_percent": ("AWS/EC2", "EBSIOBalance%", "Minimum"),
    "ebs_byte_balance_percent": ("AWS/EC2", "EBSByteBalance%", "Minimum"),
    "bw_in_allowance_exceeded": ("CWAgent", "ethtool_bw_in_allowance_exceeded", "Sum"),
    "bw_out_allowance_exceeded": ("CWAgent", "ethtool_bw_out_allowance_exceeded", "Sum"),
    "pps_allowance_exceeded": ("CWAgent", "ethtool_pps_allowance_exceeded", "Sum"),
    "conntrack_allowance_exceeded": ("CWAgent", "ethtool_conntrack_allowance_exceeded", "Sum"),
}


def cleanup(store: StateStore, session=None):
    session = session or new_session(store.data["profile"], store.data["region"])
    assert_state_identity(store, session)
    errors = []
    terminate_instance(session, store, "instance", errors)
    cleanup_instance_role(session, store, errors)
    return finalize_cleanup(store, errors)


def create_main() -> None:
    args = common_create_parser("Create the live EC2 telemetry fixture").parse_args()
    run_id = args.run_id or make_run_id("ec2")
    state_path = args.state or state_path_for(FOLDER, run_id)
    require_apply(args, {"rightsizer": "ec2", "run_id": run_id, "state": str(state_path),
                         "profile": args.profile, "region": args.region})
    session = new_session(args.profile, args.region)
    identity = caller_identity(session, args.region)
    store = StateStore.create(state_path, rightsizer="ec2", run_id=run_id,
                              profile=args.profile, region=args.region, identity=identity)
    try:
        ec2 = session.client("ec2", region_name=args.region)
        ssm = session.client("ssm", region_name=args.region)
        network = default_network(ec2)
        add_reused_network(store, network)
        tags = aws_tags("ec2", run_id, store.data["expires_at"])
        profile = create_instance_role(session, store, name_prefix=f"maxops-{run_id}", tags=tags)
        wait_for_instance_profile(session.client("iam"), profile, store.resource("instance_role")["id_or_arn"])
        instance_type = select_offered_type(ec2, ("t3a.small", "t3.small"))
        instance_id = launch_managed_instance(
            session, store, logical_name="instance", image_id=latest_al2023_ami(ssm),
            instance_type=instance_type, subnet_id=network["subnet_id"],
            security_group_ids=[network["security_group_id"]], profile_name=profile, tags=tags)
        wait_for_ssm(ssm, [instance_id])
        configure_cloudwatch_agent(ssm, store, [instance_id], cloudwatch_agent_config())
        submit_host_workload(ssm, store, [instance_id])
        store.set_phase("READY")
        print_result({"result": "CREATED", "state": str(store.path), "run_id": run_id})
    except Exception:
        store.set_phase("CREATE_FAILED")
        cleanup(store, session)
        raise


def _readiness(store, session):
    instance_id = store.resource("instance")["id_or_arn"]
    statuses = session.client("ec2", region_name=store.data["region"]).describe_instance_status(
        InstanceIds=[instance_id], IncludeAllInstances=True).get("InstanceStatuses", [])
    if not statuses or statuses[0].get("InstanceState", {}).get("Name") != "running":
        from rightsizers.common.telemetry_testing import NotReady
        raise NotReady("EC2 instance is not running")
    command_readiness(session.client("ssm", region_name=store.data["region"]), store)


def validate_main() -> None:
    args = common_state_parser("Validate EC2 telemetry", validate=True).parse_args()
    store = StateStore.load(args.state)
    session = new_session(store.data["profile"], store.data["region"])
    assert_state_identity(store, session)

    def validate(start, end):
        _readiness(store, session)
        instance_id = store.resource("instance")["id_or_arn"]
        adapter, recording = production_adapter(session, store.data["region"])
        raw = adapter.get_ec2_rightsizing_metrics(instance_id, start, end,
                                                   region=store.data["region"], period_seconds=300)
        calls = metric_data_calls(recording)
        memory = raw["metrics"]["memory_percent"]
        source = memory.get("source") or {}
        expected = {key: (ns, name, [("InstanceId", instance_id)], 300, stat)
                    for key, (ns, name, stat) in DEFINITIONS.items()}
        expected["memory_percent"] = (
            source.get("namespace", "CWAgent"), source.get("metric_name", "mem_used_percent"),
            [(item["Name"], item["Value"]) for item in source.get("dimensions", [])], 300, "Average")
        assert_query_map(calls, expected)
        assert_complete_responses(calls)
        results = {}
        for alias in [*DEFINITIONS, "memory_percent"]:
            try:
                results[alias] = require_series(alias, raw["metrics"][alias],
                    maximum=100 if alias.endswith("percent") or alias == "cpu_percent" else None,
                    positive=alias in {"cpu_percent", "network_out_bytes", "ebs_write_bytes"})
            except ValidationFailure:
                raise
        before = len(recording.calls)
        adapter.get_ec2_confidence_trend(instance_id, start, end, region=store.data["region"],
                                         memory_source=source)
        trend_queries = [q for call in recording.calls[before:]
                         if call["operation"] == "get_metric_data"
                         for q in call["request"].get("MetricDataQueries", [])]
        if len({q["Id"] for q in trend_queries}) != 30:
            raise ValidationFailure("EC2 CPU/memory confidence trend request shape is incomplete")
        return {"decision_metrics": results, "trend_query_count": 30,
                "request_count": len(recording.calls)}

    report, code = run_validation(store, args.window_minutes, validate)
    print_result(report)
    raise SystemExit(code)


def cleanup_main() -> None:
    args = common_state_parser("Clean up EC2 telemetry fixture").parse_args()
    result = cleanup(StateStore.load(args.state))
    print_result(result)
    raise SystemExit(0 if result["result"] == "SUCCESS" else 1)
