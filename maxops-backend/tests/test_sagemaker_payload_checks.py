"""Payload checks activate automatically after an operator captures fixtures."""

from pathlib import Path

import pytest

from tests.payload_helpers import build_sagemaker_payload_adapter


PAYLOAD_ROOT = Path(__file__).parent / "payloads" / "sagemaker"


pytestmark = [pytest.mark.unit, pytest.mark.payload]


def _require_fixtures(check_id: str, scenario: str):
    """Skip until real SageMaker fixtures exist; never xfail this layer."""
    if not (PAYLOAD_ROOT / check_id / scenario).exists():
        pytest.skip("sagemaker payload fixtures not captured; run tests_generator/sagemaker")
    return build_sagemaker_payload_adapter(check_id, scenario)


def test_sagemaker_payload_fixtures_are_replayable_when_captured():
    _require_fixtures("sagemaker_endpoint_idle", "pass_zero_invocations")
