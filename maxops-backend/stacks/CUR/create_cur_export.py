#!/usr/bin/env python3
"""Create the MaxOps daily CUR v2 AWS Data Export."""

from __future__ import annotations

import argparse
import json
import time
from typing import Any, Dict, Iterable, Optional


DEFAULT_REGION = "us-east-1"
DEFAULT_EXPORT_NAME = "maxops-curv2-daily"
DEFAULT_PREFIX = "curv2"
DEFAULT_DATABASE = "maxops_cur"
DEFAULT_RAW_TABLE = "curv2_raw"

CUR_COLUMNS = [
    "bill_bill_type",
    "bill_billing_entity",
    "bill_billing_period_end_date",
    "bill_billing_period_start_date",
    "bill_invoice_id",
    "bill_invoicing_entity",
    "bill_payer_account_id",
    "bill_payer_account_name",
    "cost_category",
    "discount",
    "discount_bundled_discount",
    "discount_total_discount",
    "identity_line_item_id",
    "identity_time_interval",
    "line_item_availability_zone",
    "line_item_blended_cost",
    "line_item_blended_rate",
    "line_item_currency_code",
    "line_item_legal_entity",
    "line_item_line_item_description",
    "line_item_line_item_type",
    "line_item_net_unblended_cost",
    "line_item_net_unblended_rate",
    "line_item_normalization_factor",
    "line_item_normalized_usage_amount",
    "line_item_operation",
    "line_item_product_code",
    "line_item_resource_id",
    "line_item_tax_type",
    "line_item_unblended_cost",
    "line_item_unblended_rate",
    "line_item_usage_account_id",
    "line_item_usage_account_name",
    "line_item_usage_amount",
    "line_item_usage_end_date",
    "line_item_usage_start_date",
    "line_item_usage_type",
    "line_item_user_identifier",
    "pricing_currency",
    "pricing_lease_contract_length",
    "pricing_offering_class",
    "pricing_public_on_demand_cost",
    "pricing_public_on_demand_rate",
    "pricing_purchase_option",
    "pricing_rate_code",
    "pricing_rate_id",
    "pricing_term",
    "pricing_unit",
    "product",
    "product_comment",
    "product_fee_code",
    "product_fee_description",
    "product_from_location",
    "product_from_location_type",
    "product_from_region_code",
    "product_instance_family",
    "product_instance_type",
    "product_instancesku",
    "product_location",
    "product_location_type",
    "product_operation",
    "product_pricing_unit",
    "product_product_family",
    "product_region_code",
    "product_servicecode",
    "product_sku",
    "product_to_location",
    "product_to_location_type",
    "product_to_region_code",
    "product_usagetype",
    "reservation_amortized_upfront_cost_for_usage",
    "reservation_amortized_upfront_fee_for_billing_period",
    "reservation_availability_zone",
    "reservation_effective_cost",
    "reservation_end_time",
    "reservation_modification_status",
    "reservation_net_amortized_upfront_cost_for_usage",
    "reservation_net_amortized_upfront_fee_for_billing_period",
    "reservation_net_effective_cost",
    "reservation_net_recurring_fee_for_usage",
    "reservation_net_unused_amortized_upfront_fee_for_billing_period",
    "reservation_net_unused_recurring_fee",
    "reservation_net_upfront_value",
    "reservation_normalized_units_per_reservation",
    "reservation_number_of_reservations",
    "reservation_recurring_fee_for_usage",
    "reservation_reservation_a_r_n",
    "reservation_start_time",
    "reservation_subscription_id",
    "reservation_total_reserved_normalized_units",
    "reservation_total_reserved_units",
    "reservation_units_per_reservation",
    "reservation_unused_amortized_upfront_fee_for_billing_period",
    "reservation_unused_normalized_unit_quantity",
    "reservation_unused_quantity",
    "reservation_unused_recurring_fee",
    "reservation_upfront_value",
    "resource_tags",
    "savings_plan_amortized_upfront_commitment_for_billing_period",
    "savings_plan_end_time",
    "savings_plan_instance_type_family",
    "savings_plan_net_amortized_upfront_commitment_for_billing_period",
    "savings_plan_net_recurring_commitment_for_billing_period",
    "savings_plan_net_savings_plan_effective_cost",
    "savings_plan_offering_type",
    "savings_plan_payment_option",
    "savings_plan_purchase_term",
    "savings_plan_recurring_commitment_for_billing_period",
    "savings_plan_region",
    "savings_plan_savings_plan_a_r_n",
    "savings_plan_savings_plan_effective_cost",
    "savings_plan_savings_plan_rate",
    "savings_plan_start_time",
    "savings_plan_total_commitment_to_date",
    "savings_plan_used_commitment",
    "tags",
]


