"""AWS cloud provider adapter."""
import logging
import math
import re
import json
import numpy as np
import boto3
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta, timezone
from botocore.exceptions import ClientError
from app.adapters.base import CloudAdapter
from app.config import settings
from app.services.aws_credentials import get_onboarding_scan_profile_name

logger = logging.getLogger("uvicorn.error")


def _iso_or_none(value: Any) -> Optional[str]:
    """Normalise an AWS timestamp to an ISO-8601 string.

    boto3 returns datetimes, but recorded payload fixtures replay them as
    strings already in ISO form -- so accept both rather than assuming.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value or None
    return None


_EC2_CLOUDWATCH_PERIODS = (
    1,
    5,
    10,
    20,
    30,
    60,
    300,
    900,
    1800,
    3600,
    10800,
    21600,
    43200,
    86400,
)


def derive_cloudwatch_period(
    start_date: Optional[datetime],
    end_date: Optional[datetime],
    requested_period: int = 300,
) -> int:
    """Choose a valid CloudWatch period that never exceeds the requested window."""
    if requested_period <= 0:
        raise ValueError("requested_period must be positive")
    if start_date is None or end_date is None:
        return requested_period
    window_seconds = (end_date - start_date).total_seconds()
    if window_seconds <= 0:
        raise ValueError("EC2 metric window must be positive")
    valid_periods = [
        period for period in _EC2_CLOUDWATCH_PERIODS if period <= window_seconds
    ]
    if not valid_periods:
        raise ValueError(
            "CloudWatch metric window is shorter than the minimum CloudWatch period"
        )
    eligible_periods = [
        period for period in valid_periods if period <= requested_period
    ]
    return max(eligible_periods or [min(valid_periods)])


# Kept as an import-compatible alias for EC2 rightsizer and third-party callers.
derive_ec2_metric_period = derive_cloudwatch_period


def _number_or_none(value: Any) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _collection_status(error: Exception) -> str:
    if isinstance(error, ClientError):
        code = str(error.response.get("Error", {}).get("Code") or "")
        if "AccessDenied" in code or "Unauthorized" in code:
            return "ACCESS_DENIED"
    return "ERROR"


def _percent_parameter_fraction(value: float) -> float:
    """Normalize AWS percent parameters while preserving stored fractions."""
    return value / 100.0 if value >= 1 else value


_MEMORY_EXCLUDED_TOKENS = frozenset(
    {
        "bytes",
        "byte",
        "free",
        "available",
        "cache",
        "cached",
        "swap",
        "total",
        # The CloudWatch agent's nvidia_smi plugin publishes
        # nvidia_smi_utilization_memory; it is GPU memory occupancy, not RAM.
        "gpu",
        "nvidia",
        "cuda",
        "accelerator",
    }
)
_MEMORY_QUALIFIER_TOKENS = frozenset(
    {"percent", "percentage", "pct", "utilization", "utilisation"}
)


def _memory_metric_tokens(metric_name: str) -> Tuple[List[str], str]:
    """Return separator/camel-case tokens and a compact lower-case name."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(metric_name or ""))
    tokens = re.findall(r"[a-z0-9]+", spaced.casefold())
    compact = "".join(tokens)
    return tokens, compact


def is_ec2_memory_metric_name(metric_name: str) -> bool:
    """Whether a metric name looks like a memory utilization percentage."""
    tokens, compact = _memory_metric_tokens(metric_name)
    if compact in {"mem", "memory"}:
        return True
    if any(token in _MEMORY_EXCLUDED_TOKENS for token in tokens):
        return False
    has_memory_stem = any(token in {"mem", "memory"} for token in tokens)
    if not has_memory_stem:
        has_memory_stem = compact.startswith(("mem", "memory"))
    has_percent_qualifier = any(
        token in _MEMORY_QUALIFIER_TOKENS for token in tokens
    ) or any(
        marker in compact for marker in ("percent", "percentage", "pct", "utilization")
    )
    return has_memory_stem and has_percent_qualifier


def _memory_metric_rank(metric_name: str) -> int:
    _, compact = _memory_metric_tokens(metric_name)
    if compact == "memusedpercent":
        return 0
    if "utilization" in compact or "utilisation" in compact:
        return 1
    if compact in {"memory", "mem"}:
        return 2
    return 3


def is_ec2_gpu_utilization_metric_name(metric_name: str) -> bool:
    """Return whether a name is a GPU compute-utilization percentage metric.

    Names are compared after separator and camel-case normalization. GPU memory
    occupancy is deliberately excluded because it does not measure GPU compute
    activity and cannot safely prove that a training job is idle.
    """
    tokens, compact = _memory_metric_tokens(metric_name)
    if any(token in {"memory", "mem"} for token in tokens):
        return False
    return compact in {
        "nvidiasmiutilizationgpu",
        "utilizationgpu",
        "gpuutilization",
        "gpuutil",
    }


def _gpu_metric_rank(metric_name: str) -> int:
    """Rank discovered GPU utilization names, preferring the agent canonical name."""
    _, compact = _memory_metric_tokens(metric_name)
    if compact == "nvidiasmiutilizationgpu":
        return 0
    if compact == "utilizationgpu":
        return 1
    if compact == "gpuutilization":
        return 2
    return 3


def is_ec2_gpu_memory_used_metric_name(metric_name: str) -> bool:
    """Return whether a name represents occupied GPU VRAM in MiB.

    The nvidia plugin's memory-bandwidth percentage and total-capacity series
    are intentionally excluded: neither is an occupancy demand signal.
    """
    tokens, compact = _memory_metric_tokens(metric_name)
    if any(token in {"total", "utilization", "utilisation", "percent", "pct"} for token in tokens):
        return False
    return compact in {
        "nvidiasmimemoryused",
        "gpumemoryused",
        "memoryusedgpu",
        "memoryused",
    }


def _gpu_memory_metric_rank(metric_name: str) -> int:
    """Rank GPU VRAM occupancy names, preferring the nvidia plugin series."""
    _, compact = _memory_metric_tokens(metric_name)
    if compact == "nvidiasmimemoryused":
        return 0
    if compact == "gpumemoryused":
        return 1
    return 2


def is_ec2_gpu_memory_total_metric_name(metric_name: str) -> bool:
    """Return whether a name represents GPU VRAM capacity in MiB."""
    _, compact = _memory_metric_tokens(metric_name)
    return compact in {"nvidiasmimemorytotal", "gpumemorytotal", "memorytotal"}


def is_agent_cpu_metric_name(metric_name: str) -> bool:
    """Return whether a CloudWatch agent metric represents CPU utilization."""
    _, compact = _memory_metric_tokens(metric_name)
    return compact in {
        "cpuusageactive",
        "cpuusageuser",
        "cpuutilization",
        "cpuutil",
    }


def _agent_cpu_metric_rank(metric_name: str) -> int:
    """Prefer the agent's aggregate CPU series, then its active series."""
    _, compact = _memory_metric_tokens(metric_name)
    return {
        "cpuusageactive": 0,
        "cpuusageuser": 1,
        "cpuutilization": 2,
        "cpuutil": 3,
    }.get(compact, 4)


def _valid_percent_value(value: Any) -> Optional[float]:
    """Return a finite 0–100 gauge percentage, or ``None`` if invalid."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0.0 <= number <= 100.0 else None


def _valid_non_negative_value(value: Any) -> Optional[float]:
    """Return a finite non-negative gauge value, preserving its native unit."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0.0 else None


def _metric_dimensions_key(
    dimensions: List[Dict[str, Any]]
) -> Tuple[Tuple[str, str], ...]:
    return tuple(
        sorted(
            (
                str(dimension.get("Name") or ""),
                str(dimension.get("Value") or ""),
            )
            for dimension in dimensions
        )
    )


def _detect_ec2_management_context(tags: Dict[str, str]) -> Optional[str]:
    """Best-effort detection of instances managed by a higher-level service."""
    if "aws:autoscaling:groupName" in tags:
        return "asg"
    if any(
        key in tags
        for key in ("aws:ecs:cluster-name", "AmazonECSManaged", "ecs:cluster")
    ):
        return "ecs"
    if any(key in tags for key in ("aws:eks:cluster-name", "eks:cluster-name")) or any(
        key.startswith("kubernetes.io/cluster/") for key in tags
    ):
        return "kubernetes"
    return None


