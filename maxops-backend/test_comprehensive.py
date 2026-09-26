from seed_comprehensive_demo import seed_comprehensive_demo_data

summary = seed_comprehensive_demo_data(force=False)
print('✅ Comprehensive demo data seeded successfully:')
print(f"  account_id: {summary['account_id']}")
print(f"  regions: {summary['regions']}")
print(f"  checks_processed: {summary['checks_processed']}")
print(f"  policies_created: {summary['policies_created']}")
print(f"  resources_created: {summary['resources_created']}")
print(f"  executions_created: {summary['executions_created']}")
print(f"  recommendations_created: {summary['recommendations_created']}")
print(f"  historical_period_days: {summary['historical_period_days']}")
print(f"  min_executions_per_check: {summary['min_executions_per_check']}")