DOUBLE_COLUMNS = {
    "discount_bundled_discount",
    "discount_total_discount",
    "line_item_blended_cost",
    "line_item_net_unblended_cost",
    "line_item_normalization_factor",
    "line_item_normalized_usage_amount",
    "line_item_unblended_cost",
    "line_item_usage_amount",
    "pricing_public_on_demand_cost",
    "reservation_amortized_upfront_cost_for_usage",
    "reservation_amortized_upfront_fee_for_billing_period",
    "reservation_effective_cost",
    "reservation_net_amortized_upfront_cost_for_usage",
    "reservation_net_amortized_upfront_fee_for_billing_period",
    "reservation_net_effective_cost",
    "reservation_net_recurring_fee_for_usage",
    "reservation_net_unused_amortized_upfront_fee_for_billing_period",
    "reservation_net_unused_recurring_fee",
    "reservation_net_upfront_value",
    "reservation_recurring_fee_for_usage",
    "reservation_unused_amortized_upfront_fee_for_billing_period",
    "reservation_unused_normalized_unit_quantity",
    "reservation_unused_quantity",
    "reservation_unused_recurring_fee",
    "reservation_upfront_value",
    "savings_plan_amortized_upfront_commitment_for_billing_period",
    "savings_plan_net_amortized_upfront_commitment_for_billing_period",
    "savings_plan_net_recurring_commitment_for_billing_period",
    "savings_plan_net_savings_plan_effective_cost",
    "savings_plan_recurring_commitment_for_billing_period",
    "savings_plan_savings_plan_effective_cost",
    "savings_plan_savings_plan_rate",
    "savings_plan_total_commitment_to_date",
    "savings_plan_used_commitment",
}

TIMESTAMP_COLUMNS = {
    "bill_billing_period_end_date",
    "bill_billing_period_start_date",
    "line_item_usage_end_date",
    "line_item_usage_start_date",
}

MAP_COLUMN_TYPES = {
    "cost_category": "map<string,string>",
    "discount": "map<string,double>",
    "product": "map<string,string>",
    "resource_tags": "map<string,string>",
    "tags": "map<string,string>",
}


def normalize_prefix(prefix: str) -> str:
    return prefix.strip("/")


def export_data_location(bucket_name: str, prefix: str = DEFAULT_PREFIX, export_name: str = DEFAULT_EXPORT_NAME) -> str:
    """S3 location of the export's Parquet, for the Athena table's LOCATION.

    AWS writes the data under a `data/` subdirectory and puts other files —
    `crawler-cfn.yml` among them — beside it. Pointing the table at the export
    root instead of `data/` makes Athena try to read that YAML as Parquet and
    the query fails, so the suffix matters.
    """
    normalized_prefix = normalize_prefix(prefix)
    path = f"{normalized_prefix}/{export_name}" if normalized_prefix else export_name
    return f"s3://{bucket_name}/{path}/data/"


def default_bucket_name(account_id: str) -> str:
    return f"maxops-cur-report-{account_id}"


def build_query_statement(columns: Iterable[str] = CUR_COLUMNS) -> str:
    return f"SELECT {', '.join(columns)} FROM COST_AND_USAGE_REPORT"


def quote_identifier(value: str) -> str:
    if not value.replace("_", "").isalnum():
        raise ValueError(f"Unsafe Athena identifier: {value}")
    return f"`{value}`"


def athena_type(column: str) -> str:
    if column in DOUBLE_COLUMNS:
        return "double"
    if column in TIMESTAMP_COLUMNS:
        return "timestamp"
    if column in MAP_COLUMN_TYPES:
        return MAP_COLUMN_TYPES[column]
    return "string"


def build_create_database_statement(database: str = DEFAULT_DATABASE) -> str:
    return f"CREATE DATABASE IF NOT EXISTS {quote_identifier(database)}"


def build_drop_external_table_statement(
    *,
    database: str = DEFAULT_DATABASE,
    table: str = DEFAULT_RAW_TABLE,
) -> str:
    return f"DROP TABLE IF EXISTS {quote_identifier(database)}.{quote_identifier(table)}"


def build_create_external_table_statement(
    *,
    database: str = DEFAULT_DATABASE,
    table: str = DEFAULT_RAW_TABLE,
    location: str,
    columns: Iterable[str] = CUR_COLUMNS,
) -> str:
    column_lines = [
        f"  {quote_identifier(column)} {athena_type(column)}"
        for column in columns
    ]
    return "\n".join(
        [
            f"CREATE EXTERNAL TABLE IF NOT EXISTS {quote_identifier(database)}.{quote_identifier(table)} (",
            ",\n".join(column_lines),
            ")",
            "STORED AS PARQUET",
            f"LOCATION '{location.rstrip('/')}/'",
        ]
    )


