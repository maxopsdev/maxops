"""The dedicated IAM role for cost-data setup.

The scan role is deliberately read-only. This role is what lets a user turn on
cost data without widening the scan role, so the properties worth pinning are
that it is genuinely separate, that its policy covers exactly the operations
setup performs, and that the S3 grants stay scoped to the report bucket.
"""

import json

import pytest

from app.services import iam_onboarding_service as iam
from app.services.cur_jobs_service import REQUIRED_ACTIONS

pytestmark = [pytest.mark.unit]


def policy_actions() -> set:
    actions = set()
    for statement in iam.COST_DATA_SETUP_POLICY["Statement"]:
        actions.update(statement["Action"])
    return actions


# ------------------------------------------------------------------- policy


def test_policy_covers_every_action_the_setup_jobs_perform():
    """If a job needs a permission the role lacks, setup fails halfway through."""
    granted = policy_actions()
    required = set(REQUIRED_ACTIONS["export"]) | set(REQUIRED_ACTIONS["refresh"])

    assert required <= granted, f"policy is missing: {sorted(required - granted)}"


def test_s3_grants_are_scoped_to_the_report_bucket():
    """A cost-data role must not be able to touch every bucket in the account."""
    for statement in iam.COST_DATA_SETUP_POLICY["Statement"]:
        if not any(action.startswith("s3:") for action in statement["Action"]):
            continue
        resources = statement["Resource"]
        resources = resources if isinstance(resources, list) else [resources]
        assert resources, "S3 statement must name a resource"
        for resource in resources:
            assert resource.startswith("arn:aws:s3:::maxops-cur-report-"), resource


def test_policy_grants_no_iam_write_or_general_wildcard():
    granted = policy_actions()
    assert "*" not in granted
    # The only IAM action allowed is read-only self-introspection, used by the
    # preflight check. Anything else would let this role change access.
    iam_actions = {action for action in granted if action.startswith("iam:")}
    assert iam_actions <= {"iam:SimulatePrincipalPolicy"}, iam_actions


def test_policy_simulation_can_only_target_this_role():
    """Unscoped, it could enumerate any principal's permissions in the account."""
    statements = [
        statement
        for statement in iam.COST_DATA_SETUP_POLICY["Statement"]
        if "iam:SimulatePrincipalPolicy" in statement["Action"]
    ]
    assert len(statements) == 1

    resource = statements[0]["Resource"]
    assert resource == f"arn:aws:iam::*:role/{iam.MAXOPS_COST_DATA_ROLE_NAME}"
    assert resource != "*"


def test_policy_is_json_serialisable():
    # It is written straight into put_role_policy and offered as a download.
    assert json.loads(json.dumps(iam.COST_DATA_SETUP_POLICY))["Version"] == "2012-10-17"


def test_cost_data_role_is_separate_from_the_scan_role():
    assert iam.MAXOPS_COST_DATA_ROLE_NAME != iam.MAXOPS_READ_ONLY_ROLE_NAME
    assert iam.MAXOPS_COST_DATA_POLICY_NAME != iam.MAXOPS_READ_ONLY_POLICY_NAME
    assert iam.MAXOPS_COST_DATA_PROFILE_NAME != iam.MAXOPS_READ_ONLY_PROFILE_NAME


def test_the_two_policies_stay_disjoint_on_writes():
    """Nothing that can create or modify AWS resources may leak into the scan policy."""
    scan_actions = set()
    for statement in iam.READ_ONLY_SCAN_POLICY["Statement"]:
        scan_actions.update(statement["Action"])

    write_verbs = ("Create", "Put", "Update", "Delete", "Start", "Stop")
    offenders = [
        action
        for action in scan_actions
        if any(action.split(":", 1)[-1].startswith(verb) for verb in write_verbs)
    ]
    assert offenders == [], f"scan policy has write actions: {offenders}"


# ------------------------------------------------------- profile generalisation


