"""`default_action` must be a slug, not a sentence.

Nine checks had prose in this field -- "Delete service; clean up ALB/TG/log
groups/roles" and similar. It is a machine-readable key: the API returns it,
MCP exposes it, and scan_service falls back to it for a finding's
`recommended_action`, which the UI renders via titleCase(). Prose there
produces a nonsense label and cannot be matched against anything.
"""
import re

import pytest

import app.checks  # noqa: F401 -- import registers every check
from app.actions import action_registry
from app.checks.registry import check_registry


SLUG = re.compile(r"^[a-z][a-z0-9_]*$")

ALL_CHECKS = sorted(check_registry.list_checks(), key=lambda c: c.check_id)


def test_registry_is_populated():
    """Guards the tests below from silently passing on an empty registry."""
    assert len(ALL_CHECKS) > 50


@pytest.mark.parametrize("check", ALL_CHECKS, ids=lambda c: c.check_id)
def test_default_action_is_a_slug(check):
    assert check.default_action, f"{check.check_id} has an empty default_action"
    assert SLUG.match(check.default_action), (
        f"{check.check_id} default_action={check.default_action!r} is not a "
        "lower_snake_case slug -- descriptive guidance belongs in the check "
        "description or the action's own metadata"
    )


def test_no_default_action_contains_whitespace():
    offenders = {
        c.check_id: c.default_action
        for c in ALL_CHECKS
        if any(ch.isspace() for ch in c.default_action)
    }
    assert offenders == {}


def test_previously_prose_checks_now_point_at_real_actions():
    """The eight of the nine that map onto a registered, executable action."""
    expected = {
        "ebs_large_volumes_low_utilization": "ebs_downsize_volume",
        "ecs_idle_clusters_no_active_services_tasks": "ecs_delete_cluster",
        "ecs_overprovisioned_task_cpu_memory_reservations": "ecs_rightsize_task_definition",
        "ecs_services_desired_count_zero": "ecs_delete_service",
        "lambda_high_log_ingestion": "reduce_log_verbosity_and_retention",
        "lambda_overprovisioned_memory": "update_memory",
        "lambda_provisioned_concurrency_low_usage": "reduce_provisioned_concurrency",
    }
    for check_id, action_key in expected.items():
        check = check_registry.get_check(check_id)
        assert check is not None, f"{check_id} is not registered"
        assert check.default_action == action_key
        assert action_registry.get_action(action_key) is not None, (
            f"{action_key} is no longer a registered action"
        )


def test_check_ids_are_slugs_too():
    for check in ALL_CHECKS:
        assert SLUG.match(check.check_id), f"{check.check_id!r} is not a slug"
