"""The AWS profile picker must show profiles from the host's ~/.aws/config.

AWS_CONFIG_FILE replaces ~/.aws/config rather than adding to it, so the
container's redirect used to hide every profile defined there.
"""
from __future__ import annotations

from configparser import ConfigParser
from pathlib import Path

import boto3
import pytest

from app.services import iam_onboarding_service as svc

HOST_CONFIG = """\
[default]
region = us-east-1

[profile prod]
region = us-west-2

[profile sandbox]
region = eu-west-1
"""


@pytest.fixture
def aws_layout(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".aws").mkdir(parents=True)
    (home / ".aws" / "config").write_text(HOST_CONFIG, encoding="utf-8")
    (home / ".aws" / "credentials").write_text(
        "[default]\naws_access_key_id = AKIATEST\naws_secret_access_key = secret\n",
        encoding="utf-8",
    )
    target = tmp_path / "data" / "aws" / "config"

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(target))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(home / ".aws" / "credentials"))
    return home, target


def _sections(path: Path) -> list[str]:
    parser = ConfigParser()
    parser.read(path, encoding="utf-8")
    return parser.sections()


def test_host_profiles_are_invisible_without_the_sync(aws_layout):
    """The bug itself: the redirect alone leaves only the credentials profile."""
    assert boto3.Session().available_profiles == ["default"]


def test_sync_makes_host_profiles_visible(aws_layout):
    svc.sync_host_aws_profiles()
    assert sorted(boto3.Session().available_profiles) == ["default", "prod", "sandbox"]


def test_listing_profiles_syncs_first(aws_layout, monkeypatch):
    """A profile added after startup shows up on the next refresh."""
    monkeypatch.setattr(svc, "_profile_summary", lambda name: {"profile_name": name})
    names = {entry["profile_name"] for entry in svc.list_available_aws_profiles()}
    assert {"prod", "sandbox"} <= names


def test_wizard_written_profile_survives_the_sync(aws_layout):
    _, target = aws_layout
    svc.sync_host_aws_profiles()
    svc._write_role_profile("arn:aws:iam::123456789012:role/MaxOpsReadOnlyRole", "prod")

    svc.sync_host_aws_profiles()

    parser = ConfigParser()
    parser.read(target, encoding="utf-8")
    section = "profile MaxOpsReadOnlyRole"
    assert parser.has_section(section)
    assert parser.get(section, "source_profile") == "prod"
    assert "profile prod" in parser.sections()


def test_host_definition_wins_over_a_stale_managed_copy(aws_layout):
    home, target = aws_layout
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "[profile MaxOpsReadOnlyRole]\nrole_arn = arn:aws:iam::123456789012:role/Stale\n",
        encoding="utf-8",
    )
    (home / ".aws" / "config").write_text(
        HOST_CONFIG
        + "\n[profile MaxOpsReadOnlyRole]\nrole_arn = arn:aws:iam::123456789012:role/Fresh\n",
        encoding="utf-8",
    )

    svc.sync_host_aws_profiles()

    parser = ConfigParser()
    parser.read(target, encoding="utf-8")
    assert parser.get("profile MaxOpsReadOnlyRole", "role_arn").endswith("/Fresh")


def test_sync_is_a_no_op_when_no_redirect_is_configured(tmp_path, monkeypatch):
    """A native run reads ~/.aws/config directly; nothing should be rewritten."""
    home = tmp_path / "home"
    (home / ".aws").mkdir(parents=True)
    config = home / ".aws" / "config"
    config.write_text(HOST_CONFIG, encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("AWS_CONFIG_FILE", raising=False)

    assert svc.sync_host_aws_profiles() is None
    assert config.read_text(encoding="utf-8") == HOST_CONFIG


def test_sync_tolerates_a_missing_host_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".aws").mkdir(parents=True)
    target = tmp_path / "data" / "aws" / "config"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(target))

    assert svc.sync_host_aws_profiles() is None
    assert not target.exists()


def test_sync_tolerates_a_malformed_host_config(aws_layout):
    home, target = aws_layout
    (home / ".aws" / "config").write_text("not = ini\n[unclosed\n", encoding="utf-8")

    assert svc.sync_host_aws_profiles() is None


def test_sync_does_not_copy_credentials_into_the_volume(aws_layout):
    _, target = aws_layout
    svc.sync_host_aws_profiles()
    assert "aws_secret_access_key" not in target.read_text(encoding="utf-8")