def test_profile_writer_uses_the_name_it_is_given(tmp_path, monkeypatch):
    config_path = tmp_path / "config"
    monkeypatch.setattr(iam, "_aws_config_path", lambda: config_path)

    result = iam._write_role_profile(
        role_arn="arn:aws:iam::123456789012:role/MaxOpsCostDataRole",
        source_profile_name="admin",
        profile_name=iam.MAXOPS_COST_DATA_PROFILE_NAME,
    )

    assert result["profile_name"] == iam.MAXOPS_COST_DATA_PROFILE_NAME
    written = config_path.read_text(encoding="utf-8")
    assert "[profile MaxOpsCostDataRole]" in written
    assert "role_arn = arn:aws:iam::123456789012:role/MaxOpsCostDataRole" in written
    assert "source_profile = admin" in written


def test_the_two_roles_write_separate_profile_sections(tmp_path, monkeypatch):
    config_path = tmp_path / "config"
    monkeypatch.setattr(iam, "_aws_config_path", lambda: config_path)

    iam._write_role_profile(
        role_arn="arn:aws:iam::1:role/MaxOpsReadOnlyRole",
        source_profile_name="admin",
        profile_name=iam.MAXOPS_READ_ONLY_PROFILE_NAME,
    )
    iam._write_role_profile(
        role_arn="arn:aws:iam::1:role/MaxOpsCostDataRole",
        source_profile_name="admin",
        profile_name=iam.MAXOPS_COST_DATA_PROFILE_NAME,
    )

    written = config_path.read_text(encoding="utf-8")
    assert "[profile MaxOpsReadOnlyRole]" in written
    assert "[profile MaxOpsCostDataRole]" in written


def test_a_managed_profile_never_sources_itself(tmp_path, monkeypatch):
    """Chaining a profile to itself produces an unusable credential loop."""
    config_path = tmp_path / "config"
    monkeypatch.setattr(iam, "_aws_config_path", lambda: config_path)

    result = iam._write_role_profile(
        role_arn="arn:aws:iam::1:role/MaxOpsCostDataRole",
        source_profile_name=iam.MAXOPS_COST_DATA_PROFILE_NAME,
        profile_name=iam.MAXOPS_COST_DATA_PROFILE_NAME,
    )

    assert result.get("source_profile") != iam.MAXOPS_COST_DATA_PROFILE_NAME


# --------------------------------------------------- profile credential check


class _StsDenied(Exception):
    def __init__(self):
        super().__init__("denied")
        self.response = {"Error": {"Code": "AccessDenied", "Message": "nope"}}


def test_a_profile_that_can_sign_in_is_usable(monkeypatch):
    class _Session:
        def __init__(self, **_kw):
            pass

        def client(self, *_a, **_kw):
            class _Sts:
                def get_caller_identity(self):
                    return {"Account": "1", "Arn": "arn:aws:iam::1:role/R"}

            return _Sts()

    monkeypatch.setattr(iam.boto3, "Session", _Session)

    assert iam.verify_profile_credentials("p")["usable"] is True


def test_being_refused_by_aws_still_counts_as_usable(monkeypatch):
    """Credentials resolved and AWS answered. Permissions are a separate problem."""

    class _Session:
        def __init__(self, **_kw):
            pass

        def client(self, *_a, **_kw):
            class _Sts:
                def get_caller_identity(self):
                    raise iam.ClientError(
                        {"Error": {"Code": "AccessDenied", "Message": "nope"}},
                        "GetCallerIdentity",
                    )

            return _Sts()

    monkeypatch.setattr(iam.boto3, "Session", _Session)

    assert iam.verify_profile_credentials("p")["usable"] is True


def test_a_profile_with_no_credential_source_is_reported_unusable(monkeypatch):
    """The real failure: credential_source=Ec2InstanceMetadata off EC2."""

    class _Session:
        def __init__(self, **_kw):
            raise RuntimeError(
                "Error when retrieving credentials from Ec2InstanceMetadata: No credentials "
                "found in credential_source referenced in profile MaxOpsCostDataRole"
            )

    monkeypatch.setattr(iam.boto3, "Session", _Session)

    result = iam.verify_profile_credentials("MaxOpsCostDataRole")

    assert result["usable"] is False
    assert "named AWS profile" in result["hint"]
    assert "Default credentials" in result["hint"]
