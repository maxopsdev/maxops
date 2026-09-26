"""Contract tests for payload-backed check mapping."""

from __future__ import annotations

import os

import pytest

os.environ["DEBUG"] = "false"

from app.checks.registry import check_registry
from tests.payload_helpers import (
    build_payload_adapter,
    import_mapped_check_function,
    load_check_manifest,
    load_payload_check_map,
    load_scenario_payloads,
)


pytestmark = [pytest.mark.unit, pytest.mark.payload]
PAYLOAD_MAPPED_SERVICES = {
    "cloudwatch",
    "dynamodb",
    "ebs",
    "ec2",
    "elasticache",
    "rds",
    "s3",
    "vpc",
    "sagemaker",
}


def _mapped_cases():
    for check_id, entry in load_payload_check_map().items():
        yield check_id, entry


def _mapped_scenario_cases():
    for check_id, entry in load_payload_check_map().items():
        for scenario in entry.get("positive_scenarios", []):
            yield check_id, entry, scenario
        for scenario in entry.get("negative_scenarios", []):
            yield check_id, entry, scenario


def test_payload_check_map_covers_all_payload_generator_checks():
    payload_check_map = load_payload_check_map()

    for service in PAYLOAD_MAPPED_SERVICES:
        manifest = load_check_manifest(service)
        for check_id in manifest:
            assert check_id in payload_check_map
            assert payload_check_map[check_id]["service"] == service

    mapped_services = {entry["service"] for entry in payload_check_map.values()}
    assert mapped_services == PAYLOAD_MAPPED_SERVICES


@pytest.mark.parametrize(("check_id", "entry"), list(_mapped_cases()))
def test_payload_check_map_points_to_registered_checks_and_real_scenarios(check_id, entry):
    assert set(entry) == {
        "service",
        "check_module",
        "check_function",
        "positive_scenarios",
        "negative_scenarios",
    }

    service = entry["service"]
    manifest = load_check_manifest(service)

    assert check_id in manifest
    check_function = import_mapped_check_function(entry)
    registered_check = check_registry.get_check(check_id)
    assert registered_check is not None
    assert check_function is registered_check.check_function

    scenario_names = set(manifest[check_id]["scenarios"])
    mapped_scenarios = entry.get("positive_scenarios", []) + entry.get("negative_scenarios", [])
    assert mapped_scenarios, f"{check_id} must map at least one payload scenario"

    for scenario in mapped_scenarios:
        assert scenario in scenario_names
        scenario_config = manifest[check_id]["scenarios"][scenario]
        payloads = load_scenario_payloads(service, check_id, scenario)

        if service == "sagemaker" and not payloads:
            pytest.skip("sagemaker payload fixtures not captured; run tests_generator/sagemaker")

        assert payloads, f"Missing payload directory for {service}/{check_id}/{scenario}"
        for filename in scenario_config["payload_files"]:
            assert filename in payloads, f"Missing payload file {filename} for {service}/{check_id}/{scenario}"


@pytest.mark.parametrize(("check_id", "entry", "scenario"), list(_mapped_scenario_cases()))
def test_mapped_payload_scenarios_match_expected_check_results(check_id, entry, scenario):
    if check_id == "elasticache_redis_convertible_to_valkey" and scenario == "flag_redis_replication_group":
        pytest.xfail(
            "Existing Valkey payload expects a match, but the captured replication-group response "
            "does not include Engine/EngineVersion for the check to evaluate."
        )

    service = entry["service"]
    payloads = load_scenario_payloads(service, check_id, scenario)
    if service == "sagemaker" and not payloads:
        pytest.skip("sagemaker payload fixtures not captured; run tests_generator/sagemaker")
    metadata = payloads["capture_metadata.json"]
    adapter = build_payload_adapter(service, check_id, scenario)
    check_function = import_mapped_check_function(entry)

    results = check_function(
        aws_adapter=adapter,
        **metadata["check_parameters"],
    )

    assert [resource["resource_id"] for resource in results] == metadata["expected_matches"]
