# Checks

A **check** inspects one AWS resource type for a specific waste or optimization pattern and returns the resources that match. MaxOps ships 112 checks across 32 resource types (EC2, RDS, S3, EBS, ElastiCache, DynamoDB, ASG, Lambda, and more).

Every check:

- Is a plain Python function taking `aws_adapter` plus tunable parameters (thresholds, lookback windows)
- Returns a list of flagged resources with `resource_id`, `resource_type`, `region`, and a `metadata` dict (including a recommended action and human-readable reason)
- Self-registers into a central registry with a `CheckMetadata` record — no manual wiring elsewhere

Checks are enabled by default. Turn individual checks off in **Settings** — a disabled check is skipped during scans without needing to touch code or configuration.

## Presets

Each check has conservative/normal/aggressive presets that adjust its thresholds, plus per-check parameter overrides in Settings if you want finer control than a preset gives you.

## Writing a new check

See [docs/ADDING_A_CHECK.md](https://github.com/maxopsdev/maxops/blob/main/docs/ADDING_A_CHECK.md) in the repository — it's a ~20 minute task and the most approachable first contribution, with no real AWS account required (checks are tested against recorded payload fixtures, not live calls).
