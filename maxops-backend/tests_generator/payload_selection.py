"""Shared payload scenario selection for service generators."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def mapped_service_scenarios(
    service: str,
    checks: Dict[str, Any],
    payload_check_map: Dict[str, Any],
) -> Dict[str, List[str]]:
    mapped: Dict[str, List[str]] = {}
    for check_id, entry in payload_check_map.items():
        if entry.get("service") != service:
            continue
        if check_id not in checks:
            continue
        scenario_names = entry.get("positive_scenarios", []) + entry.get("negative_scenarios", [])
        if not scenario_names:
            raise ValueError(f"payload_check_map.json has no scenarios for {service} check {check_id}")
        known_scenarios = checks[check_id].get("scenarios", {})
        unmapped = set(known_scenarios).difference(scenario_names)
        if unmapped:
            raise ValueError(
                f"payload_check_map.json does not cover {service} scenarios for {check_id}: "
                + ", ".join(sorted(unmapped))
            )
        mapped[check_id] = [
            scenario_name
            for scenario_name in dict.fromkeys(scenario_names)
            if scenario_name in known_scenarios
        ]

    missing_checks = set(checks).difference(mapped)
    if missing_checks:
        raise ValueError(
            f"payload_check_map.json does not cover {service} checks: "
            + ", ".join(sorted(missing_checks))
        )
    return mapped


def scenario_payload_complete(
    payload_root: Path,
    check_id: str,
    scenario_name: str,
    scenario_config: Dict[str, Any],
) -> bool:
    scenario_dir = payload_root / check_id / scenario_name
    return all(
        (scenario_dir / filename).is_file()
        for filename in scenario_config.get("payload_files", [])
    )


def select_check_scenarios(
    service: str,
    checks: Dict[str, Any],
    payload_check_map: Dict[str, Any],
    payload_root: Path,
    regenerate_all: bool = False,
) -> Dict[str, Dict[str, Any]]:
    mapped = mapped_service_scenarios(service, checks, payload_check_map)
    selected: Dict[str, Dict[str, Any]] = {}
    for check_id, scenario_names in mapped.items():
        scenarios = checks[check_id]["scenarios"]
        selected_scenarios = {
            scenario_name: scenarios[scenario_name]
            for scenario_name in scenario_names
            if regenerate_all
            or not scenario_payload_complete(
                payload_root,
                check_id,
                scenario_name,
                scenarios[scenario_name],
            )
        }
        if selected_scenarios:
            selected[check_id] = {
                **checks[check_id],
                "scenarios": selected_scenarios,
            }
    return selected
