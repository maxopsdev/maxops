"""Read-only S3 optimizer check and advisory-action contracts."""

from types import SimpleNamespace
from copy import deepcopy

import pytest

from app.actions.registry import ActionMetadata, ActionRegistry
from app.checks.s3.bucket_low_access import check_s3_bucket_low_access
from app.checks.s3.bucket_retrieval_cost_dominant import check_s3_bucket_retrieval_cost_dominant
from app.checks.s3.bucket_unused import check_s3_bucket_unused


def _adapter(result):
    """Return an adapter whose bucket carries one stored optimizer result."""

    return SimpleNamespace(get_resources=lambda *_args, **_kwargs: [{"resource_id": "bucket", "metadata": {"s3_optimizer": result}}])


def _base_result():
    """Return a small stored result with known coverage and storage."""

    return {
        "telemetry_status": "usable",
        "confidence": "high",
        "coverage": {"cur_days_covered": 90},
        "current": {"storage_class_breakdown": {"STANDARD": {"bytes": 2**30, "objects": 100, "avg_object_bytes": 2**20}}},
        "signals": {"monthly_data_read_requests": 0, "monthly_data_write_requests": 0, "monthly_list_requests": 0, "monthly_config_requests": 10, "requests_per_object_per_month": 0, "retrieval_ratio_by_class": {}, "window_totals": {"tier1": {}, "tier2": {}, "restore_requests": {}}},
        "scenarios": [{"policy": "STANDARD_IA", "status": "ok", "savings_monthly": {"high": 1}, "risks": []}],
        "pattern": {"pattern_label": "write_once_cold"},
    }


def test_unused_skips_unknown_and_fires_on_covered_zero_data_activity():
    """Config traffic does not count as use, while unknown telemetry never fires."""

    result = _base_result()
    finding = check_s3_bucket_unused(_adapter(result), window_days=90)
    assert finding[0]["metadata"]["recommended_action"] == "s3_review_unused_bucket"
    result["telemetry_status"] = "unknown"
    assert check_s3_bucket_unused(_adapter(result), window_days=90) == []


def test_unused_reports_storage_savings_and_deep_archive_retain_alternative():
    """Unused findings report monthly/yearly storage savings and retention guidance."""

    result = _base_result()
    result["current"]["monthly_storage_cost"] = 2.5
    deep_archive = {"policy": "DEEP_ARCHIVE", "status": "ok", "savings_monthly": {"high": 1}}
    result["scenarios"].append(deep_archive)

    finding = check_s3_bucket_unused(_adapter(result), window_days=90)[0]

    assert finding["metadata"]["potential_savings_monthly"] == 2.5
    assert finding["metadata"]["potential_savings_yearly"] == 30.0
    assert finding["metadata"]["retain_alternative"] == deep_archive


def test_unused_keeps_savings_unknown_when_storage_cost_is_unknown():
    """An absent current storage cost remains unknown instead of becoming zero."""

    result = _base_result()
    result["current"]["monthly_storage_cost"] = None

    finding = check_s3_bucket_unused(_adapter(result), window_days=90)[0]

    assert finding["metadata"]["potential_savings_monthly"] is None
    assert finding["metadata"]["potential_savings_yearly"] is None


def test_low_access_attaches_ranked_scenarios():
    """Low access uses stored thresholds and returns candidate scenarios."""

    result = _base_result()
    finding = check_s3_bucket_low_access(_adapter(result))
    assert finding[0]["metadata"]["scenarios"][0]["policy"] == "STANDARD_IA"


def test_retrieval_dominant_is_per_class_and_versioned_savings_are_unknown():
    """The check uses the losing class only and nulls savings for versioned copies."""

    result = _base_result()
    result["current"]["storage_class_breakdown"] = {"STANDARD_IA": {"bytes": 2**30, "objects": 100, "avg_object_bytes": 2**20}}
    result["signals"]["per_class_observed_retrieval_cost"] = {"STANDARD_IA": 3.0}
    result["signals"]["standard_storage_cost_equivalent"] = {"STANDARD_IA": 2.0}
    result["signals"]["current_class_storage_cost"] = {"STANDARD_IA": 1.0}
    result["signals"]["request_cost_delta_vs_standard"] = {"STANDARD_IA": 0.0}
    resource = _adapter(result).get_resources()[0]
    resource["metadata"]["versioning_status"] = "Enabled"
    finding = check_s3_bucket_retrieval_cost_dominant(SimpleNamespace(get_resources=lambda *_a, **_k: [resource]))
    assert finding[0]["metadata"]["potential_savings_monthly"] is None


def test_retrieval_dominant_requires_positive_monthly_savings():
    """The dominance inequality and reported savings use the same class inputs."""

    losing = _base_result()
    losing["signals"].update(
        {
            "per_class_observed_retrieval_cost": {"STANDARD_IA": 3.0},
            "standard_storage_cost_equivalent": {"STANDARD_IA": 2.0},
            "current_class_storage_cost": {"STANDARD_IA": 1.0},
            "request_cost_delta_vs_standard": {"STANDARD_IA": 0.0},
        }
    )
    losing["current"]["storage_class_breakdown"] = {
        "STANDARD_IA": {"bytes": 2**30, "objects": 100, "avg_object_bytes": 2**20}
    }
    finding = check_s3_bucket_retrieval_cost_dominant(_adapter(losing))[0]
    assert finding["metadata"]["potential_savings_monthly"] == 2.0
    assert "potential_savings_yearly" not in finding["metadata"]
    assert finding["metadata"]["per_class_observed_retrieval_cost"] == 3.0
    assert finding["metadata"]["standard_storage_cost_equivalent"] == 2.0
    assert finding["metadata"]["current_class_storage_cost"] == 1.0

    not_losing = deepcopy(losing)
    not_losing["signals"]["per_class_observed_retrieval_cost"] = {"STANDARD_IA": 0.5}
    assert check_s3_bucket_retrieval_cost_dominant(_adapter(not_losing)) == []


def test_non_executable_action_rejects_before_handler():
    """Advisory actions cannot fall through to a missing or mutating handler."""

    registry = ActionRegistry()
    called = []
    registry.register(ActionMetadata("advisory", "Advisory", "Review", ["s3"], None, executable=False))
    with pytest.raises(RuntimeError, match="advisory-only"):
        registry.execute(SimpleNamespace(action_key="advisory"))
    assert called == []
    with pytest.raises(ValueError, match="must not have a handler"):
        registry.register(ActionMetadata("invalid", "Invalid", "Review", ["s3"], lambda _ctx: None, executable=False))
    with pytest.raises(ValueError, match="requires a handler"):
        registry.register(ActionMetadata("also-invalid", "Invalid", "Review", ["s3"], None))
