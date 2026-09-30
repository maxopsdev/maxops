"""IAM Identity Center (SSO) profiles must reach the container intact.

An SSO profile is split across two ini sections and a token cache file, none
of which the container can produce itself: there is no browser in it, so the
login always happens on the host and MaxOps only ever reads the result.
"""
from __future__ import annotations

import io
from configparser import ConfigParser
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import SSOTokenLoadError

from app.services import iam_onboarding_service as svc

SSO_CONFIG = """\
[default]
region = us-east-1

[sso-session corp]
sso_start_url = https://corp.awsapps.com/start
sso_region = us-east-1
sso_registration_scopes = sso:account:access

[profile eng-prod]
sso_session = corp
sso_account_id = 123456789012
sso_role_name = PowerUserAccess
region = us-west-2

[profile legacy-sso]
sso_start_url = https://corp.awsapps.com/start
sso_region = us-east-1
sso_account_id = 123456789012
sso_role_name = ReadOnly
"""


@pytest.fixture
def sso_host(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".aws" / "sso" / "cache").mkdir(parents=True)
    (home / ".aws" / "config").write_text(SSO_CONFIG, encoding="utf-8")
    target = tmp_path / "data" / "aws" / "config"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(target))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(home / ".aws" / "credentials"))
    return home, target


def test_the_sso_session_section_is_carried_across(sso_host):
    """The profile is useless without it: it holds the start URL and region."""
    _, target = sso_host
    svc.sync_host_aws_profiles()

    parser = ConfigParser()
    parser.read(target, encoding="utf-8")
    assert "sso-session corp" in parser.sections()
    assert parser.get("sso-session corp", "sso_start_url").endswith("/start")
    assert parser.get("sso-session corp", "sso_region") == "us-east-1"


def test_sso_profiles_reach_the_picker(sso_host):
    svc.sync_host_aws_profiles()
    assert set(boto3.Session().available_profiles) >= {"eng-prod", "legacy-sso"}


def test_profile_fields_survive_the_copy(sso_host):
    _, target = sso_host
    svc.sync_host_aws_profiles()

    parser = ConfigParser()
    parser.read(target, encoding="utf-8")
    assert parser.get("profile eng-prod", "sso_session") == "corp"
    assert parser.get("profile eng-prod", "sso_role_name") == "PowerUserAccess"
    # The older inline style has no sso-session to point at.
    assert parser.get("profile legacy-sso", "sso_start_url").endswith("/start")


def test_a_missing_token_says_to_log_in_on_the_host(sso_host):
    """The raw botocore error never mentions the host or the mount."""
    message = svc._aws_error_message(
        "Resolve AWS account for profile",
        SSOTokenLoadError(error_msg="Token for corp does not exist"),
    )
    assert "aws sso login" in message
    assert "not in the container" in message
    assert "AWS_CONFIG_HOST_DIR" in message


def test_non_sso_errors_get_no_sso_advice(sso_host):
    message = svc._aws_error_message("List AWS profiles", RuntimeError("disk on fire"))
    assert "aws sso login" not in message
    assert "disk on fire" in message


def test_the_hint_keys_off_the_exception_name_not_its_message():
    """Botocore's SSO error classes and wording have both moved between versions."""
    assert svc._sso_token_hint(SSOTokenLoadError(error_msg="anything")) != ""
    assert svc._sso_token_hint(ValueError("Token for corp does not exist")) == ""


def test_the_compose_mount_mode_is_configurable_and_read_only_by_default():
    """SSO needs a writable mount; everyone else must keep the safe default."""
    compose = io.open(
        Path(__file__).resolve().parents[2] / "docker-compose.yml", encoding="utf-8"
    ).read()
    assert "/root/.aws:${AWS_CONFIG_MOUNT_MODE:-ro}" in compose


def test_the_example_env_documents_the_sso_mount_mode():
    example = io.open(
        Path(__file__).resolve().parents[2] / ".env.example", encoding="utf-8"
    ).read()
    assert "AWS_CONFIG_MOUNT_MODE" in example
    # Commented out: a blank or stray value must not silently loosen the mount.
    assert "\n# AWS_CONFIG_MOUNT_MODE=rw" in example


SSO_CALLER = "arn:aws:sts::123456789012:assumed-role/AWSReservedSSO_Admin_f837792/user@corp.com"
IAM_CALLER = "arn:aws:iam::123456789012:user/alice"


@pytest.fixture
def role_unusable(monkeypatch):
    monkeypatch.setattr(svc, "_wait_for_role_permissions_ready", lambda name, **kw: False)


@pytest.fixture
def role_usable(monkeypatch):
    monkeypatch.setattr(svc, "_wait_for_role_permissions_ready", lambda name, **kw: True)


def test_a_working_role_becomes_the_scan_profile(role_usable):
    result = svc._usable_scan_profile("MaxOpsReadOnlyRole", "my-sso", SSO_CALLER)
    assert result["scan_profile_name"] == "MaxOpsReadOnlyRole"
    assert result["scan_profile_warning"] is None


def test_an_unassumable_role_does_not_replace_a_working_profile(role_unusable):
    """Onboarding must not move a user from credentials that work to ones that do not."""
    result = svc._usable_scan_profile("MaxOpsReadOnlyRole", "my-sso", SSO_CALLER)
    assert result["scan_profile_name"] == "my-sso"


def test_the_fallback_explains_itself_to_an_sso_user(role_unusable):
    warning = svc._usable_scan_profile("MaxOpsReadOnlyRole", "my-sso", SSO_CALLER)[
        "scan_profile_warning"
    ]
    assert "sts:AssumeRole" in warning
    assert "Identity Center" in warning
    assert "Scan Credentials" in warning


def test_a_non_sso_caller_gets_plain_advice(role_unusable):
    warning = svc._usable_scan_profile("MaxOpsReadOnlyRole", "admin", IAM_CALLER)[
        "scan_profile_warning"
    ]
    assert "Identity Center" not in warning
    assert "sts:AssumeRole" in warning


def test_the_fallback_copes_with_no_source_profile(role_unusable):
    result = svc._usable_scan_profile("MaxOpsReadOnlyRole", None, IAM_CALLER)
    assert result["scan_profile_name"] is None
    assert "your existing credentials" in result["scan_profile_warning"]


def test_the_readiness_probe_reports_failure_rather_than_swallowing_it(monkeypatch):
    """It used to return None either way, so a permanent denial looked like success."""
    def always_fails(*args, **kwargs):
        raise svc.ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "not authorized"}}, "AssumeRole"
        )

    monkeypatch.setattr(svc.boto3, "Session", always_fails)
    assert svc._wait_for_role_permissions_ready("MaxOpsReadOnlyRole", timeout_seconds=0.2) is False


def test_the_readiness_probe_reports_success(monkeypatch):
    class _Session:
        def __init__(self, **kwargs):
            pass

        def client(self, *args, **kwargs):
            class _C:
                def describe_instances(self, **kw):
                    return {"Reservations": []}

            return _C()

    monkeypatch.setattr(svc.boto3, "Session", _Session)
    assert svc._wait_for_role_permissions_ready("MaxOpsReadOnlyRole", timeout_seconds=5) is True
