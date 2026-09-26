"""Dataset definitions for local CUR aggregate cache."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


PACKAGE_DIR = Path(__file__).resolve().parent
QUERY_DIR = PACKAGE_DIR / "queries"
DEFAULT_CACHE_ROOT = Path("data/cur_cache")
DEFAULT_REGION = "us-east-1"
DEFAULT_DATABASE = "maxops_cur"
DEFAULT_RAW_TABLE = "curv2_raw"

# The S3 bucket holding the CUR export and the Athena results it produces.
#
# There is deliberately no default bucket name. MaxOps creates and uses
# `maxops-cur-report-<account id>`, which can only be known once the account is
# resolved, and the setup page and jobs derive it that way. A hardcoded
# fallback here would silently point every deployment at one specific bucket
# that exists in nobody else's account, producing permission errors that name a
# bucket the operator has never heard of.
#
# Set MAXOPS_CUR_BUCKET_ARN only to use a bucket MaxOps did not create.
DEFAULT_CUR_BUCKET_ARN = os.environ.get("MAXOPS_CUR_BUCKET_ARN") or None


def bucket_name_from_arn(bucket_arn: str) -> str:
    prefix = "arn:aws:s3:::"
    if not bucket_arn.startswith(prefix) or not bucket_arn[len(prefix) :]:
        raise ValueError(f"Expected S3 bucket ARN, got: {bucket_arn}")
    return bucket_arn[len(prefix) :]


def athena_results_from_bucket_arn(bucket_arn: str) -> str:
    return f"s3://{bucket_name_from_arn(bucket_arn)}/athena-results/"


def s3_cache_root_from_bucket_arn(bucket_arn: str) -> str:
    return f"s3://{bucket_name_from_arn(bucket_arn)}/cur-aggregates"


# None unless MAXOPS_CUR_BUCKET_ARN is set. The CLI requires the corresponding
# flag in that case; the setup page derives these from the resolved account and
# never reads them.
DEFAULT_ATHENA_RESULTS_S3 = (
    athena_results_from_bucket_arn(DEFAULT_CUR_BUCKET_ARN) if DEFAULT_CUR_BUCKET_ARN else None
)
DEFAULT_S3_CACHE_ROOT = (
    s3_cache_root_from_bucket_arn(DEFAULT_CUR_BUCKET_ARN) if DEFAULT_CUR_BUCKET_ARN else None
)


@dataclass(frozen=True)
class DatasetDefinition:
    name: str
    grain: str
    description: str
    date_column: str
    dimensions: List[str]
    query_file: str

    @property
    def query_path(self) -> Path:
        return QUERY_DIR / self.query_file


DATASETS: Dict[str, DatasetDefinition] = {
    "service_daily": DatasetDefinition(
        name="service_daily",
        grain="day",
        description="Daily CUR cost and usage grouped by service, account, and region.",
        date_column="usage_date",
        dimensions=[
            "usage_date",
            "bill_payer_account_id",
            "line_item_usage_account_id",
            "service_code",
            "product_region_code",
            "product_location",
        ],
        query_file="service_daily.sql",
    ),
    "usage_type_daily": DatasetDefinition(
        name="usage_type_daily",
        grain="day",
        description="Daily CUR cost and usage grouped by service usage type and operation.",
        date_column="usage_date",
        dimensions=[
            "usage_date",
            "bill_payer_account_id",
            "line_item_usage_account_id",
            "service_code",
            "product_region_code",
            "product_location",
            "line_item_usage_type",
            "line_item_operation",
            "pricing_unit",
        ],
        query_file="usage_type_daily.sql",
    ),
    "service_monthly": DatasetDefinition(
        name="service_monthly",
        grain="month",
        description="Monthly CUR cost and usage grouped by service, account, and region.",
        date_column="billing_month",
        dimensions=[
            "billing_month",
            "bill_payer_account_id",
            "line_item_usage_account_id",
            "service_code",
            "product_region_code",
            "product_location",
        ],
        query_file="service_monthly.sql",
    ),
    "usage_type_monthly": DatasetDefinition(
        name="usage_type_monthly",
        grain="month",
        description="Monthly CUR cost and usage grouped by service usage type and operation.",
        date_column="billing_month",
        dimensions=[
            "billing_month",
            "bill_payer_account_id",
            "line_item_usage_account_id",
            "service_code",
            "product_region_code",
            "product_location",
            "line_item_usage_type",
            "line_item_operation",
            "pricing_unit",
        ],
        query_file="usage_type_monthly.sql",
    ),
    "resource_daily": DatasetDefinition(
        name="resource_daily",
        grain="day",
        description="Daily CUR cost and usage grouped by resource and line-item dimensions.",
        date_column="usage_date",
        dimensions=[
            "usage_date",
            "bill_payer_account_id",
            "line_item_usage_account_id",
            "line_item_resource_id",
            "service_code",
            "product_region_code",
            "product_location",
            "line_item_availability_zone",
            "line_item_usage_type",
            "line_item_operation",
            "line_item_line_item_type",
            "pricing_unit",
        ],
        query_file="resource_daily.sql",
    ),
    "resource_monthly": DatasetDefinition(
        name="resource_monthly",
        grain="month",
        description="Monthly CUR cost and usage grouped by resource and line-item dimensions.",
        date_column="billing_month",
        dimensions=[
            "billing_month",
            "bill_payer_account_id",
            "line_item_usage_account_id",
            "line_item_resource_id",
            "service_code",
            "product_region_code",
            "product_location",
            "line_item_availability_zone",
            "line_item_usage_type",
            "line_item_operation",
            "line_item_line_item_type",
            "pricing_unit",
        ],
        query_file="resource_monthly.sql",
    ),
}
