"""The trust policy principal must be a role ARN IAM can actually resolve.

sts:GetCallerIdentity returns an assumed-role ARN, which omits the role's
path. Rebuilding an IAM ARN from it names a role that does not exist for
anything under a path, and IAM rejects that with MalformedPolicyDocument.
"""
from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from app.services import iam_onboarding_service as svc

ACCOUNT = "123456789012"
SSO_CALLER = f"arn:aws:sts::{ACCOUNT}:assumed-role/AWSReservedSSO_Admin_abc123/user@corp.com"
SSO_REAL_ARN = (
    f"arn:aws:iam::{ACCOUNT}:role/aws-reserved/sso.amazonaws.com/"
    "us-east-1/AWSReservedSSO_Admin_abc123"
)


class FakeIam:
    def __init__(self, arns=None, error=None):
        self.arns = arns or {}
        self.error = error
        self.calls = []

    def get_role(self, RoleName):
        self.calls.append(RoleName)
        if self.error:
            raise self.error
        if RoleName not in self.arns:
            raise ClientError(
                {"Error": {"Code": "NoSuchEntity", "Message": "not found"}}, "GetRole"
            )
        return {"Role": {"Arn": self.arns[RoleName]}}


def _malformed(message="Invalid principal in policy"):
    return ClientError(
        {"Error": {"Code": "MalformedPolicyDocument", "Message": message}}, "CreateRole"
    )


def test_identity_center_role_keeps_its_path():
    iam = FakeIam({"AWSReservedSSO_Admin_abc123": SSO_REAL_ARN})
    assert svc._iam_principal_from_caller(SSO_CALLER, ACCOUNT, iam) == SSO_REAL_ARN
    assert iam.calls == ["AWSReservedSSO_Admin_abc123"]


def test_pathless_role_is_unchanged():
    arn = f"arn:aws:iam::{ACCOUNT}:role/AdminRole"
    iam = FakeIam({"AdminRole": arn})
    caller = f"arn:aws:sts::{ACCOUNT}:assumed-role/AdminRole/session"
    assert svc._iam_principal_from_caller(caller, ACCOUNT, iam) == arn


def test_falls_back_to_the_derived_arn_when_get_role_is_denied():
    """A denied iam:GetRole must not break the path that used to work."""
    denied = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "no"}}, "GetRole"
    )
    caller = f"arn:aws:sts::{ACCOUNT}:assumed-role/AdminRole/session"
    result = svc._iam_principal_from_caller(caller, ACCOUNT, FakeIam(error=denied))
    assert result == f"arn:aws:iam::{ACCOUNT}:role/AdminRole"


def test_no_iam_client_still_derives_an_arn():
    caller = f"arn:aws:sts::{ACCOUNT}:assumed-role/AdminRole/session"
    assert svc._iam_principal_from_caller(caller, ACCOUNT) == (
        f"arn:aws:iam::{ACCOUNT}:role/AdminRole"
    )


def test_partition_is_preserved_in_the_fallback():
    caller = f"arn:aws-us-gov:sts::{ACCOUNT}:assumed-role/AdminRole/session"
    result = svc._iam_principal_from_caller(caller, ACCOUNT)
    assert result == f"arn:aws-us-gov:iam::{ACCOUNT}:role/AdminRole"


def test_iam_user_caller_is_used_directly():
    caller = f"arn:aws:iam::{ACCOUNT}:user/alice"
    iam = FakeIam()
    assert svc._iam_principal_from_caller(caller, ACCOUNT, iam) == caller
    assert iam.calls == []


def test_empty_caller_falls_back_to_account_root():
    assert svc._iam_principal_from_caller("", ACCOUNT) == f"arn:aws:iam::{ACCOUNT}:root"


def test_invalid_principal_error_names_the_derived_arn():
    hint = svc._principal_error_hint(_malformed(), SSO_CALLER, SSO_REAL_ARN)
    assert SSO_REAL_ARN in hint
    assert SSO_CALLER in hint
    assert "iam:GetRole" in hint


def test_unrelated_malformed_policy_errors_get_no_principal_hint():
    other = _malformed("Syntax errors in policy.")
    assert svc._principal_error_hint(other, SSO_CALLER, SSO_REAL_ARN) == ""


def test_other_error_codes_get_no_hint():
    denied = ClientError({"Error": {"Code": "AccessDenied", "Message": "principal"}}, "CreateRole")
    assert svc._principal_error_hint(denied, SSO_CALLER, SSO_REAL_ARN) == ""


FEDERATED_CALLER = f"arn:aws:sts::{ACCOUNT}:federated-user/Bob"
ACCOUNT_ROOT = f"arn:aws:iam::{ACCOUNT}:root"


def test_federated_user_falls_back_to_the_account_root():
    """A federated-user ARN names a session, not an entity; IAM rejects it."""
    iam = FakeIam()
    assert svc._iam_principal_from_caller(FEDERATED_CALLER, ACCOUNT, iam) == ACCOUNT_ROOT
    assert iam.calls == []


def test_federated_user_keeps_a_non_default_partition():
    caller = f"arn:aws-cn:sts::{ACCOUNT}:federated-user/Bob"
    assert svc._iam_principal_from_caller(caller, ACCOUNT) == f"arn:aws-cn:iam::{ACCOUNT}:root"


def test_federated_user_error_states_the_sts_limitation():
    hint = svc._principal_error_hint(_malformed(), FEDERATED_CALLER, ACCOUNT_ROOT)
    assert "federated-user session" in hint
    assert "assume a role" in hint


def test_federated_user_is_explained_for_any_error_code():
    """The limitation surfaces as AccessDenied too, so do not gate on one code."""
    denied = ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "CreateRole")
    assert "federated-user session" in svc._principal_error_hint(denied, FEDERATED_CALLER, ACCOUNT_ROOT)


def test_non_federated_callers_are_unaffected_by_the_federated_branch():
    denied = ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "CreateRole")
    assert svc._principal_error_hint(denied, SSO_CALLER, SSO_REAL_ARN) == ""
