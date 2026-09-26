from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.actions.base import ActionExecutionContext
from app.actions.handlers_ec2 import handle_release_unused_elastic_ip
from app.actions.handlers_rds import handle_rds_migrate_graviton
from app.checks.ebs.iops_overprovisioned_volume import (
    check_ebs_iops_overprovisioned_volume,
)
from app.checks.ebs.unattached_volumes import check_ebs_unattached_volumes
from app.checks.elasticache.low_items_count import (
    check_elasticache_low_item_count,
)
from app.checks.elasticache.non_graviton import (
    check_elasticache_non_graviton_instance_class,
)
from app.checks.elasticache.valkey_compatible import (
    check_elasticache_redis_convertible_to_valkey,
)
from app.checks.ec2.unused_elastic_ips import check_unused_elastic_ips
from app.checks.rds.non_graviton import check_rds_non_graviton_instance_class
from app.schemas.check import CheckActionRequest


class CheckAdapter:
    def __init__(self, resources, utilization=None):
        self.resources = resources
        self.utilization = utilization or {}

    def get_resources(self, resource_type, filters=None, region=None):
        return self.resources.get(resource_type, [])

    def get_resource_utilization(self, resource_id, resource_type, *args):
        return self.utilization.get(resource_id, {})


class Session:
    def __init__(self, clients):
        self.clients = clients

    def client(self, service_name, region_name=None):
        assert region_name == "us-east-1"
        return self.clients[service_name]


def make_context(client_name, client, action, resource_id, parameters):
    payload = CheckActionRequest(
        action=action,
        account_id="123456789012",
        region="us-east-1",
        resource_id=resource_id,
        parameters=parameters,
    )
    return ActionExecutionContext(
        check_id="test-check",
        action_key=action,
        payload=payload,
        check=None,
        aws_adapter=SimpleNamespace(session=Session({client_name: client})),
        db=None,
        action_execution=None,
    )


def test_elasticache_checks_expose_registered_actions():
    low_resource = {
        "resource_id": "cache-low",
        "metadata": {"CacheNodeType": "cache.m6g.large"},
    }
    low = check_elasticache_low_item_count(
        CheckAdapter(
            {"elasticache_cluster": [low_resource]},
            {"cache-low": {"CurrItems": [1, 2]}},
        ),
        low_item_threshold=10,
        region="us-east-1",
    )
    assert low[0]["metadata"]["recommended_actions"] == [
        "elasticache_downsize",
        "elasticache_delete",
    ]

    non_graviton = check_elasticache_non_graviton_instance_class(
        CheckAdapter({
            "elasticache_cluster": [
                {
                    "resource_id": "cache-x86",
                    "metadata": {"CacheNodeType": "cache.m5.large"},
                }
            ]
        }),
        "us-east-1",
    )
    assert non_graviton[0]["metadata"]["recommended_actions"] == [
        "elasticache_migrate_graviton"
    ]

    valkey = check_elasticache_redis_convertible_to_valkey(
        CheckAdapter({
            "elasticache_replication_group": [
                {
                    "resource_id": "redis",
                    "metadata": {"Engine": "redis", "EngineVersion": "7.0"},
                }
            ],
            "elasticache_cluster": [
                {
                    "resource_id": "redis-001",
                    "metadata": {
                        "Engine": "redis",
                        "EngineVersion": "7.0",
                        "ReplicationGroupId": "redis",
                    },
                }
            ],
        }),
        region="us-east-1",
    )
    assert [item["resource_id"] for item in valkey] == ["redis"]
    assert valkey[0]["metadata"]["recommended_actions"] == [
        "elasticache_upgrade_valkey"
    ]


def test_ebs_checks_expose_snapshot_lifecycle_and_iops_without_downsize():
    unattached = check_ebs_unattached_volumes(
        CheckAdapter({
            "ebs": [
                {
                    "resource_id": "vol-unused",
                    "state": "available",
                    "attached": False,
                    "metadata": {
                        "size": 100,
                        "volume_type": "gp3",
                        # old enough to clear the min_age_days gate
                        "create_time": "2024-01-15T10:30:00+00:00",
                    },
                }
            ]
        }),
        region="us-east-1",
    )
    assert unattached[0]["metadata"]["recommended_actions"] == [
        "snapshot_and_terminate",
        "ebs_lifecycle_policy",
    ]

    iops = check_ebs_iops_overprovisioned_volume(
        CheckAdapter(
            {"ebs": [{"resource_id": "vol-iops", "metadata": {"iops": 5000}}]},
            {"vol-iops": {"VolumeReadOps": [10], "VolumeWriteOps": [10]}},
        ),
        region="us-east-1",
    )
    assert iops[0]["metadata"]["recommended_actions"] == ["ebs_reduce_iops"]
    assert "ebs_downsize_volume" not in unattached[0]["metadata"][
        "recommended_actions"
    ]
    assert "ebs_downsize_volume" not in iops[0]["metadata"]["recommended_actions"]


