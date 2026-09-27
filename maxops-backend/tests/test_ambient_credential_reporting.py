"""With no profiles to list, the picker must say where credentials came from.

Labelling that entry "default" reads as the [default] profile. It is not one:
it means no profile at all, so exported AWS_ACCESS_KEY_ID keys silently decide
which account MaxOps talks to.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import iam_onboarding_service as svc

AMBIENT_VARS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
)


@pytest.fixture
def no_profiles(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".aws").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "data" / "aws" / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(home / ".aws" / "credentials"))
    for name in AMBIENT_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(svc, "_profile_summary", lambda name: svc._profile_summary_shell(name))
    return home


def _only_entry():
    rows = svc.list_available_aws_profiles()
    assert len(rows) == 1
    return rows[0]


def test_environment_keys_are_named_not_disguised_as_default(no_profiles, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")

    entry = _only_entry()

    assert entry["display_name"] == "environment credentials"
    assert entry["profile_name"] is None


def test_container_credentials_are_named(no_profiles, monkeypatch):
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "/v2/credentials/abc")
    assert _only_entry()["display_name"] == "container credentials"


def test_falls_back_to_default_when_no_source_is_identifiable(no_profiles):
    assert _only_entry()["display_name"] == "default"


def test_missing_config_is_explained_with_the_docker_setting(no_profiles):
    error = _only_entry()["error"]
    assert error is not None
    assert "AWS_CONFIG_HOST_DIR" in error


def test_hint_is_dropped_once_a_config_file_exists(no_profiles):
    (no_profiles / ".aws" / "config").write_text("[default]\nregion = us-east-1\n", encoding="utf-8")
    assert svc._missing_aws_config_hint() is None


def test_a_real_profile_list_is_left_alone(no_profiles, monkeypatch):
    (no_profiles / ".aws" / "config").write_text(
        "[default]\nregion = us-east-1\n\n[profile prod]\nregion = us-west-2\n", encoding="utf-8"
    )
    names = {entry["display_name"] for entry in svc.list_available_aws_profiles()}
    assert names == {"default", "prod"}


def test_an_explicit_profile_overrides_exported_keys(tmp_path, monkeypatch):
    """Why listing profiles matters: choosing one takes the env keys out of play."""
    import botocore.session

    aws = tmp_path / "aws"
    aws.mkdir()
    (aws / "config").write_text("[profile prod]\nregion = us-west-2\n", encoding="utf-8")
    (aws / "credentials").write_text(
        "[prod]\naws_access_key_id = AKIA_FROM_PROFILE\naws_secret_access_key = s\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AWS_CONFIG_FILE", str(aws / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(aws / "credentials"))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_FROM_ENVIRONMENT")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "s")

    ambient = botocore.session.Session().get_credentials().access_key
    chosen = botocore.session.Session(profile="prod").get_credentials().access_key

    assert ambient == "AKIA_FROM_ENVIRONMENT"
    assert chosen == "AKIA_FROM_PROFILE"
