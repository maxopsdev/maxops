SELECT
  CAST(line_item_usage_start_date AS date) AS usage_date,
  bill_payer_account_id,
  line_item_usage_account_id,
  COALESCE(product_servicecode, line_item_product_code) AS service_code,
  product_region_code,
  product_location,
  SUM(COALESCE(TRY_CAST(line_item_usage_amount AS double), 0.0)) AS usage_amount,
  SUM(COALESCE(TRY_CAST(line_item_unblended_cost AS double), 0.0)) AS unblended_cost,
  SUM(COALESCE(TRY_CAST(line_item_net_unblended_cost AS double), 0.0)) AS net_unblended_cost,
  SUM(
    CASE
      WHEN line_item_line_item_type IN ('DiscountedUsage', 'DiscountUsage')
        THEN COALESCE(
          TRY_CAST(reservation_net_effective_cost AS double),
          TRY_CAST(reservation_effective_cost AS double),
          0.0
        )
      WHEN line_item_line_item_type = 'SavingsPlanCoveredUsage'
        THEN COALESCE(
          TRY_CAST(savings_plan_net_savings_plan_effective_cost AS double),
          TRY_CAST(savings_plan_savings_plan_effective_cost AS double),
          0.0
        )
      WHEN line_item_line_item_type IN ('SavingsPlanNegation', 'SavingsPlanUpfrontFee')
        THEN 0.0
      ELSE COALESCE(
        TRY_CAST(line_item_net_unblended_cost AS double),
        TRY_CAST(line_item_unblended_cost AS double),
        0.0
      )
    END
  ) AS net_amortized_cost,
  SUM(COALESCE(TRY_CAST(reservation_effective_cost AS double), 0.0)) AS reservation_effective_cost,
  SUM(COALESCE(TRY_CAST(reservation_net_effective_cost AS double), 0.0)) AS reservation_net_effective_cost,
  SUM(COALESCE(TRY_CAST(savings_plan_savings_plan_effective_cost AS double), 0.0)) AS savings_plan_effective_cost,
  SUM(COALESCE(TRY_CAST(savings_plan_net_savings_plan_effective_cost AS double), 0.0)) AS savings_plan_net_effective_cost,
  SUM(COALESCE(TRY_CAST(discount_total_discount AS double), 0.0)) AS total_discount,
  SUM(COALESCE(TRY_CAST(discount_bundled_discount AS double), 0.0)) AS bundled_discount
FROM {database}.{raw_table}
WHERE line_item_usage_start_date >= TIMESTAMP '{month_start} 00:00:00'
  AND line_item_usage_start_date < TIMESTAMP '{month_end} 00:00:00'
GROUP BY 1, 2, 3, 4, 5, 6