def test_rds_check_recommends_graviton_action():
    result = check_rds_non_graviton_instance_class(
        CheckAdapter({
            "rds_instance": [
                {
                    "resource_id": "database-1",
                    "metadata": {"DBInstanceClass": "db.m5.large"},
                }
            ]
        }),
        "us-east-1",
    )

    assert result[0]["metadata"]["recommended_actions"] == [
        "rds_migrate_graviton"
    ]
    assert result[0]["metadata"]["recommended_instance_class"] == "db.m6g.large"


class RDSClient:
    def __init__(self):
        self.calls = []

    def describe_db_instances(self, **kwargs):
        self.calls.append(("describe_db_instances", kwargs))
        return {
            "DBInstances": [
                {
                    "DBInstanceIdentifier": "database-1",
                    "DBInstanceClass": "db.m5.large",
                }
            ]
        }

    def modify_db_instance(self, **kwargs):
        self.calls.append(("modify_db_instance", kwargs))
        return {"DBInstance": {"DBInstanceIdentifier": "database-1"}}


def test_rds_migration_requires_graviton_and_preserves_maintenance_default():
    client = RDSClient()
    response = handle_rds_migrate_graviton(
        make_context(
            "rds",
            client,
            "rds_migrate_graviton",
            "database-1",
            {"target_instance_class": "db.m6g.large"},
        )
    )

    assert client.calls == [
        (
            "describe_db_instances",
            {"DBInstanceIdentifier": "database-1"},
        ),
        (
            "modify_db_instance",
            {
                "DBInstanceIdentifier": "database-1",
                "DBInstanceClass": "db.m6g.large",
                "ApplyImmediately": False,
            },
        ),
    ]
    assert response.details["previous_instance_class"] == "db.m5.large"

    with pytest.raises(HTTPException) as invalid:
        handle_rds_migrate_graviton(
            make_context(
                "rds",
                RDSClient(),
                "rds_migrate_graviton",
                "database-1",
                {"target_instance_class": "db.m6i.large"},
            )
        )
    assert invalid.value.status_code == 400


def test_unused_elastic_ip_check_flags_only_unassociated_allocations():
    result = check_unused_elastic_ips(
        CheckAdapter({
            "elastic_ip": [
                {
                    "resource_id": "eipalloc-unused",
                    "metadata": {
                        "AllocationId": "eipalloc-unused",
                        "PublicIp": "198.51.100.10",
                    },
                },
                {
                    "resource_id": "eipalloc-used",
                    "metadata": {
                        "AllocationId": "eipalloc-used",
                        "AssociationId": "eipassoc-1",
                        "PublicIp": "198.51.100.11",
                    },
                },
            ]
        }),
        "us-east-1",
    )

    assert [item["resource_id"] for item in result] == ["eipalloc-unused"]
    assert result[0]["metadata"]["recommended_actions"] == [
        "release_unused_elastic_ip"
    ]


class EC2Client:
    def __init__(self, associated=False):
        self.associated = associated
        self.calls = []

    def describe_addresses(self, **kwargs):
        self.calls.append(("describe_addresses", kwargs))
        address = {
            "AllocationId": "eipalloc-unused",
            "PublicIp": "198.51.100.10",
        }
        if self.associated:
            address["AssociationId"] = "eipassoc-1"
        return {"Addresses": [address]}

    def release_address(self, **kwargs):
        self.calls.append(("release_address", kwargs))
        return {}


def test_release_elastic_ip_requires_confirmation_and_rechecks_association():
    unconfirmed = EC2Client()
    with pytest.raises(HTTPException) as confirmation:
        handle_release_unused_elastic_ip(
            make_context(
                "ec2",
                unconfirmed,
                "release_unused_elastic_ip",
                "eipalloc-unused",
                {},
            )
        )
    assert confirmation.value.status_code == 400
    assert unconfirmed.calls == []

    associated = EC2Client(associated=True)
    with pytest.raises(HTTPException) as conflict:
        handle_release_unused_elastic_ip(
            make_context(
                "ec2",
                associated,
                "release_unused_elastic_ip",
                "eipalloc-unused",
                {"confirm_release": True},
            )
        )
    assert conflict.value.status_code == 409
    assert not any(call[0] == "release_address" for call in associated.calls)

    client = EC2Client()
    response = handle_release_unused_elastic_ip(
        make_context(
            "ec2",
            client,
            "release_unused_elastic_ip",
            "eipalloc-unused",
            {"confirm_release": True},
        )
    )
    assert client.calls[-1] == (
        "release_address",
        {"AllocationId": "eipalloc-unused"},
    )
    assert response.details["public_ip"] == "198.51.100.10"


def test_new_actions_checks_and_aliases_are_registered():
    from app.actions import action_registry
    from app.actions.action_mapping import resolve_action_key
    from app.checks import check_registry

    assert action_registry.get_action("rds_migrate_graviton") is not None
    assert action_registry.get_action("release_unused_elastic_ip") is not None
    assert check_registry.get_check("unused_elastic_ips") is not None
    assert resolve_action_key(
        "migrate to graviton", "rds_non_graviton_instance_class"
    ) == "rds_migrate_graviton"
    assert resolve_action_key("release unused EIPs".lower()) == (
        "release_unused_elastic_ip"
    )
