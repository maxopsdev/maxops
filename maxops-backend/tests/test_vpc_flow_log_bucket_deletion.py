from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException

from app.actions.base import ActionExecutionContext
from app.actions.handlers_vpc import (
    _apply_bucket_expiration_policy,
    handle_vpc_flow_logs_disabling_with_bucket_deletion,
)
from app.checks.vpc.vpc_flow_logs_enabled import check_vpc_flow_logs_enabled
from app.schemas.check import CheckActionRequest


def flow_log(flow_log_id, bucket, vpc_id="vpc-123"):
    return {
        "FlowLogId": flow_log_id,
        "ResourceId": vpc_id,
        "ResourceType": "VPC",
        "LogDestinationType": "s3",
        "LogDestination": f"arn:aws:s3:::{bucket}/AWSLogs/123456789012",
    }


class Paginator:
    def __init__(self, operation, calls, pages):
        self.operation = operation
        self.calls = calls
        self.pages = pages

    def paginate(self, **kwargs):
        self.calls.append((self.operation, kwargs))
        return iter(self.pages)


class EC2Client:
    def __init__(self, calls, selected, all_logs=None):
        self.calls = calls
        self.selected = selected
        self.all_logs = all_logs if all_logs is not None else selected

    def describe_vpcs(self, **kwargs):
        self.calls.append(("describe_vpcs", kwargs))
        return {"Vpcs": [{"VpcId": "vpc-123"}]}

    def describe_flow_logs(self, **kwargs):
        self.calls.append(("describe_flow_logs", kwargs))
        if "FlowLogIds" in kwargs:
            requested = set(kwargs["FlowLogIds"])
            return {
                "FlowLogs": [
                    item
                    for item in self.selected
                    if item["FlowLogId"] in requested
                ]
            }
        if "Filters" in kwargs:
            return {"FlowLogs": self.selected}
        return {"FlowLogs": self.all_logs}

    def delete_flow_logs(self, **kwargs):
        self.calls.append(("delete_flow_logs", kwargs))
        return {"Unsuccessful": []}


class S3Client:
    def __init__(self, calls, version_pages=None, object_pages=None):
        self.calls = calls
        self.version_pages = version_pages or [{}]
        self.object_pages = object_pages or [{}]

    def get_paginator(self, operation):
        pages = (
            self.version_pages
            if operation == "list_object_versions"
            else self.object_pages
        )
        return Paginator(operation, self.calls, pages)

    def delete_objects(self, **kwargs):
        self.calls.append(("delete_objects", kwargs))
        return {}

    def delete_bucket(self, **kwargs):
        self.calls.append(("delete_bucket", kwargs))
        return {}


class LifecycleS3Client:
    def __init__(self):
        self.calls = []

    def get_bucket_lifecycle_configuration(self, **kwargs):
        self.calls.append(("get_bucket_lifecycle_configuration", kwargs))
        raise ClientError(
            {
                "Error": {
                    "Code": "NoSuchLifecycleConfiguration",
                    "Message": "missing",
                }
            },
            "GetBucketLifecycleConfiguration",
        )

    def put_bucket_lifecycle_configuration(self, **kwargs):
        self.calls.append(("put_bucket_lifecycle_configuration", kwargs))
        return {}


class Session:
    def __init__(self, ec2, s3):
        self.ec2 = ec2
        self.s3 = s3

    def client(self, service_name, region_name=None):
        assert region_name == "us-east-1"
        return {"ec2": self.ec2, "s3": self.s3}[service_name]


def make_context(ec2, s3, parameters):
    action = "vpc_flow_logs_disabling_with_bucket_deletion"
    payload = CheckActionRequest(
        action=action,
        account_id="123456789012",
        region="us-east-1",
        resource_id="vpc-123",
        parameters=parameters,
    )
    return ActionExecutionContext(
        check_id="vpc_flow_logs_enabled",
        action_key=action,
        payload=payload,
        check=None,
        aws_adapter=SimpleNamespace(session=Session(ec2, s3)),
        db=None,
        action_execution=None,
    )


def test_bucket_deletion_requires_explicit_confirmation():
    calls = []
    ec2 = EC2Client(calls, [flow_log("fl-1", "logs-bucket")])
    s3 = S3Client(calls)

    with pytest.raises(HTTPException) as exc_info:
        handle_vpc_flow_logs_disabling_with_bucket_deletion(
            make_context(ec2, s3, {"bucket_names": ["logs-bucket"]})
        )

    assert exc_info.value.status_code == 400
    assert "confirm_delete_buckets=true" in exc_info.value.detail
    assert calls == []