def build_bucket_policy(bucket_name: str, account_id: str, region: str = DEFAULT_REGION) -> Dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "EnableAWSDataExportsToWriteToS3",
                "Effect": "Allow",
                "Principal": {"Service": ["bcm-data-exports.amazonaws.com"]},
                "Action": ["s3:PutObject"],
                "Resource": f"arn:aws:s3:::{bucket_name}/*",
                "Condition": {
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bcm-data-exports:{region}:{account_id}:export/*"
                    },
                    "StringEquals": {"aws:SourceAccount": account_id},
                },
            }
        ],
    }


def build_export_payload(
    *,
    account_id: str,
    bucket_name: str,
    prefix: str = DEFAULT_PREFIX,
    region: str = DEFAULT_REGION,
    export_name: str = DEFAULT_EXPORT_NAME,
) -> Dict[str, Any]:
    return {
        "Name": export_name,
        "DataQuery": {
            "QueryStatement": build_query_statement(),
            "TableConfigurations": {
                "COST_AND_USAGE_REPORT": {
                    "INCLUDE_RESOURCES": "TRUE",
                    "INCLUDE_SPLIT_COST_ALLOCATION_DATA": "FALSE",
                    "INCLUDE_CAPACITY_RESERVATION_DATA": "FALSE",
                    "INCLUDE_IAM_PRINCIPAL_DATA": "FALSE",
                    "TIME_GRANULARITY": "DAILY",
                    "BILLING_VIEW_ARN": f"arn:aws:billing::{account_id}:billingview/primary",
                }
            },
        },
        "DestinationConfigurations": {
            "S3Destination": {
                "S3Bucket": bucket_name,
                "S3Prefix": normalize_prefix(prefix),
                "S3Region": region,
                "S3OutputConfigurations": {
                    "OutputType": "ATHENA",
                    "Format": "PARQUET",
                    "Compression": "PARQUET",
                    "Overwrite": "OVERWRITE_REPORT",
                },
            }
        },
        "RefreshCadence": {"Frequency": "SYNCHRONOUS"},
    }


def create_session(profile: Optional[str] = None):
    import boto3

    if profile:
        return boto3.Session(profile_name=profile)
    return boto3.Session()


def resolve_account_id(session, region: str) -> str:
    return session.client("sts", region_name=region).get_caller_identity()["Account"]


def _error_code(error: Exception) -> str:
    response = getattr(error, "response", {})
    return str(response.get("Error", {}).get("Code", ""))


def ensure_bucket(session, bucket_name: str, region: str) -> None:
    s3 = session.client("s3", region_name=region)

    try:
        s3.head_bucket(Bucket=bucket_name)
        return
    except Exception as error:
        if _error_code(error) in {"403", "AccessDenied"}:
            raise

    if region == "us-east-1":
        s3.create_bucket(Bucket=bucket_name)
    else:
        s3.create_bucket(
            Bucket=bucket_name,
            CreateBucketConfiguration={"LocationConstraint": region},
        )


def put_bucket_policy(session, bucket_name: str, policy: Dict[str, Any], region: str) -> None:
    s3 = session.client("s3", region_name=region)
    s3.put_bucket_policy(Bucket=bucket_name, Policy=json.dumps(policy))


def create_cur_export(session, export_payload: Dict[str, Any], region: str) -> Dict[str, Any]:
    """Create the export, treating an existing one of the same name as success.

    AWS rejects a duplicate export name outright. Since the desired end state
    is "this export exists", an existing one is not an error — and failing here
    would abort the caller before it gets to create the Athena table, which is
    usually the part that actually needs repairing.
    """
    data_exports = session.client("bcm-data-exports", region_name=region)
    try:
        return data_exports.create_export(Export=export_payload, ResourceTags=[])
    except Exception as error:
        message = str(error)
        already_exists = _error_code(error) == "ValidationException" and (
            "duplicate export name" in message.lower()
        )
        if not already_exists:
            raise
        return {"AlreadyExists": True, "ExportName": export_payload.get("Name")}


def run_athena_statement(
    session,
    statement: str,
    *,
    database: str,
    output_location: str,
    region: str,
    workgroup: str,
    poll_seconds: int = 2,
) -> str:
    athena = session.client("athena", region_name=region)
    response = athena.start_query_execution(
        QueryString=statement,
        QueryExecutionContext={"Database": database},
        ResultConfiguration={"OutputLocation": output_location},
        WorkGroup=workgroup,
    )
    query_execution_id = response["QueryExecutionId"]

    while True:
        execution = athena.get_query_execution(QueryExecutionId=query_execution_id)["QueryExecution"]
        state = execution["Status"]["State"]
        if state == "SUCCEEDED":
            return query_execution_id
        if state in {"FAILED", "CANCELLED"}:
            reason = execution["Status"].get("StateChangeReason", "No reason provided")
            raise RuntimeError(f"Athena query {query_execution_id} {state}: {reason}")
        time.sleep(poll_seconds)


def create_external_table(
    session,
    *,
    database: str,
    table: str,
    location: str,
    output_location: str,
    region: str,
    workgroup: str,
    recreate: bool = False,
) -> Dict[str, str]:
    database_query_id = run_athena_statement(
        session,
        build_create_database_statement(database),
        database="default",
        output_location=output_location,
        region=region,
        workgroup=workgroup,
    )
    drop_query_id = None
    if recreate:
        drop_query_id = run_athena_statement(
            session,
            build_drop_external_table_statement(database=database, table=table),
            database=database,
            output_location=output_location,
            region=region,
            workgroup=workgroup,
        )
    table_query_id = run_athena_statement(
        session,
        build_create_external_table_statement(database=database, table=table, location=location),
        database=database,
        output_location=output_location,
        region=region,
        workgroup=workgroup,
    )
    return {
        "database": database,
        "table": table,
        "location": location,
        "database_query_execution_id": database_query_id,
        "drop_query_execution_id": drop_query_id,
        "table_query_execution_id": table_query_id,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create the MaxOps CUR v2 daily data export.")
    parser.add_argument("--profile", help="AWS CLI profile to use.")
    parser.add_argument("--region", default=DEFAULT_REGION, help="AWS region for the export and bucket.")
    parser.add_argument("--account-id", help="AWS account ID. Defaults to STS caller identity.")
    parser.add_argument("--bucket-name", help="Destination bucket. Defaults to maxops-cur-report-{account_id}.")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help="S3 prefix for report output.")
    parser.add_argument("--database", default=DEFAULT_DATABASE, help="Athena/Glue database for the raw CUR table.")
    parser.add_argument("--raw-table", default=DEFAULT_RAW_TABLE, help="Athena/Glue raw CUR table name.")
    parser.add_argument("--table-location", help="S3 location for the raw CUR external table.")
    parser.add_argument("--athena-results-s3", help="S3 location for Athena DDL query results.")
    parser.add_argument("--workgroup", default="primary", help="Athena workgroup for external table DDL.")
    parser.add_argument("--skip-export", action="store_true", help="Create the external table only.")
    parser.add_argument("--skip-external-table", action="store_true", help="Create the export only.")
    parser.add_argument("--recreate-external-table", action="store_true", help="Drop and recreate the raw CUR table.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned payloads without changing AWS.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = create_session(args.profile)
    account_id = args.account_id or resolve_account_id(session, args.region)
    bucket_name = args.bucket_name or default_bucket_name(account_id)
    bucket_policy = build_bucket_policy(bucket_name, account_id, args.region)
    export_payload = build_export_payload(
        account_id=account_id,
        bucket_name=bucket_name,
        prefix=args.prefix,
        region=args.region,
    )
    table_location = args.table_location or export_data_location(bucket_name, args.prefix, DEFAULT_EXPORT_NAME)
    athena_results_s3 = args.athena_results_s3 or f"s3://{bucket_name}/athena-results/"
    create_database_statement = build_create_database_statement(args.database)
    create_table_statement = build_create_external_table_statement(
        database=args.database,
        table=args.raw_table,
        location=table_location,
    )
    drop_table_statement = build_drop_external_table_statement(database=args.database, table=args.raw_table)

    if args.dry_run:
        print(
            json.dumps(
                {
                    "AthenaResultsS3": athena_results_s3,
                    "BucketPolicy": bucket_policy,
                    "CreateDatabaseStatement": create_database_statement,
                    "CreateTableStatement": None if args.skip_external_table else create_table_statement,
                    "DropTableStatement": drop_table_statement if args.recreate_external_table else None,
                    "Export": None if args.skip_export else export_payload,
                    "TableLocation": table_location,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    ensure_bucket(session, bucket_name, args.region)
    result = {}
    if not args.skip_export:
        put_bucket_policy(session, bucket_name, bucket_policy, args.region)
        result["Export"] = create_cur_export(session, export_payload, args.region)
    if not args.skip_external_table:
        result["ExternalTable"] = create_external_table(
            session,
            database=args.database,
            table=args.raw_table,
            location=table_location,
            output_location=athena_results_s3,
            region=args.region,
            workgroup=args.workgroup,
            recreate=args.recreate_external_table,
        )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
