"""Unit tests for registering an existing IAM role ARN during onboarding.

Unlike `create_or_update_read_only_role`, this path makes no IAM write calls,
so it's safe to test without any real AWS credentials — only `_aws_session`
(mocked) and the local AWS config file (redirected to a tmp path) are touched.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services import iam_onboarding_service as svc
from app.services.iam_onboarding_service import IamRoleCreationError, use_existing_read_only_role

pytestmark = [pytest.mark.unit]


def _fake_session(account_id: str = "123456789012") -> MagicMock:
    session = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {"Account": account_id, "Arn": "arn:aws:iam::123456789012:user/dev"}
    session.client.return_value = sts_client
    return session


@pytest.fixture(autouse=True)
def _skip_propagation_wait(monkeypatch):
    """The post-write readiness probe retries against real AWS for up to 20s;
    pointless (and slow) against mocked sessions. It returns True because a
    False now means "the role could not be assumed", which makes onboarding
    keep the source profile instead of adopting the role."""
    monkeypatch.setattr(svc, "_wait_for_role_permissions_ready", lambda *a, **k: True)


def test_use_existing_role_registers_local_profile_without_iam_writes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setattr(svc, "_aws_session", lambda profile_name=None: _fake_session())

    role_arn = "arn:aws:iam::123456789012:role/SomeExistingReadOnlyRole"
    result = use_existing_read_only_role(role_arn=role_arn, profile_name="my-profile")

    assert result["status"] == "existing_role_registered"
    assert result["role_arn"] == role_arn
    assert result["aws_account_id"] == "123456789012"
    assert result["scan_profile_name"] == svc.MAXOPS_READ_ONLY_PROFILE_NAME

    config_text = (tmp_path / "config").read_text()
    assert role_arn in config_text
    assert "source_profile = my-profile" in config_text


def test_use_existing_role_rejects_blank_arn() -> None:
    with pytest.raises(IamRoleCreationError):
        use_existing_read_only_role(role_arn="   ")


def test_use_existing_role_rejects_non_arn_looking_value() -> None:
    with pytest.raises(IamRoleCreationError):
        use_existing_read_only_role(role_arn="not-an-arn")


def test_use_existing_role_surfaces_sts_errors(monkeypatch) -> None:
    from botocore.exceptions import ClientError

    def _raise_session(profile_name=None):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "nope"}}, "GetCallerIdentity")

    monkeypatch.setattr(svc, "_aws_session", _raise_session)

    with pytest.raises(IamRoleCreationError):
        use_existing_read_only_role(role_arn="arn:aws:iam::123456789012:role/Foo")


def test_use_existing_role_rejects_malformed_account_id(monkeypatch) -> None:
    monkeypatch.setattr(svc, "_aws_session", lambda profile_name=None: _fake_session())

    with pytest.raises(IamRoleCreationError, match="does not look like a valid IAM role ARN"):
        use_existing_read_only_role(role_arn="arn:aws:iam::123:role/TooShortAccount")


def test_use_existing_role_rejects_nonexistent_role(monkeypatch) -> None:
    from botocore.exceptions import ClientError

    session = _fake_session()
    iam_client = MagicMock()
    iam_client.get_role.side_effect = ClientError(
        {"Error": {"Code": "NoSuchEntity", "Message": "not found"}}, "GetRole"
    )
    sts_client = session.client.return_value
    session.client.side_effect = lambda service, **kwargs: iam_client if service == "iam" else sts_client
    monkeypatch.setattr(svc, "_aws_session", lambda profile_name=None: session)

    with pytest.raises(IamRoleCreationError, match="does not exist in account"):
        use_existing_read_only_role(role_arn="arn:aws:iam::123456789012:role/DoesNotExist")


def test_use_existing_role_rejects_unassumable_role(monkeypatch) -> None:
    from botocore.exceptions import ClientError

    session = _fake_session()
    sts_client = session.client.return_value
    sts_client.assume_role.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "not authorized to assume"}}, "AssumeRole"
    )
    monkeypatch.setattr(svc, "_aws_session", lambda profile_name=None: session)

    with pytest.raises(IamRoleCreationError, match="Could not assume"):
        use_existing_read_only_role(role_arn="arn:aws:iam::123456789012:role/NotAssumable")


def test_use_existing_role_skips_validation_when_caller_is_the_role(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    session = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": "123456789012",
        "Arn": "arn:aws:sts::123456789012:assumed-role/MaxOpsReadOnlyRole/session",
    }
    sts_client.assume_role.side_effect = AssertionError("assume_role should not be called for the role's own session")
    session.client.return_value = sts_client
    monkeypatch.setattr(svc, "_aws_session", lambda profile_name=None: session)

    result = use_existing_read_only_role(
        role_arn="arn:aws:iam::123456789012:role/MaxOpsReadOnlyRole",
        profile_name="MaxOpsReadOnlyRole",
    )
    assert result["status"] == "existing_role_registered"
