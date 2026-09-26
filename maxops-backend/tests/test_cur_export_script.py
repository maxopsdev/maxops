import importlib.util
import pathlib


SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "stacks" / "CUR" / "create_cur_export.py"
SPEC = importlib.util.spec_from_file_location("create_cur_export", SCRIPT_PATH)
cur_export = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cur_export)


def test_query_statement_contains_only_requested_columns_in_order():
    statement = cur_export.build_query_statement()

    assert statement == f"SELECT {', '.join(cur_export.CUR_COLUMNS)} FROM COST_AND_USAGE_REPORT"
    assert cur_export.CUR_COLUMNS == [
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


def test_default_bucket_name_uses_account_id():
    assert cur_export.default_bucket_name("123456789012") == "maxops-cur-report-123456789012"


def test_export_data_location_points_at_the_data_subdirectory():
    # AWS writes the Parquet under data/ and puts crawler-cfn.yml beside it.
    # A table pointed at the export root tries to read that YAML as Parquet.
    assert (
        cur_export.export_data_location("maxops-cur-report-123456789012", "/curv2/", "maxops-curv2-daily")
        == "s3://maxops-cur-report-123456789012/curv2/maxops-curv2-daily/data/"
    )


def test_create_external_table_statement_matches_cur_export_schema():
    statement = cur_export.build_create_external_table_statement(
        database="maxops_cur",
        table="curv2_raw",
        location="s3://maxops-cur-report-123456789012/curv2/maxops-curv2-daily/",
    )

    assert statement.startswith("CREATE EXTERNAL TABLE IF NOT EXISTS `maxops_cur`.`curv2_raw` (")
    assert "`line_item_usage_start_date` timestamp" in statement
    assert "`line_item_unblended_cost` double" in statement
    assert "`line_item_unblended_rate` string" in statement
    assert "`savings_plan_savings_plan_effective_cost` double" in statement
    assert "`reservation_effective_cost` double" in statement
    assert "`reservation_normalized_units_per_reservation` string" in statement
    assert "`reservation_start_time` string" in statement
    assert "`discount` map<string,double>" in statement
    assert "`resource_tags` map<string,string>" in statement
    assert "STORED AS PARQUET" in statement
    assert "LOCATION 's3://maxops-cur-report-123456789012/curv2/maxops-curv2-daily/'" in statement


def test_drop_external_table_statement_quotes_database_and_table():
    assert (
        cur_export.build_drop_external_table_statement(database="maxops_cur", table="curv2_raw")
        == "DROP TABLE IF EXISTS `maxops_cur`.`curv2_raw`"
    )


def test_bucket_policy_matches_data_exports_requirements():
    policy = cur_export.build_bucket_policy("maxops-cur-report-123456789012", "123456789012")
    statement = policy["Statement"][0]

    assert statement["Principal"] == {"Service": ["bcm-data-exports.amazonaws.com"]}
    assert statement["Action"] == ["s3:PutObject"]
    assert statement["Resource"] == "arn:aws:s3:::maxops-cur-report-123456789012/*"
    assert statement["Condition"] == {
        "ArnLike": {
            "aws:SourceArn": "arn:aws:bcm-data-exports:us-east-1:123456789012:export/*"
        },
        "StringEquals": {"aws:SourceAccount": "123456789012"},
    }


def test_export_payload_matches_daily_curv2_configuration():
    payload = cur_export.build_export_payload(
        account_id="123456789012",
        bucket_name="maxops-cur-report-123456789012",
        prefix="/curv2",
    )

    assert payload["Name"] == "maxops-curv2-daily"
    assert payload["DataQuery"]["QueryStatement"] == cur_export.build_query_statement()
    assert payload["DataQuery"]["TableConfigurations"]["COST_AND_USAGE_REPORT"] == {
        "INCLUDE_RESOURCES": "TRUE",
        "INCLUDE_SPLIT_COST_ALLOCATION_DATA": "FALSE",
        "INCLUDE_CAPACITY_RESERVATION_DATA": "FALSE",
        "INCLUDE_IAM_PRINCIPAL_DATA": "FALSE",
        "TIME_GRANULARITY": "DAILY",
        "BILLING_VIEW_ARN": "arn:aws:billing::123456789012:billingview/primary",
    }
    assert payload["DestinationConfigurations"]["S3Destination"] == {
        "S3Bucket": "maxops-cur-report-123456789012",
        "S3Prefix": "curv2",
        "S3Region": "us-east-1",
        "S3OutputConfigurations": {
            "OutputType": "ATHENA",
            "Format": "PARQUET",
            "Compression": "PARQUET",
            "Overwrite": "OVERWRITE_REPORT",
        },
    }
    assert payload["RefreshCadence"] == {"Frequency": "SYNCHRONOUS"}


class StubSession:
    def client(self, service_name, region_name=None):
        raise AssertionError(f"dry-run should not create a {service_name} client")


def test_dry_run_does_not_call_mutating_boto_methods(monkeypatch, capsys):
    monkeypatch.setattr(
        cur_export,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "profile": None,
                "region": "us-east-1",
                "account_id": "123456789012",
                "bucket_name": None,
                "prefix": "/curv2",
                "database": "maxops_cur",
                "raw_table": "curv2_raw",
                "table_location": None,
                "athena_results_s3": None,
                "workgroup": "primary",
                "skip_export": False,
                "skip_external_table": False,
                "recreate_external_table": False,
                "dry_run": True,
            },
        )(),
    )
    monkeypatch.setattr(cur_export, "create_session", lambda profile: StubSession())

    assert cur_export.main() == 0
    output = capsys.readouterr().out
    assert '"Name": "maxops-curv2-daily"' in output
    assert '"S3Bucket": "maxops-cur-report-123456789012"' in output
    assert '"TableLocation": "s3://maxops-cur-report-123456789012/curv2/maxops-curv2-daily/data/"' in output
    assert "CREATE EXTERNAL TABLE IF NOT EXISTS `maxops_cur`.`curv2_raw`" in output