def test_bucket_deletion_rejects_unrelated_and_shared_destinations():
    calls = []
    selected = [flow_log("fl-1", "logs-bucket")]
    ec2 = EC2Client(calls, selected)
    s3 = S3Client(calls)
    with pytest.raises(HTTPException) as unrelated:
        handle_vpc_flow_logs_disabling_with_bucket_deletion(
            make_context(
                ec2,
                s3,
                {
                    "flow_log_ids": ["fl-1"],
                    "bucket_names": ["other-bucket"],
                    "confirm_delete_buckets": True,
                },
            )
        )
    assert unrelated.value.status_code == 400
    assert not any(call[0].startswith("delete") for call in calls)

    calls = []
    ec2 = EC2Client(
        calls,
        selected,
        all_logs=selected + [flow_log("fl-shared", "logs-bucket", "vpc-999")],
    )
    with pytest.raises(HTTPException) as shared:
        handle_vpc_flow_logs_disabling_with_bucket_deletion(
            make_context(
                ec2,
                S3Client(calls),
                {
                    "flow_log_ids": ["fl-1"],
                    "bucket_names": ["logs-bucket"],
                    "confirm_delete_buckets": True,
                },
            )
        )
    assert shared.value.status_code == 409
    assert "fl-shared" in shared.value.detail
    assert not any(call[0].startswith("delete") for call in calls)


def test_bucket_deletion_disables_flow_logs_before_emptying_versioned_bucket():
    calls = []
    selected = [flow_log("fl-1", "logs-bucket")]
    ec2 = EC2Client(calls, selected)
    s3 = S3Client(
        calls,
        version_pages=[
            {
                "Versions": [{"Key": "old.log", "VersionId": "v1"}],
                "DeleteMarkers": [{"Key": "gone.log", "VersionId": "v2"}],
            }
        ],
        object_pages=[{"Contents": [{"Key": "current.log"}]}],
    )

    response = handle_vpc_flow_logs_disabling_with_bucket_deletion(
        make_context(
            ec2,
            s3,
            {
                "flow_log_ids": ["fl-1"],
                "bucket_names": ["logs-bucket"],
                "confirm_delete_buckets": True,
            },
        )
    )

    operation_names = [call[0] for call in calls]
    assert operation_names.index("delete_flow_logs") < operation_names.index(
        "delete_objects"
    )
    assert operation_names.index("delete_flow_logs") < operation_names.index(
        "delete_bucket"
    )
    assert calls[2] == ("describe_flow_logs", {})
    assert calls[3] == ("delete_flow_logs", {"FlowLogIds": ["fl-1"]})
    assert calls[5] == (
        "delete_objects",
        {
            "Bucket": "logs-bucket",
            "Delete": {
                "Objects": [
                    {"Key": "old.log", "VersionId": "v1"},
                    {"Key": "gone.log", "VersionId": "v2"},
                ],
                "Quiet": True,
            },
        },
    )
    assert calls[-1] == ("delete_bucket", {"Bucket": "logs-bucket"})
    assert response.details["deleted_flow_log_ids"] == ["fl-1"]
    assert response.details["deleted_bucket_names"] == ["logs-bucket"]


def test_vpc_deletion_action_is_registered_and_mapped():
    from app.actions import action_registry
    from app.actions.action_mapping import resolve_action_key

    action_key = "vpc_flow_logs_disabling_with_bucket_deletion"
    assert action_registry.get_action(action_key) is not None
    assert resolve_action_key("disable flow logs and delete buckets") == action_key


def test_bucket_expiration_policy_handles_missing_lifecycle_configuration():
    s3 = LifecycleS3Client()

    _apply_bucket_expiration_policy(
        s3,
        bucket="logs-bucket",
        expiration_days=30,
        rule_id="maxops-vpc-flow-logs-expiration",
    )

    assert s3.calls == [
        ("get_bucket_lifecycle_configuration", {"Bucket": "logs-bucket"}),
        (
            "put_bucket_lifecycle_configuration",
            {
                "Bucket": "logs-bucket",
                "LifecycleConfiguration": {
                    "Rules": [
                        {
                            "ID": "maxops-vpc-flow-logs-expiration",
                            "Status": "Enabled",
                            "Filter": {"Prefix": ""},
                            "Expiration": {"Days": 30},
                        }
                    ]
                },
            },
        ),
    ]


def test_flow_log_check_offers_disable_expiration_and_deletion_choices():
    class Adapter:
        def get_resources(self, resource_type, filters, region):
            if resource_type == "vpc":
                return [{"resource_id": "vpc-123", "metadata": {}}]
            if resource_type == "vpc_flow_log":
                return [
                    {
                        "resource_id": "fl-1",
                        "metadata": {
                            "LogDestinationType": "s3",
                            "LogDestination": "arn:aws:s3:::logs-bucket/AWSLogs",
                        },
                    }
                ]
            raise AssertionError(resource_type)

    result = check_vpc_flow_logs_enabled(Adapter(), "us-east-1")

    assert result[0]["metadata"]["recommended_actions"] == [
        "vpc_flow_logs_disabling",
        "vpc_flow_logs_disabling_with_bucket_expiration",
        "vpc_flow_logs_disabling_with_bucket_deletion",
    ]
    assert result[0]["metadata"]["flow_logs_samples"][0]["log_destination"] == (
        "arn:aws:s3:::logs-bucket/AWSLogs"
    )
