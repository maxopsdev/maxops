"""IAM role bootstrap helpers for onboarding."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from configparser import ConfigParser, Error as ConfigParserError
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings

logger = logging.getLogger("uvicorn.error")

MAXOPS_READ_ONLY_ROLE_NAME = "MaxOpsReadOnlyRole"
MAXOPS_READ_ONLY_POLICY_NAME = "MaxOpsReadOnlyScanPolicy"
MAXOPS_READ_ONLY_PROFILE_NAME = "MaxOpsReadOnlyRole"

# A second, separate role for cost-data setup. Kept apart from the scan role
# on purpose: this one can create an S3 bucket, register a Cost and Usage
# Report export and run Athena queries, none of which a scanning role should
# ever be able to do.
MAXOPS_COST_DATA_ROLE_NAME = "MaxOpsCostDataRole"
MAXOPS_COST_DATA_POLICY_NAME = "MaxOpsCostDataSetupPolicy"
MAXOPS_COST_DATA_PROFILE_NAME = "MaxOpsCostDataRole"

DEFAULT_AWS_PROFILE_NAME = "default"

# The report bucket is named maxops-cur-report-<account id>; the wildcard keeps
# the policy valid without templating the account in at policy-build time.
_CUR_BUCKET_PATTERN = "arn:aws:s3:::maxops-cur-report-*"

COST_DATA_SETUP_POLICY: Dict[str, Any] = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "Identity",
            "Effect": "Allow",
            "Action": ["sts:GetCallerIdentity"],
            "Resource": "*",
        },
        {
            # Lets the setup page check its own permissions before running a
            # job, so a missing grant is reported up front instead of the job
            # failing halfway through. Scoped to this role, so it can only
            # introspect itself, not enumerate other principals' permissions.
            "Sid": "SelfPolicySimulation",
            "Effect": "Allow",
            "Action": ["iam:SimulatePrincipalPolicy"],
            "Resource": f"arn:aws:iam::*:role/{MAXOPS_COST_DATA_ROLE_NAME}",
        },
        {
            # Create and own the report bucket, and read the billing data AWS
            # writes into it. Scoped to the MaxOps report bucket only.
            "Sid": "ReportBucket",
            "Effect": "Allow",
            "Action": [
                "s3:CreateBucket",
                "s3:GetBucketLocation",
                "s3:GetBucketPolicy",
                "s3:PutBucketPolicy",
                "s3:ListBucket",
                "s3:GetObject",
                "s3:PutObject",
                "s3:DeleteObject",
                # Athena writes query results into this bucket and uses
                # multipart uploads to do it.
                "s3:ListBucketMultipartUploads",
                "s3:ListMultipartUploadParts",
                "s3:AbortMultipartUpload",
            ],
            "Resource": [_CUR_BUCKET_PATTERN, f"{_CUR_BUCKET_PATTERN}/*"],
        },
        {
            # Registering the Cost and Usage Report export itself.
            #
            # The bcm-data-exports API is a front end over the older CUR API:
            # CreateExport also authorises against cur:PutReportDefinition, so
            # granting only the bcm-data-exports actions is not enough.
            "Sid": "CostAndUsageExport",
            "Effect": "Allow",
            "Action": [
                "bcm-data-exports:CreateExport",
                "bcm-data-exports:GetExport",
                "bcm-data-exports:ListExports",
                "bcm-data-exports:UpdateExport",
                "cur:PutReportDefinition",
                "cur:ModifyReportDefinition",
                "cur:DeleteReportDefinition",
                "cur:DescribeReportDefinitions",
            ],
            "Resource": "*",
        },
        {
            # Athena runs the queries that summarise the bill. Query execution
            # is not resource-scopeable beyond the workgroup.
            "Sid": "AthenaQueries",
            "Effect": "Allow",
            "Action": [
                "athena:StartQueryExecution",
                "athena:GetQueryExecution",
                "athena:GetQueryResults",
                "athena:GetWorkGroup",
                "athena:StopQueryExecution",
            ],
            "Resource": "*",
        },
        {
            # The Glue catalog backs the Athena table over the raw export.
            "Sid": "GlueCatalog",
            "Effect": "Allow",
            "Action": [
                "glue:CreateDatabase",
                "glue:GetDatabase",
                "glue:GetDatabases",
                "glue:CreateTable",
                "glue:GetTable",
                "glue:GetTables",
                "glue:UpdateTable",
                "glue:DeleteTable",
                "glue:GetPartitions",
            ],
            "Resource": "*",
        },
    ],
}
PROFILE_STS_CONFIG = Config(
    connect_timeout=1,
    read_timeout=1,
    retries={"total_max_attempts": 1},
)

READ_ONLY_SCAN_POLICY: Dict[str, Any] = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "IdentityAndPricingRead",
            "Effect": "Allow",
            "Action": [
                "pricing:GetProducts",
                "sts:GetCallerIdentity",
            ],
            "Resource": "*",
        },
        {
            "Sid": "Ec2EbsVpcRead",
            "Effect": "Allow",
            "Action": [
                "ec2:DescribeAddresses",
                "ec2:DescribeFlowLogs",
                "ec2:DescribeInstances",
                "ec2:DescribeSnapshots",
                "ec2:DescribeVolumes",
                "ec2:DescribeVpcEndpoints",
                "ec2:DescribeVpcs",
            ],
            "Resource": "*",
        },
        {
            "Sid": "CloudWatchAndLogsRead",
            "Effect": "Allow",
            "Action": [
                "cloudwatch:DescribeAlarmHistory",
                "cloudwatch:DescribeAlarms",
                "cloudwatch:GetMetricData",
                "cloudwatch:GetMetricStatistics",
                "cloudwatch:ListMetrics",
                "logs:DescribeLogGroups",
                "logs:FilterLogEvents",
            ],
            "Resource": "*",
        },
        {
            "Sid": "DatabaseRead",
            "Effect": "Allow",
            "Action": [
                "rds:DescribeDBClusterSnapshots",
                "rds:DescribeDBClusters",
                "rds:DescribeDBInstances",
                "rds:DescribeDBParameters",
                "rds:DescribeEvents",
                "rds:DescribeGlobalClusters",
                "rds:DescribeOrderableDBInstanceOptions",
                "rds:DescribePendingMaintenanceActions",
                "rds:DescribeValidDBInstanceModifications",
                "rds:ListTagsForResource",
            ],
            "Resource": "*",
        },
        {
            "Sid": "PerformanceInsightsOptionalRead",
            "Effect": "Allow",
            "Action": ["pi:GetResourceMetrics"],
            "Resource": "*",
        },
        {
            "Sid": "S3AccountBucketList",
            "Effect": "Allow",
            "Action": [
                "s3:ListAllMyBuckets",
            ],
            "Resource": "*",
        },
        {
            "Sid": "S3BucketConfigurationRead",
            "Effect": "Allow",
            "Action": [
                "s3:GetBucketLocation",
                "s3:GetBucketLogging",
                "s3:GetBucketTagging",
                "s3:GetBucketVersioning",
                "s3:GetInventoryConfiguration",
                "s3:GetLifecycleConfiguration",
                "s3:GetReplicationConfiguration",
                "s3:ListBucket",
            ],
            "Resource": "arn:aws:s3:::*",
        },
        {
            "Sid": "ComputeInventoryRead",
            "Effect": "Allow",
            "Action": [
                "autoscaling:DescribeAutoScalingGroups",
                "autoscaling:DescribeInstanceRefreshes",
                "autoscaling:DescribeLaunchConfigurations",
                "autoscaling:DescribePolicies",
                "autoscaling:DescribeScalingActivities",
                "autoscaling:DescribeScheduledActions",
                "autoscaling:DescribeWarmPool",
                "ec2:DescribeLaunchTemplateVersions",
                "ecs:DescribeClusters",
                "ecs:DescribeServices",
                "ecs:DescribeTaskDefinition",
                "ecs:ListClusters",
                "ecs:ListServices",
                "lambda:GetProvisionedConcurrencyConfig",
                "lambda:ListAliases",
                "lambda:ListFunctions",
                "lambda:ListTags",
            ],
            "Resource": "*",
        },
        {
            "Sid": "DataServicesRead",
            "Effect": "Allow",
            "Action": [
                "athena:GetWorkGroup",
                "athena:ListWorkGroups",
                "dynamodb:DescribeTable",
                "dynamodb:ListTables",
                "firehose:DescribeDeliveryStream",
                "firehose:ListDeliveryStreams",
                "glue:GetDatabases",
                "glue:GetJob",
                "glue:GetJobRuns",
                "glue:GetJobs",
                "glue:GetPartitions",
                "glue:GetTables",
                "glue:ListJobs",
                "kinesis:DescribeStreamSummary",
                "kinesis:ListStreams",
            ],
            "Resource": "*",
        },
        {
            "Sid": "AnalyticsAndStorageRead",
            "Effect": "Allow",
            "Action": [
                "elasticache:DescribeCacheClusters",
                "elasticache:DescribeCacheParameterGroups",
                "elasticache:DescribeCacheParameters",
                "elasticache:DescribeReplicationGroups",
                "elasticache:ListAllowedNodeTypeModifications",
                "application-autoscaling:DescribeScalableTargets",
                "elasticfilesystem:DescribeFileSystems",
                "elasticfilesystem:DescribeLifecycleConfiguration",
                "elasticfilesystem:DescribeMountTargets",
                "elasticfilesystem:DescribeTags",
                "elasticmapreduce:DescribeCluster",
                "elasticmapreduce:ListClusters",
                "elasticmapreduce:ListInstanceFleets",
                "elasticmapreduce:ListInstanceGroups",
                "redshift:DescribeClusterSnapshots",
                "redshift:DescribeClusters",
            ],
            "Resource": "*",
        },
        {
            "Sid": "OpenSearchRead",
            "Effect": "Allow",
            "Action": [
                "es:DescribeDomain",
                "es:ESHttpGet",
                "es:ListDomainNames",
            ],
            "Resource": "*",
        },
    ],
}


class IamRoleCreationError(Exception):
    """Raised when AWS rejects onboarding IAM role creation."""


def _normalize_profile_name(profile_name: Optional[str]) -> Optional[str]:
    if not profile_name:
        return None
    normalized = profile_name.strip()
    return normalized or None


def _aws_session(profile_name: Optional[str] = None) -> boto3.Session:
    selected_profile = _normalize_profile_name(profile_name)
    if settings.aws_use_iam_role:
        return boto3.Session(region_name=settings.aws_region)
    if settings.aws_role_arn:
        return _assume_role_session(selected_profile)
    if selected_profile:
        return boto3.Session(profile_name=selected_profile, region_name=settings.aws_region)
    if settings.aws_profile:
        return boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
    session_kwargs: Dict[str, Any] = {"region_name": settings.aws_region}
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        session_kwargs.update(
            {
                "aws_access_key_id": settings.aws_access_key_id,
                "aws_secret_access_key": settings.aws_secret_access_key,
            }
        )
    return boto3.Session(**session_kwargs)


def _assume_role_session(profile_name: Optional[str] = None) -> boto3.Session:
    source_profile = _normalize_profile_name(profile_name) or settings.aws_profile
    source_session = (
        boto3.Session(profile_name=source_profile, region_name=settings.aws_region)
        if source_profile
        else boto3.Session(region_name=settings.aws_region)
    )
    sts_client = source_session.client("sts", region_name=settings.aws_region)
    try:
        response = sts_client.assume_role(
            RoleArn=settings.aws_role_arn,
            RoleSessionName=settings.aws_role_session_name,
            DurationSeconds=3600,
        )
    except (BotoCoreError, ClientError) as exc:
        raise IamRoleCreationError(_aws_error_message("Assume configured AWS role", exc)) from exc

    credentials = response["Credentials"]
    return boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=settings.aws_region,
    )


def _resolve_role_arn(iam_client, role_name: str) -> Optional[str]:
    """Look up a role's real ARN, or None when it cannot be read.

    An assumed-role ARN drops the role's path, so the ARN rebuilt from it names
    a role that does not exist for anything stored under one -- Identity Center
    roles above all, which live at /aws-reserved/sso.amazonaws.com/<region>/.
    IAM rejects a principal that does not resolve with MalformedPolicyDocument.
    """
    if iam_client is None or not role_name:
        return None
    try:
        return str(iam_client.get_role(RoleName=role_name)["Role"]["Arn"]) or None
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Could not resolve the ARN for role %s: %s", role_name, exc)
        return None


def _partition_from_arn(caller_arn: str) -> str:
    """Read the partition out of an ARN, defaulting to "aws"."""
    parts = caller_arn.split(":")
    return parts[1] if len(parts) > 2 and parts[1] else "aws"


def _account_root_principal(account_id: str, partition: str = "aws") -> str:
    return f"arn:{partition}:iam::{account_id}:root"


def _iam_principal_from_caller(caller_arn: str, account_id: str, iam_client=None) -> str:
    """Pick a trust-policy principal IAM will accept for the current caller.

    Falls back to the account root, which always resolves, when the caller has
    no ARN that can name a principal.
    """
    assumed_role_marker = ":assumed-role/"
    if assumed_role_marker in caller_arn:
        prefix, suffix = caller_arn.split(assumed_role_marker, 1)
        role_name = suffix.split("/", 1)[0]
        partition = prefix.split(":", 2)[1]
        resolved = _resolve_role_arn(iam_client, role_name)
        return resolved or f"arn:{partition}:iam::{account_id}:role/{role_name}"
    if ":federated-user/" in caller_arn:
        # GetFederationToken identities have no IAM principal to name -- the
        # federated-user ARN is a session, not an entity, and IAM rejects it.
        # The account root is the only principal that resolves; which callers
        # may then assume the role is decided by their own IAM permissions.
        return _account_root_principal(account_id, _partition_from_arn(caller_arn))
    if caller_arn:
        return caller_arn
    return _account_root_principal(account_id)


def _principal_error_hint(exc: Exception, caller_arn: str, principal_arn: str) -> str:
    """Explain a failed role setup in terms of the caller, or "" if unexplained.

    IAM reports only "invalid principal in policy", never which principal, so
    the ARN MaxOps derived from the caller is the whole diagnosis.
    """
    if ":federated-user/" in caller_arn:
        # Documented STS limitation, not a permissions gap: GetFederationToken
        # credentials cannot call any IAM operation, nor any STS operation but
        # GetCallerIdentity. No trust policy makes this flow work.
        return (
            f" The signed-in identity {caller_arn} is a federated-user session from "
            "sts:GetFederationToken. AWS does not allow these credentials to call IAM "
            "operations, or to assume a role, so MaxOps cannot create a scan role with "
            "them. Scan with this profile directly, or run role setup once with an IAM "
            "user or an assumed role."
        )
    if not isinstance(exc, ClientError):
        return ""
    error = exc.response.get("Error", {})
    if error.get("Code") != "MalformedPolicyDocument":
        return ""
    if "principal" not in str(error.get("Message", "")).lower():
        return ""
    return (
        f" MaxOps trusted {principal_arn}, derived from the signed-in identity {caller_arn}. "
        "IAM could not resolve it, which usually means the caller's role sits under a path "
        "and MaxOps lacks iam:GetRole to read its full ARN. Grant iam:GetRole, or sign in "
        "with a profile whose role has no path."
    )


def _trust_policy(principal_arn: str) -> Dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowCurrentMaxOpsCaller",
                "Effect": "Allow",
                "Principal": {"AWS": principal_arn},
                "Action": "sts:AssumeRole",
            }
        ],
    }


def _aws_error_message(operation: str, exc: Exception) -> str:
    if not isinstance(exc, ClientError):
        return f"{operation} failed: {exc}"
    error = exc.response.get("Error", {})
    code = error.get("Code", "AWSClientError")
    message = error.get("Message", str(exc))
    return f"{operation} failed ({code}): {message}"


def _put_inline_policy(
    iam_client,
    role_name: str,
    policy_name: str,
    policy_document: Dict[str, Any],
) -> None:
    iam_client.put_role_policy(
        RoleName=role_name,
        PolicyName=policy_name,
        PolicyDocument=json.dumps(policy_document),
    )


def _put_read_only_policy(iam_client, role_name: str) -> None:
    _put_inline_policy(
        iam_client,
        role_name,
        MAXOPS_READ_ONLY_POLICY_NAME,
        READ_ONLY_SCAN_POLICY,
    )


def _aws_config_path() -> Path:
    configured_path = os.environ.get("AWS_CONFIG_FILE")
    if configured_path:
        return Path(configured_path).expanduser()
    return Path.home() / ".aws" / "config"


def _profile_section_name(profile_name: str) -> str:
    return "default" if profile_name == DEFAULT_AWS_PROFILE_NAME else f"profile {profile_name}"


def _host_aws_config_path() -> Path:
    return Path.home() / ".aws" / "config"


def sync_host_aws_profiles() -> Optional[Dict[str, Any]]:
    """Mirror the host's ~/.aws/config into the config file MaxOps reads.

    Returns None when no copy is needed or the host file is unreadable.

    AWS_CONFIG_FILE replaces ~/.aws/config rather than adding to it, so
    redirecting it -- which the container does, to keep the mounted ~/.aws
    read-only while still being able to write the role profile -- hides every
    profile the user has defined there. Only ~/.aws/credentials stays visible,
    which is why such a setup lists `default` and nothing else.
    """
    target = _aws_config_path()
    source = _host_aws_config_path()
    if target.resolve() == source.resolve() or not source.is_file():
        return None

    host_config = ConfigParser()
    existing = ConfigParser()
    try:
        host_config.read(source, encoding="utf-8")
        if target.is_file():
            existing.read(target, encoding="utf-8")
    except (OSError, UnicodeDecodeError, ConfigParserError) as exc:
        logger.warning("Could not read AWS config files to sync profiles: %s", exc)
        return None

    merged = ConfigParser()
    for section in host_config.sections():
        merged[section] = dict(host_config.items(section))

    # Profiles the wizard wrote live only in the target; the host copy has no
    # record of them, so carry them over unless the host defines them itself.
    for managed_name in (MAXOPS_READ_ONLY_PROFILE_NAME, MAXOPS_COST_DATA_PROFILE_NAME):
        section = _profile_section_name(managed_name)
        if existing.has_section(section) and not merged.has_section(section):
            merged[section] = dict(existing.items(section))

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as config_file:
            merged.write(config_file)
    except OSError as exc:
        logger.warning("Could not write the synced AWS config at %s: %s", target, exc)
        return None

    return {
        "source": str(source),
        "target": str(target),
        "sections": merged.sections(),
    }


def _source_profile_settings(
    source_profile_name: Optional[str],
    managed_profile_name: str = MAXOPS_READ_ONLY_PROFILE_NAME,
) -> Dict[str, str]:
    normalized_source_profile = _normalize_profile_name(source_profile_name)
    # A managed profile must never list itself as its own source.
    if normalized_source_profile == managed_profile_name:
        normalized_source_profile = None
    if normalized_source_profile:
        return {"source_profile": normalized_source_profile}

    if settings.aws_use_iam_role:
        return {"credential_source": "Ec2InstanceMetadata"}

    if settings.aws_access_key_id and settings.aws_secret_access_key:
        return {"credential_source": "Environment"}

    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return {"credential_source": "Environment"}

    return {"credential_source": "Ec2InstanceMetadata"}


def _write_role_profile(
    role_arn: str,
    source_profile_name: Optional[str],
    profile_name: str = MAXOPS_READ_ONLY_PROFILE_NAME,
) -> Dict[str, Any]:
    config_path = _aws_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    parser = ConfigParser()
    parser.read(config_path)

    section_name = _profile_section_name(profile_name)
    if not parser.has_section(section_name):
        parser.add_section(section_name)

    preserved_source_settings: Dict[str, str] = {}
    if _normalize_profile_name(source_profile_name) == profile_name:
        existing_source_profile = parser.get(section_name, "source_profile", fallback=None)
        existing_credential_source = parser.get(section_name, "credential_source", fallback=None)
        if existing_source_profile and existing_source_profile != profile_name:
            preserved_source_settings["source_profile"] = existing_source_profile
        elif existing_credential_source:
            preserved_source_settings["credential_source"] = existing_credential_source

    for stale_key in ("source_profile", "credential_source"):
        if parser.has_option(section_name, stale_key):
            parser.remove_option(section_name, stale_key)

    parser.set(section_name, "role_arn", role_arn)
    parser.set(section_name, "region", settings.aws_region)
    profile_settings = preserved_source_settings or _source_profile_settings(
        source_profile_name, profile_name
    )
    for key, value in profile_settings.items():
        parser.set(section_name, key, value)

    with config_path.open("w", encoding="utf-8") as config_file:
        parser.write(config_file)

    source_settings = dict(parser.items(section_name))
    return {
        "profile_name": profile_name,
        "config_path": str(config_path),
        "role_arn": role_arn,
        "source_profile": source_settings.get("source_profile"),
        "credential_source": source_settings.get("credential_source"),
    }


def _wait_for_role_permissions_ready(profile_name: str, timeout_seconds: float = 20.0) -> None:
    """Best-effort wait for a freshly created/updated role's permissions to propagate.

    IAM role and inline-policy changes are eventually consistent -- using a
    brand-new (or just-updated) role immediately can trip transient
    AccessDenied/UnauthorizedOperation errors for a few seconds after the
    change. Onboarding's "run all checks" step fires ~100 checks with 3-way
    concurrency right after this returns; if the same transient error hits 3
    checks in a row, its circuit breaker cancels the entire scan after only a
    handful of checks. Absorbing that propagation window here -- before the
    frontend ever starts running checks -- avoids that failure mode instead of
    relying on the caller to retry.

    Deliberately swallows failures once the timeout elapses: the role is very
    likely fine and just still propagating in a slower account/region, and
    onboarding shouldn't hard-fail over a best-effort readiness probe.
    """
    deadline = time.monotonic() + timeout_seconds
    delay = 1.0
    while time.monotonic() < deadline:
        try:
            session = boto3.Session(profile_name=profile_name, region_name=settings.aws_region)
            session.client("ec2", region_name=settings.aws_region).describe_instances(MaxResults=5)
            return
        except (BotoCoreError, ClientError):
            time.sleep(delay)
            delay = min(delay * 1.5, 4.0)


def _session_for_profile_lookup(profile_name: Optional[str]) -> boto3.Session:
    selected_profile = _normalize_profile_name(profile_name)
    if selected_profile:
        return boto3.Session(profile_name=selected_profile, region_name=settings.aws_region)
    return boto3.Session(region_name=settings.aws_region)


def _profile_summary_shell(profile_name: Optional[str]) -> Dict[str, Any]:
    selected_profile = _normalize_profile_name(profile_name)
    return {
        "profile_name": selected_profile,
        "display_name": selected_profile or DEFAULT_AWS_PROFILE_NAME,
        "account_id": None,
        "arn": None,
        "is_default": selected_profile is None,
        "error": None,
    }


def _profile_summary(profile_name: Optional[str]) -> Dict[str, Any]:
    selected_profile = _normalize_profile_name(profile_name)
    summary = _profile_summary_shell(selected_profile)

    try:
        session = _session_for_profile_lookup(selected_profile)
        sts_client_kwargs: Dict[str, Any] = {
            "region_name": settings.aws_region,
            "config": PROFILE_STS_CONFIG,
        }
        caller = session.client("sts", **sts_client_kwargs).get_caller_identity()
        summary["account_id"] = str(caller.get("Account") or "") or None
        summary["arn"] = str(caller.get("Arn") or "") or None
    except (BotoCoreError, ClientError) as exc:
        summary["error"] = _aws_error_message("Resolve AWS account for profile", exc)

    return summary


def _ambient_credential_label() -> str:
    """Name the credential source in play when no named profile exists.

    Falls back to "default" when nothing more specific can be determined.
    """
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return "environment credentials"
    if os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI") or os.environ.get(
        "AWS_CONTAINER_CREDENTIALS_FULL_URI"
    ):
        return "container credentials"
    if settings.aws_use_iam_role:
        return "instance role"
    return DEFAULT_AWS_PROFILE_NAME


def _missing_aws_config_hint() -> Optional[str]:
    """Explain an empty profile list, or None when a config file does exist."""
    host_config = _host_aws_config_path()
    if host_config.is_file():
        return None
    return (
        f"No AWS config file found at {host_config}, so there are no profiles to list. "
        "Under Docker that path is the read-only ~/.aws mount: set AWS_CONFIG_HOST_DIR "
        "in .env to your local ~/.aws directory and restart to pick a profile."
    )


def list_available_aws_profiles() -> List[Dict[str, Any]]:
    sync_host_aws_profiles()
    try:
        profile_names = boto3.Session().available_profiles
    except (BotoCoreError, ClientError) as exc:
        summary = _profile_summary_shell(None)
        summary["error"] = _aws_error_message("List AWS profiles", exc)
        return [summary]
    if not profile_names:
        # No profile is selected here, so credentials come from the ambient
        # chain. Labelling that "default" reads as the [default] profile and
        # hides both the real source and the fact that nothing was found.
        summary = _profile_summary(None)
        summary["display_name"] = _ambient_credential_label()
        hint = _missing_aws_config_hint()
        if hint:
            summary["error"] = f"{summary['error']} {hint}" if summary["error"] else hint
        return [summary]
    with ThreadPoolExecutor(max_workers=min(8, len(profile_names))) as executor:
        return list(executor.map(_profile_summary, profile_names))


IAM_ROLE_ARN_PATTERN = re.compile(r"^arn:aws[a-zA-Z-]*:iam::(\d{12}):role/([\w+=,.@/-]+)$")


def _validate_existing_role(
    session: boto3.Session,
    caller: Dict[str, Any],
    role_arn: str,
    selected_profile: Optional[str],
) -> None:
    """Best-effort verification that a user-supplied role ARN is real and usable.

    Read-only: at most iam:GetRole and one sts:AssumeRole attempt -- no IAM
    writes, keeping the "use existing role" path's no-IAM-writes promise.
    Without this, a typo'd or unassumable ARN was accepted silently and every
    subsequent scan failed with far less obvious errors.
    """
    match = IAM_ROLE_ARN_PATTERN.match(role_arn)
    if not match:
        raise IamRoleCreationError(
            f"'{role_arn}' does not look like a valid IAM role ARN "
            "(expected arn:aws:iam::<12-digit-account-id>:role/<role-name>)."
        )
    role_account_id, role_path_and_name = match.groups()
    role_name = role_path_and_name.split("/")[-1]

    caller_arn = str(caller.get("Arn") or "")
    caller_account = str(caller.get("Account") or "")

    # The selected profile may itself already be a session of this very role
    # (e.g. the MaxOpsReadOnlyRole scan profile) -- resolving the caller
    # identity above then already proved the role exists and is assumable.
    if caller_account == role_account_id and f":assumed-role/{role_name}/" in caller_arn:
        return

    if caller_account == role_account_id:
        try:
            session.client("iam").get_role(RoleName=role_name)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "NoSuchEntity":
                raise IamRoleCreationError(
                    f"Role '{role_name}' does not exist in account {role_account_id}."
                ) from exc
            # No iam:GetRole permission (or similar) -- the assume-role check
            # below still validates existence and usability.
        except BotoCoreError:
            pass

    try:
        session.client("sts", region_name=settings.aws_region).assume_role(
            RoleArn=role_arn,
            RoleSessionName="maxops-role-validation",
            DurationSeconds=900,
        )
    except (BotoCoreError, ClientError) as exc:
        profile_label = selected_profile or DEFAULT_AWS_PROFILE_NAME
        raise IamRoleCreationError(
            f"Could not assume '{role_arn}' with profile '{profile_label}'. Confirm the role exists and "
            f"its trust policy allows this profile to assume it. ({_aws_error_message('AssumeRole', exc)})"
        ) from exc


def use_existing_read_only_role(role_arn: str, profile_name: Optional[str] = None) -> Dict[str, Any]:
    """Register an existing IAM role ARN as the MaxOps scan profile.

    Unlike `create_or_update_read_only_role`, this makes no IAM write calls at all
    (no `create_role`/`put_role_policy`) — it only reads the caller identity and
    writes the local AWS config profile, for users whose credentials can't create
    IAM resources but who already have a suitable read-only role.
    """
    normalized_role_arn = (role_arn or "").strip()
    if not normalized_role_arn:
        raise IamRoleCreationError("Role ARN is required.")
    if not normalized_role_arn.startswith("arn:aws"):
        raise IamRoleCreationError(f"'{normalized_role_arn}' does not look like a valid IAM role ARN.")

    selected_profile = _normalize_profile_name(profile_name)
    try:
        session = _aws_session(selected_profile)
        caller = session.client("sts", region_name=settings.aws_region).get_caller_identity()
    except (BotoCoreError, ClientError) as exc:
        raise IamRoleCreationError(_aws_error_message("Resolve AWS caller identity", exc)) from exc

    account_id = str(caller.get("Account") or "")

    _validate_existing_role(session, caller, normalized_role_arn, selected_profile)

    try:
        scan_profile = _write_role_profile(
            role_arn=normalized_role_arn,
            source_profile_name=selected_profile,
            profile_name=MAXOPS_READ_ONLY_PROFILE_NAME,
        )
    except OSError as exc:
        raise IamRoleCreationError(f"Create local AWS profile failed: {exc}") from exc

    _wait_for_role_permissions_ready(scan_profile["profile_name"])

    return {
        "role_name": MAXOPS_READ_ONLY_ROLE_NAME,
        "role_arn": normalized_role_arn,
        "policy_name": None,
        "trusted_principal_arn": None,
        "aws_profile_name": selected_profile,
        "aws_account_id": account_id,
        "scan_profile_name": scan_profile["profile_name"],
        "scan_profile_config_path": scan_profile["config_path"],
        "scan_profile_source_profile": scan_profile.get("source_profile"),
        "scan_profile_credential_source": scan_profile.get("credential_source"),
        "status": "existing_role_registered",
        "message": (
            f"Registered {normalized_role_arn} as the MaxOps scan role and configured AWS profile "
            f"{scan_profile['profile_name']}. MaxOps did not create or modify any IAM resources."
        ),
    }


def create_or_update_read_only_role(profile_name: Optional[str] = None) -> Dict[str, Any]:
    selected_profile = _normalize_profile_name(profile_name)
    try:
        session = _aws_session(selected_profile)
    except (BotoCoreError, ClientError) as exc:
        raise IamRoleCreationError(_aws_error_message("Load AWS credentials", exc)) from exc

    iam_client_kwargs: Dict[str, Any] = {}
    sts_client_kwargs: Dict[str, Any] = {"region_name": settings.aws_region}

    iam_client = session.client("iam", **iam_client_kwargs)
    sts_client = session.client("sts", **sts_client_kwargs)

    try:
        caller = sts_client.get_caller_identity()
    except (BotoCoreError, ClientError) as exc:
        raise IamRoleCreationError(_aws_error_message("Resolve AWS caller identity", exc)) from exc

    account_id = str(caller.get("Account") or "")
    caller_arn = str(caller.get("Arn") or "")
    trusted_principal_arn = _iam_principal_from_caller(caller_arn, account_id, iam_client)
    trust_document = _trust_policy(trusted_principal_arn)

    role: Optional[Dict[str, Any]] = None
    created = False

    try:
        response = iam_client.create_role(
            RoleName=MAXOPS_READ_ONLY_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust_document),
            Description="Read-only role used by MaxOps onboarding and scan checks.",
            MaxSessionDuration=3600,
            Tags=[
                {"Key": "ManagedBy", "Value": "MaxOps"},
                {"Key": "MaxOpsAccess", "Value": "ReadOnlyScan"},
            ],
        )
        role = response.get("Role")
        created = True
        try:
            iam_client.get_waiter("role_exists").wait(RoleName=MAXOPS_READ_ONLY_ROLE_NAME)
        except Exception:
            pass
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "EntityAlreadyExists":
            raise IamRoleCreationError(
                _aws_error_message("Create IAM role", exc)
                + _principal_error_hint(exc, caller_arn, trusted_principal_arn)
            ) from exc
        try:
            role = iam_client.get_role(RoleName=MAXOPS_READ_ONLY_ROLE_NAME).get("Role")
            iam_client.update_assume_role_policy(
                RoleName=MAXOPS_READ_ONLY_ROLE_NAME,
                PolicyDocument=json.dumps(trust_document),
            )
        except ClientError as update_exc:
            raise IamRoleCreationError(
                _aws_error_message("Update existing IAM role trust policy", update_exc)
                + _principal_error_hint(update_exc, caller_arn, trusted_principal_arn)
            ) from update_exc

    try:
        _put_read_only_policy(iam_client, MAXOPS_READ_ONLY_ROLE_NAME)
    except ClientError as exc:
        operation = "Attach read-only inline policy" if created else "Update read-only inline policy"
        raise IamRoleCreationError(_aws_error_message(operation, exc)) from exc

    if role is None:
        try:
            role = iam_client.get_role(RoleName=MAXOPS_READ_ONLY_ROLE_NAME).get("Role")
        except ClientError as exc:
            raise IamRoleCreationError(_aws_error_message("Fetch IAM role", exc)) from exc

    role_arn = (role or {}).get("Arn")
    if not role_arn:
        raise IamRoleCreationError("Create local AWS profile failed: IAM role ARN was not returned by AWS.")

    try:
        scan_profile = _write_role_profile(
            role_arn=role_arn,
            source_profile_name=selected_profile,
            profile_name=MAXOPS_READ_ONLY_PROFILE_NAME,
        )
    except OSError as exc:
        raise IamRoleCreationError(f"Create local AWS profile failed: {exc}") from exc

    _wait_for_role_permissions_ready(scan_profile["profile_name"])

    status = "created" if created else "updated_existing"
    return {
        "role_name": MAXOPS_READ_ONLY_ROLE_NAME,
        "role_arn": role_arn,
        "policy_name": MAXOPS_READ_ONLY_POLICY_NAME,
        "trusted_principal_arn": trusted_principal_arn,
        "aws_profile_name": selected_profile,
        "aws_account_id": account_id,
        "scan_profile_name": scan_profile["profile_name"],
        "scan_profile_config_path": scan_profile["config_path"],
        "scan_profile_source_profile": scan_profile.get("source_profile"),
        "scan_profile_credential_source": scan_profile.get("credential_source"),
        "status": status,
        "message": (
            f"Created {MAXOPS_READ_ONLY_ROLE_NAME}, attached the read-only scan policy, "
            f"and configured AWS profile {scan_profile['profile_name']}."
            if created
            else f"{MAXOPS_READ_ONLY_ROLE_NAME} already existed; trust policy, read-only scan policy, "
            f"and AWS profile {scan_profile['profile_name']} were updated."
        ),
    }


def verify_profile_credentials(profile_name: str) -> Dict[str, Any]:
    """Check that a written profile can actually resolve credentials.

    Distinct from waiting for permissions to propagate. A profile whose
    `credential_source` cannot supply credentials at all — `Ec2InstanceMetadata`
    when not on EC2, most commonly — is permanently broken, not slow. Reporting
    that as success leaves the operator with a role that looks created and a
    page that cannot reach AWS.
    """
    try:
        session = boto3.Session(profile_name=profile_name, region_name=settings.aws_region)
        session.client("sts", region_name=settings.aws_region).get_caller_identity()
        return {"usable": True, "error": None, "hint": None}
    except ClientError as exc:
        # Reached AWS and was refused: credentials resolved, so the profile
        # itself works. Permissions are a separate matter.
        return {"usable": True, "error": _aws_error_message("Verify profile", exc), "hint": None}
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        hint = None
        if "credential_source" in message or "Ec2InstanceMetadata" in message:
            hint = (
                "The profile was written with no usable credential source. Re-create the "
                "role and pick a named AWS profile under 'Create it using' rather than "
                "'Default credentials'."
            )
        return {"usable": False, "error": message, "hint": hint}


def create_or_update_cost_data_role(profile_name: Optional[str] = None) -> Dict[str, Any]:
    """Create the role MaxOps uses to set up cost data, and a profile for it.

    Deliberately separate from the scan role. Setting up cost data has to
    create an S3 bucket, register a Cost and Usage Report export and run
    Athena queries; a role that can scan your account should never be able to
    do any of that. Keeping them apart means turning on cost data does not
    widen what a scan could do.

    `profile_name` is the existing profile used to *create* the role, and
    becomes the source profile the new one chains from.
    """
    selected_profile = _normalize_profile_name(profile_name)
    try:
        session = _aws_session(selected_profile)
    except (BotoCoreError, ClientError) as exc:
        raise IamRoleCreationError(_aws_error_message("Load AWS credentials", exc)) from exc

    iam_client = session.client("iam")
    sts_client = session.client("sts", region_name=settings.aws_region)

    try:
        caller = sts_client.get_caller_identity()
    except (BotoCoreError, ClientError) as exc:
        raise IamRoleCreationError(_aws_error_message("Resolve AWS caller identity", exc)) from exc

    account_id = str(caller.get("Account") or "")
    caller_arn = str(caller.get("Arn") or "")
    trusted_principal_arn = _iam_principal_from_caller(caller_arn, account_id, iam_client)
    trust_document = _trust_policy(trusted_principal_arn)

    role: Optional[Dict[str, Any]] = None
    created = False

    try:
        response = iam_client.create_role(
            RoleName=MAXOPS_COST_DATA_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust_document),
            Description="Role used by MaxOps to set up Cost and Usage Report data.",
            MaxSessionDuration=3600,
            Tags=[
                {"Key": "ManagedBy", "Value": "MaxOps"},
                {"Key": "MaxOpsAccess", "Value": "CostDataSetup"},
            ],
        )
        role = response.get("Role")
        created = True
        try:
            iam_client.get_waiter("role_exists").wait(RoleName=MAXOPS_COST_DATA_ROLE_NAME)
        except Exception:
            pass
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "EntityAlreadyExists":
            raise IamRoleCreationError(
                _aws_error_message("Create IAM role", exc)
                + _principal_error_hint(exc, caller_arn, trusted_principal_arn)
            ) from exc
        try:
            role = iam_client.get_role(RoleName=MAXOPS_COST_DATA_ROLE_NAME).get("Role")
            iam_client.update_assume_role_policy(
                RoleName=MAXOPS_COST_DATA_ROLE_NAME,
                PolicyDocument=json.dumps(trust_document),
            )
        except ClientError as update_exc:
            raise IamRoleCreationError(
                _aws_error_message("Update existing IAM role trust policy", update_exc)
                + _principal_error_hint(update_exc, caller_arn, trusted_principal_arn)
            ) from update_exc

    try:
        _put_inline_policy(
            iam_client,
            MAXOPS_COST_DATA_ROLE_NAME,
            MAXOPS_COST_DATA_POLICY_NAME,
            COST_DATA_SETUP_POLICY,
        )
    except ClientError as exc:
        operation = "Attach cost data policy" if created else "Update cost data policy"
        raise IamRoleCreationError(_aws_error_message(operation, exc)) from exc

    if role is None:
        try:
            role = iam_client.get_role(RoleName=MAXOPS_COST_DATA_ROLE_NAME).get("Role")
        except ClientError as exc:
            raise IamRoleCreationError(_aws_error_message("Fetch IAM role", exc)) from exc

    role_arn = (role or {}).get("Arn")
    if not role_arn:
        raise IamRoleCreationError(
            "Create local AWS profile failed: IAM role ARN was not returned by AWS."
        )

    try:
        setup_profile = _write_role_profile(
            role_arn=role_arn,
            source_profile_name=selected_profile,
            profile_name=MAXOPS_COST_DATA_PROFILE_NAME,
        )
    except OSError as exc:
        raise IamRoleCreationError(f"Create local AWS profile failed: {exc}") from exc

    _wait_for_role_permissions_ready(setup_profile["profile_name"])
    credentials_check = verify_profile_credentials(setup_profile["profile_name"])

    return {
        "credentials_usable": credentials_check["usable"],
        "credentials_error": credentials_check["error"],
        "credentials_hint": credentials_check["hint"],
        "role_name": MAXOPS_COST_DATA_ROLE_NAME,
        "role_arn": role_arn,
        "policy_name": MAXOPS_COST_DATA_POLICY_NAME,
        "trusted_principal_arn": trusted_principal_arn,
        "aws_profile_name": selected_profile,
        "aws_account_id": account_id,
        "setup_profile_name": setup_profile["profile_name"],
        "setup_profile_config_path": setup_profile["config_path"],
        "setup_profile_source_profile": setup_profile.get("source_profile"),
        "setup_profile_credential_source": setup_profile.get("credential_source"),
        "status": "created" if created else "updated_existing",
        "message": (
            f"Created {MAXOPS_COST_DATA_ROLE_NAME}, attached the cost data setup policy, "
            f"and configured AWS profile {setup_profile['profile_name']}."
            if created
            else f"{MAXOPS_COST_DATA_ROLE_NAME} already existed; its trust policy, cost data "
            f"setup policy, and AWS profile {setup_profile['profile_name']} were updated."
        ),
    }
