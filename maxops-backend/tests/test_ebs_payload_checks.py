"""Offline EBS check tests backed by saved payload fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.checks.ebs.gp2_convertible_to_gp3 import check_ebs_gp2_volumes_convertible_to_gp3
from app.checks.ebs.iops_overprovisioned_volume import check_ebs_iops_overprovisioned_volume
from app.checks.ebs.large_volumes_low_utilization import check_ebs_large_volumes_low_utilization
from app.checks.ebs.unattached_volumes import check_ebs_unattached_volumes
from app.checks.ebs.underutilized_provisioned_iops import check_ebs_underutilized_provisioned_iops
from app.checks.ebs.underutilized_volume import check_ebs_underutilized_volume
from tests.payload_helpers import build_ebs_payload_adapter, load_scenario_payloads


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "payloads" / "ebs"
pytestmark = [pytest.mark.unit, pytest.mark.payload, pytest.mark.ebs]


@pytest.mark.skipif(not FIXTURE_ROOT.exists(), reason="EBS payload fixtures have not been captured from AWS yet.")
class TestEBSPayloadBackedChecks:
    def test_unattached_check_flags_available_volume(self):
        payloads = load_scenario_payloads("ebs", "ebs_unattached_volumes", "flag_unattached_volume")
        adapter = build_ebs_payload_adapter("ebs_unattached_volumes", "flag_unattached_volume")
        metadata = payloads["capture_metadata.json"]

        results = check_ebs_unattached_volumes(aws_adapter=adapter, **metadata["check_parameters"])

        assert [resource["resource_id"] for resource in results] == metadata["expected_matches"]

    def test_unattached_check_skips_attached_volumes(self):
        adapter = build_ebs_payload_adapter("ebs_unattached_volumes", "pass_attached_volumes")

        results = check_ebs_unattached_volumes(aws_adapter=adapter, min_age_days=0, region="us-east-1")

        assert results == []

    def test_gp2_check_flags_attached_gp2_volume(self):
        payloads = load_scenario_payloads("ebs", "ebs_gp2_volumes_convertible_to_gp3", "flag_attached_gp2")
        adapter = build_ebs_payload_adapter("ebs_gp2_volumes_convertible_to_gp3", "flag_attached_gp2")
        metadata = payloads["capture_metadata.json"]

        results = check_ebs_gp2_volumes_convertible_to_gp3(aws_adapter=adapter, **metadata["check_parameters"])

        assert [resource["resource_id"] for resource in results] == metadata["expected_matches"]

    def test_gp2_check_skips_non_gp2_volumes(self):
        adapter = build_ebs_payload_adapter("ebs_gp2_volumes_convertible_to_gp3", "pass_non_gp2_volumes")

        results = check_ebs_gp2_volumes_convertible_to_gp3(aws_adapter=adapter, min_size_gb=1, region="us-east-1")

        assert results == []

    def test_underutilized_volume_flags_low_io(self):
        payloads = load_scenario_payloads("ebs", "ebs_underutilized_volume", "flag_low_io_gp2")
        adapter = build_ebs_payload_adapter("ebs_underutilized_volume", "flag_low_io_gp2")
        metadata = payloads["capture_metadata.json"]

        results = check_ebs_underutilized_volume(aws_adapter=adapter, **metadata["check_parameters"])

        assert [resource["resource_id"] for resource in results] == metadata["expected_matches"]

    def test_underutilized_volume_skips_high_io(self):
        adapter = build_ebs_payload_adapter("ebs_underutilized_volume", "pass_high_io_gp2")

        results = check_ebs_underutilized_volume(
            aws_adapter=adapter,
            lookback_days=14,
            iops_threshold=5.0,
            throughput_mb_threshold=1.0,
            region="us-east-1",
        )

        assert results == []

    def test_large_volume_check_uses_minimal_threshold(self):
        payloads = load_scenario_payloads("ebs", "ebs_large_volumes_low_utilization", "flag_minimal_large_threshold_gp2")
        adapter = build_ebs_payload_adapter("ebs_large_volumes_low_utilization", "flag_minimal_large_threshold_gp2")
        metadata = payloads["capture_metadata.json"]

        results = check_ebs_large_volumes_low_utilization(aws_adapter=adapter, **metadata["check_parameters"])

        assert [resource["resource_id"] for resource in results] == metadata["expected_matches"]

    def test_large_volume_check_skips_high_utilization(self):
        adapter = build_ebs_payload_adapter("ebs_large_volumes_low_utilization", "pass_high_utilization_gp2")

        results = check_ebs_large_volumes_low_utilization(
            aws_adapter=adapter,
            lookback_days=14,
            min_size_gb=1,
            iops_threshold=5.0,
            throughput_mb_threshold=1.0,
            region="us-east-1",
        )

        assert results == []

    def test_iops_overprovisioned_flags_low_iops(self):
        payloads = load_scenario_payloads("ebs", "ebs_iops_overprovisioned_volume", "flag_low_iops_io1")
        adapter = build_ebs_payload_adapter("ebs_iops_overprovisioned_volume", "flag_low_iops_io1")
        metadata = payloads["capture_metadata.json"]

        results = check_ebs_iops_overprovisioned_volume(aws_adapter=adapter, **metadata["check_parameters"])

        assert [resource["resource_id"] for resource in results] == metadata["expected_matches"]

    def test_iops_overprovisioned_skips_busy_iops(self):
        adapter = build_ebs_payload_adapter("ebs_iops_overprovisioned_volume", "pass_busy_iops_io1")

        results = check_ebs_iops_overprovisioned_volume(
            aws_adapter=adapter,
            lookback_days=14,
            utilization_threshold_pct=30.0,
            min_provisioned_iops=100,
            region="us-east-1",
        )

        assert results == []

    def test_underutilized_provisioned_iops_flags_low_iops(self):
        payloads = load_scenario_payloads("ebs", "ebs_underutilized_provisioned_iops", "flag_low_provisioned_iops_io1")
        adapter = build_ebs_payload_adapter("ebs_underutilized_provisioned_iops", "flag_low_provisioned_iops_io1")
        metadata = payloads["capture_metadata.json"]

        results = check_ebs_underutilized_provisioned_iops(aws_adapter=adapter, **metadata["check_parameters"])

        assert [resource["resource_id"] for resource in results] == metadata["expected_matches"]

    def test_underutilized_provisioned_iops_skips_busy_iops(self):
        adapter = build_ebs_payload_adapter("ebs_underutilized_provisioned_iops", "pass_busy_provisioned_iops_io1")

        results = check_ebs_underutilized_provisioned_iops(
            aws_adapter=adapter,
            lookback_days=14,
            utilization_threshold_pct=30.0,
            min_provisioned_iops=100,
            region="us-east-1",
        )

        assert results == []