class AWSAdapter(CloudAdapter):
    """AWS cloud provider adapter implementation."""

    def __init__(self, default_region: Optional[str] = None):
        """Initialize AWS adapter with credentials."""
        self._default_region = default_region
        self._ec2_memory_metric_cache: Dict[
            str, Optional[Dict[str, List[Dict[str, Any]]]]
        ] = {}
        self._ec2_gpu_metric_cache: Dict[
            str, Optional[Dict[str, List[Dict[str, Any]]]]
        ] = {}
        self._ec2_metric_discovery_cache: Dict[
            str, Optional[Dict[str, Dict[str, List[Dict[str, Any]]]]]
        ] = {}
        self._s3_optimizer_storage_types: Dict[str, Dict[str, set[str]]] = {}
        onboarding_scan_profile = get_onboarding_scan_profile_name()
        if onboarding_scan_profile:
            # Use the local assume-role profile created during onboarding.
            self.session = boto3.Session(
                profile_name=onboarding_scan_profile,
                region_name=default_region or settings.aws_region,
            )
            self.ec2_client = self.session.client("ec2")
            self.cloudwatch_client = self.session.client("cloudwatch")
            logger.info(
                "AWSAdapter using onboarding AWS profile %s", onboarding_scan_profile
            )
        elif settings.aws_use_iam_role:
            # Use IAM role (for EC2/ECS deployment)
            # boto3 will automatically use instance profile/role
            self.session = boto3.Session(
                region_name=default_region or settings.aws_region
            )
            self.ec2_client = self.session.client("ec2")
            self.cloudwatch_client = self.session.client("cloudwatch")
            logger.info("AWSAdapter using IAM role credentials")
        elif settings.aws_role_arn:
            # Assume IAM role (for local development)
            self.session = self._assume_role_session()
            self.ec2_client = self.session.client("ec2")
            self.cloudwatch_client = self.session.client("cloudwatch")
            logger.info("AWSAdapter assuming role %s", settings.aws_role_arn)
        elif settings.aws_profile:
            # Use AWS CLI profile
            self.session = boto3.Session(
                profile_name=settings.aws_profile,
                region_name=default_region or settings.aws_region,
            )
            self.ec2_client = self.session.client("ec2")
            self.cloudwatch_client = self.session.client("cloudwatch")
            logger.info("AWSAdapter using AWS profile %s", settings.aws_profile)
        else:
            # Use access keys or default credential chain
            session_kwargs = {"region_name": default_region or settings.aws_region}
            if settings.aws_access_key_id and settings.aws_secret_access_key:
                session_kwargs.update(
                    {
                        "aws_access_key_id": settings.aws_access_key_id,
                        "aws_secret_access_key": settings.aws_secret_access_key,
                    }
                )
            self.session = boto3.Session(**session_kwargs)
            self.ec2_client = self.session.client("ec2")
            self.cloudwatch_client = self.session.client("cloudwatch")
            logger.info("AWSAdapter using default credential chain")

        self._log_caller_identity()

    def _log_caller_identity(self) -> None:
        """Log the AWS caller identity for debugging credential selection."""
        try:
            sts = self.session.client(
                "sts", region_name=self._default_region or settings.aws_region
            )
            identity = sts.get_caller_identity()
            logger.info(
                "AWS caller identity resolved",
                extra={
                    "account": identity.get("Account"),
                    "arn": identity.get("Arn"),
                    "user_id": identity.get("UserId"),
                },
            )
        except Exception:
            logger.exception("Failed to resolve AWS caller identity")

    def _resolve_region(self, region: Optional[str]) -> str:
        """Resolve region using provided value or settings default."""
        if region:
            return region
        if self._default_region:
            return self._default_region
        raise ValueError("AWS region is required. Please set it in Settings.")

    def _assume_role_session(self) -> boto3.Session:
        """
        Assume an IAM role and create a boto3 session with temporary credentials.

        Returns:
            boto3.Session with assumed role credentials
        """
        import boto3
        from botocore.exceptions import ClientError

        # Create STS client with default credentials (from profile or env)
        sts_client = boto3.client("sts", region_name=settings.aws_region)

        try:
            # Assume the role
            response = sts_client.assume_role(
                RoleArn=settings.aws_role_arn,
                RoleSessionName=settings.aws_role_session_name,
                DurationSeconds=3600,  # 1 hour session
            )

            credentials = response["Credentials"]

            # Create session with assumed role credentials
            session = boto3.Session(
                aws_access_key_id=credentials["AccessKeyId"],
                aws_secret_access_key=credentials["SecretAccessKey"],
                aws_session_token=credentials["SessionToken"],
                region_name=settings.aws_region,
            )

            return session
        except ClientError as e:
            raise Exception(
                f"Failed to assume role {settings.aws_role_arn}: {str(e)}\n"
                f"Make sure your AWS credentials have permission to assume this role."
            )

    def get_resources(
        self,
        resource_type: str,
        filters: Optional[Dict[str, Any]] = None,
        region: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get AWS resources of a specific type."""
        if resource_type == "ec2":
            return self._get_ec2_instances(filters, region)
        elif resource_type == "asg":
            return self._get_asg_resources(filters, region)
        elif resource_type == "rds" or resource_type == "rds_instance":
            return self._get_rds_instances(filters, region)
        elif resource_type == "ebs":
            return self._get_ebs_volumes(filters, region)
        elif resource_type == "snapshot":
            return self._get_snapshots(filters, region)
        elif resource_type == "cloudwatch_alarm":
            return self._get_cloudwatch_alarms(filters, region)
        elif resource_type == "cloudwatch_log_group":
            return self._get_cloudwatch_log_groups(filters, region)
        elif resource_type == "s3":
            return self._get_s3_buckets(filters, region)
        elif resource_type == "vpc":
            return self._get_vpcs(filters, region)
        elif resource_type in ("elastic_ip", "eip"):
            return self._get_elastic_ips(filters, region)
        elif resource_type in (
            "vpc_endpoint",
            "vpc_endpoints",
            "ec2_vpc_endpoint",
            "ec2_vpc_endpoints",
        ):
            return self._get_vpc_endpoints(filters, region)
        elif resource_type in (
            "vpc_flow_log",
            "vpc_flow_logs",
            "ec2_flow_log",
            "ec2_flow_logs",
            "flow_log",
            "flow_logs",
        ):
            return self._get_vpc_flow_logs(filters, region)
        elif (
            resource_type == "elasticache_replication_group"
            or resource_type == "elasticache_cluster"
        ):
            return self._get_elasticache_resources(resource_type, filters, region)
        elif resource_type == "glue_job":
            return self._get_glue_jobs(filters, region)
        elif resource_type == "emr_cluster":
            return self._get_emr_clusters(filters, region)
        elif resource_type in ("emr_instance_fleet", "emr_instance_fleets"):
            return self._get_emr_instance_fleets(filters, region)
        elif resource_type in ("emr_instance_group", "emr_instance_groups"):
            return self._get_emr_instance_groups(filters, region)
        elif resource_type == "dynamodb_table" or resource_type == "dynamodb":
            return self._get_dynamodb_tables(filters, region)
        elif resource_type == "dynamodb_gsi":
            return self._get_dynamodb_gsi(filters, region)
        elif resource_type == "efs" or resource_type == "efs_file_system":
            return self._get_efs_file_systems(filters, region)
        elif resource_type in ("ecs", "ecs_service"):
            return self._get_ecs_services(filters, region)
        elif resource_type == "ecs_cluster":
            return self._get_ecs_clusters(filters, region)
        elif resource_type == "lambda" or resource_type == "lambda_function":
            return self._get_lambda_functions(filters, region)
        elif resource_type in {
            "sagemaker",
            "sagemaker_notebook",
            "sagemaker_endpoint",
            "sagemaker_training_job",
        }:
            if resource_type == "sagemaker":
                return (
                    self._get_sagemaker_notebooks(filters, region)
                    + self._get_sagemaker_endpoints(filters, region)
                    + self._get_sagemaker_training_jobs(filters, region)
                )
            if resource_type == "sagemaker_notebook":
                return self._get_sagemaker_notebooks(filters, region)
            if resource_type == "sagemaker_endpoint":
                return self._get_sagemaker_endpoints(filters, region)
            return self._get_sagemaker_training_jobs(filters, region)
        else:
            raise ValueError(f"Unsupported resource type: {resource_type}")

    def _sagemaker_client(self, region: Optional[str]) -> Any:
        """Return a SageMaker client scoped to the requested region."""
        query_region = region or getattr(self, "_default_region", None) or settings.aws_region
        return self.session.client("sagemaker", region_name=query_region)

    def _sagemaker_application_autoscaling_client(self, region: Optional[str]) -> Any:
        """Return the regional Application Auto Scaling client."""
        query_region = region or getattr(self, "_default_region", None) or settings.aws_region
        return self.session.client("application-autoscaling", region_name=query_region)

    @staticmethod
    def _sagemaker_pages(
        client: Any, operation: str, result_key: str, **kwargs: Any
    ) -> List[Dict[str, Any]]:
        """Collect a paginated SageMaker list response without losing pages."""
        values: List[Dict[str, Any]] = []
        token: Optional[str] = None
        while True:
            request = dict(kwargs)
            if token:
                request["NextToken"] = token
            response = getattr(client, operation)(**request)
            values.extend(response.get(result_key) or [])
            token = response.get("NextToken")
            if not token:
                return values

    @staticmethod
    def _sagemaker_gpu_metadata(instance_type: Optional[str]) -> Dict[str, Any]:
        """Add stable GPU resolution evidence to SageMaker inventory metadata."""
        from app.checks.sagemaker.common import resolve_sagemaker_instance_type

        resolved = resolve_sagemaker_instance_type(instance_type)
        return {
            "instance_type": instance_type,
            "instance_type_known": resolved["known"],
            "gpu_count": resolved["gpu_count"],
            "gpu_device_count": resolved["gpu_device_count"],
            "gpu_model": resolved["gpu_model"],
            "gpu_memory_mib": resolved["gpu_memory_mib"],
            "vcpus": resolved["vcpus"],
        }

    def _get_sagemaker_notebooks(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Discover notebook instances and their lifecycle/configuration fields."""
        client = self._sagemaker_client(region)
        summaries = self._sagemaker_pages(client, "list_notebook_instances", "NotebookInstances")
        resources: List[Dict[str, Any]] = []
        for summary in summaries:
            name = summary.get("NotebookInstanceName")
            if not name:
                continue
            described = client.describe_notebook_instance(NotebookInstanceName=name)
            metadata = {
                "notebook_status": described.get("NotebookInstanceStatus", summary.get("NotebookInstanceStatus")),
                "lifecycle_config_name": described.get("NotebookInstanceLifecycleConfigName"),
                "volume_size_gb": described.get("VolumeSizeInGB"),
                "platform_identifier": described.get("PlatformIdentifier"),
                "direct_internet_access": described.get("DirectInternetAccess"),
                "last_modified_time": described.get("LastModifiedTime"),
                "creation_time": described.get("CreationTime", summary.get("CreationTime")),
                **self._sagemaker_gpu_metadata(
                    described.get("InstanceType", summary.get("InstanceType"))
                ),
            }
            lifecycle_name = metadata["lifecycle_config_name"]
            if lifecycle_name:
                try:
                    lifecycle = client.describe_notebook_instance_lifecycle_config(
                        NotebookInstanceLifecycleConfigName=lifecycle_name
                    )
                    metadata["lifecycle_on_start"] = lifecycle.get("OnStart") or []
                    from app.checks.sagemaker.common import lifecycle_auto_stop_match
                    from app.checks.sagemaker.notebook_no_auto_stop import DEFAULT_AUTO_STOP_PATTERNS

                    metadata["lifecycle_config_has_auto_stop"] = any(
                        lifecycle_auto_stop_match(entry.get("Content"), DEFAULT_AUTO_STOP_PATTERNS)
                        for entry in metadata["lifecycle_on_start"]
                        if isinstance(entry, dict)
                    )
                except Exception as exc:
                    metadata["lifecycle_config_describe_error"] = str(exc)
            resources.append(
                {
                    "resource_id": name,
                    "resource_type": "sagemaker_notebook",
                    "resource_subtype": "notebook",
                    "resource_name": name,
                    "region": region or getattr(self, "_default_region", None) or settings.aws_region,
                    "state": metadata["notebook_status"],
                    "status": metadata["notebook_status"],
                    "instance_type": metadata["instance_type"],
                    "created_at": metadata["creation_time"],
                    "metadata": metadata,
                    "aws_payload": described,
                }
            )
        return resources

    def _get_sagemaker_endpoints(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Discover endpoints, variants, serverless settings, and scaling policy state."""
        client = self._sagemaker_client(region)
        autoscaling = self._sagemaker_application_autoscaling_client(region)
        summaries = self._sagemaker_pages(client, "list_endpoints", "Endpoints")
        resources: List[Dict[str, Any]] = []
        for summary in summaries:
            name = summary.get("EndpointName")
            if not name:
                continue
            endpoint = client.describe_endpoint(EndpointName=name)
            config_name = endpoint.get("EndpointConfigName")
            config = (
                client.describe_endpoint_config(EndpointConfigName=config_name)
                if config_name
                else {}
            )
            config_variants = {
                str(item.get("VariantName")): item
                for item in config.get("ProductionVariants") or []
                if item.get("VariantName")
            }
            current_variants = {
                str(item.get("VariantName")): item
                for item in endpoint.get("ProductionVariants") or []
                if item.get("VariantName")
            }
            variants: List[Dict[str, Any]] = []
            for variant_name, variant_config in config_variants.items():
                current = current_variants.get(variant_name) or {}
                policy_present = False
                try:
                    policy_response = autoscaling.describe_scaling_policies(
                        ServiceNamespace="sagemaker",
                        ResourceId=f"endpoint/{name}/variant/{variant_name}",
                        ScalableDimension="sagemaker:variant:DesiredInstanceCount",
                    )
                    policy_present = bool(policy_response.get("ScalingPolicies"))
                except Exception:
                    # Missing permission is configuration uncertainty, not proof
                    # that the variant has no autoscaling policy.
                    policy_present = None
                serverless = bool(variant_config.get("ServerlessConfig"))
                variants.append(
                    {
                        "variant_name": variant_name,
                        "instance_type": variant_config.get("InstanceType"),
                        "initial_instance_count": variant_config.get("InitialInstanceCount"),
                        "current_instance_count": current.get("CurrentInstanceCount", variant_config.get("InitialInstanceCount")),
                        "serverless": serverless,
                        "autoscaling_policy_present": policy_present,
                        # DescribeEndpointConfig has no EnableEnhancedMetrics
                        # member in the SageMaker service model.  Keep this
                        # persisted key stable, while enhanced per-device
                        # metrics are discovered from CloudWatch dimensions.
                        "enhanced_metrics_enabled": False,
                        **self._sagemaker_gpu_metadata(variant_config.get("InstanceType")),
                    }
                )
            metadata = {
                "endpoint_status": endpoint.get("EndpointStatus", summary.get("EndpointStatus")),
                "endpoint_config_name": config_name,
                "variants": variants,
                "creation_time": endpoint.get("CreationTime", summary.get("CreationTime")),
                "last_modified_time": endpoint.get("LastModifiedTime"),
            }
            resources.append(
                {
                    "resource_id": name,
                    "resource_type": "sagemaker_endpoint",
                    "resource_subtype": "endpoint",
                    "resource_name": name,
                    "region": region or getattr(self, "_default_region", None) or settings.aws_region,
                    "state": metadata["endpoint_status"],
                    "status": metadata["endpoint_status"],
                    "created_at": metadata["creation_time"],
                    "metadata": metadata,
                    "variants": variants,
                    "aws_payload": {"endpoint": endpoint, "config": config},
                }
            )
        return resources

    def _get_sagemaker_training_jobs(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Discover recent training jobs and the config needed by V1 checks."""
        client = self._sagemaker_client(region)
        days = int((filters or {}).get("job_history_days", 30))
        creation_after = datetime.now(timezone.utc) - timedelta(days=days)
        summaries = self._sagemaker_pages(
            client,
            "list_training_jobs",
            "TrainingJobSummaries",
            CreationTimeAfter=creation_after,
        )
        resources: List[Dict[str, Any]] = []
        for summary in summaries:
            name = summary.get("TrainingJobName")
            if not name:
                continue
            described = client.describe_training_job(TrainingJobName=name)
            resources.append(
                {
                    "resource_id": name,
                    "resource_type": "sagemaker_training_job",
                    "resource_subtype": "training_job",
                    "resource_name": name,
                    "region": region or getattr(self, "_default_region", None) or settings.aws_region,
                    "state": described.get("TrainingJobStatus", summary.get("TrainingJobStatus")),
                    "status": described.get("TrainingJobStatus", summary.get("TrainingJobStatus")),
                    "created_at": described.get("CreationTime", summary.get("CreationTime")),
                    "instance_type": described.get("ResourceConfig", {}).get("InstanceType"),
                    "instance_count": described.get("ResourceConfig", {}).get("InstanceCount"),
                    "metadata": {
                        "training_job_status": described.get("TrainingJobStatus"),
                        "instance_type": described.get("ResourceConfig", {}).get("InstanceType"),
                        "instance_count": described.get("ResourceConfig", {}).get("InstanceCount"),
                        "enable_managed_spot_training": described.get("EnableManagedSpotTraining", False),
                        "checkpoint_config_present": bool(described.get("CheckpointConfig")),
                        "max_wait_time_seconds": described.get("StoppingCondition", {}).get("MaxWaitTimeInSeconds"),
                        "training_time_seconds": described.get("TrainingTimeInSeconds"),
                        "billable_time_seconds": described.get("BillableTimeInSeconds"),
                        "creation_time": described.get("CreationTime", summary.get("CreationTime")),
                        "job_family": self._sagemaker_training_job_family(name),
                    },
                    "aws_payload": described,
                }
            )
        return resources

    @staticmethod
    def _sagemaker_training_job_family(job_name: str) -> str:
        """Return the default recurring-job family used by the training check."""
        from app.checks.sagemaker.common import training_job_family

        return training_job_family(job_name)

    def get_sagemaker_lifecycle_config(
        self, lifecycle_config_name: str, region: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fetch one notebook lifecycle configuration for config checks."""
        return self._sagemaker_client(region).describe_notebook_instance_lifecycle_config(
            NotebookInstanceLifecycleConfigName=lifecycle_config_name
        )

    @staticmethod
    def _paged_autoscaling_call(
        client: Any, operation: str, result_key: str, **kwargs: Any
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        next_token: Optional[str] = None
        while True:
            request = dict(kwargs)
            if next_token:
                request["NextToken"] = next_token
            response = getattr(client, operation)(**request)
            items.extend(response.get(result_key) or [])
            next_token = response.get("NextToken")
            if not next_token:
                return items

    @staticmethod
    def _asg_platform(instances: List[Dict[str, Any]]) -> str:
        platforms = {
            str(instance.get("PlatformDetails") or instance.get("Platform") or "linux")
            .strip()
            .lower()
            for instance in instances
        }
        if any("windows" in value for value in platforms):
            return "mswin"
        if any("red hat" in value or "rhel" in value for value in platforms):
            return "rhel"
        if any("suse" in value or "sles" in value for value in platforms):
            return "sles"
        if any("ubuntu" in value for value in platforms):
            return "ubuntu"
        return "linux"

    def _resolve_asg_launch_instance_type(
        self,
        autoscaling_client: Any,
        ec2_client: Any,
        group: Dict[str, Any],
    ) -> Optional[str]:
        launch_template = group.get("LaunchTemplate") or {}
        if launch_template:
            request: Dict[str, Any] = {
                "Versions": [str(launch_template.get("Version") or "$Default")]
            }
            if launch_template.get("LaunchTemplateId"):
                request["LaunchTemplateId"] = launch_template["LaunchTemplateId"]
            elif launch_template.get("LaunchTemplateName"):
                request["LaunchTemplateName"] = launch_template["LaunchTemplateName"]
            else:
                return None
            response = ec2_client.describe_launch_template_versions(**request)
            versions = response.get("LaunchTemplateVersions") or []
            if versions:
                return (versions[0].get("LaunchTemplateData") or {}).get(
                    "InstanceType"
                )
            return None
        launch_configuration = group.get("LaunchConfigurationName")
        if launch_configuration:
            response = autoscaling_client.describe_launch_configurations(
                LaunchConfigurationNames=[launch_configuration]
            )
            configurations = response.get("LaunchConfigurations") or []
            if configurations:
                return configurations[0].get("InstanceType")
        return None

    def _get_asg_resources(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return normalized ASG inventory with rightsizer scope evidence."""
        query_region = region or settings.aws_region
        autoscaling = self.session.client("autoscaling", region_name=query_region)
        ec2 = self.session.client("ec2", region_name=query_region)
        groups = self._paged_autoscaling_call(
            autoscaling,
            "describe_auto_scaling_groups",
            "AutoScalingGroups",
        )
        resources: List[Dict[str, Any]] = []
        for group in groups:
            name = str(group.get("AutoScalingGroupName") or "").strip()
            if not name:
                continue
            errors: List[Dict[str, str]] = []

            def collect(operation: str, result_key: str, **kwargs: Any):
                try:
                    return self._paged_autoscaling_call(
                        autoscaling, operation, result_key, **kwargs
                    )
                except Exception as exc:
                    errors.append({"operation": operation, "error": str(exc)})
                    return []

            def collect_warm_pool() -> Tuple[List[Dict[str, Any]], Dict[str, Any] | None]:
                instances: List[Dict[str, Any]] = []
                configuration: Dict[str, Any] | None = None
                next_token: Optional[str] = None
                try:
                    while True:
                        request: Dict[str, Any] = {
                            "AutoScalingGroupName": name,
                            "MaxRecords": 100,
                        }
                        if next_token:
                            request["NextToken"] = next_token
                        response = autoscaling.describe_warm_pool(**request)
                        instances.extend(response.get("Instances") or [])
                        if configuration is None and isinstance(
                            response.get("WarmPoolConfiguration"), dict
                        ):
                            configuration = response["WarmPoolConfiguration"]
                        next_token = response.get("NextToken")
                        if not next_token:
                            return instances, configuration
                except Exception as exc:
                    errors.append(
                        {"operation": "describe_warm_pool", "error": str(exc)}
                    )
                    return instances, configuration

            activities = collect(
                "describe_scaling_activities",
                "Activities",
                AutoScalingGroupName=name,
                MaxRecords=100,
            )
            scheduled_actions = collect(
                "describe_scheduled_actions",
                "ScheduledUpdateGroupActions",
                AutoScalingGroupName=name,
                MaxRecords=100,
            )
            policies = collect(
                "describe_policies",
                "ScalingPolicies",
                AutoScalingGroupName=name,
                MaxRecords=100,
            )
            warm_pool, warm_pool_configuration = collect_warm_pool()
            refreshes = collect(
                "describe_instance_refreshes",
                "InstanceRefreshes",
                AutoScalingGroupName=name,
                MaxRecords=100,
            )

            member_summaries = group.get("Instances") or []
            member_ids = [
                str(item.get("InstanceId"))
                for item in member_summaries
                if item.get("InstanceId")
            ]
            member_instances: List[Dict[str, Any]] = []
            try:
                for offset in range(0, len(member_ids), 100):
                    response = ec2.describe_instances(
                        InstanceIds=member_ids[offset : offset + 100]
                    )
                    for reservation in response.get("Reservations") or []:
                        member_instances.extend(reservation.get("Instances") or [])
            except Exception as exc:
                errors.append({"operation": "describe_instances", "error": str(exc)})

            launch_type: Optional[str] = None
            if not group.get("MixedInstancesPolicy"):
                try:
                    launch_type = self._resolve_asg_launch_instance_type(
                        autoscaling, ec2, group
                    )
                except Exception as exc:
                    errors.append(
                        {"operation": "resolve_launch_instance_type", "error": str(exc)}
                    )
            member_types = {
                str(item.get("InstanceType"))
                for item in member_instances
                if item.get("InstanceType")
            }
            member_types.update(
                str(item.get("InstanceType"))
                for item in member_summaries
                if item.get("InstanceType")
            )
            effective_types = set(member_types)
            if launch_type:
                effective_types.add(str(launch_type))

            overrides = (
                ((group.get("MixedInstancesPolicy") or {}).get("LaunchTemplate") or {})
                .get("Overrides")
                or []
            )
            weighted = any(item.get("WeightedCapacity") for item in overrides)
            in_service_ids = [
                str(item.get("InstanceId"))
                for item in member_summaries
                if item.get("InstanceId")
                and str(item.get("LifecycleState") or "").casefold()
                == "inservice"
            ]
            active_refresh_statuses = {
                "pending",
                "inprogress",
                "baking",
                "rollbackinprogress",
            }
            active_refresh = any(
                str(item.get("Status") or "").replace("_", "").casefold()
                in active_refresh_statuses
                for item in refreshes
            )
            predictive = any(
                str(policy.get("PolicyType") or "").casefold()
                == "predictivescaling"
                for policy in policies
            )
            policy_kinds = sorted(
                {
                    str(policy.get("PolicyType"))
                    for policy in policies
                    if policy.get("PolicyType")
                }
            )
            policy_metrics: List[str] = []
            for policy in policies:
                config = policy.get("TargetTrackingConfiguration") or {}
                predefined = config.get("PredefinedMetricSpecification") or {}
                customized = config.get("CustomizedMetricSpecification") or {}
                metric = predefined.get("PredefinedMetricType") or customized.get(
                    "MetricName"
                )
                if metric:
                    policy_metrics.append(str(metric))

            tags = {
                str(item.get("Key")): str(item.get("Value") or "")
                for item in group.get("Tags") or []
                if item.get("Key")
            }
            platform = self._asg_platform(member_instances)
            lifecycle_values = {
                str(item.get("InstanceLifecycle") or "on-demand").casefold()
                for item in member_instances
            }
            described_member_ids = {
                str(item.get("InstanceId"))
                for item in member_instances
                if item.get("InstanceId")
            }
            if set(member_ids) - described_member_ids:
                lifecycle_values.add("unknown")
            metadata = {
                "min_size": int(group.get("MinSize") or 0),
                "desired_capacity": int(group.get("DesiredCapacity") or 0),
                "max_size": int(group.get("MaxSize") or 0),
                "instance_type": (
                    next(iter(effective_types)) if len(effective_types) == 1 else None
                ),
                "platform_normalized": platform,
                "availability_zones": sorted(
                    {
                        normalized
                        for value in group.get("AvailabilityZones") or []
                        if (normalized := str(value).strip())
                    }
                ),
                "instance_ids": member_ids,
                "in_service_instance_ids": in_service_ids,
                "member_instances": member_instances,
                "mixed_instances_policy_present": bool(
                    group.get("MixedInstancesPolicy")
                ),
                "weighted_capacity_present": weighted,
                "capacity_unit_kind": "weighted" if weighted else "instances",
                "effective_instance_types": sorted(effective_types),
                "warm_pool_present": warm_pool_configuration is not None
                or bool(warm_pool),
                "warm_pool_configuration": warm_pool_configuration,
                "warm_pool_instances": warm_pool,
                "scheduled_actions_present": bool(scheduled_actions),
                "predictive_scaling_present": predictive,
                "dynamic_policy_kinds": policy_kinds,
                "dynamic_policy_metrics": sorted(set(policy_metrics)),
                "default_instance_warmup": group.get("DefaultInstanceWarmup"),
                "default_cooldown": group.get("DefaultCooldown"),
                "capacity_rebalance": bool(group.get("CapacityRebalance")),
                "scale_in_protection_present": any(
                    bool(item.get("ProtectedFromScaleIn")) for item in member_summaries
                ),
                "suspended_processes": sorted(
                    str(item.get("ProcessName"))
                    for item in group.get("SuspendedProcesses") or []
                    if item.get("ProcessName")
                ),
                "instance_refresh_in_progress": active_refresh,
                "instance_lifecycles": sorted(lifecycle_values),
                "scaling_activities": activities,
                "scaling_policies": policies,
                "scheduled_actions": scheduled_actions,
                "instance_refreshes": refreshes,
                "collection_errors": errors,
            }
            state_filter = str((filters or {}).get("state") or "").casefold()
            if state_filter and state_filter != "active":
                continue
            resources.append(
                {
                    "resource_id": name,
                    "resource_name": name,
                    "resource_type": "asg",
                    "region": query_region,
                    "state": "active",
                    "min_size": metadata["min_size"],
                    "desired_capacity": metadata["desired_capacity"],
                    "max_size": metadata["max_size"],
                    "instance_type": metadata["instance_type"],
                    "platform_normalized": platform,
                    "tags": tags,
                    "metadata": metadata,
                    "aws_payload": group,
                }
            )
        return resources

    def _get_ec2_instances(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get EC2 instances."""
        try:
            ec2_filters = []

            # Convert our filter format to AWS filter format
            if filters:
                if "state" in filters:
                    ec2_filters.append(
                        {"Name": "instance-state-name", "Values": [filters["state"]]}
                    )
                if "tag" in filters:
                    tag_key = filters["tag"].get("key")
                    tag_value = filters["tag"].get("value")
                    if tag_key:
                        ec2_filters.append(
                            {
                                "Name": f"tag:{tag_key}",
                                "Values": [tag_value] if tag_value else ["*"],
                            }
                        )

            query_region = region or settings.aws_region
            client = self.ec2_client
            if region:
                client = self.session.client("ec2", region_name=region)

            print(
                f"[AWS_ADAPTER] Querying EC2 instances in region: {query_region}, filters: {ec2_filters}"
            )

            # Enable boto3 debug logging to capture raw XML
            import logging

            boto3_logger = logging.getLogger("boto3")
            botocore_logger = logging.getLogger("botocore")
            urllib3_logger = logging.getLogger("urllib3")

            # Set to DEBUG to see raw HTTP responses
            boto3_logger.setLevel(logging.DEBUG)
            botocore_logger.setLevel(logging.DEBUG)
            urllib3_logger.setLevel(logging.DEBUG)

            try:
                response = client.describe_instances(
                    Filters=ec2_filters if ec2_filters else None
                )
                print(
                    f"[AWS_ADAPTER] EC2 describe_instances response: {len(response.get('Reservations', []))} reservations"
                )
                if len(response.get("Reservations", [])) == 0:
                    print(
                        f"[AWS_ADAPTER] WARNING: Received 0 reservations but expected some. Response keys: {list(response.keys())}"
                    )
                    print(
                        f"[AWS_ADAPTER] ResponseMetadata: {response.get('ResponseMetadata', {})}"
                    )
                # Try to get raw response to see what we actually received
                if hasattr(client, "_client_config"):
                    print(f"[AWS_ADAPTER] Client config: {client._client_config}")
                # Try to get the raw HTTP response to inspect XML
                try:
                    # Access the low-level response from botocore
                    operation_model = client._service_model.operation_model(
                        "DescribeInstances"
                    )
                    # The response parser might have failed silently
                    print(f"[AWS_ADAPTER] Operation model: {operation_model}")
                    # Check if there's a parsed error in the response
                    if "Error" in response:
                        print(
                            f"[AWS_ADAPTER] Error in response: {response.get('Error')}"
                        )

                    # Try to get the raw XML response from the HTTP layer
                    # This is tricky - boto3 doesn't expose raw XML easily
                    # But we can check the ResponseMetadata for clues
                    response_metadata = response.get("ResponseMetadata", {})
                    http_headers = response_metadata.get("HTTPHeaders", {})
                    content_length = http_headers.get("content-length", "unknown")
                    content_type = http_headers.get("content-type", "unknown")
                    print(
                        f"[AWS_ADAPTER] Raw HTTP response - Content-Type: {content_type}, Content-Length: {content_length}"
                    )
                    print(
                        f"[AWS_ADAPTER] This suggests XML was received but boto3 parsed it as empty. Possible XML structure issue."
                    )
                except Exception as e:
                    print(f"[AWS_ADAPTER] Could not inspect operation model: {e}")
            except Exception as e:
                print(f"[AWS_ADAPTER] ERROR calling describe_instances: {e}")
                print(f"[AWS_ADAPTER] Exception type: {type(e).__name__}")
                import traceback

                print(f"[AWS_ADAPTER] Traceback: {traceback.format_exc()}")
                raise

            instances = []
            for reservation in response.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    tags = {
                        tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])
                    }
                    name = tags.get("Name", instance["InstanceId"])
                    cw_agent_installed = self._parse_bool_tag(
                        tags.get("CloudWatchAgentInstalled")
                    )

                    instances.append(
                        {
                            "resource_id": instance["InstanceId"],
                            "resource_type": "ec2",
                            "resource_name": name,
                            "region": instance["Placement"]["AvailabilityZone"][:-1],
                            "state": instance["State"]["Name"],
                            "instance_type": instance["InstanceType"],
                            "launch_time": instance["LaunchTime"].isoformat(),
                            "tags": tags,
                            "metadata": {
                                "vpc_id": instance.get("VpcId"),
                                "subnet_id": instance.get("SubnetId"),
                                # EC2 has no "stopped at" field; the timestamp is
                                # embedded in this free-text reason, e.g.
                                # "User initiated (2024-01-15 10:30:00 GMT)".
                                # The unused-instances check parses it for its
                                # stopped_days threshold.
                                "state_transition_reason": instance.get(
                                    "StateTransitionReason"
                                ),
                                "security_groups": [
                                    sg["GroupId"]
                                    for sg in instance.get("SecurityGroups", [])
                                ],
                                "cloudwatch_agent_installed": cw_agent_installed,
                                "platform": instance.get("Platform"),
                                "PlatformDetails": instance.get("PlatformDetails"),
                                "virtualization_type": instance.get(
                                    "VirtualizationType"
                                ),
                                "root_device_type": instance.get("RootDeviceType"),
                                "ena_support": instance.get("EnaSupport"),
                                "attached_eni_count": len(
                                    instance.get("NetworkInterfaces", [])
                                ),
                                "attached_volume_count": len(
                                    instance.get("BlockDeviceMappings", [])
                                ),
                                "attached_volume_ids": [
                                    mapping.get("Ebs", {}).get("VolumeId")
                                    for mapping in instance.get(
                                        "BlockDeviceMappings", []
                                    )
                                    if mapping.get("Ebs", {}).get("VolumeId")
                                ],
                                "requires_efa": any(
                                    interface.get("InterfaceType") == "efa"
                                    for interface in instance.get(
                                        "NetworkInterfaces", []
                                    )
                                ),
                                "network_bandwidth_weighting": (
                                    instance.get("NetworkPerformanceOptions") or {}
                                ).get("BandwidthWeighting", "default"),
                                "tenancy": (instance.get("Placement") or {}).get(
                                    "Tenancy", "default"
                                ),
                                "lifecycle": instance.get("InstanceLifecycle"),
                                "management_context": _detect_ec2_management_context(
                                    tags
                                ),
                            },
                        }
                    )

            return instances
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    @staticmethod
    def _parse_bool_tag(value: Optional[str]) -> Optional[bool]:
        if value is None:
            return None
        normalized = str(value).strip().lower()
        if normalized in ("true", "1", "yes", "y"):
            return True
        if normalized in ("false", "0", "no", "n"):
            return False
        return None

    def get_ec2_cloudwatch_agent_status(
        self, instance_id: str, region: Optional[str] = None
    ) -> Optional[bool]:
        """Get CloudWatch agent status for an EC2 instance from tags."""
        try:
            query_region = region or settings.aws_region
            client = (
                self.ec2_client
                if not region
                else self.session.client("ec2", region_name=query_region)
            )

            response = client.describe_instances(InstanceIds=[instance_id])
            for reservation in response.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    tags = {
                        tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])
                    }
                    return self._parse_bool_tag(tags.get("CloudWatchAgentInstalled"))
            return None
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "InvalidInstanceID.NotFound":
                return None
            raise Exception(f"AWS API error: {str(e)}")

    def set_ec2_cloudwatch_agent_status(
        self, instance_id: str, installed: bool, region: Optional[str] = None
    ) -> None:
        """Set CloudWatch agent status tag for an EC2 instance."""
        query_region = region or settings.aws_region
        client = (
            self.ec2_client
            if not region
            else self.session.client("ec2", region_name=query_region)
        )

        client.create_tags(
            Resources=[instance_id],
            Tags=[
                {
                    "Key": "CloudWatchAgentInstalled",
                    "Value": "true" if installed else "false",
                }
            ],
        )

    def _get_rds_instances(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get fully paginated RDS DB-instance inventory."""
        try:
            client = (
                self.session.client("rds", region_name=region)
                if region
                else self.session.client("rds")
            )
            instances = []
            marker: Optional[str] = None
            while True:
                request: Dict[str, Any] = {}
                if marker:
                    request["Marker"] = marker
                response = client.describe_db_instances(**request)
                for db_instance in response.get("DBInstances", []):
                    tags_response = client.list_tags_for_resource(
                        ResourceName=db_instance["DBInstanceArn"]
                    )
                    tags = {
                        tag["Key"]: tag["Value"]
                        for tag in tags_response.get("TagList", [])
                    }
                    identifier = db_instance["DBInstanceIdentifier"]
                    name = tags.get("Name", identifier)
                    metadata = dict(db_instance)
                    # Stable normalized aliases keep existing checks and views working.
                    metadata.update(
                        {
                            "db_instance_class": db_instance.get("DBInstanceClass"),
                            "instance_class": db_instance.get("DBInstanceClass"),
                            "engine": db_instance.get("Engine"),
                            "engine_version": db_instance.get("EngineVersion"),
                            "license_model": db_instance.get("LicenseModel"),
                            "allocated_storage": db_instance.get("AllocatedStorage"),
                            "storage_type": db_instance.get("StorageType"),
                            "iops": db_instance.get("Iops"),
                            "storage_throughput": db_instance.get("StorageThroughput"),
                            "multi_az": db_instance.get("MultiAZ", False),
                            "created_at": db_instance.get("InstanceCreateTime"),
                            "availability_zone": db_instance.get("AvailabilityZone"),
                            "performance_insights_enabled": db_instance.get("PerformanceInsightsEnabled", False),
                            "dbi_resource_id": db_instance.get("DbiResourceId"),
                        }
                    )
                    instances.append(
                        {
                            "resource_id": identifier,
                            "resource_type": "rds",
                            "resource_name": name,
                            "region": region or self._resolve_region(None),
                            "availability_zone": db_instance.get("AvailabilityZone"),
                            "state": db_instance.get("DBInstanceStatus"),
                            "instance_type": db_instance.get("DBInstanceClass"),
                            "db_instance_class": db_instance.get("DBInstanceClass"),
                            "engine": db_instance.get("Engine"),
                            "created_at": db_instance.get("InstanceCreateTime"),
                            "tags": tags,
                            "metadata": metadata,
                        }
                    )
                marker = response.get("Marker")
                if not marker:
                    break

            return instances
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def get_rds_rightsizing_context(
        self,
        db_instance: Dict[str, Any],
        *,
        region: Optional[str] = None,
        orderable_options: Optional[List[Dict[str, Any]]] = None,
        orderable_status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Collect runtime compatibility and operational context for one DB."""
        query_region = self._resolve_region(region)
        client = self.session.client(
            "rds",
            region_name=query_region,
        )
        engine = str(db_instance.get("Engine") or db_instance.get("engine") or "")
        engine_version = str(db_instance.get("EngineVersion") or db_instance.get("engine_version") or "")
        license_model = str(db_instance.get("LicenseModel") or db_instance.get("license_model") or "")
        identifier = str(db_instance.get("DBInstanceIdentifier") or db_instance.get("resource_id") or "")
        if orderable_options is not None:
            orderable = orderable_options
            resolved_orderable_status = orderable_status or "SUCCESS"
        else:
            try:
                orderable = self.get_rds_orderable_options(
                    engine, engine_version, license_model=license_model, region=query_region
                )
                resolved_orderable_status = "SUCCESS"
            except Exception as exc:
                orderable = []
                resolved_orderable_status = _collection_status(exc)
        try:
            valid = client.describe_valid_db_instance_modifications(
                DBInstanceIdentifier=identifier
            ).get("ValidDBInstanceModificationsMessage") or {}
            valid_status = "SUCCESS"
        except Exception as exc:
            valid = {}
            valid_status = _collection_status(exc)
        maintenance: List[Dict[str, Any]] = []
        maintenance_status = "SUCCESS"
        try:
            marker: Optional[str] = None
            while True:
                request = {"ResourceIdentifier": db_instance.get("DBInstanceArn") or identifier}
                if marker:
                    request["Marker"] = marker
                response = client.describe_pending_maintenance_actions(**request)
                maintenance.extend(response.get("PendingMaintenanceActions") or [])
                marker = response.get("Marker")
                if not marker:
                    break
        except Exception as exc:
            maintenance_status = _collection_status(exc)
        events: List[Dict[str, Any]] = []
        events_status = "SUCCESS"
        try:
            marker = None
            while True:
                request = {
                    "SourceIdentifier": identifier,
                    "SourceType": "db-instance",
                    "Duration": 20160,
                }
                if marker:
                    request["Marker"] = marker
                response = client.describe_events(**request)
                events.extend(response.get("Events") or [])
                marker = response.get("Marker")
                if not marker:
                    break
        except Exception as exc:
            events_status = _collection_status(exc)

        connection_limit: Dict[str, Any] = {
            "status": "EMPTY",
            "parameter_name": "max_connections",
            "resolved_value": None,
            "raw_values": [],
        }
        parameter_groups = db_instance.get("DBParameterGroups") or db_instance.get("db_parameter_groups") or []
        try:
            for group in parameter_groups:
                if not isinstance(group, dict):
                    continue
                group_name = str(group.get("DBParameterGroupName") or "")
                if not group_name:
                    continue
                marker = None
                while True:
                    request = {"DBParameterGroupName": group_name}
                    if marker:
                        request["Marker"] = marker
                    response = client.describe_db_parameters(**request)
                    for parameter in response.get("Parameters") or []:
                        if str(parameter.get("ParameterName") or "").lower() != "max_connections":
                            continue
                        raw_value = parameter.get("ParameterValue")
                        connection_limit["raw_values"].append(
                            {
                                "parameter_group": group_name,
                                "apply_status": group.get("ParameterApplyStatus"),
                                "value": raw_value,
                                "source": parameter.get("Source"),
                                "apply_type": parameter.get("ApplyType"),
                            }
                        )
                        try:
                            resolved = int(str(raw_value))
                        except (TypeError, ValueError):
                            resolved = None
                        if resolved is not None and resolved > 0:
                            connection_limit["resolved_value"] = resolved
                    marker = response.get("Marker")
                    if not marker:
                        break
            connection_limit["status"] = (
                "PRESENT" if connection_limit["raw_values"] else "EMPTY"
            )
        except Exception as exc:
            connection_limit["status"] = _collection_status(exc)

        explicit_connection_values = {
            int(str(item["value"]))
            for item in connection_limit["raw_values"]
            if str(item.get("source") or "").lower() == "user"
            and str(item.get("value") or "").isdigit()
            and int(str(item["value"])) > 0
        }
        target_connection_limits = (
            {
                str(option["DBInstanceClass"]): next(iter(explicit_connection_values))
                for option in orderable
                if option.get("DBInstanceClass")
            }
            if len(explicit_connection_values) == 1
            else {}
        )

        return {
            "orderable_status": resolved_orderable_status,
            "orderable_option_key": {
                "engine": engine,
                "engine_version": engine_version,
                "license_model": license_model,
                "region": query_region,
                "vpc": True,
            },
            "orderable_options": orderable,
            "orderable_target_classes": sorted(
                {str(item.get("DBInstanceClass")) for item in orderable if item.get("DBInstanceClass")}
            ),
            "valid_storage_status": valid_status,
            "valid_storage_options": valid.get("ValidStorageOptions") or [],
            "pending_maintenance_actions": maintenance,
            "pending_maintenance_status": maintenance_status,
            "recent_events": events,
            "recent_events_status": events_status,
            "last_rds_event_at": max(
                (item.get("Date") for item in events if isinstance(item.get("Date"), datetime)),
                default=None,
            ),
            "recent_event_categories": sorted(
                {str(category) for item in events for category in (item.get("EventCategories") or [])}
            ),
            "connection_limit": connection_limit,
            "target_connection_limits": target_connection_limits,
        }

    def get_rds_orderable_options(
        self,
        engine: str,
        engine_version: str,
        *,
        license_model: str = "",
        region: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return the complete engine/version/license/VPC orderable set."""
        query_region = self._resolve_region(region)
        client = self.session.client(
            "rds",
            region_name=query_region,
        )
        orderable: List[Dict[str, Any]] = []
        marker = None
        while True:
            request: Dict[str, Any] = {
                "Engine": engine,
                "EngineVersion": engine_version,
                "Vpc": True,
            }
            if license_model:
                request["LicenseModel"] = license_model
            if marker:
                request["Marker"] = marker
            response = client.describe_orderable_db_instance_options(**request)
            orderable.extend(response.get("OrderableDBInstanceOptions") or [])
            marker = response.get("Marker")
            if not marker:
                break
        return orderable

    def get_rds_rightsizing_prices(
        self,
        db_instance: Dict[str, Any],
        target_classes: List[str],
        *,
        region: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve exact on-demand class and storage prices from AWS Price List.

        Results are reduced to decimal strings so recommendation reads remain
        AWS-free. The Price List API is hosted in us-east-1 even when the priced
        products belong to another region.
        """
        query_region = self._resolve_region(region)
        engine = str(db_instance.get("Engine") or db_instance.get("engine") or "")
        engine_dimensions = {
            "postgres": ("PostgreSQL", None),
            "mysql": ("MySQL", None),
            "mariadb": ("MariaDB", None),
            "oracle-ee": ("Oracle", "Enterprise"),
            "oracle-se2": ("Oracle", "Standard Two"),
            "oracle-se1": ("Oracle", "Standard One"),
            "oracle-se": ("Oracle", "Standard"),
            "sqlserver-ee": ("SQL Server", "Enterprise"),
            "sqlserver-se": ("SQL Server", "Standard"),
            "sqlserver-ex": ("SQL Server", "Express"),
            "sqlserver-web": ("SQL Server", "Web"),
            "db2-ae": ("Db2", "Advanced Edition"),
            "db2-se": ("Db2", "Standard Edition"),
        }
        dimensions = engine_dimensions.get(engine.lower())
        if not dimensions:
            return {"status": "NOT_APPLICABLE", "class_prices": {}, "storage_prices": {}}
        database_engine, database_edition = dimensions
        deployment = "Multi-AZ" if db_instance.get("MultiAZ") else "Single-AZ"
        license_names = {
            "general-public-license": "No license required",
            "postgresql-license": "No license required",
            "license-included": "License included",
            "bring-your-own-license": "Bring your own license",
        }
        raw_license_model = str(db_instance.get("LicenseModel") or "").lower()
        license_model = license_names.get(raw_license_model)
        if license_model is None:
            return {
                "status": "INVALID",
                "class_prices": {},
                "storage_prices": {},
                "reason_code": "INVENTORY_CONFIGURATION_INCOMPLETE",
            }
        client = self.session.client("pricing", region_name="us-east-1")

        def products(filters: List[Dict[str, str]]) -> List[Dict[str, Any]]:
            rows: List[Dict[str, Any]] = []
            token: Optional[str] = None
            while True:
                request: Dict[str, Any] = {
                    "ServiceCode": "AmazonRDS",
                    "FormatVersion": "aws_v1",
                    "Filters": filters,
                    "MaxResults": 100,
                }
                if token:
                    request["NextToken"] = token
                response = client.get_products(**request)
                for raw in response.get("PriceList") or []:
                    try:
                        parsed = json.loads(raw) if isinstance(raw, str) else raw
                    except json.JSONDecodeError:
                        continue
                    if isinstance(parsed, dict):
                        rows.append(parsed)
                token = response.get("NextToken")
                if not token:
                    return rows

        def ondemand_dimensions(product: Dict[str, Any]) -> List[Dict[str, Any]]:
            result: List[Dict[str, Any]] = []
            for term in (product.get("terms", {}).get("OnDemand") or {}).values():
                result.extend((term.get("priceDimensions") or {}).values())
            return result

        common = [
            {"Type": "TERM_MATCH", "Field": "regionCode", "Value": query_region},
            {"Type": "TERM_MATCH", "Field": "databaseEngine", "Value": database_engine},
            {"Type": "TERM_MATCH", "Field": "deploymentOption", "Value": deployment},
        ]
        class_filters = list(common)
        class_filters.append({"Type": "TERM_MATCH", "Field": "licenseModel", "Value": license_model})
        if database_edition:
            class_filters.append(
                {"Type": "TERM_MATCH", "Field": "databaseEdition", "Value": database_edition}
            )
        wanted = set(target_classes) if target_classes else None
        class_prices: Dict[str, Any] = {}
        versions: set[str] = set()
        for product in products(class_filters + [{"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Database Instance"}]):
            attributes = product.get("product", {}).get("attributes") or {}
            instance_type = str(attributes.get("instanceType") or "")
            if wanted is not None and instance_type not in wanted:
                continue
            hourly = None
            for dimension in ondemand_dimensions(product):
                if dimension.get("unit") in {"Hrs", "Hours"}:
                    hourly = (dimension.get("pricePerUnit") or {}).get("USD")
                    if hourly is not None:
                        break
            if hourly is None:
                continue
            from decimal import Decimal

            class_prices[instance_type] = {
                "hourly": str(Decimal(str(hourly))),
                "monthly": str(Decimal(str(hourly)) * Decimal("730")),
                "currency": "USD",
                "sku": product.get("product", {}).get("sku"),
            }
            if product.get("publicationDate"):
                versions.add(str(product["publicationDate"]))

        storage_prices: Dict[str, Dict[str, Any]] = {}
        for product in products(common):
            attributes = product.get("product", {}).get("attributes") or {}
            dimensions = ondemand_dimensions(product)
            storage_name = " ".join(
                str(value or "")
                for value in (
                    attributes.get("volumeType"), attributes.get("storageMedia"),
                    attributes.get("group"), attributes.get("groupDescription"),
                    attributes.get("operation"),
                    *(dimension.get("description") for dimension in dimensions),
                )
            ).lower()
            storage_type = next(
                (kind for kind in ("gp3", "gp2", "io2", "io1") if kind in storage_name),
                None,
            )
            if storage_type is None:
                continue
            entry = storage_prices.setdefault(storage_type, {"currency": "USD"})
            for dimension in dimensions:
                unit = str(dimension.get("unit") or "")
                description = str(dimension.get("description") or "").lower()
                price = (dimension.get("pricePerUnit") or {}).get("USD")
                if price is None:
                    continue
                if unit in {"GB-Mo", "GB-month"} and "throughput" not in description:
                    entry["storage_gib_month"] = str(price)
                elif "iops" in unit.lower() or "iops" in description:
                    entry["iops_month"] = str(price)
                elif "throughput" in description or unit in {"GiBps-Mo", "MBps-Mo"}:
                    entry["throughput_mibps_month"] = str(price)
                if dimension.get("rateCode"):
                    entry.setdefault("rate_codes", []).append(str(dimension["rateCode"]))
            if storage_type == "gp3":
                entry.setdefault("included_iops", 3000)
                entry.setdefault("included_throughput_mibps", 125)
            if product.get("publicationDate"):
                versions.add(str(product["publicationDate"]))
        return {
            "status": "SUCCESS",
            "class_prices": class_prices,
            "storage_prices": storage_prices,
            "catalog_versions": sorted(versions),
            "source": "AWS_PRICE_LIST_API",
            "region": query_region,
            "database_engine": database_engine,
            "database_edition": database_edition,
            "rds_engine": engine.lower(),
            "deployment": deployment,
            "license_model": license_model,
        }

    def _get_ebs_volumes(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get EBS volumes."""
        try:
            client = self.ec2_client
            if region:
                client = self.session.client("ec2", region_name=region)

            response = client.describe_volumes()

            volumes = []
            for volume in response.get("Volumes", []):
                tags = {tag["Key"]: tag["Value"] for tag in volume.get("Tags", [])}
                name = tags.get("Name", volume["VolumeId"])

                volumes.append(
                    {
                        "resource_id": volume["VolumeId"],
                        "resource_type": "ebs",
                        "resource_name": name,
                        "region": volume["AvailabilityZone"][:-1],
                        "state": volume["State"],
                        "size": volume["Size"],
                        "volume_type": volume["VolumeType"],
                        "attached": len(volume.get("Attachments", [])) > 0,
                        "tags": tags,
                        "metadata": {
                            "size": volume["Size"],
                            "volume_type": volume["VolumeType"],
                            "iops": volume.get("Iops"),
                            "throughput": volume.get("Throughput"),
                            "encrypted": volume.get("Encrypted", False),
                            "attachments": volume.get("Attachments", []),
                            # Age gate for the unattached-volumes check. AWS does
                            # not publish a detach time, so creation time is the
                            # only age signal available without CloudTrail.
                            "create_time": _iso_or_none(volume.get("CreateTime")),
                        },
                    }
                )

            return volumes
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def _get_snapshots(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get EBS snapshots."""
        try:
            client = self.ec2_client
            if region:
                client = self.session.client("ec2", region_name=region)

            response = client.describe_snapshots(OwnerIds=["self"])

            snapshots = []
            for snapshot in response.get("Snapshots", []):
                tags = {tag["Key"]: tag["Value"] for tag in snapshot.get("Tags", [])}
                name = tags.get("Name", snapshot["SnapshotId"])

                snapshots.append(
                    {
                        "resource_id": snapshot["SnapshotId"],
                        "resource_type": "snapshot",
                        "resource_name": name,
                        "region": snapshot["AvailabilityZone"][:-1]
                        if snapshot.get("AvailabilityZone")
                        else None,
                        "state": snapshot["State"],
                        "size": snapshot["VolumeSize"],
                        "start_time": snapshot["StartTime"].isoformat(),
                        "tags": tags,
                        "metadata": {
                            "size": snapshot["VolumeSize"],
                            "volume_id": snapshot.get("VolumeId"),
                            "encrypted": snapshot.get("Encrypted", False),
                        },
                    }
                )

            return snapshots
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def _get_cloudwatch_alarms(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get CloudWatch alarms."""
        try:
            client = self.cloudwatch_client
            if region:
                client = self.session.client("cloudwatch", region_name=region)
            print(
                f"[AWS_ADAPTER] Querying CloudWatch alarms from real AWS, region: {region}"
            )

            response = client.describe_alarms()
            print(
                f"[AWS_ADAPTER] describe_alarms() returned {len(response.get('MetricAlarms', []))} metric alarms and {len(response.get('CompositeAlarms', []))} composite alarms"
            )

            alarms = []
            for alarm in response.get("MetricAlarms", []):
                alarms.append(
                    {
                        "resource_id": alarm["AlarmName"],
                        "resource_type": "cloudwatch_alarm",
                        "resource_name": alarm["AlarmName"],
                        "region": alarm.get("Region", region or settings.aws_region),
                        "state": alarm.get("StateValue", "UNKNOWN"),
                        "tags": {},  # CloudWatch alarms don't have tags in describe_alarms
                        "metadata": {
                            "Namespace": alarm.get("Namespace"),
                            "MetricName": alarm.get("MetricName"),
                            "Statistic": alarm.get("Statistic"),
                            "ExtendedStatistic": alarm.get("ExtendedStatistic"),
                            "Period": alarm.get("Period"),
                            "EvaluationPeriods": alarm.get("EvaluationPeriods"),
                            "Threshold": alarm.get("Threshold"),
                            "ComparisonOperator": alarm.get("ComparisonOperator"),
                            "TreatMissingData": alarm.get("TreatMissingData"),
                            "Dimensions": alarm.get("Dimensions", []),
                            "AlarmActions": alarm.get("AlarmActions", []),
                            "OKActions": alarm.get("OKActions", []),
                            "InsufficientDataActions": alarm.get(
                                "InsufficientDataActions", []
                            ),
                        },
                    }
                )

            # Also include composite alarms
            for alarm in response.get("CompositeAlarms", []):
                alarms.append(
                    {
                        "resource_id": alarm["AlarmName"],
                        "resource_type": "cloudwatch_alarm",
                        "resource_name": alarm["AlarmName"],
                        "region": alarm.get("Region", region or settings.aws_region),
                        "state": alarm.get("StateValue", "UNKNOWN"),
                        "tags": {},
                        "metadata": {
                            "AlarmRule": alarm.get("AlarmRule"),
                            "AlarmActions": alarm.get("AlarmActions", []),
                            "OKActions": alarm.get("OKActions", []),
                            "InsufficientDataActions": alarm.get(
                                "InsufficientDataActions", []
                            ),
                        },
                    }
                )

            return alarms
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def _get_cloudwatch_log_groups(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get CloudWatch Log Groups."""
        try:
            client = (
                self.session.client("logs", region_name=region)
                if region
                else self.session.client("logs")
            )

            response = client.describe_log_groups()

            log_groups = []
            for lg in response.get("logGroups", []):
                log_groups.append(
                    {
                        "resource_id": lg["logGroupName"],
                        "resource_type": "cloudwatch_log_group",
                        "resource_name": lg["logGroupName"],
                        "region": region or settings.aws_region,
                        "state": "active",
                        "tags": {},  # Tags need separate API call
                        "metadata": {
                            "creationTime": lg.get("creationTime"),
                            "retentionInDays": lg.get("retentionInDays"),
                            "storedBytes": lg.get("storedBytes", 0),
                            "kmsKeyId": lg.get("kmsKeyId"),
                        },
                    }
                )

            return log_groups
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def _get_s3_buckets(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get S3 buckets."""
        try:
            # S3 is global, but we can filter by region in bucket location
            client = self.session.client("s3")

            response = client.list_buckets()

            buckets = []
            for bucket in response.get("Buckets", []):
                bucket_name = bucket["Name"]

                # Get bucket location and other details
                try:
                    location_response = client.get_bucket_location(Bucket=bucket_name)
                    bucket_region = (
                        location_response.get("LocationConstraint") or "us-east-1"
                    )
                except:
                    bucket_region = "us-east-1"

                # Get bucket tags
                try:
                    tags_response = client.get_bucket_tagging(Bucket=bucket_name)
                    tags = {
                        tag["Key"]: tag["Value"]
                        for tag in tags_response.get("TagSet", [])
                    }
                except:
                    tags = {}

                # Get lifecycle configuration
                try:
                    lifecycle_response = client.get_bucket_lifecycle_configuration(
                        Bucket=bucket_name
                    )
                    lifecycle_rules = lifecycle_response.get("Rules", [])
                except:
                    lifecycle_rules = []

                buckets.append(
                    {
                        "resource_id": bucket_name,
                        "resource_type": "s3",
                        "resource_name": bucket_name,
                        "region": bucket_region,
                        "state": "active",
                        "tags": tags,
                        "metadata": {
                            "creation_date": bucket.get("CreationDate"),
                            "lifecycle_rules": lifecycle_rules,
                            "tags": tags,
                        },
                    }
                )

            return buckets
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def get_s3_bucket_logging(
        self, bucket_name: str, region: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get S3 bucket logging configuration."""
        try:
            client = self.session.client(
                "s3", region_name=region or settings.aws_region
            )
            return client.get_bucket_logging(Bucket=bucket_name)
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def get_s3_bucket_lifecycle(
        self, bucket_name: str, region: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get S3 bucket lifecycle configuration."""
        try:
            client = self.session.client(
                "s3", region_name=region or settings.aws_region
            )
            return client.get_bucket_lifecycle_configuration(Bucket=bucket_name)
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def discover_storage_types(self, region: str) -> Dict[str, set[str]]:
        """Discover sparse S3 CloudWatch storage dimensions for a region."""
        from app.adapters.aws.s3_optimizer_collect import discover_storage_types

        return discover_storage_types(self, region)

    def fetch_daily_storage_metrics(
        self,
        bucket_name: str,
        storage_types: Any,
        days: int = 90,
        region: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch free daily S3 storage metrics, preserving absent series."""
        from app.adapters.aws.s3_optimizer_collect import fetch_daily_storage_metrics

        return fetch_daily_storage_metrics(
            self, bucket_name, storage_types, days=days, region=region
        )

    def get_s3_bucket_lifecycle_typed(
        self, bucket_name: str, region: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fetch lifecycle rules with distinct absent and unknown statuses."""
        from app.adapters.aws.s3_optimizer_collect import get_s3_bucket_lifecycle_typed

        return get_s3_bucket_lifecycle_typed(self, bucket_name, region=region)

    def list_s3_bucket_intelligent_tiering_configurations(
        self, bucket_name: str, region: Optional[str] = None
    ) -> Dict[str, Any]:
        """List parsed Intelligent-Tiering configurations for one bucket."""
        from app.adapters.aws.s3_optimizer_collect import (
            list_s3_bucket_intelligent_tiering_configurations,
        )

        return list_s3_bucket_intelligent_tiering_configurations(
            self, bucket_name, region=region
        )

    def get_s3_bucket_versioning(
        self, bucket_name: str, region: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get S3 bucket versioning configuration."""
        try:
            client = self.session.client(
                "s3", region_name=region or settings.aws_region
            )
            return client.get_bucket_versioning(Bucket=bucket_name)
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def get_s3_bucket_replication(
        self, bucket_name: str, region: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get S3 bucket replication configuration."""
        try:
            client = self.session.client(
                "s3", region_name=region or settings.aws_region
            )
            return client.get_bucket_replication(Bucket=bucket_name)
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def get_s3_bucket_inventory(
        self, bucket_name: str, region: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get S3 bucket inventory configurations."""
        try:
            client = self.session.client(
                "s3", region_name=region or settings.aws_region
            )
            return client.list_bucket_inventory_configurations(Bucket=bucket_name)
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def _get_vpcs(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get VPCs."""
        try:
            client = self.ec2_client
            if region:
                client = self.session.client("ec2", region_name=region)

            ec2_filters = []
            if filters and "vpc_id" in filters:
                ec2_filters.append({"Name": "vpc-id", "Values": [filters["vpc_id"]]})

            response = client.describe_vpcs(Filters=ec2_filters)

            vpcs = []
            for vpc in response.get("Vpcs", []):
                tags = {tag["Key"]: tag["Value"] for tag in vpc.get("Tags", [])}
                name = tags.get("Name", vpc["VpcId"])

                vpcs.append(
                    {
                        "resource_id": vpc["VpcId"],
                        "resource_type": "vpc",
                        "resource_name": name,
                        "region": region or settings.aws_region,
                        "state": vpc.get("State", "available"),
                        "tags": tags,
                        "metadata": {
                            "cidr_block": vpc.get("CidrBlock"),
                            "is_default": vpc.get("IsDefault", False),
                        },
                    }
                )

            return vpcs
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def _get_vpc_endpoints(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get VPC endpoints."""
        try:
            client = self.ec2_client
            if region:
                client = self.session.client("ec2", region_name=region)

            ec2_filters = []
            if filters and "vpc_id" in filters:
                ec2_filters.append({"Name": "vpc-id", "Values": [filters["vpc_id"]]})

            response = client.describe_vpc_endpoints(Filters=ec2_filters)

            endpoints = []
            for endpoint in response.get("VpcEndpoints", []):
                tags = {tag["Key"]: tag["Value"] for tag in endpoint.get("Tags", [])}
                name = tags.get("Name", endpoint["VpcEndpointId"])

                endpoints.append(
                    {
                        "resource_id": endpoint["VpcEndpointId"],
                        "resource_type": "vpc_endpoint",
                        "resource_name": name,
                        "region": region or settings.aws_region,
                        "state": endpoint.get("State", "unknown"),
                        "tags": tags,
                        "metadata": {
                            "VpcId": endpoint.get("VpcId"),
                            "VpcEndpointType": endpoint.get("VpcEndpointType"),
                            "ServiceName": endpoint.get("ServiceName"),
                            "vpc_id": endpoint.get("VpcId"),
                            "vpc_endpoint_type": endpoint.get("VpcEndpointType"),
                            "service_name": endpoint.get("ServiceName"),
                        },
                    }
                )

            return endpoints
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def _get_vpc_flow_logs(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get VPC Flow Logs."""
        try:
            client = self.ec2_client
            if region:
                client = self.session.client("ec2", region_name=region)

            ec2_filters = []
            if filters and "vpc_id" in filters:
                ec2_filters.append(
                    {"Name": "resource-id", "Values": [filters["vpc_id"]]}
                )

            response = client.describe_flow_logs(Filters=ec2_filters)

            flow_logs = []
            for flow_log in response.get("FlowLogs", []):
                flow_logs.append(
                    {
                        "resource_id": flow_log["FlowLogId"],
                        "resource_type": "vpc_flow_log",
                        "resource_name": flow_log.get(
                            "FlowLogName", flow_log["FlowLogId"]
                        ),
                        "region": region or settings.aws_region,
                        "state": flow_log.get("FlowLogStatus", "unknown"),
                        "tags": {
                            tag["Key"]: tag["Value"] for tag in flow_log.get("Tags", [])
                        },
                        "metadata": {
                            "ResourceId": flow_log.get("ResourceId"),
                            "ResourceType": flow_log.get("ResourceType"),
                            "TrafficType": flow_log.get("TrafficType"),
                            "LogDestinationType": flow_log.get("LogDestinationType"),
                            "LogDestination": flow_log.get("LogDestination"),
                            "vpc_id": flow_log.get("ResourceId")
                            if flow_log.get("ResourceType") == "VPC"
                            else None,
                        },
                    }
                )

            return flow_logs
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def _get_elastic_ips(
        self,
        filters: Optional[Dict[str, Any]] = None,
        region: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get VPC Elastic IP allocations."""
        try:
            client = self.ec2_client
            if region:
                client = self.session.client("ec2", region_name=region)

            response = client.describe_addresses()
            resources: List[Dict[str, Any]] = []
            for address in response.get("Addresses", []):
                allocation_id = address.get("AllocationId")
                public_ip = address.get("PublicIp")
                resource_id = allocation_id or public_ip
                if not resource_id:
                    continue
                resources.append(
                    {
                        "resource_id": resource_id,
                        "resource_type": "elastic_ip",
                        "resource_name": public_ip or resource_id,
                        "region": region or settings.aws_region,
                        "state": (
                            "associated"
                            if address.get("AssociationId")
                            else "unassociated"
                        ),
                        "tags": {
                            tag["Key"]: tag["Value"] for tag in address.get("Tags", [])
                        },
                        "metadata": {
                            "AllocationId": allocation_id,
                            "AssociationId": address.get("AssociationId"),
                            "PublicIp": public_ip,
                            "Domain": address.get("Domain"),
                            "InstanceId": address.get("InstanceId"),
                            "NetworkInterfaceId": address.get("NetworkInterfaceId"),
                            "PrivateIpAddress": address.get("PrivateIpAddress"),
                        },
                    }
                )
            return resources
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def _get_elasticache_resources(
        self,
        resource_type: str,
        filters: Optional[Dict[str, Any]] = None,
        region: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get canonical ElastiCache groups and standalone clusters."""
        try:
            query_region = region or settings.aws_region
            client = (
                self.session.client("elasticache", region_name=region)
                if region
                else self.session.client("elasticache")
            )

            resources = []

            def paged(
                method: Any, result_key: str, **kwargs: Any
            ) -> List[Dict[str, Any]]:
                values: List[Dict[str, Any]] = []
                marker: Optional[str] = None
                while True:
                    request = dict(kwargs)
                    if marker:
                        request["Marker"] = marker
                    response = method(**request)
                    values.extend(response.get(result_key, []))
                    marker = response.get("Marker")
                    if not marker:
                        return values

            auto_scaling: Optional[set[str]] = set()
            try:
                autoscaling_client = self.session.client(
                    "application-autoscaling", region_name=region
                )
                next_token: Optional[str] = None
                while True:
                    request: Dict[str, Any] = {"ServiceNamespace": "elasticache"}
                    if next_token:
                        request["NextToken"] = next_token
                    response = autoscaling_client.describe_scalable_targets(**request)
                    for target in response.get("ScalableTargets", []):
                        resource_id = str(target.get("ResourceId") or "")
                        if resource_id.startswith("replication-group/"):
                            auto_scaling.add(resource_id.partition("/")[2])
                        elif resource_id.startswith("cache-cluster/"):
                            auto_scaling.add(resource_id.partition("/")[2])
                    next_token = response.get("NextToken")
                    if not next_token:
                        break
            except Exception:
                auto_scaling = None

            if resource_type == "elasticache_replication_group":
                # Fetch member details once for the region. A group can have
                # hundreds of members, and one describe call per member both
                # amplifies throttling and lets a transient member deletion
                # abort the entire regional inventory.
                all_member_details: Dict[str, Dict[str, Any]] = {}
                try:
                    for cluster in paged(
                        client.describe_cache_clusters,
                        "CacheClusters",
                        ShowCacheNodeInfo=True,
                    ):
                        cluster_id = cluster.get("CacheClusterId")
                        if cluster_id:
                            all_member_details[str(cluster_id)] = cluster
                except Exception as exc:
                    logger.warning(
                        "Unable to enrich ElastiCache member details in %s: %s",
                        query_region,
                        exc,
                    )
                for rg in paged(
                    client.describe_replication_groups, "ReplicationGroups"
                ):
                    engine = rg.get("Engine")
                    engine_version = rg.get("EngineVersion")
                    member_clusters = rg.get("MemberClusters", [])
                    member_details = {
                        member_id: all_member_details[member_id]
                        for member_id in member_clusters
                        if member_id in all_member_details
                    }
                    first_member = next(iter(member_details.values()), {})
                    engine = engine or first_member.get("Engine")
                    engine_version = engine_version or first_member.get("EngineVersion")
                    roles: Dict[str, Dict[str, Any]] = {}
                    replicas_per_group: List[int] = []
                    for index, node_group in enumerate(rg.get("NodeGroups") or []):
                        group_id = str(
                            node_group.get("NodeGroupId") or f"{index + 1:04d}"
                        )
                        group_members = node_group.get("NodeGroupMembers") or []
                        replicas_per_group.append(max(len(group_members) - 1, 0))
                        for member in group_members:
                            cluster_id = member.get("CacheClusterId")
                            if cluster_id:
                                roles[cluster_id] = {
                                    "role": str(
                                        member.get("CurrentRole") or "unknown"
                                    ).lower(),
                                    "node_group_id": group_id,
                                }
                    allowed_up: Optional[List[str]] = None
                    allowed_down: Optional[List[str]] = None
                    try:
                        allowed_response = client.list_allowed_node_type_modifications(
                            ReplicationGroupId=rg["ReplicationGroupId"]
                        )
                        allowed_up = allowed_response.get("ScaleUpModifications") or []
                        allowed_down = (
                            allowed_response.get("ScaleDownModifications") or []
                        )
                    except Exception:
                        pass
                    reserved_percent: Optional[float] = None
                    reserved_bytes: Optional[float] = None
                    parameter_group = (
                        first_member.get("CacheParameterGroup") or {}
                    ).get("CacheParameterGroupName")
                    if parameter_group:
                        try:
                            parameters = paged(
                                client.describe_cache_parameters,
                                "Parameters",
                                CacheParameterGroupName=parameter_group,
                            )
                            for parameter in parameters:
                                name = str(parameter.get("ParameterName") or "").lower()
                                value = _number_or_none(parameter.get("ParameterValue"))
                                if value is None:
                                    continue
                                if name in {
                                    "reserved-memory-percent",
                                    "reserved_memory_percent",
                                }:
                                    reserved_percent = _percent_parameter_fraction(
                                        value
                                    )
                                elif name in {"reserved-memory", "reserved_memory"}:
                                    reserved_bytes = value
                        except Exception:
                            pass
                    resources.append(
                        {
                            "resource_id": rg["ReplicationGroupId"],
                            "resource_type": "elasticache_replication_group",
                            "resource_name": rg["ReplicationGroupId"],
                            "region": region or settings.aws_region,
                            "state": rg.get("Status", "unknown"),
                            "tags": {},
                            "metadata": {
                                "Engine": engine,
                                "EngineVersion": engine_version,
                                "CacheNodeType": rg.get("CacheNodeType"),
                                "Status": rg.get("Status"),
                                "MemberClusters": member_clusters,
                                "NumNodeGroups": len(rg.get("NodeGroups") or []) or 1,
                                "ReplicasPerNodeGroup": replicas_per_group
                                or [max(len(member_clusters) - 1, 0)],
                                "cluster_mode_enabled": len(rg.get("NodeGroups") or [])
                                > 1,
                                "automatic_failover": str(
                                    rg.get("AutomaticFailover") or ""
                                ).lower()
                                == "enabled",
                                "multi_az": str(rg.get("MultiAZ") or "").lower()
                                == "enabled",
                                "member_cluster_ids": member_clusters,
                                "member_roles": roles,
                                "member_created_at": {
                                    member_id: details.get("CacheClusterCreateTime")
                                    for member_id, details in member_details.items()
                                },
                                "global_datastore_member": bool(
                                    rg.get("GlobalReplicationGroupInfo")
                                ),
                                "data_tiering_enabled": str(
                                    rg.get("DataTiering") or ""
                                ).lower()
                                == "enabled",
                                "snapshot_retention_limit": rg.get(
                                    "SnapshotRetentionLimit"
                                ),
                                "auto_scaling_attached": None
                                if auto_scaling is None
                                else rg["ReplicationGroupId"] in auto_scaling,
                                "allowed_scale_up_types": allowed_up,
                                "allowed_scale_down_types": allowed_down,
                                "reserved_memory_percent": reserved_percent,
                                "reserved_memory_bytes": reserved_bytes,
                            },
                        }
                    )
            elif resource_type == "elasticache_cluster":
                for cluster in paged(client.describe_cache_clusters, "CacheClusters"):
                    if cluster.get("ReplicationGroupId"):
                        continue
                    resources.append(
                        {
                            "resource_id": cluster["CacheClusterId"],
                            "resource_type": "elasticache_cluster",
                            "resource_name": cluster["CacheClusterId"],
                            "region": region or settings.aws_region,
                            "state": cluster.get("CacheClusterStatus", "unknown"),
                            "tags": {},
                            "metadata": {
                                "Engine": cluster.get("Engine"),
                                "EngineVersion": cluster.get("EngineVersion"),
                                "CacheNodeType": cluster.get("CacheNodeType"),
                                "ReplicationGroupId": cluster.get("ReplicationGroupId"),
                                "NumCacheNodes": cluster.get("NumCacheNodes"),
                                "CacheClusterStatus": cluster.get("CacheClusterStatus"),
                                "auto_scaling_attached": None
                                if auto_scaling is None
                                else cluster["CacheClusterId"] in auto_scaling,
                            },
                        }
                    )

            return resources
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def _get_glue_jobs(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get Glue jobs."""
        try:
            client = (
                self.session.client("glue", region_name=region)
                if region
                else self.session.client("glue")
            )

            response = client.list_jobs()

            jobs = []
            for job_name in response.get("JobNames", []):
                try:
                    job_response = client.get_job(JobName=job_name)
                    job = job_response.get("Job", {})

                    jobs.append(
                        {
                            "resource_id": job_name,
                            "resource_type": "glue_job",
                            "resource_name": job_name,
                            "region": region or settings.aws_region,
                            "state": job.get("LastRun", {}).get(
                                "JobRunState", "unknown"
                            )
                            if job.get("LastRun")
                            else "unknown",
                            "tags": job.get("Tags", {}),
                            "metadata": {
                                "GlueVersion": job.get("GlueVersion"),
                                "Command": job.get("Command", {}),
                                "MaxRetries": job.get("MaxRetries"),
                            },
                        }
                    )
                except:
                    # If we can't get job details, still include it
                    jobs.append(
                        {
                            "resource_id": job_name,
                            "resource_type": "glue_job",
                            "resource_name": job_name,
                            "region": region or settings.aws_region,
                            "state": "unknown",
                            "tags": {},
                            "metadata": {},
                        }
                    )

            return jobs
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def _get_emr_clusters(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get EMR clusters."""
        try:
            client = (
                self.session.client("emr", region_name=region)
                if region
                else self.session.client("emr")
            )

            cluster_states = ["RUNNING", "WAITING"]
            if filters and "state" in filters:
                state_filter = filters["state"]
                if isinstance(state_filter, list):
                    cluster_states = state_filter
                elif isinstance(state_filter, str):
                    cluster_states = [state_filter]

            response = client.list_clusters(ClusterStates=cluster_states)

            clusters = []
            for cluster_summary in response.get("Clusters", []):
                cluster_id = cluster_summary["Id"]

                try:
                    cluster_response = client.describe_cluster(ClusterId=cluster_id)
                    cluster = cluster_response.get("Cluster", {})

                    clusters.append(
                        {
                            "resource_id": cluster_id,
                            "resource_type": "emr_cluster",
                            "resource_name": cluster.get("Name", cluster_id),
                            "region": region or settings.aws_region,
                            "state": cluster.get("Status", {}).get("State", "unknown"),
                            "tags": {
                                tag["Key"]: tag["Value"]
                                for tag in cluster.get("Tags", [])
                            },
                            "metadata": {
                                "ReleaseLabel": cluster.get("ReleaseLabel"),
                                "State": cluster.get("Status", {}).get("State"),
                            },
                        }
                    )
                except:
                    # Fallback to summary data
                    clusters.append(
                        {
                            "resource_id": cluster_id,
                            "resource_type": "emr_cluster",
                            "resource_name": cluster_summary.get("Name", cluster_id),
                            "region": region or settings.aws_region,
                            "state": cluster_summary.get("Status", {}).get(
                                "State", "unknown"
                            ),
                            "tags": {},
                            "metadata": {
                                "ReleaseLabel": cluster_summary.get("ReleaseLabel"),
                            },
                        }
                    )

            return clusters
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def _get_emr_instance_fleets(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get EMR instance fleets."""
        try:
            client = (
                self.session.client("emr", region_name=region)
                if region
                else self.session.client("emr")
            )

            fleets = []
            cluster_id = filters.get("cluster_id") if filters else None

            if cluster_id:
                # Get instance fleets for a specific cluster
                try:
                    response = client.list_instance_fleets(ClusterId=cluster_id)
                    for fleet in response.get("InstanceFleets", []):
                        fleets.append(
                            {
                                "resource_id": fleet.get("Id", ""),
                                "resource_type": "emr_instance_fleet",
                                "resource_name": fleet.get("Name", fleet.get("Id", "")),
                                "region": region or settings.aws_region,
                                "state": fleet.get("Status", {}).get(
                                    "State", "unknown"
                                ),
                                "tags": {},
                                "metadata": {
                                    "ClusterId": cluster_id,
                                    "InstanceFleetType": fleet.get("InstanceFleetType"),
                                    "TargetOnDemandCapacity": fleet.get(
                                        "TargetOnDemandCapacity"
                                    ),
                                    "TargetSpotCapacity": fleet.get(
                                        "TargetSpotCapacity"
                                    ),
                                    "ProvisionedOnDemandCapacity": fleet.get(
                                        "ProvisionedOnDemandCapacity"
                                    ),
                                    "ProvisionedSpotCapacity": fleet.get(
                                        "ProvisionedSpotCapacity"
                                    ),
                                },
                            }
                        )
                except:
                    pass
            else:
                # If no cluster_id, return empty (fleets are cluster-specific)
                pass

            return fleets
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def _get_emr_instance_groups(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get EMR instance groups."""
        try:
            client = (
                self.session.client("emr", region_name=region)
                if region
                else self.session.client("emr")
            )

            groups = []
            cluster_id = filters.get("cluster_id") if filters else None

            if cluster_id:
                # Get instance groups for a specific cluster
                try:
                    response = client.list_instance_groups(ClusterId=cluster_id)
                    for group in response.get("InstanceGroups", []):
                        groups.append(
                            {
                                "resource_id": group.get("Id", ""),
                                "resource_type": "emr_instance_group",
                                "resource_name": group.get("Name", group.get("Id", "")),
                                "region": region or settings.aws_region,
                                "state": group.get("Status", {}).get(
                                    "State", "unknown"
                                ),
                                "tags": {},
                                "metadata": {
                                    "ClusterId": cluster_id,
                                    "InstanceGroupType": group.get("InstanceGroupType"),
                                    "InstanceType": group.get("InstanceType"),
                                    "RequestedInstanceCount": group.get(
                                        "RequestedInstanceCount"
                                    ),
                                    "RunningInstanceCount": group.get(
                                        "RunningInstanceCount"
                                    ),
                                },
                            }
                        )
                except:
                    pass
            else:
                # If no cluster_id, return empty (groups are cluster-specific)
                pass

            return groups
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def _get_dynamodb_tables(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get DynamoDB tables."""
        try:
            client = (
                self.session.client("dynamodb", region_name=region)
                if region
                else self.session.client("dynamodb")
            )

            response = client.list_tables()

            tables = []
            for table_name in response.get("TableNames", []):
                try:
                    table_response = client.describe_table(TableName=table_name)
                    table = table_response.get("Table", {})

                    provisioned_throughput = table.get("ProvisionedThroughput", {})

                    # DescribeTable omits BillingModeSummary for tables that
                    # have always been provisioned, where AWS treats the
                    # absent value as PROVISIONED. Leaving it None makes a
                    # provisioned table look like it has no billing mode at
                    # all, which downstream cost logic then has to guess at.
                    billing_mode = table.get("BillingModeSummary", {}).get("BillingMode")
                    if not billing_mode and provisioned_throughput:
                        billing_mode = "PROVISIONED"

                    tables.append(
                        {
                            "resource_id": table_name,
                            "resource_type": "dynamodb_table",
                            "resource_name": table_name,
                            "region": region or settings.aws_region,
                            "state": table.get("TableStatus", "unknown"),
                            "tags": {},
                            "metadata": {
                                "BillingMode": billing_mode,
                                "ProvisionedThroughput": provisioned_throughput,
                                "GlobalSecondaryIndexes": table.get(
                                    "GlobalSecondaryIndexes", []
                                ),
                                # Add capacity units for easy access
                                "read_capacity_units": provisioned_throughput.get(
                                    "ReadCapacityUnits", 0
                                )
                                if provisioned_throughput
                                else 0,
                                "write_capacity_units": provisioned_throughput.get(
                                    "WriteCapacityUnits", 0
                                )
                                if provisioned_throughput
                                else 0,
                            },
                        }
                    )
                except:
                    tables.append(
                        {
                            "resource_id": table_name,
                            "resource_type": "dynamodb_table",
                            "resource_name": table_name,
                            "region": region or settings.aws_region,
                            "state": "unknown",
                            "tags": {},
                            "metadata": {},
                        }
                    )

            return tables
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def _get_dynamodb_gsi(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get DynamoDB Global Secondary Indexes."""
        try:
            client = (
                self.session.client("dynamodb", region_name=region)
                if region
                else self.session.client("dynamodb")
            )

            # Get all tables first
            response = client.list_tables()
            gsis = []

            for table_name in response.get("TableNames", []):
                try:
                    table_response = client.describe_table(TableName=table_name)
                    table = table_response.get("Table", {})

                    # Extract GSIs from table
                    for gsi in table.get("GlobalSecondaryIndexes", []):
                        gsi_name = gsi.get("IndexName")
                        gsi_id = f"{table_name}/{gsi_name}"  # Composite ID: table/gsi

                        gsis.append(
                            {
                                "resource_id": gsi_id,
                                "resource_type": "dynamodb_gsi",
                                "resource_name": gsi_name,
                                "region": region or settings.aws_region,
                                "state": gsi.get("IndexStatus", "unknown"),
                                "tags": {},
                                "metadata": {
                                    "TableName": table_name,
                                    "IndexName": gsi_name,
                                    "IndexStatus": gsi.get("IndexStatus"),
                                    "KeySchema": gsi.get("KeySchema", []),
                                    "Projection": gsi.get("Projection", {}),
                                    "ProvisionedThroughput": gsi.get(
                                        "ProvisionedThroughput"
                                    ),
                                },
                            }
                        )
                except:
                    continue

            return gsis
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def _get_efs_file_systems(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get EFS file systems."""
        try:
            client = (
                self.session.client("efs", region_name=region)
                if region
                else self.session.client("efs")
            )

            response = client.describe_file_systems()

            file_systems = []
            for fs in response.get("FileSystems", []):
                file_system_id = fs["FileSystemId"]

                # Get tags
                try:
                    tags_response = client.describe_tags(FileSystemId=file_system_id)
                    tags = {
                        tag["Key"]: tag["Value"]
                        for tag in tags_response.get("Tags", [])
                    }
                except:
                    tags = {}

                name = tags.get("Name", file_system_id)

                # Get mount targets
                try:
                    mount_targets_response = client.describe_mount_targets(
                        FileSystemId=file_system_id
                    )
                    mount_targets = mount_targets_response.get("MountTargets", [])
                except:
                    mount_targets = []

                # Get lifecycle policy
                try:
                    lifecycle_policies_response = (
                        client.describe_lifecycle_configuration(
                            FileSystemId=file_system_id
                        )
                    )
                    lifecycle_policies = lifecycle_policies_response.get(
                        "LifecyclePolicies", []
                    )
                except:
                    lifecycle_policies = []

                file_systems.append(
                    {
                        "resource_id": file_system_id,
                        "resource_type": "efs",
                        "resource_name": name,
                        "region": region or settings.aws_region,
                        "state": fs.get("LifeCycleState", "unknown"),
                        "tags": tags,
                        "metadata": {
                            "CreationToken": fs.get("CreationToken"),
                            # Age gate for the unused-file-systems check.
                            "CreationTime": _iso_or_none(fs.get("CreationTime")),
                            "PerformanceMode": fs.get(
                                "PerformanceMode", "generalPurpose"
                            ),
                            "ThroughputMode": fs.get("ThroughputMode", "bursting"),
                            "ProvisionedThroughputInMibps": fs.get(
                                "ProvisionedThroughputInMibps"
                            ),
                            "SizeInBytes": fs.get("SizeInBytes", {}),
                            "NumberOfMountTargets": len(mount_targets),
                            "MountTargets": mount_targets,
                            "LifecyclePolicies": lifecycle_policies,
                            "Encrypted": fs.get("Encrypted", False),
                            "KmsKeyId": fs.get("KmsKeyId"),
                        },
                    }
                )

            return file_systems
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def _get_lambda_functions(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get Lambda functions."""
        try:
            client = (
                self.session.client("lambda", region_name=region)
                if region
                else self.session.client("lambda")
            )

            functions = []
            paginator = client.get_paginator("list_functions")

            for page in paginator.paginate():
                for func in page.get("Functions", []):
                    function_name = func["FunctionName"]
                    function_arn = func["FunctionArn"]

                    # Get tags
                    try:
                        tags_response = client.list_tags(Resource=function_arn)
                        tags = tags_response.get("Tags", {})
                    except:
                        tags = {}

                    # Get provisioned concurrency config from aliases.
                    # Lambda does not support provisioned concurrency on $LATEST.
                    provisioned_concurrency = None
                    provisioned_concurrency_qualifier = None
                    provisioned_concurrency_status = None
                    try:
                        aliases_response = client.list_aliases(
                            FunctionName=function_name
                        )
                        for alias in aliases_response.get("Aliases", []):
                            qualifier = alias.get("Name")
                            if not qualifier:
                                continue
                            try:
                                pc_response = client.get_provisioned_concurrency_config(
                                    FunctionName=function_name, Qualifier=qualifier
                                )
                                requested_pc = pc_response.get(
                                    "RequestedProvisionedConcurrentExecutions"
                                )
                                if requested_pc is not None:
                                    provisioned_concurrency = requested_pc
                                    provisioned_concurrency_qualifier = qualifier
                                    provisioned_concurrency_status = pc_response.get(
                                        "Status"
                                    )
                                    break
                            except (
                                client.exceptions.ProvisionedConcurrencyConfigNotFoundException
                            ):
                                continue
                            except Exception:
                                continue
                    except Exception:
                        pass

                    # Get log group retention
                    log_retention_days = None
                    try:
                        logs_client = (
                            self.session.client("logs", region_name=region)
                            if region
                            else self.session.client("logs")
                        )
                        log_group_name = f"/aws/lambda/{function_name}"
                        log_response = logs_client.describe_log_groups(
                            logGroupNamePrefix=log_group_name, limit=1
                        )
                        if log_response.get("logGroups"):
                            log_retention_days = log_response["logGroups"][0].get(
                                "retentionInDays", "Never expire"
                            )
                    except:
                        pass

                    functions.append(
                        {
                            "resource_id": function_name,
                            "resource_type": "lambda",
                            "resource_name": function_name,
                            "region": region or settings.aws_region,
                            "state": func.get("State", "Active"),
                            "tags": tags,
                            "metadata": {
                                "FunctionArn": function_arn,
                                "Runtime": func.get("Runtime"),
                                "Handler": func.get("Handler"),
                                "CodeSize": func.get("CodeSize"),
                                "Description": func.get("Description", ""),
                                "Timeout": func.get("Timeout"),
                                "MemorySize": func.get("MemorySize"),
                                "LastModified": func.get("LastModified"),
                                "Version": func.get("Version"),
                                "Role": func.get("Role"),
                                "Environment": func.get("Environment", {}).get(
                                    "Variables", {}
                                ),
                                "VpcConfig": func.get("VpcConfig"),
                                "Layers": func.get("Layers", []),
                                "ProvisionedConcurrencyConfig": {
                                    "RequestedProvisionedConcurrentExecutions": provisioned_concurrency,
                                    "Qualifier": provisioned_concurrency_qualifier,
                                    "Status": provisioned_concurrency_status,
                                }
                                if provisioned_concurrency
                                else None,
                                "Architectures": func.get("Architectures", ["x86_64"]),
                                "EphemeralStorage": func.get(
                                    "EphemeralStorage", {}
                                ).get("Size", 512),
                                "log_retention_days": log_retention_days,
                            },
                        }
                    )

            return functions
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def _get_ecs_clusters(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get ECS clusters."""
        try:
            client = (
                self.session.client("ecs", region_name=region)
                if region
                else self.session.client("ecs")
            )
            cluster_arns = []
            paginator = client.get_paginator("list_clusters")
            for page in paginator.paginate():
                cluster_arns.extend(page.get("clusterArns", []))

            clusters: List[Dict[str, Any]] = []
            for i in range(0, len(cluster_arns), 100):
                batch = cluster_arns[i : i + 100]
                response = client.describe_clusters(
                    clusters=batch, include=["SETTINGS", "STATISTICS", "TAGS"]
                )
                for cluster in response.get("clusters", []):
                    cluster_name = cluster.get("clusterName")
                    if not cluster_name:
                        continue
                    tags = {
                        tag.get("key"): tag.get("value")
                        for tag in cluster.get("tags", [])
                        if tag.get("key")
                    }
                    clusters.append(
                        {
                            "resource_id": cluster_name,
                            "resource_type": "ecs_cluster",
                            "resource_name": cluster_name,
                            "region": region or settings.aws_region,
                            "state": cluster.get("status", "ACTIVE"),
                            "tags": tags,
                            "metadata": {
                                "clusterArn": cluster.get("clusterArn"),
                                "registeredContainerInstancesCount": cluster.get(
                                    "registeredContainerInstancesCount", 0
                                ),
                                "runningTasksCount": cluster.get(
                                    "runningTasksCount", 0
                                ),
                                "pendingTasksCount": cluster.get(
                                    "pendingTasksCount", 0
                                ),
                                "activeServicesCount": cluster.get(
                                    "activeServicesCount", 0
                                ),
                                "capacityProviders": cluster.get(
                                    "capacityProviders", []
                                ),
                                "defaultCapacityProviderStrategy": cluster.get(
                                    "defaultCapacityProviderStrategy", []
                                ),
                            },
                        }
                    )
            return clusters
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def _get_ecs_services(
        self, filters: Optional[Dict[str, Any]] = None, region: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get ECS services across all clusters."""
        try:
            client = (
                self.session.client("ecs", region_name=region)
                if region
                else self.session.client("ecs")
            )
            clusters = self._get_ecs_clusters(region=region)
            services: List[Dict[str, Any]] = []

            for cluster in clusters:
                cluster_name = cluster.get("resource_id")
                if not cluster_name:
                    continue
                service_arns = []
                paginator = client.get_paginator("list_services")
                for page in paginator.paginate(cluster=cluster_name):
                    service_arns.extend(page.get("serviceArns", []))
                if not service_arns:
                    continue

                for i in range(0, len(service_arns), 10):
                    batch = service_arns[i : i + 10]
                    response = client.describe_services(
                        cluster=cluster_name, services=batch, include=["TAGS"]
                    )
                    for service in response.get("services", []):
                        if service.get("status") == "INACTIVE":
                            continue
                        service_name = service.get("serviceName")
                        if not service_name:
                            continue

                        tags = {
                            tag.get("key"): tag.get("value")
                            for tag in service.get("tags", [])
                            if tag.get("key")
                        }
                        task_definition_arn = service.get("taskDefinition")
                        task_cpu = None
                        task_memory = None
                        task_family = None
                        task_revision = None
                        task_requires_compatibilities = []
                        try:
                            if task_definition_arn:
                                td = client.describe_task_definition(
                                    taskDefinition=task_definition_arn
                                ).get("taskDefinition", {})
                                task_cpu = td.get("cpu")
                                task_memory = td.get("memory")
                                task_family = td.get("family")
                                task_revision = td.get("revision")
                                task_requires_compatibilities = td.get(
                                    "requiresCompatibilities", []
                                )
                        except Exception:
                            pass

                        services.append(
                            {
                                "resource_id": f"{cluster_name}/{service_name}",
                                "resource_type": "ecs",
                                "resource_name": service_name,
                                "region": region or settings.aws_region,
                                "state": service.get("status", "ACTIVE"),
                                "tags": tags,
                                "metadata": {
                                    "clusterName": cluster_name,
                                    "clusterArn": cluster.get("metadata", {}).get(
                                        "clusterArn"
                                    ),
                                    "serviceName": service_name,
                                    "serviceArn": service.get("serviceArn"),
                                    "desiredCount": service.get("desiredCount", 0),
                                    "runningCount": service.get("runningCount", 0),
                                    "pendingCount": service.get("pendingCount", 0),
                                    "launchType": service.get("launchType"),
                                    "schedulingStrategy": service.get(
                                        "schedulingStrategy"
                                    ),
                                    "loadBalancers": service.get("loadBalancers", []),
                                    "serviceRegistries": service.get(
                                        "serviceRegistries", []
                                    ),
                                    "taskDefinition": task_definition_arn,
                                    "taskCpu": task_cpu,
                                    "taskMemory": task_memory,
                                    "taskFamily": task_family,
                                    "taskRevision": task_revision,
                                    "taskRequiresCompatibilities": task_requires_compatibilities,
                                    "deploymentController": (
                                        service.get("deploymentController") or {}
                                    ).get("type"),
                                },
                            }
                        )
            return services
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    def get_resource_utilization(
        self,
        resource_id: str,
        resource_type: str,
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get utilization metrics for a resource."""
        try:
            if resource_type == "ec2":
                return self._get_ec2_utilization(
                    resource_id, start_date, end_date, region
                )
            elif resource_type == "rds" or resource_type == "rds_instance":
                return self._get_rds_utilization(
                    resource_id, start_date, end_date, region
                )
            elif resource_type == "cloudwatch_alarm":
                return self._get_cloudwatch_alarm_utilization(
                    resource_id, start_date, end_date
                )
            elif resource_type == "cloudwatch_log_group":
                return self._get_cloudwatch_log_group_utilization(
                    resource_id, start_date, end_date
                )
            elif resource_type == "dynamodb" or resource_type == "dynamodb_table":
                return self._get_dynamodb_table_utilization(
                    resource_id, start_date, end_date, region
                )
            elif resource_type == "dynamodb_gsi":
                return self._get_dynamodb_gsi_utilization(
                    resource_id, start_date, end_date
                )
            elif resource_type in {
                "elasticache_replication_group",
                "elasticache_cluster",
            }:
                return self._get_elasticache_utilization(
                    resource_id, resource_type, start_date, end_date, region
                )
            elif resource_type == "ebs":
                return self._get_ebs_utilization(
                    resource_id, start_date, end_date, region
                )
            elif resource_type in {"ecs", "ecs_service"}:
                return self._get_ecs_service_utilization(
                    resource_id, start_date, end_date, region
                )
            elif resource_type == "lambda" or resource_type == "lambda_function":
                return self._get_lambda_utilization(
                    resource_id, start_date, end_date, region
                )
            elif resource_type in {"sagemaker_endpoint", "sagemaker"}:
                return self._get_sagemaker_endpoint_utilization(
                    resource_id, start_date, end_date, region
                )
            elif resource_type == "sagemaker_notebook":
                return self._get_sagemaker_notebook_utilization(
                    resource_id, start_date, end_date, region
                )
            elif resource_type == "sagemaker_training_job":
                return self._get_sagemaker_training_utilization(
                    resource_id, start_date, end_date, region
                )
            else:
                return {}
        except ClientError as e:
            raise Exception(f"AWS API error: {str(e)}")

    @staticmethod
    def _sagemaker_history(
        values: List[Any], maximum_values: Optional[List[Any]] = None
    ) -> Dict[str, Any]:
        """Create local percentile evidence from a native metric window."""
        numbers = []
        for value in values:
            try:
                number = float(value)
                if math.isfinite(number):
                    numbers.append(number)
            except (TypeError, ValueError):
                continue
        maximum = []
        for value in maximum_values if maximum_values is not None else values:
            try:
                number = float(value)
                if math.isfinite(number):
                    maximum.append(number)
            except (TypeError, ValueError):
                continue
        return {
            "timestamps": [],
            "average": numbers,
            "maximum": maximum,
            "p90": list(np.percentile(numbers, 90) for _ in [0]) if numbers else [],
            "p95": list(np.percentile(numbers, 95) for _ in [0]) if numbers else [],
            "p99": list(np.percentile(numbers, 99) for _ in [0]) if numbers else [],
        }

    @staticmethod
    def _sagemaker_scalar_summary(
        values: List[Any], maximum_values: Optional[List[Any]] = None
    ) -> Dict[str, Any]:
        """Return scalar summary fields expected by check consumers."""
        history = AWSAdapter._sagemaker_history(values, maximum_values)
        return {
            "average": float(np.mean(history["average"])) if history["average"] else None,
            "maximum": float(np.max(history["maximum"])) if history["maximum"] else None,
            "p90": float(np.percentile(history["average"], 90)) if history["average"] else None,
            "p95": float(np.percentile(history["average"], 95)) if history["average"] else None,
            "p99": float(np.percentile(history["average"], 99)) if history["average"] else None,
            "sample_count": len(history["average"]),
        }

    def _get_sagemaker_endpoint_utilization(
        self,
        endpoint_name: str,
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Collect native endpoint metrics per production variant."""
        client = self._cloudwatch_client_for_region(region)
        variants = []
        try:
            endpoint = self._sagemaker_client(region).describe_endpoint(
                EndpointName=endpoint_name
            )
            config_name = endpoint.get("EndpointConfigName")
            config = (
                self._sagemaker_client(region).describe_endpoint_config(
                    EndpointConfigName=config_name
                )
                if config_name
                else {}
            )
            current = {
                item.get("VariantName"): item
                for item in endpoint.get("ProductionVariants") or []
            }
            for item in config.get("ProductionVariants") or []:
                name = item.get("VariantName")
                if name:
                    variants.append(name)
            if not variants:
                variants = [name for name in current if name]
        except Exception:
            # The check already has inventory metadata.  Returning an empty
            # variant map makes the native signal unavailable safely.
            variants = []
        if not variants:
            variants = [endpoint_name]

        metric_definitions = {
            "invocations": ("AWS/SageMaker", "Invocations", "Sum"),
            "invocations_per_instance": (
                "AWS/SageMaker", "InvocationsPerInstance", "Sum"
            ),
            "cpuutilization": ("/aws/sagemaker/Endpoints", "CPUUtilization", "Average"),
            "memoryutilization": ("/aws/sagemaker/Endpoints", "MemoryUtilization", "Average"),
            "gpuutilization": ("/aws/sagemaker/Endpoints", "GPUUtilization", "Average"),
            "gpumemoryutilization": ("/aws/sagemaker/Endpoints", "GPUMemoryUtilization", "Average"),
            "gpuutilization_normalized": (
                "/aws/sagemaker/Endpoints", "GPUUtilizationNormalized", "Average"
            ),
        }
        result: Dict[str, Any] = {"variants": {}}
        for variant_name in variants:
            queries = []
            for alias, (namespace, metric, statistic) in metric_definitions.items():
                for suffix, stat in (("average", statistic), ("maximum", "Maximum")):
                    query_id = f"{alias}_{suffix}"
                    queries.append(
                        {
                            "Id": query_id,
                            "MetricStat": {
                                "Metric": {
                                    "Namespace": namespace,
                                    "MetricName": metric,
                                    "Dimensions": [
                                        {"Name": "EndpointName", "Value": endpoint_name},
                                        {"Name": "VariantName", "Value": variant_name},
                                    ],
                                },
                                "Period": derive_cloudwatch_period(start_date, end_date, 60),
                                "Stat": stat,
                            },
                            "ReturnData": True,
                        }
                    )
            try:
                queried = self._query_metric_series(client, queries, start_date, end_date)
            except Exception as exc:
                logger.warning("SageMaker endpoint metric query failed for %s: %s", endpoint_name, exc)
                queried = {}
            variant_result: Dict[str, Any] = {}
            for alias in metric_definitions:
                average_payload = queried.get(f"{alias}_average", {})
                maximum_payload = queried.get(f"{alias}_maximum", {})
                average_values = average_payload.get("values") or []
                maximum_values = maximum_payload.get("values") or []
                history = self._sagemaker_history(average_values, maximum_values)
                status = "usable" if average_values else "unavailable"
                reason = None if average_values else ("query_failed" if not queried else "no_datapoints")
                variant_result[alias] = {
                    "metric_history": history,
                    "metric_summary": {
                        "average": float(np.mean(history["average"])) if history["average"] else None,
                        "maximum": float(np.max(history["maximum"])) if history["maximum"] else None,
                        "p95": float(np.percentile(history["average"], 95)) if history["average"] else None,
                        "sample_count": len(history["average"]),
                        "period_seconds": derive_cloudwatch_period(start_date, end_date, 60),
                    },
                    "metric_status": status,
                    "metric_unavailable_reason": reason,
                    "values": history["average"],
                }
            accelerator_sources = self._sagemaker_accelerator_sources(
                client, endpoint_name, variant_name
            )
            if accelerator_sources:
                enhanced_queries = []
                for index, sources in accelerator_sources.items():
                    query_index = re.sub(r"[^A-Za-z0-9_]", "_", str(index))
                    for alias, source in sources.items():
                        for suffix, stat in (("average", "Average"), ("maximum", "Maximum")):
                            enhanced_queries.append(
                                {
                                    "Id": f"enhanced_{query_index}_{alias}_{suffix}",
                                    "MetricStat": {
                                        "Metric": source,
                                        "Period": derive_cloudwatch_period(start_date, end_date, 60),
                                        "Stat": stat,
                                    },
                                    "ReturnData": True,
                                }
                            )
                try:
                    enhanced = self._query_metric_series(
                        client, enhanced_queries, start_date, end_date
                    )
                except Exception as exc:
                    logger.warning(
                        "SageMaker enhanced GPU query failed for %s/%s: %s",
                        endpoint_name,
                        variant_name,
                        exc,
                    )
                    enhanced = {}
                devices: Dict[str, Dict[str, Any]] = {}
                for index in accelerator_sources:
                    query_index = re.sub(r"[^A-Za-z0-9_]", "_", str(index))
                    gpu_values = enhanced.get(
                        f"enhanced_{query_index}_gpuutilization_average", {}
                    ).get("values", [])
                    gpu_maximum = enhanced.get(
                        f"enhanced_{query_index}_gpuutilization_maximum", {}
                    ).get("values", [])
                    memory_values = enhanced.get(
                        f"enhanced_{query_index}_gpumemoryutilization_average", {}
                    ).get("values", [])
                    if gpu_values:
                        devices[index] = {
                            "values": gpu_values,
                            "maximum": gpu_maximum,
                            "memory": memory_values,
                        }
                if devices:
                    variant_result["accelerator_devices"] = devices
            result["variants"][variant_name] = variant_result
        return result

    @staticmethod
    def _sagemaker_accelerator_sources(
        cloudwatch_client: Any, endpoint_name: str, variant_name: str
    ) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Discover enhanced endpoint GPU sources grouped by AcceleratorId."""
        sources: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for metric_name, alias in (
            ("GPUUtilization", "gpuutilization"),
            ("GPUMemoryUtilization", "gpumemoryutilization"),
        ):
            token: Optional[str] = None
            try:
                while True:
                    request: Dict[str, Any] = {
                        "Namespace": "/aws/sagemaker/Endpoints",
                        "MetricName": metric_name,
                        "Dimensions": [
                            {"Name": "EndpointName", "Value": endpoint_name},
                            {"Name": "VariantName", "Value": variant_name},
                        ],
                    }
                    if token:
                        request["NextToken"] = token
                    response = cloudwatch_client.list_metrics(**request)
                    for metric in response.get("Metrics") or []:
                        dimensions = metric.get("Dimensions") or []
                        accelerator = next(
                            (
                                item.get("Value")
                                for item in dimensions
                                if item.get("Name") == "AcceleratorId"
                            ),
                            None,
                        )
                        if accelerator:
                            sources.setdefault(str(accelerator), {})[alias] = {
                                "Namespace": metric.get("Namespace", "/aws/sagemaker/Endpoints"),
                                "MetricName": metric_name,
                                "Dimensions": dimensions,
                            }
                    token = response.get("NextToken")
                    if not token:
                        break
            except Exception:
                # Enhanced metrics are optional. Base summed metrics remain a
                # valid mean-only path when ListMetrics is unavailable.
                return {}
        return sources

    def _get_sagemaker_notebook_utilization(
        self,
        notebook_name: str,
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Collect discovered CloudWatch-agent metrics for a notebook instance."""
        client = self._cloudwatch_client_for_region(region)
        identity_dimension = "NotebookInstanceName"
        self._discover_ec2_metric_candidates(client, region, identity_dimension)
        cache_key = (
            region or getattr(self, "_default_region", None) or settings.aws_region,
            identity_dimension,
        )

        def agent_history(signal: str, ranker: Any, validator: Any) -> Tuple[Dict[str, Any], Optional[str]]:
            """Fetch the highest-ranked discovered agent series for a signal."""
            cache = getattr(self, f"_ec2_{signal}_metric_cache", {})
            discovered = cache.get(cache_key)
            if discovered is None:
                return self._sagemaker_history([]), "discovery_unavailable"
            candidates = []
            for item in discovered.get(notebook_name, []) or []:
                source = item.get("source") or {}
                candidates.append(source)
            candidates.sort(key=lambda source: (ranker(source.get("metric_name", "")), source.get("metric_name", "")))
            if not candidates:
                return self._sagemaker_history([]), "no_candidates"
            source = candidates[0]
            queries = [
                {
                    "Id": f"notebook_{signal}_{suffix}",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": source["namespace"],
                            "MetricName": source["metric_name"],
                            "Dimensions": source["dimensions"],
                        },
                        "Period": derive_cloudwatch_period(start_date, end_date),
                        "Stat": statistic,
                    },
                    "ReturnData": True,
                }
                for suffix, statistic in (("average", "Average"), ("maximum", "Maximum"))
            ]
            try:
                queried = self._query_metric_series(client, queries, start_date, end_date)
            except Exception:
                return self._sagemaker_history([]), "query_failed"
            average = [validator(value) for value in queried.get(f"notebook_{signal}_average", {}).get("values", [])]
            maximum = [validator(value) for value in queried.get(f"notebook_{signal}_maximum", {}).get("values", [])]
            average = [value for value in average if value is not None]
            maximum = [value for value in maximum if value is not None]
            return self._sagemaker_history(average, maximum), None if average else "no_datapoints"

        cpu, cpu_reason = agent_history("cpu", _agent_cpu_metric_rank, _valid_percent_value)
        memory, memory_reason = agent_history("memory", _memory_metric_rank, _valid_percent_value)
        gpu_devices, gpu_source, gpu_candidates, gpu_reason = self._get_ec2_gpu_signal_series(
            notebook_name,
            start_date,
            end_date,
            client,
            region,
            signal="gpu",
            ranker=_gpu_metric_rank,
            validator=_valid_percent_value,
            identity_dimension=identity_dimension,
        )
        gpu_values_by_timestamp: Dict[str, List[float]] = {}
        for device in gpu_devices.values():
            for timestamp, value in zip(device["timestamps"], device["values"]):
                gpu_values_by_timestamp.setdefault(timestamp, []).append(value)
        gpu_history = {
            "timestamps": sorted(gpu_values_by_timestamp),
            "average": [float(np.mean(gpu_values_by_timestamp[key])) for key in sorted(gpu_values_by_timestamp)],
            "maximum": [float(np.max(gpu_values_by_timestamp[key])) for key in sorted(gpu_values_by_timestamp)],
            "p90": [], "p95": [], "p99": [],
            "gpu_devices": {key: {"timestamps": value["timestamps"], "average": value["values"]} for key, value in gpu_devices.items()},
        }
        return {
            "cpu_metric_status": "usable" if cpu["average"] else "unavailable",
            "cpu_metric_unavailable_reason": None if cpu["average"] else cpu_reason,
            "memory_metric_status": "usable" if memory["average"] else "unavailable",
            "memory_metric_unavailable_reason": None if memory["average"] else memory_reason,
            "gpu_metric_status": "usable" if gpu_history["average"] else "unavailable",
            "gpu_metric_unavailable_reason": None if gpu_history["average"] else gpu_reason,
            "metric_history": {
                "cpuutilization": cpu,
                "memoryutilization": memory,
                "gpuutilization": gpu_history,
                "gpu_devices": gpu_history["gpu_devices"],
            },
            "metric_summary": {
                "cpuutilization": self._sagemaker_scalar_summary(cpu["average"], cpu["maximum"]),
                "memoryutilization": self._sagemaker_scalar_summary(memory["average"], memory["maximum"]),
                "gpuutilization": self._sagemaker_scalar_summary(gpu_history["average"], gpu_history["maximum"]),
            },
            "gpu_metric_source": gpu_source,
            "gpu_candidate_count": gpu_candidates,
        }

    def _get_sagemaker_training_utilization(
        self,
        training_job_name: str,
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Collect native training-job host metrics using SageMaker dimensions."""
        client = self._cloudwatch_client_for_region(region)
        host = f"{training_job_name}/algo-1"
        metrics = {
            "cpuutilization": "CPUUtilization",
            "memoryutilization": "MemoryUtilization",
            "gpuutilization": "GPUUtilization",
            "gpumemoryutilization": "GPUMemoryUtilization",
        }
        output: Dict[str, Any] = {"metric_history": {}, "metric_summary": {}}
        for alias, metric in metrics.items():
            queries = [
                {
                    "Id": f"training_{alias}_{suffix}",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "/aws/sagemaker/TrainingJobs",
                            "MetricName": metric,
                            "Dimensions": [{"Name": "Host", "Value": host}],
                        },
                        "Period": derive_cloudwatch_period(start_date, end_date, 60),
                        "Stat": statistic,
                    },
                    "ReturnData": True,
                }
                for suffix, statistic in (("average", "Average"), ("maximum", "Maximum"))
            ]
            try:
                queried = self._query_metric_series(client, queries, start_date, end_date)
            except Exception:
                queried = {}
            history = self._sagemaker_history(
                queried.get(f"training_{alias}_average", {}).get("values", []),
                queried.get(f"training_{alias}_maximum", {}).get("values", []),
            )
            output["metric_history"][alias] = history
            output["metric_summary"][alias] = history
            output[f"{alias}_metric_status"] = "usable" if history["average"] else "unavailable"
            output[f"{alias}_metric_unavailable_reason"] = None if history["average"] else "no_datapoints"
        return output

    def _get_ecs_service_utilization(
        self,
        resource_id: str,
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get ECS service utilization metrics from CloudWatch."""
        utilization: Dict[str, Any] = {}
        try:
            cluster_name, service_name = resource_id.split("/", 1)
        except ValueError:
            return {
                "cpu_utilization": 0.0,
                "memory_utilization": 0.0,
            }

        cloudwatch_client = (
            self.session.client("cloudwatch", region_name=region)
            if region
            else self.cloudwatch_client
        )
        metrics = ["CPUUtilization", "MemoryUtilization"]
        for metric in metrics:
            try:
                response = cloudwatch_client.get_metric_statistics(
                    Namespace="AWS/ECS",
                    MetricName=metric,
                    Dimensions=[
                        {"Name": "ClusterName", "Value": cluster_name},
                        {"Name": "ServiceName", "Value": service_name},
                    ],
                    StartTime=start_date,
                    EndTime=end_date,
                    Period=3600,
                    Statistics=["Average"],
                )
                datapoints = response.get("Datapoints", [])
                if datapoints:
                    avg_value = sum(dp.get("Average", 0) for dp in datapoints) / len(
                        datapoints
                    )
                    utilization[metric] = avg_value
                    utilization[metric.lower()] = avg_value
                else:
                    utilization[metric] = 0.0
                    utilization[metric.lower()] = 0.0
            except Exception:
                utilization[metric] = 0.0
                utilization[metric.lower()] = 0.0

        utilization["cpu_utilization"] = utilization.get("cpuutilization", 0.0)
        utilization["memory_utilization"] = utilization.get("memoryutilization", 0.0)
        return utilization

    def _discover_ec2_metric_candidates(
        self,
        cloudwatch_client: Any,
        region: Optional[str],
        identity_dimension: str = "InstanceId",
    ) -> None:
        """Populate memory and GPU discovery caches with one paginated sweep.

        CloudWatch ``ListMetrics`` is the expensive part of discovery. The
        signal registry below lets a future metric consumer join this sweep by
        adding a matcher and ranker, rather than copying the pagination loop.
        A failed sweep is cached as ``None`` so a permission failure does not
        cause repeated calls for every instance in a region.
        """
        cache_key = (
            region or getattr(self, "_default_region", None) or settings.aws_region,
            identity_dimension,
        )
        discovery_cache = getattr(self, "_ec2_metric_discovery_cache", None)
        if discovery_cache is None:
            discovery_cache = {}
            self._ec2_metric_discovery_cache = discovery_cache
        if cache_key in discovery_cache:
            return

        signal_definitions = {
            "memory": (is_ec2_memory_metric_name, _memory_metric_rank),
            "cpu": (is_agent_cpu_metric_name, _agent_cpu_metric_rank),
            "gpu": (is_ec2_gpu_utilization_metric_name, _gpu_metric_rank),
            "gpu_memory": (
                is_ec2_gpu_memory_used_metric_name,
                _gpu_memory_metric_rank,
            ),
            "gpu_memory_total": (
                is_ec2_gpu_memory_total_metric_name,
                lambda _name: 0,
            ),
        }
        discovered_by_signal: Dict[
            str, Optional[Dict[str, List[Dict[str, Any]]]]
        ] = {signal: {} for signal in signal_definitions}
        try:
            next_token: Optional[str] = None
            while True:
                request: Dict[str, Any] = {
                    "Dimensions": [{"Name": identity_dimension}],
                }
                if next_token:
                    request["NextToken"] = next_token
                response = cloudwatch_client.list_metrics(**request)
                for metric in response.get("Metrics", []) or []:
                    metric_name = str(metric.get("MetricName") or "")
                    namespace = str(metric.get("Namespace") or "")
                    dimensions = [
                        {
                            "Name": str(dimension.get("Name") or ""),
                            "Value": str(dimension.get("Value") or ""),
                        }
                        for dimension in (metric.get("Dimensions") or [])
                        if dimension.get("Name")
                        and dimension.get("Value") is not None
                    ]
                    instance_dimension = next(
                        (
                            dimension
                            for dimension in dimensions
                            if dimension["Name"].casefold()
                            == identity_dimension.casefold()
                        ),
                        None,
                    )
                    if not namespace or not metric_name or not instance_dimension:
                        continue
                    source = {
                        "namespace": namespace,
                        "metric_name": metric_name,
                        "dimensions": sorted(
                            dimensions,
                            key=lambda item: (item["Name"], item["Value"]),
                        ),
                    }
                    for signal, (matcher, _ranker) in signal_definitions.items():
                        if matcher(metric_name):
                            discovered_by_signal[signal].setdefault(
                                instance_dimension["Value"], []
                            ).append({"source": source, "fallback": False})
                next_token = response.get("NextToken")
                if not next_token:
                    break
        except Exception as exc:
            # Memory retains its fallback query; GPU has no safe fallback because
            # the nvidia plugin is optional and a fabricated series could flag a
            # busy training machine as idle.
            logger.warning(
                "EC2 metric discovery failed in %s: %s", cache_key, exc
            )
            discovered_by_signal = {signal: None for signal in signal_definitions}

        discovery_cache[cache_key] = discovered_by_signal
        self._ec2_memory_metric_cache = getattr(
            self, "_ec2_memory_metric_cache", {}
        )
        self._ec2_gpu_metric_cache = getattr(self, "_ec2_gpu_metric_cache", {})
        self._ec2_memory_metric_cache[cache_key] = discovered_by_signal["memory"]
        self._ec2_gpu_metric_cache[cache_key] = discovered_by_signal["gpu"]
        self._ec2_gpu_memory_metric_cache = getattr(
            self, "_ec2_gpu_memory_metric_cache", {}
        )
        self._ec2_gpu_memory_metric_cache[cache_key] = discovered_by_signal[
            "gpu_memory"
        ]
        self._ec2_gpu_memory_total_metric_cache = getattr(
            self, "_ec2_gpu_memory_total_metric_cache", {}
        )
        self._ec2_gpu_memory_total_metric_cache[cache_key] = discovered_by_signal[
            "gpu_memory_total"
        ]
        self._ec2_cpu_metric_cache = getattr(self, "_ec2_cpu_metric_cache", {})
        self._ec2_cpu_metric_cache[cache_key] = discovered_by_signal["cpu"]

    def _ec2_memory_metric_candidates(
        self,
        instance_id: str,
        cloudwatch_client: Any,
        region: Optional[str],
        identity_dimension: str = "InstanceId",
    ) -> List[Dict[str, Any]]:
        """Discover memory percentage metrics and associate them by InstanceId."""
        cache_key = (
            region or getattr(self, "_default_region", None) or settings.aws_region,
            identity_dimension,
        )
        self._discover_ec2_metric_candidates(
            cloudwatch_client, region, identity_dimension
        )
        cache = getattr(self, "_ec2_memory_metric_cache", {})
        discovered = cache.get(cache_key) or {}
        candidates_by_key: Dict[
            Tuple[str, str, Tuple[Tuple[str, str], ...]], Dict[str, Any]
        ] = {}
        for item in discovered.get(instance_id, []):
            source = item.get("source") or {}
            key = (
                str(source.get("namespace") or ""),
                str(source.get("metric_name") or ""),
                _metric_dimensions_key(source.get("dimensions") or []),
            )
            candidates_by_key.setdefault(key, item)
        candidates = list(candidates_by_key.values())
        fallback_source = {
            "namespace": "CWAgent",
            "metric_name": "mem_used_percent",
            "dimensions": [{"Name": identity_dimension, "Value": instance_id}],
        }
        existing_keys = {
            (
                item["source"]["namespace"],
                item["source"]["metric_name"],
                _metric_dimensions_key(item["source"]["dimensions"]),
            )
            for item in candidates
        }
        fallback_key = (
            fallback_source["namespace"],
            fallback_source["metric_name"],
            _metric_dimensions_key(fallback_source["dimensions"]),
        )
        if fallback_key not in existing_keys:
            candidates.append({"source": fallback_source, "fallback": True})

        candidates.sort(
            key=lambda item: (
                _memory_metric_rank(item["source"]["metric_name"]),
                1 if item.get("fallback") else 0,
                0 if item["source"]["namespace"] == "CWAgent" else 1,
                item["source"]["metric_name"].casefold(),
                _metric_dimensions_key(item["source"]["dimensions"]),
            )
        )
        return candidates

    def _ec2_gpu_signal_metric_candidates(
        self,
        instance_id: str,
        cloudwatch_client: Any,
        region: Optional[str],
        signal: str,
        ranker: Any,
        identity_dimension: str = "InstanceId",
    ) -> List[Dict[str, Any]]:
        """Return one discovered source per device for a GPU signal.

        The ``index`` dimension identifies an addressable GPU. If a publisher
        omits it, the source is treated as device ``"0"``; no synthetic source
        is added when discovery finds nothing.
        """
        cache_key = (
            region or getattr(self, "_default_region", None) or settings.aws_region,
            identity_dimension,
        )
        self._discover_ec2_metric_candidates(
            cloudwatch_client, region, identity_dimension
        )
        cache = getattr(self, f"_ec2_{signal}_metric_cache", {})
        discovered = cache.get(cache_key)
        if discovered is None:
            return []

        candidates_by_device: Dict[str, Dict[str, Any]] = {}
        for item in discovered.get(instance_id, []):
            source = item.get("source") or {}
            device_dimension = next(
                (
                    dimension
                    for dimension in source.get("dimensions") or []
                    if str(dimension.get("Name") or "").casefold() == "index"
                ),
                None,
            )
            device_index = str((device_dimension or {}).get("Value") or "0")
            candidate = {"source": source, "device_index": device_index}
            current = candidates_by_device.get(device_index)
            if current is None or (
                ranker(source.get("metric_name", "")),
                source.get("namespace", "").casefold(),
                source.get("metric_name", "").casefold(),
            ) < (
                ranker(current["source"].get("metric_name", "")),
                current["source"].get("namespace", "").casefold(),
                current["source"].get("metric_name", "").casefold(),
            ):
                candidates_by_device[device_index] = candidate
        return [
            candidates_by_device[index]
            for index in sorted(
                candidates_by_device,
                key=lambda value: (0, int(value)) if value.isdigit() else (1, value),
            )
        ]

    def _ec2_gpu_metric_candidates(
        self,
        instance_id: str,
        cloudwatch_client: Any,
        region: Optional[str],
        identity_dimension: str = "InstanceId",
    ) -> List[Dict[str, Any]]:
        """Return one discovered GPU compute-utilization source per device."""
        return self._ec2_gpu_signal_metric_candidates(
            instance_id,
            cloudwatch_client,
            region,
            "gpu",
            _gpu_metric_rank,
            identity_dimension,
        )

    def _ec2_gpu_memory_metric_candidates(
        self,
        instance_id: str,
        cloudwatch_client: Any,
        region: Optional[str],
        identity_dimension: str = "InstanceId",
    ) -> List[Dict[str, Any]]:
        """Return one discovered GPU VRAM-occupancy source per device."""
        return self._ec2_gpu_signal_metric_candidates(
            instance_id,
            cloudwatch_client,
            region,
            "gpu_memory",
            _gpu_memory_metric_rank,
            identity_dimension,
        )

    def _ec2_gpu_memory_total_metric_candidates(
        self,
        instance_id: str,
        cloudwatch_client: Any,
        region: Optional[str],
        identity_dimension: str = "InstanceId",
    ) -> List[Dict[str, Any]]:
        """Return discovered GPU VRAM-capacity sources as optional evidence."""
        return self._ec2_gpu_signal_metric_candidates(
            instance_id,
            cloudwatch_client,
            region,
            "gpu_memory_total",
            lambda _name: 0,
            identity_dimension,
        )

    @staticmethod
    def _memory_metric_history(
        results: List[Dict[str, Any]], query_prefix: str
    ) -> Tuple[Dict[str, List[Any]], bool]:
        stat_keys = {
            "average": "average",
            "maximum": "maximum",
            "p90": "p90",
            "p95": "p95",
            "p99": "p99",
        }
        results_by_id: Dict[str, Dict[str, Any]] = {}
        for item in results:
            query_id = item.get("Id")
            if not query_id:
                continue
            aggregate = results_by_id.setdefault(
                query_id, {"Timestamps": [], "Values": []}
            )
            aggregate["Timestamps"].extend(item.get("Timestamps") or [])
            aggregate["Values"].extend(item.get("Values") or [])
        history: Dict[str, List[Any]] = {
            "timestamps": [],
            "average": [],
            "maximum": [],
            "p90": [],
            "p95": [],
            "p99": [],
        }
        for stat_name, history_key in stat_keys.items():
            payload = results_by_id.get(f"{query_prefix}_{stat_name}", {})
            timestamps = payload.get("Timestamps") or []
            values = payload.get("Values") or []
            pairs = [
                (timestamp, number)
                for timestamp, value in zip(timestamps, values)
                if (number := _valid_percent_value(value)) is not None
            ]
            history[history_key] = [number for _, number in pairs]
            if not history["timestamps"] and pairs:
                history["timestamps"] = [
                    timestamp.isoformat()
                    if isinstance(timestamp, datetime)
                    else str(timestamp)
                    for timestamp, _ in pairs
                ]
        return history, bool(history["average"])

    def _get_ec2_memory_utilization(
        self,
        instance_id: str,
        start_date: datetime,
        end_date: datetime,
        cloudwatch_client: Any,
        region: Optional[str],
    ) -> Tuple[Dict[str, List[Any]], Optional[Dict[str, Any]]]:
        """Fetch the highest-priority discovered memory series with usable data."""
        for index, candidate in enumerate(
            self._ec2_memory_metric_candidates(instance_id, cloudwatch_client, region)
        ):
            source = candidate["source"]
            query_prefix = "memory" if index == 0 else f"memory_{index}"
            queries = [
                {
                    "Id": f"{query_prefix}_{stat}",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": source["namespace"],
                            "MetricName": source["metric_name"],
                            "Dimensions": source["dimensions"],
                        },
                        "Period": derive_ec2_metric_period(start_date, end_date),
                        "Stat": stat.title()
                        if stat in {"average", "maximum"}
                        else stat,
                    },
                    "ReturnData": True,
                }
                for stat in ("average", "maximum")
            ]
            results: List[Dict[str, Any]] = []
            next_token: Optional[str] = None
            try:
                while True:
                    request: Dict[str, Any] = {
                        "MetricDataQueries": queries,
                        "StartTime": start_date,
                        "EndTime": end_date,
                        "ScanBy": "TimestampDescending",
                    }
                    if next_token:
                        request["NextToken"] = next_token
                    response = cloudwatch_client.get_metric_data(**request)
                    results.extend(response.get("MetricDataResults", []) or [])
                    next_token = response.get("NextToken")
                    if not next_token:
                        break
            except Exception as exc:
                logger.warning(
                    "EC2 memory metric query failed for %s (%s/%s): %s",
                    instance_id,
                    source["namespace"],
                    source["metric_name"],
                    exc,
                )
                continue
            history, has_data = self._memory_metric_history(results, query_prefix)
            if has_data:
                return history, source
        return {
            "timestamps": [],
            "average": [],
            "maximum": [],
            "p90": [],
            "p95": [],
            "p99": [],
        }, None

    @staticmethod
    def _gpu_metric_history(
        results: List[Dict[str, Any]],
        query_to_device: Dict[str, str],
    ) -> Tuple[Dict[str, Any], bool]:
        """Build per-device and across-device GPU histories from query results.

        CloudWatch GPU values are per device. At each timestamp this helper
        computes the mean and maximum over devices that reported that timestamp;
        an empty result remains unavailable rather than becoming zero.
        """
        results_by_id: Dict[str, Dict[str, List[Any]]] = {}
        for item in results:
            query_id = item.get("Id")
            if not query_id or query_id not in query_to_device:
                continue
            aggregate = results_by_id.setdefault(
                query_id, {"Timestamps": [], "Values": []}
            )
            aggregate["Timestamps"].extend(item.get("Timestamps") or [])
            aggregate["Values"].extend(item.get("Values") or [])

        device_histories: Dict[str, Dict[str, List[Any]]] = {}
        values_by_timestamp: Dict[str, List[float]] = {}
        timestamp_order: List[str] = []
        for query_id, device_index in query_to_device.items():
            timestamps: List[str] = []
            values: List[float] = []
            payload = results_by_id.get(query_id, {})
            for timestamp, value in zip(
                payload.get("Timestamps") or [], payload.get("Values") or []
            ):
                number = _valid_percent_value(value)
                if number is None:
                    continue
                timestamp_value = (
                    timestamp.isoformat()
                    if isinstance(timestamp, datetime)
                    else str(timestamp)
                )
                timestamps.append(timestamp_value)
                values.append(number)
                if timestamp_value not in values_by_timestamp:
                    values_by_timestamp[timestamp_value] = []
                    timestamp_order.append(timestamp_value)
                values_by_timestamp[timestamp_value].append(number)
            device_histories[device_index] = {
                "timestamps": timestamps,
                "average": values,
            }

        across_average = [
            float(np.mean(values)) for timestamp in timestamp_order
            if (values := values_by_timestamp[timestamp])
        ]
        across_maximum = [
            float(np.max(values)) for timestamp in timestamp_order
            if (values := values_by_timestamp[timestamp])
        ]
        return (
            {
                "timestamps": timestamp_order,
                "average": across_average,
                # CPU/memory maximum is CloudWatch's Maximum statistic over a
                # period. GPU maximum is the across-device maximum of per-device
                # Averages: different derivation, same busiest-signal meaning.
                "maximum": across_maximum,
                "p90": [],
                "p95": [],
                "p99": [],
                "gpu_devices": device_histories,
            },
            bool(across_average),
        )

    def _get_ec2_gpu_utilization(
        self,
        instance_id: str,
        start_date: datetime,
        end_date: datetime,
        cloudwatch_client: Any,
        region: Optional[str],
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], int, Optional[str]]:
        """Fetch discovered GPU averages and return the legacy check-path shape."""
        devices, source, candidate_count, reason = self._get_ec2_gpu_signal_series(
            instance_id,
            start_date,
            end_date,
            cloudwatch_client,
            region,
            signal="gpu",
            ranker=_gpu_metric_rank,
            validator=_valid_percent_value,
        )
        values_by_timestamp: Dict[str, List[float]] = {}
        for payload in devices.values():
            for timestamp, value in zip(payload["timestamps"], payload["values"]):
                values_by_timestamp.setdefault(timestamp, []).append(value)
        timestamps = sorted(values_by_timestamp)
        average = [float(np.mean(values_by_timestamp[item])) for item in timestamps]
        maximum = [float(np.max(values_by_timestamp[item])) for item in timestamps]
        history = {
            "timestamps": timestamps,
            "average": average,
            "maximum": maximum,
            "p90": [],
            "p95": [],
            "p99": [],
            "gpu_devices": {
                device_index: {
                    "timestamps": payload["timestamps"],
                    "average": payload["values"],
                }
                for device_index, payload in devices.items()
            },
        }
        return history, source, candidate_count, (None if average else reason)

    def _get_ec2_gpu_signal_series(
        self,
        instance_id: str,
        start_date: datetime,
        end_date: datetime,
        cloudwatch_client: Any,
        region: Optional[str],
        *,
        signal: str,
        ranker: Any,
        validator: Any,
        identity_dimension: str = "InstanceId",
    ) -> Tuple[Dict[str, Dict[str, Any]], Optional[Dict[str, Any]], int, Optional[str]]:
        """Fetch one discovered per-device GPU signal with native-unit validation."""
        candidates = self._ec2_gpu_signal_metric_candidates(
            instance_id,
            cloudwatch_client,
            region,
            signal,
            ranker,
            identity_dimension,
        )
        cache_key = (
            region or getattr(self, "_default_region", None) or settings.aws_region,
            identity_dimension,
        )
        discovery_cache = getattr(self, f"_ec2_{signal}_metric_cache", {})
        if not candidates:
            reason = (
                "discovery_unavailable"
                if discovery_cache.get(cache_key) is None
                else "no_candidates"
            )
            return {}, None, 0, reason

        queries = []
        query_to_candidate: Dict[str, Dict[str, Any]] = {}
        for position, candidate in enumerate(candidates):
            query_id = (
                f"gpu_{position}_average"
                if signal == "gpu"
                else f"gpu_{signal}_{position}"
            )
            query_to_candidate[query_id] = candidate
            source = candidate["source"]
            queries.append(
                {
                    "Id": query_id,
                    "MetricStat": {
                        "Metric": {
                            "Namespace": source["namespace"],
                            "MetricName": source["metric_name"],
                            "Dimensions": source["dimensions"],
                        },
                        "Period": derive_ec2_metric_period(start_date, end_date),
                        "Stat": "Average",
                    },
                    "ReturnData": True,
                }
            )
        try:
            results = self._query_metric_series(
                cloudwatch_client, queries, start_date, end_date
            )
        except Exception as exc:
            logger.warning(
                "EC2 GPU %s query failed for %s: %s", signal, instance_id, exc
            )
            return {}, None, len(candidates), "query_failed"

        devices: Dict[str, Dict[str, Any]] = {}
        for query_id, candidate in query_to_candidate.items():
            payload = results.get(query_id, {})
            pairs = [
                (timestamp, number)
                for timestamp, value in zip(
                    payload.get("timestamps") or [], payload.get("values") or []
                )
                if (number := validator(value)) is not None
            ]
            if not pairs:
                continue
            device_index = candidate["device_index"]
            devices[device_index] = {
                "timestamps": [timestamp for timestamp, _ in pairs],
                "values": [number for _, number in pairs],
                "source": candidate["source"],
            }
        source = next(iter(devices.values()), {}).get("source")
        return devices, source, len(candidates), (None if devices else "no_datapoints")

    def _get_ec2_utilization(
        self,
        instance_id: str,
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get EC2 instance utilization metrics."""
        query_groups = [
            ("AWS/EC2", ["CPUUtilization", "NetworkIn", "NetworkOut"]),
        ]
        metric_aliases = {
            "CPUUtilization": "cpuutilization",
            "NetworkIn": "networkin",
            "NetworkOut": "networkout",
        }
        utilization = {
            "cpuutilization": 0.0,
            "networkin": 0.0,
            "networkout": 0.0,
            "memoryutilization": 0.0,
            "memory_metric_status": "unavailable",
            "gpuutilization": None,
            "gpu_metric_status": "unavailable",
            "gpu_metric_source": None,
            "gpu_metric_unavailable_reason": "no_candidates",
            "metric_history": {
                "cpuutilization": {
                    "timestamps": [],
                    "average": [],
                    "maximum": [],
                    "p90": [],
                    "p95": [],
                    "p99": [],
                },
                "networkin": {
                    "timestamps": [],
                    "average": [],
                    "maximum": [],
                    "p90": [],
                    "p95": [],
                    "p99": [],
                },
                "networkout": {
                    "timestamps": [],
                    "average": [],
                    "maximum": [],
                    "p90": [],
                    "p95": [],
                    "p99": [],
                },
                "memoryutilization": {
                    "timestamps": [],
                    "average": [],
                    "maximum": [],
                    "p90": [],
                    "p95": [],
                    "p99": [],
                },
                "gpuutilization": {
                    "timestamps": [],
                    "average": [],
                    "maximum": [],
                    "p90": [],
                    "p95": [],
                    "p99": [],
                },
                "gpu_devices": {},
            },
        }

        def build_query_id(metric_name: str, stat_name: str) -> str:
            prefixes = {
                "CPUUtilization": "cpu",
                "NetworkIn": "network_in",
                "NetworkOut": "network_out",
            }
            return f"{prefixes[metric_name]}_{stat_name.lower()}"

        def parse_metric_data_results(
            results: List[Dict[str, Any]], namespace_metrics: List[str]
        ) -> None:
            results_by_id: Dict[str, Dict[str, Any]] = {}
            for item in results:
                query_id = item.get("Id")
                if not query_id:
                    continue
                aggregate = results_by_id.setdefault(
                    query_id, {"Timestamps": [], "Values": []}
                )
                # CloudWatch returns matching timestamp/value positions. Extend
                # both lists page-by-page to preserve descending ScanBy order.
                aggregate["Timestamps"].extend(item.get("Timestamps") or [])
                aggregate["Values"].extend(item.get("Values") or [])
            stat_keys = {"Average": "average", "Maximum": "maximum"}
            for metric_name in namespace_metrics:
                alias = metric_aliases[metric_name]
                history = utilization["metric_history"][alias]
                timestamps = None
                for stat_name, history_key in stat_keys.items():
                    payload = results_by_id.get(
                        build_query_id(metric_name, stat_name), {}
                    )
                    values = payload.get("Values", []) or []
                    result_timestamps = payload.get("Timestamps", []) or []
                    history[history_key] = values
                    if timestamps is None:
                        timestamps = result_timestamps
                history["timestamps"] = [
                    ts.isoformat() if isinstance(ts, datetime) else str(ts)
                    for ts in (timestamps or [])
                ]
                average_values = history["average"]
                utilization[alias] = (
                    sum(average_values) / len(average_values) if average_values else 0.0
                )

        # Use instance region if provided, otherwise use the default region
        cloudwatch_client = self.cloudwatch_client
        if region:
            cloudwatch_client = self.session.client(
                "cloudwatch", region_name=region
            )

        for namespace, metrics in query_groups:
            try:
                queries = []
                for metric in metrics:
                    for stat in ["Average", "Maximum"]:
                        queries.append(
                            {
                                "Id": build_query_id(metric, stat),
                                "MetricStat": {
                                    "Metric": {
                                        "Namespace": namespace,
                                        "MetricName": metric,
                                        "Dimensions": [
                                            {"Name": "InstanceId", "Value": instance_id}
                                        ],
                                    },
                                    "Period": derive_ec2_metric_period(start_date, end_date),
                                    "Stat": stat,
                                },
                                "ReturnData": True,
                            }
                        )
                results: List[Dict[str, Any]] = []
                next_token: Optional[str] = None
                while True:
                    request: Dict[str, Any] = {
                        "MetricDataQueries": queries,
                        "StartTime": start_date,
                        "EndTime": end_date,
                        "ScanBy": "TimestampDescending",
                    }
                    if next_token:
                        request["NextToken"] = next_token
                    response = cloudwatch_client.get_metric_data(**request)
                    results.extend(response.get("MetricDataResults", []) or [])
                    next_token = response.get("NextToken")
                    if not next_token:
                        break
                parse_metric_data_results(results, metrics)
            except ClientError as e:
                print(
                    f"[CLOUDWATCH] Error getting metric data for {instance_id} in {namespace}: {e}"
                )
                for metric in metrics:
                    alias = metric_aliases[metric]
                    utilization[alias] = 0.0
                    utilization["metric_history"][alias] = {
                        "timestamps": [],
                        "average": [],
                        "maximum": [],
                        "p90": [],
                        "p95": [],
                        "p99": [],
                    }

        memory_history, memory_source = self._get_ec2_memory_utilization(
            instance_id,
            start_date,
            end_date,
            cloudwatch_client,
            region,
        )
        utilization["metric_history"]["memoryutilization"] = memory_history
        utilization["memoryutilization"] = (
            sum(memory_history["average"]) / len(memory_history["average"])
            if memory_history["average"]
            else 0.0
        )
        utilization["memory_metric_status"] = (
            "usable" if memory_history["average"] else "unavailable"
        )
        if memory_source:
            utilization["memory_metric_source"] = memory_source

        gpu_history, gpu_source, gpu_device_count, gpu_unavailable_reason = (
            self._get_ec2_gpu_utilization(
                instance_id,
                start_date,
                end_date,
                cloudwatch_client,
                region,
            )
        )
        gpu_devices = gpu_history.pop("gpu_devices", {})
        utilization["metric_history"]["gpuutilization"] = gpu_history
        utilization["metric_history"]["gpu_devices"] = gpu_devices
        utilization["gpu_metric_status"] = (
            "usable" if gpu_history["average"] else "unavailable"
        )
        utilization["gpu_metric_unavailable_reason"] = (
            None if gpu_history["average"] else gpu_unavailable_reason
        )
        if gpu_source:
            utilization["gpu_metric_source"] = gpu_source
        if gpu_history["average"]:
            # This scalar intentionally means the mean of the busiest-device series.
            utilization["gpuutilization"] = float(np.mean(gpu_history["maximum"]))

        period_seconds = derive_ec2_metric_period(start_date, end_date)
        summary_inputs = {
            alias: utilization["metric_history"][alias]["average"]
            for alias in [*metric_aliases.values(), "memoryutilization"]
        }
        # GPU percentiles deliberately use the across-device maximum series:
        # p95 means the busiest device was idle 95% of the window.
        summary_inputs["gpuutilization"] = utilization["metric_history"][
            "gpuutilization"
        ]["maximum"]
        for alias in [*metric_aliases.values(), "memoryutilization", "gpuutilization"]:
            history = utilization["metric_history"][alias]
            average_values = history["average"]
            maximum_values = history["maximum"]
            percentile_values = summary_inputs[alias]
            utilization.setdefault("metric_summary", {})[alias] = {
                "average": float(np.mean(average_values))
                if average_values
                else None,
                "maximum": float(np.max(maximum_values))
                if maximum_values
                else None,
                # Detailed monitoring averages each five-minute bucket first;
                # V1 accepts percentiles over those smoothed values.
                "p90": float(np.percentile(percentile_values, 90))
                if percentile_values
                else None,
                "p95": float(np.percentile(percentile_values, 95))
                if percentile_values
                else None,
                "p99": float(np.percentile(percentile_values, 99))
                if percentile_values
                else None,
                "sample_count": len(percentile_values),
                "period_seconds": period_seconds,
            }
            if alias == "gpuutilization":
                # For gpuutilization, percentiles are over the busiest-device series; average is over the device-mean series.
                summary = utilization["metric_summary"][alias]
                summary["device_count_observed"] = gpu_device_count

        return utilization

    def get_ec2_rightsizing_metrics(
        self,
        instance_id: str,
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None,
        period_seconds: int = 300,
    ) -> Dict[str, Any]:
        """Collect raw period-aware metrics used by the EC2 rightsizer.

        Counter metrics intentionally use ``Sum``. Capacity interpretation is
        performed later by the rightsizer, never in this AWS adapter.
        """
        period_seconds = derive_ec2_metric_period(
            start_date, end_date, period_seconds
        )
        query_region = region or settings.aws_region
        client = (
            self.session.client("cloudwatch", region_name=query_region)
            if region
            else self.cloudwatch_client
        )

        memory_candidates = self._ec2_memory_metric_candidates(
            instance_id, client, region
        )
        memory_source = memory_candidates[0]["source"]
        gpu_utilization_devices, gpu_utilization_source, _gpu_utilization_count, gpu_utilization_reason = (
            self._get_ec2_gpu_signal_series(
                instance_id,
                start_date,
                end_date,
                client,
                region,
                signal="gpu",
                ranker=_gpu_metric_rank,
                validator=_valid_percent_value,
            )
        )
        gpu_memory_devices, gpu_memory_source, _gpu_memory_count, gpu_memory_reason = (
            self._get_ec2_gpu_signal_series(
                instance_id,
                start_date,
                end_date,
                client,
                region,
                signal="gpu_memory",
                ranker=_gpu_memory_metric_rank,
                validator=_valid_non_negative_value,
            )
        )
        gpu_total_devices, gpu_total_source, _gpu_total_count, _gpu_total_reason = (
            self._get_ec2_gpu_signal_series(
                instance_id,
                start_date,
                end_date,
                client,
                region,
                signal="gpu_memory_total",
                ranker=lambda _name: 0,
                validator=_valid_non_negative_value,
            )
        )
        definitions = {
            "cpu_percent": ("AWS/EC2", "CPUUtilization", "Average"),
            "network_in_bytes": ("AWS/EC2", "NetworkIn", "Sum"),
            "network_out_bytes": ("AWS/EC2", "NetworkOut", "Sum"),
            "network_packets_in": ("AWS/EC2", "NetworkPacketsIn", "Sum"),
            "network_packets_out": ("AWS/EC2", "NetworkPacketsOut", "Sum"),
            "ebs_read_operations": ("AWS/EC2", "EBSReadOps", "Sum"),
            "ebs_write_operations": ("AWS/EC2", "EBSWriteOps", "Sum"),
            "ebs_read_bytes": ("AWS/EC2", "EBSReadBytes", "Sum"),
            "ebs_write_bytes": ("AWS/EC2", "EBSWriteBytes", "Sum"),
            "instance_ebs_iops_exceeded": (
                "AWS/EC2",
                "InstanceEBSIOPSExceededCheck",
                "Maximum",
            ),
            "instance_ebs_throughput_exceeded": (
                "AWS/EC2",
                "InstanceEBSThroughputExceededCheck",
                "Maximum",
            ),
            "ebs_io_balance_percent": ("AWS/EC2", "EBSIOBalance%", "Minimum"),
            "ebs_byte_balance_percent": ("AWS/EC2", "EBSByteBalance%", "Minimum"),
            "memory_percent": (
                memory_source["namespace"],
                memory_source["metric_name"],
                "Average",
            ),
            "bw_in_allowance_exceeded": ("CWAgent", "ethtool_bw_in_allowance_exceeded", "Sum"),
            "bw_out_allowance_exceeded": (
                "CWAgent",
                "ethtool_bw_out_allowance_exceeded",
                "Sum",
            ),
            "pps_allowance_exceeded": ("CWAgent", "ethtool_pps_allowance_exceeded", "Sum"),
            "conntrack_allowance_exceeded": (
                "CWAgent",
                "ethtool_conntrack_allowance_exceeded",
                "Sum",
            ),
        }
        queries = [
            {
                "Id": key,
                "MetricStat": {
                    "Metric": {
                        "Namespace": namespace,
                        "MetricName": metric,
                        "Dimensions": (
                            memory_source["dimensions"]
                            if key == "memory_percent"
                            else [{"Name": "InstanceId", "Value": instance_id}]
                        ),
                    },
                    "Period": period_seconds,
                    "Stat": statistic,
                },
                "ReturnData": True,
            }
            for key, (namespace, metric, statistic) in definitions.items()
        ]
        collected: Dict[str, Dict[str, Any]] = {
            key: {
                "timestamps": [],
                "values": [],
                "period_seconds": period_seconds,
                "statistic": definition[2],
            }
            for key, definition in definitions.items()
        }
        collected["memory_percent"]["source"] = memory_source
        next_token: Optional[str] = None
        while True:
            request: Dict[str, Any] = {
                "MetricDataQueries": queries,
                "StartTime": start_date,
                "EndTime": end_date,
                "ScanBy": "TimestampAscending",
                "MaxDatapoints": 100800,
            }
            if next_token:
                request["NextToken"] = next_token
            response = client.get_metric_data(**request)
            for result in response.get("MetricDataResults", []):
                key = result.get("Id")
                if key not in collected:
                    continue
                collected[key]["timestamps"].extend(
                    item.isoformat() if isinstance(item, datetime) else str(item)
                    for item in (result.get("Timestamps") or [])
                )
                collected[key]["values"].extend(result.get("Values") or [])
            next_token = response.get("NextToken")
            if not next_token:
                break

        def sanitize_memory_payload(payload: Dict[str, Any]) -> bool:
            pairs = [
                (timestamp, number)
                for timestamp, value in zip(
                    payload.get("timestamps") or [], payload.get("values") or []
                )
                if (number := _valid_percent_value(value)) is not None
            ]
            payload["timestamps"] = [
                item.isoformat() if isinstance(item, datetime) else str(item)
                for item, _ in pairs
            ]
            payload["values"] = [number for _, number in pairs]
            return bool(pairs)

        if not sanitize_memory_payload(collected["memory_percent"]):
            # A preferred metric can be present in ListMetrics but empty for the
            # requested window. Probe lower-ranked candidates one at a time.
            for candidate in memory_candidates[1:]:
                source = candidate["source"]
                next_payload: Dict[str, Any] = {
                    "timestamps": [],
                    "values": [],
                    "period_seconds": period_seconds,
                    "statistic": "Average",
                    "source": source,
                }
                next_token = None
                try:
                    while True:
                        request = {
                            "MetricDataQueries": [
                                {
                                    "Id": "memory_percent",
                                    "MetricStat": {
                                        "Metric": {
                                            "Namespace": source["namespace"],
                                            "MetricName": source["metric_name"],
                                            "Dimensions": source["dimensions"],
                                        },
                                        "Period": period_seconds,
                                        "Stat": "Average",
                                    },
                                    "ReturnData": True,
                                }
                            ],
                            "StartTime": start_date,
                            "EndTime": end_date,
                            "ScanBy": "TimestampAscending",
                            "MaxDatapoints": 100800,
                        }
                        if next_token:
                            request["NextToken"] = next_token
                        response = client.get_metric_data(**request)
                        for result in response.get("MetricDataResults", []) or []:
                            if result.get("Id") != "memory_percent":
                                continue
                            next_payload["timestamps"].extend(
                                result.get("Timestamps") or []
                            )
                            next_payload["values"].extend(result.get("Values") or [])
                        next_token = response.get("NextToken")
                        if not next_token:
                            break
                except Exception as exc:
                    logger.warning(
                        "EC2 memory fallback query failed for %s (%s/%s): %s",
                        instance_id,
                        source["namespace"],
                        source["metric_name"],
                        exc,
                    )
                    continue
                if sanitize_memory_payload(next_payload):
                    collected["memory_percent"] = next_payload
                    break
        gpu_devices: Dict[str, Dict[str, Any]] = {}
        for device_index, payload in gpu_utilization_devices.items():
            gpu_devices.setdefault(device_index, {})["utilization_gpu"] = payload
        for device_index, payload in gpu_memory_devices.items():
            gpu_devices.setdefault(device_index, {})["memory_used"] = payload
        for device_index, payload in gpu_total_devices.items():
            gpu_devices.setdefault(device_index, {})["memory_total"] = payload

        usable_device_indexes = set(gpu_utilization_devices) & set(gpu_memory_devices)
        gpu_reason = (
            gpu_utilization_reason
            if gpu_utilization_reason in {"discovery_unavailable", "query_failed"}
            else gpu_memory_reason
            if gpu_memory_reason in {"discovery_unavailable", "query_failed"}
            else gpu_utilization_reason or gpu_memory_reason
        )
        gpu_metric_status = "usable" if usable_device_indexes else "unavailable"
        gpu_source = gpu_utilization_source
        collected["gpu_devices"] = gpu_devices
        return {
            "period_seconds": period_seconds,
            "metrics": collected,
            "gpu_metric_status": gpu_metric_status,
            "gpu_metric_unavailable_reason": None if gpu_metric_status == "usable" else gpu_reason,
            "gpu_metric_source": gpu_source,
            "gpu_memory_metric_source": gpu_memory_source,
            "gpu_memory_total_metric_source": gpu_total_source,
            "gpu_device_count_observed": len(usable_device_indexes),
        }

    def _asg_memory_metric_candidates(
        self, asg_name: str, cloudwatch_client: Any
    ) -> List[Dict[str, Any]]:
        discovered: List[Dict[str, Any]] = []
        next_token: Optional[str] = None
        try:
            while True:
                request: Dict[str, Any] = {
                    "Dimensions": [
                        {"Name": "AutoScalingGroupName", "Value": asg_name}
                    ]
                }
                if next_token:
                    request["NextToken"] = next_token
                response = cloudwatch_client.list_metrics(**request)
                for metric in response.get("Metrics") or []:
                    metric_name = str(metric.get("MetricName") or "")
                    namespace = str(metric.get("Namespace") or "")
                    dimensions = [
                        {
                            "Name": str(item.get("Name") or ""),
                            "Value": str(item.get("Value") or ""),
                        }
                        for item in metric.get("Dimensions") or []
                        if item.get("Name") and item.get("Value") is not None
                    ]
                    association = next(
                        (
                            item
                            for item in dimensions
                            if item["Name"].casefold() == "autoscalinggroupname"
                            and item["Value"] == asg_name
                        ),
                        None,
                    )
                    if (
                        namespace
                        and association
                        and is_ec2_memory_metric_name(metric_name)
                    ):
                        discovered.append(
                            {
                                "source": {
                                    "kind": "group",
                                    "namespace": namespace,
                                    "metric_name": metric_name,
                                    "dimensions": sorted(
                                        dimensions,
                                        key=lambda item: (item["Name"], item["Value"]),
                                    ),
                                },
                                "fallback": False,
                            }
                        )
                next_token = response.get("NextToken")
                if not next_token:
                    break
        except Exception as exc:
            logger.warning("ASG memory discovery failed for %s: %s", asg_name, exc)
        fallback = {
            "source": {
                "kind": "group",
                "namespace": "CWAgent",
                "metric_name": "mem_used_percent",
                "dimensions": [
                    {"Name": "AutoScalingGroupName", "Value": asg_name}
                ],
            },
            "fallback": True,
        }
        unique: Dict[
            Tuple[str, str, Tuple[Tuple[str, str], ...]], Dict[str, Any]
        ] = {}
        for item in [*discovered, fallback]:
            source = item["source"]
            key = (
                source["namespace"],
                source["metric_name"],
                _metric_dimensions_key(source["dimensions"]),
            )
            unique.setdefault(key, item)
        candidates = list(unique.values())
        candidates.sort(
            key=lambda item: (
                _memory_metric_rank(item["source"]["metric_name"]),
                1 if item.get("fallback") else 0,
                0 if item["source"]["namespace"] == "CWAgent" else 1,
                item["source"]["metric_name"].casefold(),
                _metric_dimensions_key(item["source"]["dimensions"]),
            )
        )
        return candidates

    def _cloudwatch_client_for_region(self, region: Optional[str]):
        query_region = region or settings.aws_region
        return (
            self.session.client("cloudwatch", region_name=query_region)
            if region
            else self.cloudwatch_client
        )

    @staticmethod
    def _query_metric_series(
        client: Any,
        queries: List[Dict[str, Any]],
        start_date: datetime,
        end_date: datetime,
    ) -> Dict[str, Dict[str, List[Any]]]:
        collected = {
            str(query["Id"]): {"timestamps": [], "values": []} for query in queries
        }
        next_token: Optional[str] = None
        while True:
            request: Dict[str, Any] = {
                "MetricDataQueries": queries,
                "StartTime": start_date,
                "EndTime": end_date,
                "ScanBy": "TimestampAscending",
                "MaxDatapoints": 100800,
            }
            if next_token:
                request["NextToken"] = next_token
            response = client.get_metric_data(**request)
            for result in response.get("MetricDataResults") or []:
                key = str(result.get("Id") or "")
                if key not in collected:
                    continue
                collected[key]["timestamps"].extend(result.get("Timestamps") or [])
                collected[key]["values"].extend(result.get("Values") or [])
            next_token = response.get("NextToken")
            if not next_token:
                break
        for payload in collected.values():
            by_timestamp: Dict[str, Any] = {}
            for item, value in zip(payload["timestamps"], payload["values"]):
                key = item.isoformat() if isinstance(item, datetime) else str(item)
                by_timestamp[key] = value
            ordered = sorted(by_timestamp.items())
            payload["timestamps"] = [timestamp for timestamp, _ in ordered]
            payload["values"] = [value for _, value in ordered]
        return collected

    def get_asg_rightsizing_metrics(
        self,
        asg_name: str,
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None,
        period_seconds: int = 300,
    ) -> Dict[str, Any]:
        """Collect the minimal group series used by the ASG rightsizer."""
        if period_seconds <= 0:
            raise ValueError("period_seconds must be positive")
        client = self._cloudwatch_client_for_region(region)
        dimension = [{"Name": "AutoScalingGroupName", "Value": asg_name}]
        base = {
            "cpu_percent": ("AWS/EC2", "CPUUtilization", "Average"),
            "desired_capacity": (
                "AWS/AutoScaling",
                "GroupDesiredCapacity",
                "Maximum",
            ),
            "in_service_instances": (
                "AWS/AutoScaling",
                "GroupInServiceInstances",
                "Maximum",
            ),
        }
        queries = [
            {
                "Id": key,
                "MetricStat": {
                    "Metric": {
                        "Namespace": namespace,
                        "MetricName": name,
                        "Dimensions": dimension,
                    },
                    "Period": period_seconds,
                    "Stat": statistic,
                },
                "ReturnData": True,
            }
            for key, (namespace, name, statistic) in base.items()
        ]
        series = self._query_metric_series(client, queries, start_date, end_date)
        metrics: Dict[str, Dict[str, Any]] = {
            key: {
                **series[key],
                "period_seconds": period_seconds,
                "statistic": definition[2],
            }
            for key, definition in base.items()
        }
        memory_payload: Dict[str, Any] = {
            "timestamps": [],
            "values": [],
            "period_seconds": period_seconds,
            "statistic": "Average",
            "source": None,
        }
        for candidate in self._asg_memory_metric_candidates(asg_name, client):
            source = candidate["source"]
            query = {
                "Id": "memory_percent",
                "MetricStat": {
                    "Metric": {
                        "Namespace": source["namespace"],
                        "MetricName": source["metric_name"],
                        "Dimensions": source["dimensions"],
                    },
                    "Period": period_seconds,
                    "Stat": "Average",
                },
                "ReturnData": True,
            }
            try:
                payload = self._query_metric_series(
                    client, [query], start_date, end_date
                )["memory_percent"]
            except Exception as exc:
                logger.warning(
                    "ASG memory query failed for %s (%s/%s): %s",
                    asg_name,
                    source["namespace"],
                    source["metric_name"],
                    exc,
                )
                continue
            pairs = [
                (timestamp, number)
                for timestamp, value in zip(
                    payload.get("timestamps") or [], payload.get("values") or []
                )
                if (number := _valid_percent_value(value)) is not None
            ]
            if pairs:
                memory_payload.update(
                    timestamps=[timestamp for timestamp, _ in pairs],
                    values=[value for _, value in pairs],
                    source=source,
                )
                break
        metrics["memory_percent"] = memory_payload
        return {"period_seconds": period_seconds, "metrics": metrics}

    def get_asg_member_memory_metrics(
        self,
        instance_ids: List[str],
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None,
        period_seconds: int = 300,
    ) -> Dict[str, Any]:
        """Collect exact member memory series for a previously proven stable group."""
        client = self._cloudwatch_client_for_region(region)
        members: Dict[str, Dict[str, Any]] = {}
        for instance_id in sorted(set(instance_ids)):
            for candidate in self._ec2_memory_metric_candidates(
                instance_id, client, region
            ):
                source = candidate["source"]
                query = {
                    "Id": "memory_percent",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": source["namespace"],
                            "MetricName": source["metric_name"],
                            "Dimensions": source["dimensions"],
                        },
                        "Period": period_seconds,
                        "Stat": "Average",
                    },
                    "ReturnData": True,
                }
                try:
                    payload = self._query_metric_series(
                        client, [query], start_date, end_date
                    )["memory_percent"]
                except Exception:
                    continue
                pairs = [
                    (timestamp, number)
                    for timestamp, value in zip(
                        payload.get("timestamps") or [], payload.get("values") or []
                    )
                if (number := _valid_percent_value(value)) is not None
                ]
                if pairs:
                    members[instance_id] = {
                        "timestamps": [timestamp for timestamp, _ in pairs],
                        "values": [value for _, value in pairs],
                        "source": source,
                    }
                    break
        return {"period_seconds": period_seconds, "members": members}

    def get_elasticache_rightsizing_metrics(
        self,
        cache_cluster_ids: List[str],
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None,
        period_seconds: int = 300,
        include_cpu_credits: bool = False,
    ) -> Dict[str, Any]:
        """Collect raw per-node ElastiCache telemetry for local aggregation."""
        if period_seconds <= 0:
            raise ValueError("period_seconds must be positive")
        query_region = region or settings.aws_region
        client = (
            self.session.client("cloudwatch", region_name=query_region)
            if region
            else self.cloudwatch_client
        )
        definitions = {
            "engine_cpu_percent": ("EngineCPUUtilization", "Average"),
            "host_cpu_percent": ("CPUUtilization", "Average"),
            "memory_percent": ("DatabaseMemoryUsagePercentage", "Average"),
            "bytes_used_for_cache": ("BytesUsedForCache", "Average"),
            "freeable_memory_bytes": ("FreeableMemory", "Average"),
            "network_in_bytes": ("NetworkBytesIn", "Sum"),
            "network_out_bytes": ("NetworkBytesOut", "Sum"),
            "evictions": ("Evictions", "Sum"),
            "get_type_cmds": ("GetTypeCmds", "Sum"),
            "set_type_cmds": ("SetTypeCmds", "Sum"),
            "swap_usage_bytes": ("SwapUsage", "Average"),
            "replication_lag_seconds": ("ReplicationLag", "Average"),
            "curr_connections": ("CurrConnections", "Average"),
            "is_master": ("IsMaster", "Average"),
            "traffic_management_active": ("TrafficManagementActive", "Maximum"),
            "network_bw_in_allowance_exceeded": (
                "NetworkBandwidthInAllowanceExceeded",
                "Sum",
            ),
            "network_bw_out_allowance_exceeded": (
                "NetworkBandwidthOutAllowanceExceeded",
                "Sum",
            ),
            "network_packets_per_second_allowance_exceeded": (
                "NetworkPacketsPerSecondAllowanceExceeded",
                "Sum",
            ),
            "network_conntrack_allowance_exceeded": (
                "NetworkConntrackAllowanceExceeded",
                "Sum",
            ),
        }
        if include_cpu_credits:
            definitions.update(
                {
                    "cpu_credit_balance": ("CPUCreditBalance", "Average"),
                    "cpu_credit_usage": ("CPUCreditUsage", "Sum"),
                }
            )
        queries: List[Dict[str, Any]] = []
        query_keys: Dict[str, Tuple[str, str]] = {}
        for node_index, cache_cluster_id in enumerate(cache_cluster_ids):
            for metric_index, (alias, (metric_name, statistic)) in enumerate(
                definitions.items()
            ):
                query_id = f"n{node_index}_m{metric_index}"
                query_keys[query_id] = (cache_cluster_id, alias)
                queries.append(
                    {
                        "Id": query_id,
                        "MetricStat": {
                            "Metric": {
                                "Namespace": "AWS/ElastiCache",
                                "MetricName": metric_name,
                                "Dimensions": [
                                    {
                                        "Name": "CacheClusterId",
                                        "Value": cache_cluster_id,
                                    }
                                ],
                            },
                            "Period": period_seconds,
                            "Stat": statistic,
                        },
                        "ReturnData": True,
                    }
                )
        collected: Dict[str, Dict[str, Dict[str, Any]]] = {
            node_id: {
                alias: {
                    "timestamps": [],
                    "values": [],
                    "period_seconds": period_seconds,
                    "statistic": statistic,
                }
                for alias, (_, statistic) in definitions.items()
            }
            for node_id in cache_cluster_ids
        }
        for offset in range(0, len(queries), 500):
            batch = queries[offset : offset + 500]
            next_token: Optional[str] = None
            while True:
                request: Dict[str, Any] = {
                    "MetricDataQueries": batch,
                    "StartTime": start_date,
                    "EndTime": end_date,
                    "ScanBy": "TimestampAscending",
                    "MaxDatapoints": 100800,
                }
                if next_token:
                    request["NextToken"] = next_token
                response = client.get_metric_data(**request)
                for result in response.get("MetricDataResults", []):
                    pair = query_keys.get(str(result.get("Id")))
                    if pair is None:
                        continue
                    node_id, alias = pair
                    collected[node_id][alias]["timestamps"].extend(
                        timestamp.isoformat()
                        if isinstance(timestamp, datetime)
                        else str(timestamp)
                        for timestamp in result.get("Timestamps") or []
                    )
                    collected[node_id][alias]["values"].extend(
                        result.get("Values") or []
                    )
                next_token = response.get("NextToken")
                if not next_token:
                    break
        return {"period_seconds": period_seconds, "per_node": collected}

    def get_ec2_confidence_trend(
        self,
        instance_id: str,
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None,
        memory_source: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Fetch CPU and optional selected-memory confidence data.

        CPU retains the legacy top-level ``daily``/``buckets`` shape.  When a
        previously discovered memory source is supplied, a parallel ``memory``
        block is returned.  This path never guesses a memory source and never
        requests network metrics.
        """
        query_region = region or settings.aws_region
        client = (
            self.session.client("cloudwatch", region_name=query_region)
            if region
            else self.cloudwatch_client
        )
        stats = {"maximum": "Maximum", "p99": "p99", "p95": "p95"}
        bucket_stats = {"p99": "p99", "p95": "p95"}
        bucket_days = (30, 90, 120, 180, 365, 455)
        metric_groups: List[Tuple[str, Dict[str, Any], Optional[Dict[str, Any]]]] = [
            (
                "",
                {
                    "Namespace": "AWS/EC2",
                    "MetricName": "CPUUtilization",
                    "Dimensions": [{"Name": "InstanceId", "Value": instance_id}],
                },
                None,
            )
        ]
        if (
            isinstance(memory_source, dict)
            and memory_source.get("namespace")
            and memory_source.get("metric_name")
            and isinstance(memory_source.get("dimensions"), list)
        ):
            metric_groups.append(
                (
                    "memory_",
                    {
                        "Namespace": memory_source["namespace"],
                        "MetricName": memory_source["metric_name"],
                        "Dimensions": memory_source["dimensions"],
                    },
                    memory_source,
                )
            )
        gpu_groups: Dict[str, Dict[str, Dict[str, Any]]] = {}
        gpu_utilization = self._ec2_gpu_metric_candidates(instance_id, client, region)
        gpu_memory = self._ec2_gpu_memory_metric_candidates(instance_id, client, region)
        for signal, candidates in (
            ("utilization_gpu", gpu_utilization),
            ("memory_used", gpu_memory),
        ):
            for position, candidate in enumerate(candidates):
                device_index = candidate["device_index"]
                prefix = f"gpu_{device_index}_{signal}_"
                gpu_groups.setdefault(device_index, {})[signal] = {
                    "prefix": prefix,
                    "source": candidate["source"],
                }
                metric_groups.append(
                    (
                        prefix,
                        {
                            "Namespace": candidate["source"]["namespace"],
                            "MetricName": candidate["source"]["metric_name"],
                            "Dimensions": candidate["source"]["dimensions"],
                        },
                        candidate["source"],
                    )
                )
        queries: List[Dict[str, Any]] = []
        for prefix, metric, _ in metric_groups:
            for key, statistic in stats.items():
                queries.append(
                    {
                        "Id": f"{prefix}daily_{key}",
                        "MetricStat": {
                            "Metric": metric,
                            "Period": 86400,
                            "Stat": statistic,
                        },
                        "ReturnData": True,
                    }
                )
            for days in bucket_days:
                for key, statistic in bucket_stats.items():
                    queries.append(
                        {
                            "Id": f"{prefix}bucket_{days}_{key}",
                            "MetricStat": {
                                "Metric": metric,
                                "Period": days * 86400,
                                "Stat": statistic,
                            },
                            "ReturnData": True,
                        }
                    )
        collected: Dict[str, List[tuple[datetime, float]]] = {
            query["Id"]: [] for query in queries
        }
        next_token: Optional[str] = None
        while True:
            request: Dict[str, Any] = {
                "MetricDataQueries": queries,
                "StartTime": start_date,
                "EndTime": end_date,
                "ScanBy": "TimestampAscending",
            }
            if next_token:
                request["NextToken"] = next_token
            response = client.get_metric_data(**request)
            for result in response.get("MetricDataResults", []):
                query_id = result.get("Id")
                if query_id not in collected:
                    continue
                collected[query_id].extend(
                    (timestamp, float(value))
                    for timestamp, value in zip(
                        result.get("Timestamps") or [], result.get("Values") or []
                    )
                    if isinstance(timestamp, datetime)
                )
            next_token = response.get("NextToken")
            if not next_token:
                break

        def build_group(prefix: str) -> Dict[str, Any]:
            daily_by_timestamp: Dict[datetime, Dict[str, Any]] = {}
            for key in stats:
                for timestamp, value in collected[f"{prefix}daily_{key}"]:
                    daily_by_timestamp.setdefault(timestamp, {})[key] = value
            daily = [
                {
                    "timestamp": timestamp.isoformat(),
                    "maximum": values.get("maximum"),
                    "p99": values.get("p99"),
                    "p95": values.get("p95"),
                }
                for timestamp, values in sorted(daily_by_timestamp.items())
            ]
            buckets: Dict[str, Dict[str, float | None]] = {}
            for days in bucket_days:
                trailing_start = end_date - timedelta(days=days)
                trailing_daily_maxima = [
                    value
                    for timestamp, value in collected[f"{prefix}daily_maximum"]
                    if timestamp >= trailing_start
                ]
                bucket: Dict[str, float | None] = {
                    "maximum": max(trailing_daily_maxima)
                    if trailing_daily_maxima
                    else None
                }
                for key in bucket_stats:
                    points = sorted(collected[f"{prefix}bucket_{days}_{key}"])
                    complete_points = [
                        point
                        for point in points
                        if point[0] + timedelta(days=days) <= end_date
                    ]
                    bucket[key] = complete_points[-1][1] if complete_points else None
                buckets[f"{days}d"] = bucket
            return {"daily": daily, "buckets": buckets}

        result = build_group("")
        if len(metric_groups) > 1:
            memory = build_group("memory_")
            memory.update(
                {
                    "source": memory_source,
                    "metric": memory_source["metric_name"],
                    "namespace": memory_source["namespace"],
                    "unit": "Percent",
                    "headline_stat": "maximum",
                }
            )
            result["memory"] = memory
        if gpu_groups:
            result["gpu_devices"] = {
                device_index: {
                    signal: build_group(details["prefix"])
                    | {
                        "source": details["source"],
                        "unit": "Percent" if signal == "utilization_gpu" else "MiB",
                        "headline_stat": "maximum",
                    }
                    for signal, details in signals.items()
                }
                for device_index, signals in gpu_groups.items()
            }
        return result

    def get_asg_confidence_trend(
        self,
        asg_name: str,
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None,
        memory_source: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Fetch CPU and persisted-source ASG confidence trends."""
        client = self._cloudwatch_client_for_region(region)
        stats = {"maximum": "Maximum", "p99": "p99", "p95": "p95"}
        bucket_stats = {"p99": "p99", "p95": "p95"}
        bucket_days = (30, 90, 120, 180, 365, 455)
        sources: List[Tuple[str, Dict[str, Any]]] = [
            (
                "cpu",
                {
                    "Namespace": "AWS/EC2",
                    "MetricName": "CPUUtilization",
                    "Dimensions": [
                        {"Name": "AutoScalingGroupName", "Value": asg_name}
                    ],
                },
            )
        ]
        memory_prefixes: List[str] = []
        if isinstance(memory_source, dict) and memory_source.get("namespace") and memory_source.get("metric_name"):
            if memory_source.get("kind") == "stable_member_aggregate":
                for index, source in enumerate(memory_source.get("member_sources") or []):
                    if isinstance(source, dict) and isinstance(source.get("dimensions"), list):
                        prefix = f"mm{index}"
                        memory_prefixes.append(prefix)
                        sources.append(
                            (
                                prefix,
                                {
                                    "Namespace": source.get("namespace"),
                                    "MetricName": source.get("metric_name"),
                                    "Dimensions": source.get("dimensions"),
                                },
                            )
                        )
            elif isinstance(memory_source.get("dimensions"), list):
                memory_prefixes.append("memory")
                sources.append(
                    (
                        "memory",
                        {
                            "Namespace": memory_source["namespace"],
                            "MetricName": memory_source["metric_name"],
                            "Dimensions": memory_source["dimensions"],
                        },
                    )
                )
        queries: List[Dict[str, Any]] = []
        for prefix, metric in sources:
            for key, statistic in stats.items():
                queries.append(
                    {
                        "Id": f"{prefix}_daily_{key}",
                        "MetricStat": {
                            "Metric": metric,
                            "Period": 86400,
                            "Stat": statistic,
                        },
                        "ReturnData": True,
                    }
                )
            for days in bucket_days:
                for key, statistic in bucket_stats.items():
                    queries.append(
                        {
                            "Id": f"{prefix}_bucket_{days}_{key}",
                            "MetricStat": {
                                "Metric": metric,
                                "Period": days * 86400,
                                "Stat": statistic,
                            },
                            "ReturnData": True,
                        }
                    )
        collected: Dict[str, List[Tuple[datetime, float]]] = {
            str(query["Id"]): [] for query in queries
        }
        for offset in range(0, len(queries), 500):
            batch = queries[offset : offset + 500]
            next_token: Optional[str] = None
            while True:
                request: Dict[str, Any] = {
                    "MetricDataQueries": batch,
                    "StartTime": start_date,
                    "EndTime": end_date,
                    "ScanBy": "TimestampAscending",
                }
                if next_token:
                    request["NextToken"] = next_token
                response = client.get_metric_data(**request)
                for result in response.get("MetricDataResults") or []:
                    query_id = str(result.get("Id") or "")
                    if query_id not in collected:
                        continue
                    collected[query_id].extend(
                        (timestamp, float(value))
                        for timestamp, value in zip(
                            result.get("Timestamps") or [], result.get("Values") or []
                        )
                        if isinstance(timestamp, datetime)
                    )
                next_token = response.get("NextToken")
                if not next_token:
                    break

        def average_member_series(suffix: str) -> List[Tuple[datetime, float]]:
            member_maps = [dict(collected.get(f"{prefix}_{suffix}") or []) for prefix in memory_prefixes]
            if not member_maps:
                return []
            common = set.intersection(*(set(values) for values in member_maps))
            return [
                (
                    timestamp,
                    sum(values[timestamp] for values in member_maps) / len(member_maps),
                )
                for timestamp in sorted(common)
            ]

        memory_output_prefix: Optional[str] = None
        if len(memory_prefixes) == 1:
            memory_output_prefix = memory_prefixes[0]
        elif memory_prefixes:
            memory_output_prefix = "memory"
            for key in stats:
                collected[f"memory_daily_{key}"] = average_member_series(
                    f"daily_{key}"
                )
            for days in bucket_days:
                for key in bucket_stats:
                    collected[f"memory_bucket_{days}_{key}"] = average_member_series(
                        f"bucket_{days}_{key}"
                    )

        def build(prefix: str) -> Dict[str, Any]:
            daily_by_timestamp: Dict[datetime, Dict[str, float]] = {}
            for key in stats:
                for timestamp, value in collected.get(f"{prefix}_daily_{key}", []):
                    daily_by_timestamp.setdefault(timestamp, {})[key] = value
            daily = [
                {"timestamp": timestamp.isoformat(), **values}
                for timestamp, values in sorted(daily_by_timestamp.items())
            ]
            buckets: Dict[str, Dict[str, float | None]] = {}
            for days in bucket_days:
                trailing_start = end_date - timedelta(days=days)
                maxima = [
                    value
                    for timestamp, value in collected.get(
                        f"{prefix}_daily_maximum", []
                    )
                    if timestamp >= trailing_start
                ]
                bucket: Dict[str, float | None] = {
                    "maximum": max(maxima) if maxima else None
                }
                for key in bucket_stats:
                    points = sorted(
                        collected.get(f"{prefix}_bucket_{days}_{key}", [])
                    )
                    complete = [
                        point
                        for point in points
                        if point[0] + timedelta(days=days) <= end_date
                    ]
                    bucket[key] = complete[-1][1] if complete else None
                buckets[f"{days}d"] = bucket
            return {"daily": daily, "buckets": buckets}

        result = build("cpu")
        if memory_output_prefix:
            memory = build(memory_output_prefix)
            memory.update(
                {
                    "source": memory_source,
                    "metric": memory_source.get("metric_name"),
                    "namespace": memory_source.get("namespace"),
                    "unit": "Percent",
                    "headline_stat": "maximum",
                }
            )
            result["memory"] = memory
        return result

    def get_elasticache_confidence_trend(
        self,
        series_by_metric: Dict[str, List[Dict[str, Any]]],
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch capped ElastiCache CPU and memory confidence series.

        The caller selects topology context. Each returned source series uses
        the EC2 confidence shape: 15 metric-stat query IDs (three daily
        statistics and p99/p95 for six aligned windows).
        """
        query_region = region or settings.aws_region
        client = (
            self.session.client("cloudwatch", region_name=query_region)
            if region
            else self.cloudwatch_client
        )
        metric_names = {
            "engine_cpu": "EngineCPUUtilization",
            "memory": "DatabaseMemoryUsagePercentage",
        }
        bucket_days = (30, 90, 120, 180, 365, 455)
        queries: List[Dict[str, Any]] = []
        for response_name, selected in series_by_metric.items():
            metric_name = metric_names.get(response_name)
            if metric_name is None:
                continue
            for index, descriptor in enumerate(selected):
                metric = {
                    "Namespace": "AWS/ElastiCache",
                    "MetricName": metric_name,
                    "Dimensions": [
                        {
                            "Name": "CacheClusterId",
                            "Value": str(descriptor["cache_cluster_id"]),
                        }
                    ],
                }
                prefix = f"{response_name}_{index}"
                for key, statistic in (
                    ("maximum", "Maximum"),
                    ("p99", "p99"),
                    ("p95", "p95"),
                ):
                    query_id = f"{prefix}_daily_{key}"
                    queries.append(
                        {
                            "Id": query_id,
                            "MetricStat": {
                                "Metric": metric,
                                "Period": 86400,
                                "Stat": statistic,
                            },
                            "ReturnData": True,
                        }
                    )
                for days in bucket_days:
                    for key in ("p99", "p95"):
                        query_id = f"{prefix}_bucket_{days}_{key}"
                        queries.append(
                            {
                                "Id": query_id,
                                "MetricStat": {
                                    "Metric": metric,
                                    "Period": days * 86400,
                                    "Stat": key,
                                },
                                "ReturnData": True,
                            }
                        )
        collected: Dict[str, List[Tuple[datetime, float]]] = {
            query["Id"]: [] for query in queries
        }
        for offset in range(0, len(queries), 500):
            batch = queries[offset : offset + 500]
            next_token: Optional[str] = None
            while batch:
                request: Dict[str, Any] = {
                    "MetricDataQueries": batch,
                    "StartTime": start_date,
                    "EndTime": end_date,
                    "ScanBy": "TimestampAscending",
                }
                if next_token:
                    request["NextToken"] = next_token
                response = client.get_metric_data(**request)
                for result in response.get("MetricDataResults", []):
                    query_id = result.get("Id")
                    if query_id not in collected:
                        continue
                    collected[query_id].extend(
                        (timestamp, float(value))
                        for timestamp, value in zip(
                            result.get("Timestamps") or [], result.get("Values") or []
                        )
                        if isinstance(timestamp, datetime)
                    )
                next_token = response.get("NextToken")
                if not next_token:
                    break

        output: Dict[str, List[Dict[str, Any]]] = {}
        for response_name, selected in series_by_metric.items():
            output[response_name] = []
            for index, descriptor in enumerate(selected):
                prefix = f"{response_name}_{index}"
                daily_by_timestamp: Dict[datetime, Dict[str, float]] = {}
                for key in ("maximum", "p99", "p95"):
                    for timestamp, value in collected.get(f"{prefix}_daily_{key}", []):
                        daily_by_timestamp.setdefault(timestamp, {})[key] = value
                daily = [
                    {
                        "timestamp": timestamp.isoformat(),
                        "maximum": values.get("maximum"),
                        "p99": values.get("p99"),
                        "p95": values.get("p95"),
                    }
                    for timestamp, values in sorted(daily_by_timestamp.items())
                ]
                buckets: Dict[str, Dict[str, float | None]] = {}
                for days in bucket_days:
                    trailing_start = end_date - timedelta(days=days)
                    maxima = [
                        value
                        for timestamp, value in collected.get(
                            f"{prefix}_daily_maximum", []
                        )
                        if timestamp >= trailing_start
                    ]
                    bucket: Dict[str, float | None] = {
                        "maximum": max(maxima) if maxima else None
                    }
                    for key in ("p99", "p95"):
                        points = sorted(
                            collected.get(f"{prefix}_bucket_{days}_{key}", [])
                        )
                        complete = [
                            point
                            for point in points
                            if point[0] + timedelta(days=days) <= end_date
                        ]
                        bucket[key] = complete[-1][1] if complete else None
                    buckets[f"{days}d"] = bucket
                output[response_name].append(
                    {
                        "cache_cluster_id": descriptor["cache_cluster_id"],
                        "node_group_id": descriptor.get("node_group_id"),
                        "role": descriptor.get("role", "unknown"),
                        "selection_reason": descriptor["selection_reason"],
                        "daily": daily,
                        "buckets": buckets,
                    }
                )
        return output

    def get_rds_rightsizing_metrics(
        self,
        db_instance_id: str,
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None,
        period_seconds: int = 300,
    ) -> Dict[str, Any]:
        """Collect raw five-minute AWS/RDS series used by the rightsizer."""
        if period_seconds <= 0:
            raise ValueError("period_seconds must be positive")
        client = self._cloudwatch_client_for_region(region)
        definitions = {
            "cpu_percent": ("CPUUtilization", "Average"),
            "freeable_memory_bytes": ("FreeableMemory", "Average"),
            "swap_bytes": ("SwapUsage", "Average"),
            "connections": ("DatabaseConnections", "Average"),
            "read_iops": ("ReadIOPS", "Average"),
            "write_iops": ("WriteIOPS", "Average"),
            "read_throughput_bps": ("ReadThroughput", "Average"),
            "write_throughput_bps": ("WriteThroughput", "Average"),
            "read_latency_seconds": ("ReadLatency", "Average"),
            "write_latency_seconds": ("WriteLatency", "Average"),
            "disk_queue_depth": ("DiskQueueDepth", "Average"),
            "free_storage_bytes": ("FreeStorageSpace", "Minimum"),
            "network_rx_bps": ("NetworkReceiveThroughput", "Average"),
            "network_tx_bps": ("NetworkTransmitThroughput", "Average"),
            "cpu_credit_balance": ("CPUCreditBalance", "Minimum"),
            "cpu_credit_usage": ("CPUCreditUsage", "Sum"),
            "burst_balance_percent": ("BurstBalance", "Minimum"),
            "ebs_io_balance_percent": ("EBSIOBalance%", "Minimum"),
            "ebs_byte_balance_percent": ("EBSByteBalance%", "Minimum"),
            "replica_lag_seconds": ("ReplicaLag", "Maximum"),
        }
        dimensions = [{"Name": "DBInstanceIdentifier", "Value": db_instance_id}]
        queries = [
            {
                "Id": f"m{index}",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/RDS",
                        "MetricName": metric_name,
                        "Dimensions": dimensions,
                    },
                    "Period": period_seconds,
                    "Stat": statistic,
                },
                "ReturnData": True,
            }
            for index, (_alias, (metric_name, statistic)) in enumerate(definitions.items())
        ]
        aliases = {f"m{index}": alias for index, alias in enumerate(definitions)}
        series = self._query_metric_series(client, queries, start_date, end_date)
        metrics: Dict[str, Any] = {}
        for query_id, payload in series.items():
            alias = aliases[query_id]
            metrics[alias] = {
                **payload,
                "period_seconds": period_seconds,
                "statistic": definitions[alias][1],
            }
        return {"period_seconds": period_seconds, "metrics": metrics}

    def get_rds_performance_insights_metrics(
        self,
        dbi_resource_id: str,
        start_date: datetime,
        end_date: datetime,
        *,
        region: Optional[str] = None,
        period_seconds: int = 300,
    ) -> Dict[str, Any]:
        """Collect total DB load and bounded wait-type load; never query SQL dimensions."""
        query_region = self._resolve_region(region)
        client = self.session.client(
            "pi",
            region_name=query_region,
        )
        queries = [
            {"Metric": "db.load.avg"},
            {
                "Metric": "db.load.avg",
                "GroupBy": {"Group": "db.wait_event_type", "Limit": 25},
            },
        ]
        results: List[Dict[str, Any]] = []
        next_token: Optional[str] = None
        while True:
            request: Dict[str, Any] = {
                "ServiceType": "RDS",
                "Identifier": dbi_resource_id,
                "MetricQueries": queries,
                "StartTime": start_date,
                "EndTime": end_date,
                "PeriodInSeconds": period_seconds,
                # Performance Insights caps MaxResults at 25; anything larger
                # is rejected outright with a ValidationException. Results are
                # paginated via NextToken below, so this is not a limit on how
                # much data is collected.
                "MaxResults": 25,
            }
            if next_token:
                request["NextToken"] = next_token
            response = client.get_resource_metrics(**request)
            results.extend(response.get("MetricList") or [])
            next_token = response.get("NextToken")
            if not next_token:
                break
        return {
            "period_seconds": period_seconds,
            "start_time": start_date.isoformat(),
            "end_time": end_date.isoformat(),
            "metric_list": results,
        }

    def get_rds_confidence_trend(
        self,
        db_instance_id: str,
        start_date: datetime,
        end_date: datetime,
        *,
        region: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return daily RDS evidence and latest complete aligned percentiles."""
        client = self._cloudwatch_client_for_region(region)
        definitions = {
            "cpu": ("CPUUtilization", "Maximum", ("p99", "p95")),
            "freeable_memory": ("FreeableMemory", "Minimum", ("p01", "p05")),
            "connections": ("DatabaseConnections", "Maximum", ("p99", "p95")),
            "free_storage": ("FreeStorageSpace", "Minimum", ("p01", "p05")),
        }
        bucket_days = (30, 90, 120, 180, 365, 455)
        queries: List[Dict[str, Any]] = []
        for key, (name, headline, percentiles) in definitions.items():
            metric = {
                "Namespace": "AWS/RDS",
                "MetricName": name,
                "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": db_instance_id}],
            }
            queries.append(
                {
                    "Id": f"{key}_daily",
                    "MetricStat": {"Metric": metric, "Period": 86400, "Stat": headline},
                    "ReturnData": True,
                }
            )
            for days in bucket_days:
                for percentile_name in percentiles:
                    queries.append(
                        {
                            "Id": f"{key}_{days}_{percentile_name}",
                            "MetricStat": {
                                "Metric": metric,
                                "Period": days * 86400,
                                "Stat": percentile_name,
                            },
                            "ReturnData": True,
                        }
                    )
        series = self._query_metric_series(client, queries, start_date, end_date)
        output: Dict[str, Any] = {}
        for key, (_name, headline, percentiles) in definitions.items():
            payload = series.get(f"{key}_daily") or {}
            daily = [
                {"timestamp": timestamp, "value": value}
                for timestamp, value in zip(payload.get("timestamps") or [], payload.get("values") or [])
            ]
            buckets: Dict[str, Any] = {}
            for days in bucket_days:
                cutoff = end_date - timedelta(days=days)
                values = [
                    float(item["value"])
                    for item in daily
                    if (parsed := datetime.fromisoformat(str(item["timestamp"]).replace("Z", "+00:00"))) >= cutoff
                ]
                bucket: Dict[str, float | None] = {
                    "maximum" if headline == "Maximum" else "minimum":
                        (max(values) if headline == "Maximum" else min(values)) if values else None
                }
                for percentile_name in percentiles:
                    percentile_payload = series.get(f"{key}_{days}_{percentile_name}") or {}
                    complete: List[Tuple[datetime, float]] = []
                    for timestamp, value in zip(
                        percentile_payload.get("timestamps") or [],
                        percentile_payload.get("values") or [],
                    ):
                        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                        if parsed + timedelta(days=days) <= end_date:
                            complete.append((parsed, float(value)))
                    bucket[percentile_name] = max(complete, default=(None, None), key=lambda point: point[0] or datetime.min.replace(tzinfo=timezone.utc))[1]
                buckets[f"{days}d"] = bucket
            output[key] = {"daily": daily, "buckets": buckets}
        output["bucket_semantics"] = {
            "cpu.maximum": "trailing_window_from_daily_maximum",
            "freeable_memory.minimum": "trailing_window_from_daily_minimum",
            "connections.maximum": "trailing_window_from_daily_maximum",
            "free_storage.minimum": "trailing_window_from_daily_minimum",
            "percentiles": "latest_complete_epoch_aligned_window",
        }
        return output

    def _get_rds_utilization(
        self,
        db_instance_id: str,
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get RDS instance utilization metrics."""
        metrics = ["CPUUtilization", "DatabaseConnections", "ReadIOPS", "WriteIOPS"]
        utilization = {}

        # Use instance region if provided, otherwise use the default region
        cloudwatch_client = self.cloudwatch_client
        if region:
            cloudwatch_client = self.session.client(
                "cloudwatch", region_name=region
            )

        for metric in metrics:
            try:
                response = cloudwatch_client.get_metric_statistics(
                    Namespace="AWS/RDS",
                    MetricName=metric,
                    Dimensions=[
                        {"Name": "DBInstanceIdentifier", "Value": db_instance_id}
                    ],
                    StartTime=start_date,
                    EndTime=end_date,
                    Period=3600,
                    Statistics=["Average"],
                )

                datapoints = response.get("Datapoints", [])
                if datapoints:
                    avg_value = sum(dp["Average"] for dp in datapoints) / len(
                        datapoints
                    )
                    utilization[metric.lower()] = avg_value
                else:
                    utilization[metric.lower()] = 0.0
            except ClientError:
                utilization[metric.lower()] = None

        return utilization

    def _get_cloudwatch_alarm_utilization(
        self, alarm_name: str, start_date: datetime, end_date: datetime
    ) -> Dict[str, Any]:
        """Get CloudWatch alarm utilization metrics (alarm state history)."""
        utilization = {}

        cloudwatch_client = self.cloudwatch_client
        try:
            # Get alarm history to count state changes
            response = cloudwatch_client.describe_alarm_history(
                AlarmName=alarm_name,
                StartDate=start_date,
                EndDate=end_date,
                MaxRecords=100,
            )

            history_records = response.get("AlarmHistoryItems", [])
            state_changes = sum(
                1
                for record in history_records
                if record.get("HistoryItemType") == "StateUpdate"
            )

            # Count transitions to ALARM state (triggers)
            alarm_triggers = 0
            for record in history_records:
                if record.get("HistoryItemType") == "StateUpdate":
                    history_data = record.get("HistoryData", {})
                    if isinstance(history_data, str):
                        # Try to parse JSON if it's a string
                        import json

                        try:
                            history_data = json.loads(history_data)
                        except:
                            pass
                    if isinstance(history_data, dict):
                        new_state = history_data.get("newState", {}).get(
                            "stateValue"
                        ) or history_data.get("newStateValue")
                        if new_state == "ALARM":
                            alarm_triggers += 1

            utilization["state_changes"] = state_changes
            utilization[
                "alarm_state_changes"
            ] = state_changes  # Alias for check compatibility
            utilization[
                "alarm_actions_triggered"
            ] = alarm_triggers  # Count of transitions to ALARM
            utilization["actions_triggered"] = alarm_triggers  # Alias
            utilization["triggers"] = alarm_triggers  # Alias
            utilization["trigger_count"] = alarm_triggers  # Alias
            utilization["history_items"] = len(history_records)
        except ClientError:
            utilization["state_changes"] = 0
            utilization["history_items"] = 0

        return utilization

    def _get_cloudwatch_log_group_utilization(
        self, log_group_name: str, start_date: datetime, end_date: datetime
    ) -> Dict[str, Any]:
        """Get CloudWatch Log Group utilization metrics."""
        utilization = {}

        # Get CloudWatch Logs client

        try:
            # Get metric statistics for IncomingBytes
            cloudwatch_client = self.cloudwatch_client
            response = cloudwatch_client.get_metric_statistics(
                Namespace="AWS/Logs",
                MetricName="IncomingBytes",
                Dimensions=[{"Name": "LogGroupName", "Value": log_group_name}],
                StartTime=start_date,
                EndTime=end_date,
                Period=3600,  # 1 hour periods
                Statistics=["Sum"],
            )

            datapoints = response.get("Datapoints", [])
            if datapoints:
                total_bytes = sum(dp["Sum"] for dp in datapoints)
                utilization["IncomingBytes"] = total_bytes
                utilization["datapoints"] = len(datapoints)
            else:
                utilization["IncomingBytes"] = 0
                utilization["datapoints"] = 0
        except ClientError:
            utilization["IncomingBytes"] = 0
            utilization["datapoints"] = 0

        return utilization

    def _get_dynamodb_table_utilization(
        self,
        table_name: str,
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get DynamoDB table utilization metrics."""
        utilization = {}

        query_region = region or settings.aws_region
        cloudwatch_client = self.cloudwatch_client
        if region:
            cloudwatch_client = self.session.client(
                "cloudwatch", region_name=region
            )

        try:
            dynamodb_client = self.session.client(
                "dynamodb", region_name=query_region
            )
            table = dynamodb_client.describe_table(TableName=table_name).get(
                "Table", {}
            )
            item_count = table.get("ItemCount")
            if item_count is not None:
                utilization["ItemCount"] = item_count
                utilization["itemcount"] = float(item_count)
        except Exception:
            utilization["ItemCount"] = 0
            utilization["itemcount"] = 0.0

        metrics = [
            "ConsumedReadCapacityUnits",
            "ConsumedWriteCapacityUnits",
            "ProvisionedReadCapacityUnits",
            "ProvisionedWriteCapacityUnits",
            "ReadThrottleEvents",
            "WriteThrottleEvents",
        ]

        period_seconds = 3600
        metric_statistics = {
            "ConsumedReadCapacityUnits": "Sum",
            "ConsumedWriteCapacityUnits": "Sum",
            "ProvisionedReadCapacityUnits": "Average",
            "ProvisionedWriteCapacityUnits": "Average",
            "ReadThrottleEvents": "Sum",
            "WriteThrottleEvents": "Sum",
        }

        for metric in metrics:
            try:
                statistic = metric_statistics.get(metric, "Average")
                print(
                    f"[ADAPTER] Querying CloudWatch for DynamoDB metric '{metric}' for table '{table_name}' (start: {start_date}, end: {end_date})"
                )
                response = cloudwatch_client.get_metric_statistics(
                    Namespace="AWS/DynamoDB",
                    MetricName=metric,
                    Dimensions=[{"Name": "TableName", "Value": table_name}],
                    StartTime=start_date,
                    EndTime=end_date,
                    Period=period_seconds,
                    Statistics=[statistic],
                )

                datapoints = response.get("Datapoints", [])
                print(
                    f"[ADAPTER] Found {len(datapoints)} datapoints for metric '{metric}' for table '{table_name}'"
                )
                if datapoints:
                    values = [
                        dp.get(statistic, dp.get("Average", dp.get("Sum", 0)))
                        for dp in sorted(datapoints, key=lambda x: x["Timestamp"])
                    ]
                    utilization[metric] = values
                    utilization[metric.lower()] = (
                        sum(values) / len(values) if values else 0.0
                    )
                    utilization[f"{metric}_statistic"] = statistic
                    utilization[f"{metric.lower()}_statistic"] = statistic
                    utilization[f"{metric}_period_seconds"] = period_seconds
                    utilization[f"{metric.lower()}_period_seconds"] = period_seconds
                    print(
                        f"[ADAPTER] Metric '{metric}' for table '{table_name}': {len(values)} values, avg={utilization[metric.lower()]:.2f}"
                    )
                else:
                    print(
                        f"[ADAPTER] No datapoints found for metric '{metric}' for table '{table_name}'"
                    )
                    utilization[metric] = []
                    utilization[metric.lower()] = 0.0
                    utilization[f"{metric}_statistic"] = statistic
                    utilization[f"{metric.lower()}_statistic"] = statistic
                    utilization[f"{metric}_period_seconds"] = period_seconds
                    utilization[f"{metric.lower()}_period_seconds"] = period_seconds
            except ClientError as e:
                # Metric might not exist, continue
                print(
                    f"[ADAPTER] Error querying metric '{metric}' for table '{table_name}': {e}"
                )
                utilization[metric] = []
                utilization[metric.lower()] = 0.0
                utilization[f"{metric}_statistic"] = metric_statistics.get(
                    metric, "Average"
                )
                utilization[f"{metric.lower()}_statistic"] = metric_statistics.get(
                    metric, "Average"
                )
                utilization[f"{metric}_period_seconds"] = period_seconds
                utilization[f"{metric.lower()}_period_seconds"] = period_seconds
            except Exception as e:
                print(
                    f"[ADAPTER] Unexpected error querying metric '{metric}' for table '{table_name}': {e}"
                )
                import traceback

                print(f"[ADAPTER] Traceback: {traceback.format_exc()}")
                utilization[metric] = []
                utilization[metric.lower()] = 0.0
                utilization[f"{metric}_statistic"] = metric_statistics.get(
                    metric, "Average"
                )
                utilization[f"{metric.lower()}_statistic"] = metric_statistics.get(
                    metric, "Average"
                )
                utilization[f"{metric}_period_seconds"] = period_seconds
                utilization[f"{metric.lower()}_period_seconds"] = period_seconds

        return utilization

    def _get_dynamodb_gsi_utilization(
        self,
        gsi_id: str,  # Format: "table_name/gsi_name"
        start_date: datetime,
        end_date: datetime,
    ) -> Dict[str, Any]:
        """Get DynamoDB GSI utilization metrics."""
        utilization = {}

        # Parse GSI ID (format: "table_name/gsi_name")
        parts = gsi_id.split("/", 1)
        if len(parts) != 2:
            return utilization

        table_name, gsi_name = parts

        # Get CloudWatch client
        cloudwatch_client = self.cloudwatch_client
        # Get GSI metrics
        metrics = ["ConsumedReadCapacityUnits", "ConsumedWriteCapacityUnits"]

        for metric in metrics:
            try:
                response = cloudwatch_client.get_metric_statistics(
                    Namespace="AWS/DynamoDB",
                    MetricName=metric,
                    Dimensions=[
                        {"Name": "TableName", "Value": table_name},
                        {"Name": "GlobalSecondaryIndexName", "Value": gsi_name},
                    ],
                    StartTime=start_date,
                    EndTime=end_date,
                    Period=3600,  # 1 hour periods
                    Statistics=["Sum"],
                )

                datapoints = response.get("Datapoints", [])
                if datapoints:
                    total = sum(dp["Sum"] for dp in datapoints)
                    utilization[metric.lower()] = total
                else:
                    utilization[metric.lower()] = 0
            except ClientError:
                utilization[metric.lower()] = 0

        return utilization

    def _get_elasticache_utilization(
        self,
        resource_id: str,
        resource_type: str,
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get ElastiCache utilization metrics (best-effort)."""
        utilization: Dict[str, Any] = {}
        query_region = region or settings.aws_region
        cloudwatch_client = self.cloudwatch_client
        if region:
            cloudwatch_client = self.session.client(
                "cloudwatch", region_name=region
            )

        shard_members: List[List[str]] = []
        if resource_type == "elasticache_replication_group":
            try:
                elasticache_client = self.session.client(
                    "elasticache", region_name=query_region
                )
                groups = elasticache_client.describe_replication_groups(
                    ReplicationGroupId=resource_id
                ).get("ReplicationGroups", [])
                group = groups[0] if groups else {}
                for node_group in group.get("NodeGroups") or []:
                    members = [
                        str(item.get("CacheClusterId"))
                        for item in node_group.get("NodeGroupMembers") or []
                        if item.get("CacheClusterId")
                    ]
                    if members:
                        shard_members.append(members)
                if not shard_members:
                    members = [str(item) for item in group.get("MemberClusters") or []]
                    if members:
                        shard_members.append(members)
            except Exception:
                shard_members = []
        else:
            shard_members = [[resource_id]]

        metrics = ["CurrItems", "KeyCount"]
        for metric in metrics:
            try:
                member_points: Dict[str, Dict[datetime, float]] = {}
                for members in shard_members:
                    for member_id in members:
                        response = cloudwatch_client.get_metric_statistics(
                            Namespace="AWS/ElastiCache",
                            MetricName=metric,
                            Dimensions=[{"Name": "CacheClusterId", "Value": member_id}],
                            StartTime=start_date,
                            EndTime=end_date,
                            Period=3600,
                            Statistics=["Average"],
                        )
                        member_points[member_id] = {
                            point["Timestamp"]: float(point.get("Average", 0))
                            for point in response.get("Datapoints", [])
                            if point.get("Timestamp") is not None
                        }
                timestamps = sorted(
                    set().union(*(set(points) for points in member_points.values()))
                    if member_points
                    else set()
                )
                values: List[float] = []
                for timestamp in timestamps:
                    shard_values: List[float] = []
                    complete = True
                    for members in shard_members:
                        available = [
                            member_points.get(member_id, {}).get(timestamp)
                            for member_id in members
                        ]
                        available = [value for value in available if value is not None]
                        if not available:
                            complete = False
                            break
                        shard_values.append(max(available))
                    if complete:
                        values.append(sum(shard_values))
                if values:
                    utilization[metric] = values
                    utilization[metric.lower()] = (
                        sum(values) / len(values) if values else 0.0
                    )
                else:
                    utilization[metric] = []
                    utilization[metric.lower()] = 0.0
            except ClientError:
                utilization[metric] = []
                utilization[metric.lower()] = 0.0
            except Exception:
                utilization[metric] = []
                utilization[metric.lower()] = 0.0

        return utilization

    def _get_ebs_utilization(
        self,
        volume_id: str,
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get EBS volume utilization metrics."""
        utilization: Dict[str, Any] = {}
        cloudwatch_client = self.cloudwatch_client
        if region:
            cloudwatch_client = self.session.client(
                "cloudwatch", region_name=region
            )

        metrics = [
            "VolumeReadOps",
            "VolumeWriteOps",
            "VolumeReadBytes",
            "VolumeWriteBytes",
        ]
        for metric in metrics:
            try:
                response = cloudwatch_client.get_metric_statistics(
                    Namespace="AWS/EBS",
                    MetricName=metric,
                    Dimensions=[{"Name": "VolumeId", "Value": volume_id}],
                    StartTime=start_date,
                    EndTime=end_date,
                    Period=3600,
                    Statistics=["Average"],
                )
                datapoints = response.get("Datapoints", [])
                if datapoints:
                    values = [
                        dp.get("Average", 0)
                        for dp in sorted(datapoints, key=lambda x: x["Timestamp"])
                    ]
                    utilization[metric] = values
                    utilization[metric.lower()] = (
                        sum(values) / len(values) if values else 0.0
                    )
                else:
                    utilization[metric] = []
                    utilization[metric.lower()] = 0.0
            except ClientError:
                utilization[metric] = []
                utilization[metric.lower()] = 0.0
            except Exception:
                utilization[metric] = []
                utilization[metric.lower()] = 0.0

        return utilization

    def _get_lambda_utilization(
        self,
        function_name: str,
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get Lambda function utilization metrics."""
        utilization = {}

        # Get CloudWatch client
        cloudwatch_client = (
            self.session.client("cloudwatch", region_name=region)
            if region
            else self.cloudwatch_client
        )
        logs_client = (
            self.session.client("logs", region_name=region)
            if region
            else self.session.client("logs")
        )

        # Lambda metrics to retrieve
        lambda_metrics = {
            "Invocations": "Sum",
            "Errors": "Sum",
            "Duration": "Average",
            "Throttles": "Sum",
            "ConcurrentExecutions": "Maximum",
        }

        for metric_name, stat in lambda_metrics.items():
            try:
                response = cloudwatch_client.get_metric_statistics(
                    Namespace="AWS/Lambda",
                    MetricName=metric_name,
                    Dimensions=[{"Name": "FunctionName", "Value": function_name}],
                    StartTime=start_date,
                    EndTime=end_date,
                    Period=3600,  # 1 hour periods
                    Statistics=[stat],
                )

                datapoints = response.get("Datapoints", [])
                if datapoints:
                    if stat == "Sum":
                        value = sum(dp[stat] for dp in datapoints)
                    elif stat == "Average":
                        value = sum(dp[stat] for dp in datapoints) / len(datapoints)
                    elif stat == "Maximum":
                        value = max(dp[stat] for dp in datapoints)
                    else:
                        value = sum(dp[stat] for dp in datapoints) / len(datapoints)

                    utilization[metric_name.lower()] = value

                    # Special handling for concurrent executions
                    if metric_name == "ConcurrentExecutions":
                        # Also calculate average
                        avg_concurrent = sum(dp[stat] for dp in datapoints) / len(
                            datapoints
                        )
                        utilization["avg_concurrent_executions"] = avg_concurrent
                        utilization["max_concurrent_executions"] = value

                    # Add avg_duration alias for duration (check expects avg_duration)
                    if metric_name == "Duration":
                        utilization["avg_duration"] = value
                        utilization[
                            "avg_duration_ms"
                        ] = value  # Already in milliseconds
                else:
                    utilization[metric_name.lower()] = 0
                    if metric_name == "Duration":
                        utilization["avg_duration"] = 0
                        utilization["avg_duration_ms"] = 0
            except ClientError as e:
                print(
                    f"[CLOUDWATCH] Error getting {metric_name} for {function_name}: {e}"
                )
                utilization[metric_name.lower()] = 0

        # Get memory utilization from CloudWatch Logs (from Lambda REPORT lines)
        # This requires parsing log events, which is complex - for now, return placeholder
        # In production, you'd use Lambda Insights for memory metrics
        try:
            log_group_name = f"/aws/lambda/{function_name}"

            # Get log ingestion bytes (for high log ingestion check)
            try:
                response = cloudwatch_client.get_metric_statistics(
                    Namespace="AWS/Logs",
                    MetricName="IncomingBytes",
                    Dimensions=[{"Name": "LogGroupName", "Value": log_group_name}],
                    StartTime=start_date,
                    EndTime=end_date,
                    Period=3600,
                    Statistics=["Sum"],
                )

                datapoints = response.get("Datapoints", [])
                if datapoints:
                    total_bytes = sum(dp["Sum"] for dp in datapoints)
                    utilization["log_bytes_ingested"] = total_bytes
                else:
                    utilization["log_bytes_ingested"] = 0
            except:
                utilization["log_bytes_ingested"] = 0

            # Try to get memory usage from CloudWatch Logs REPORT lines
            # Parse recent log events to extract memory usage
            try:
                import re

                # Query recent log events (last 50 to get a good sample)
                # We look for REPORT lines which contain memory info
                memory_values = []

                # Use filter_log_events to get recent logs
                response = logs_client.filter_log_events(
                    logGroupName=log_group_name,
                    startTime=int(start_date.timestamp() * 1000),
                    endTime=int(end_date.timestamp() * 1000),
                    filterPattern='[report_line="REPORT", ...]',
                    limit=100,  # Get up to 100 REPORT lines
                )

                # Parse REPORT lines for memory usage
                # Format: "REPORT RequestId: xxx Duration: xxx ms  Billed Duration: xxx ms  Memory Size: xxx MB  Max Memory Used: xxx MB"
                pattern = r"Max Memory Used:\s+(\d+)\s+MB"

                for event in response.get("events", []):
                    message = event.get("message", "")
                    match = re.search(pattern, message)
                    if match:
                        memory_mb = int(match.group(1))
                        memory_values.append(memory_mb)

                if memory_values:
                    utilization["max_memory_used"] = max(memory_values)
                    utilization["avg_memory_used"] = sum(memory_values) / len(
                        memory_values
                    )
                    utilization["max_memory_used_mb"] = max(memory_values)
                    utilization["avg_memory_used_mb"] = sum(memory_values) / len(
                        memory_values
                    )
                    print(
                        f"[CLOUDWATCH_LOGS] {function_name}: Parsed {len(memory_values)} memory samples, "
                        f"max={max(memory_values)}MB, avg={sum(memory_values) / len(memory_values):.1f}MB"
                    )
                else:
                    # No REPORT lines found, set to None
                    utilization["max_memory_used"] = None
                    utilization["avg_memory_used"] = None
                    utilization["max_memory_used_mb"] = None
                    utilization["avg_memory_used_mb"] = None
                    print(
                        f"[CLOUDWATCH_LOGS] {function_name}: No REPORT lines found in logs"
                    )
            except Exception as log_parse_error:
                print(
                    f"[CLOUDWATCH_LOGS] Error parsing memory from logs for {function_name}: {log_parse_error}"
                )
                utilization["max_memory_used"] = None
                utilization["avg_memory_used"] = None
                utilization["max_memory_used_mb"] = None
                utilization["avg_memory_used_mb"] = None

        except Exception as e:
            print(
                f"[CLOUDWATCH_LOGS] Error getting log metrics for {function_name}: {e}"
            )

        return utilization

    def get_resource_tags(self, resource_id: str, resource_type: str) -> Dict[str, str]:
        """Get tags for a resource."""
        # Tags are already included in get_resources response
        # This method is for future use when we need to fetch tags separately
        resources = self.get_resources(resource_type)
        for resource in resources:
            if resource["resource_id"] == resource_id:
                return resource.get("tags", {})
        return {}

    # Public methods for execution registry
    def get_ec2_instances(
        self, filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Get EC2 instances (public method for execution registry)."""
        return self._get_ec2_instances(filters)

    def get_rds_instances(
        self, filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Get RDS instances (public method for execution registry)."""
        return self._get_rds_instances(filters)

    def get_ebs_volumes(
        self, filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Get EBS volumes (public method for execution registry)."""
        return self._get_ebs_volumes(filters)

    def get_ebs_snapshots(
        self, filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Get EBS snapshots (public method for execution registry)."""
        return self._get_snapshots(filters)

    # Filter converter methods for execution registry
    def convert_ec2_filters(self, filters: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Convert filter conditions to EC2 filter format."""
        converted = {}
        for filter_cond in filters:
            filter_type = filter_cond.get("type")
            value = filter_cond.get("value")

            if filter_type == "tags":
                if isinstance(value, dict):
                    if "tag" not in converted:
                        converted["tag"] = {}
                    converted["tag"][value.get("key")] = value.get("value")
            elif filter_type == "state":
                converted["state"] = value
            elif filter_type == "region":
                converted["region"] = value

        return converted

    def convert_rds_filters(self, filters: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Convert filter conditions to RDS filter format."""
        converted = {}
        for filter_cond in filters:
            filter_type = filter_cond.get("type")
            value = filter_cond.get("value")

            if filter_type == "state":
                converted["state"] = value
            elif filter_type == "region":
                converted["region"] = value

        return converted

    def convert_ebs_filters(self, filters: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Convert filter conditions to EBS filter format."""
        converted = {}
        for filter_cond in filters:
            filter_type = filter_cond.get("type")
            value = filter_cond.get("value")

            if filter_type == "state":
                converted["state"] = value
            elif filter_type == "region":
                converted["region"] = value

        return converted

    def convert_snapshot_filters(self, filters: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Convert filter conditions to snapshot filter format."""
        converted = {}
        for filter_cond in filters:
            filter_type = filter_cond.get("type")
            value = filter_cond.get("value")

            if filter_type == "region":
                converted["region"] = value

        return converted

    def convert_s3_filters(self, filters: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Convert filter conditions to S3 filter format."""
        # Placeholder for future S3 support
        return {}

    # AWS Action Methods - Perform actual operations on resources
    def stop_ec2_instance(
        self, instance_id: str, region: Optional[str] = None
    ) -> Dict[str, Any]:
        """Stop an EC2 instance."""
        try:
            client = self.ec2_client
            if region:
                client = self.session.client("ec2", region_name=region)

            response = client.stop_instances(InstanceIds=[instance_id])
            current_state = response["StoppingInstances"][0]["CurrentState"]["Name"]
            previous_state = response["StoppingInstances"][0]["PreviousState"]["Name"]

            return {
                "success": True,
                "action": "stop",
                "resource_id": instance_id,
                "current_state": current_state,
                "previous_state": previous_state,
                "message": f"Instance {instance_id} stop initiated",
            }
        except ClientError as e:
            return {
                "success": False,
                "action": "stop",
                "resource_id": instance_id,
                "error": str(e),
                "message": f"Failed to stop instance {instance_id}: {str(e)}",
            }

    def terminate_ec2_instance(
        self, instance_id: str, region: Optional[str] = None
    ) -> Dict[str, Any]:
        """Terminate an EC2 instance."""
        try:
            client = self.ec2_client
            if region:
                client = self.session.client("ec2", region_name=region)

            response = client.terminate_instances(InstanceIds=[instance_id])
            current_state = response["TerminatingInstances"][0]["CurrentState"]["Name"]
            previous_state = response["TerminatingInstances"][0]["PreviousState"][
                "Name"
            ]

            return {
                "success": True,
                "action": "terminate",
                "resource_id": instance_id,
                "current_state": current_state,
                "previous_state": previous_state,
                "message": f"Instance {instance_id} termination initiated",
            }
        except ClientError as e:
            return {
                "success": False,
                "action": "terminate",
                "resource_id": instance_id,
                "error": str(e),
                "message": f"Failed to terminate instance {instance_id}: {str(e)}",
            }

    def delete_ebs_volume(
        self, volume_id: str, region: Optional[str] = None
    ) -> Dict[str, Any]:
        """Delete an EBS volume."""
        try:
            client = self.ec2_client
            if region:
                client = self.session.client("ec2", region_name=region)

            client.delete_volume(VolumeId=volume_id)

            return {
                "success": True,
                "action": "delete",
                "resource_id": volume_id,
                "message": f"Volume {volume_id} deletion initiated",
            }
        except ClientError as e:
            return {
                "success": False,
                "action": "delete",
                "resource_id": volume_id,
                "error": str(e),
                "message": f"Failed to delete volume {volume_id}: {str(e)}",
            }

    def delete_ebs_snapshot(
        self, snapshot_id: str, region: Optional[str] = None
    ) -> Dict[str, Any]:
        """Delete an EBS snapshot."""
        try:
            client = self.ec2_client
            if region:
                client = self.session.client("ec2", region_name=region)

            client.delete_snapshot(SnapshotId=snapshot_id)

            return {
                "success": True,
                "action": "delete",
                "resource_id": snapshot_id,
                "message": f"Snapshot {snapshot_id} deleted",
            }
        except ClientError as e:
            return {
                "success": False,
                "action": "delete",
                "resource_id": snapshot_id,
                "error": str(e),
                "message": f"Failed to delete snapshot {snapshot_id}: {str(e)}",
            }

    def stop_rds_instance(
        self, db_instance_id: str, region: Optional[str] = None
    ) -> Dict[str, Any]:
        """Stop an RDS instance."""
        try:
            client = (
                self.session.client("rds", region_name=region)
                if region
                else self.session.client("rds")
            )

            response = client.stop_db_instance(DBInstanceIdentifier=db_instance_id)

            return {
                "success": True,
                "action": "stop",
                "resource_id": db_instance_id,
                "current_state": response["DBInstance"]["DBInstanceStatus"],
                "message": f"RDS instance {db_instance_id} stop initiated",
            }
        except ClientError as e:
            return {
                "success": False,
                "action": "stop",
                "resource_id": db_instance_id,
                "error": str(e),
                "message": f"Failed to stop RDS instance {db_instance_id}: {str(e)}",
            }

    def delete_rds_instance(
        self,
        db_instance_id: str,
        skip_final_snapshot: bool = True,
        region: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Delete an RDS instance."""
        try:
            client = (
                self.session.client("rds", region_name=region)
                if region
                else self.session.client("rds")
            )

            response = client.delete_db_instance(
                DBInstanceIdentifier=db_instance_id,
                SkipFinalSnapshot=skip_final_snapshot,
            )

            return {
                "success": True,
                "action": "delete",
                "resource_id": db_instance_id,
                "current_state": response["DBInstance"]["DBInstanceStatus"],
                "message": f"RDS instance {db_instance_id} deletion initiated",
            }
        except ClientError as e:
            return {
                "success": False,
                "action": "delete",
                "resource_id": db_instance_id,
                "error": str(e),
                "message": f"Failed to delete RDS instance {db_instance_id}: {str(e)}",
            }

    def execute_action(
        self,
        resource_type: str,
        resource_id: str,
        action: str,
        region: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Execute an action on a resource.

        Args:
            resource_type: Type of resource (ec2, rds, ebs, snapshot)
            resource_id: ID of the resource
            action: Action to perform (stop, terminate, delete)
            region: AWS region
            **kwargs: Additional parameters for specific actions

        Returns:
            Dictionary with action result
        """
        if resource_type == "ec2":
            if action == "stop":
                return self.stop_ec2_instance(resource_id, region)
            elif action == "terminate":
                return self.terminate_ec2_instance(resource_id, region)
            else:
                return {
                    "success": False,
                    "error": f"Unknown action {action} for EC2 instances",
                }
        elif resource_type == "rds":
            if action == "stop":
                return self.stop_rds_instance(resource_id, region)
            elif action == "delete":
                skip_snapshot = kwargs.get("skip_final_snapshot", True)
                return self.delete_rds_instance(resource_id, skip_snapshot, region)
            else:
                return {
                    "success": False,
                    "error": f"Unknown action {action} for RDS instances",
                }
        elif resource_type == "ebs":
            if action == "delete":
                return self.delete_ebs_volume(resource_id, region)
            else:
                return {
                    "success": False,
                    "error": f"Unknown action {action} for EBS volumes",
                }
        elif resource_type == "snapshot":
            if action == "delete":
                return self.delete_ebs_snapshot(resource_id, region)
            else:
                return {
                    "success": False,
                    "error": f"Unknown action {action} for snapshots",
                }
        else:
            return {
                "success": False,
                "error": f"Unsupported resource type: {resource_type}",
            }
