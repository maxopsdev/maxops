"""Offline adapter tests backed by checked-in EBS payload fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.payload_helpers import build_ebs_payload_adapter


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "payloads" / "ebs"
pytestmark = [pytest.mark.unit, pytest.mark.payload, pytest.mark.ebs]


@pytest.mark.skipif(not FIXTURE_ROOT.exists(), reason="EBS payload fixtures have not been captured from AWS yet.")
class TestEBSPayloadAdapter:
    def test_unattached_volume_payload_is_normalized(self):
        adapter = build_ebs_payload_adapter("ebs_unattached_volumes", "flag_unattached_volume")

        resources = adapter.get_resources("ebs", {}, region="us-east-1")

        assert len(resources) == 1
        resource = resources[0]
        assert resource["resource_id"] == "vol-UNATTACHED"
        assert resource["resource_type"] == "ebs"
        assert resource["state"] == "available"
        assert resource["attached"] is False
        assert resource["metadata"]["size"] == 1
        assert resource["metadata"]["volume_type"] == "gp3"

    def test_attached_iops_volume_payload_is_normalized(self):
        adapter = build_ebs_payload_adapter("ebs_iops_overprovisioned_volume", "flag_low_iops_io1")

        resources = adapter.get_resources("ebs", {}, region="us-east-1")

        assert len(resources) == 1
        resource = resources[0]
        assert resource["resource_id"] == "vol-PROVISIONEDIO1"
        assert resource["attached"] is True
        assert resource["metadata"]["volume_type"] == "io1"
        assert resource["metadata"]["size"] == 4
        assert resource["metadata"]["iops"] == 100

    def test_ebs_utilization_parses_metric_statistics_payloads(self):
        adapter = build_ebs_payload_adapter("ebs_underutilized_volume", "flag_low_io_gp2")

        utilization = adapter.get_resource_utilization(
            "vol-ATTACHEDGP2",
            "ebs",
            start_date=None,
            end_date=None,
            region="us-east-1",
        )

        assert utilization["volumereadops"] == 1.0
        assert utilization["volumewriteops"] == 1.0
        assert utilization["volumereadbytes"] == 1024.0
        assert utilization["volumewritebytes"] == 1024.0
