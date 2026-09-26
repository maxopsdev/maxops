# EC2 Synthetic Data Generation Guide

This document defines the current synthetic EC2 generation contract so future engineers and LLMs keep the same DB-oriented shape, file layout, and formatting.

## Purpose

The synthetic EC2 dataset is separate from test payload generation.

It exists to produce believable EC2 account data for:
- DB import prototypes
- UI demos backed by imported data
- offline product development without live AWS

The source of truth for this flow lives in:
- [ec2_syn_config.json](ec2_syn_config.json)
- [ec2_syn_data.py](ec2_syn_data.py)

The default generated artifacts live in:
- `synthetic_data/generated/ec2`

## Current Contract

The generator must produce exactly two JSON files under `synthetic_data/generated/ec2/`:

1. `inventory.json`
2. `ec2_maxops.json`

Do not add a third summary or findings file in this flow.

The join key between the two files is:
- `inventory_id`

Every generated EC2 instance must produce:
- one AWS-like instance object inside `inventory.json.aws_payload`
- one flattened instance record inside `inventory.json.instances`
- one companion row inside `ec2_maxops.json`

## Output 1: `inventory.json`

`inventory.json` is one top-level object with:
- `generated_at`
- `account_id`
- `inventory_count`
- `aws_payload`
- `instances`

### `aws_payload`

`aws_payload` should stay close to a real `describe_instances` response, modeled after:
- [describe_instances.json](../tests/payloads/ec2/ec2_idle_instances/pass_idle_instance/describe_instances.json)

Required shape:
- `Reservations`
- each reservation includes:
  - `OwnerId`
  - `ReservationId`
  - `Instances`
- each instance includes AWS-style keys such as:
  - `InstanceId`
  - `InstanceType`
  - `State`
  - `Placement`
  - `Tags`
  - `VpcId`
  - `SubnetId`
  - `SecurityGroups`
  - `LaunchTime`

Synthetic addition:
- each AWS-like instance also includes `InventoryId`

This is the only intentional non-AWS field in the AWS-like payload because it gives the future importer a direct bridge to MaxOps companion data.

### `instances`

`instances` is the flattened import-friendly list.

Each record should contain:
- `inventory_id`
- `resource_id`
- `resource_name`
- `resource_type`
- `account_id`
- `region`
- `availability_zone`
- `state`
- `instance_type`
- `launch_time`
- `tags`
- `metadata`

`metadata` should contain realistic supporting details such as:
- `vpc_id`
- `subnet_id`
- `security_groups`
- `cloudwatch_agent_installed`
- `avg_cpu_utilization`
- `avg_network_in`
- `avg_network_out`
- `usage_profile`
- `monthly_cost_estimate`
- `owner`
- `criticality`
- `business_service`
- `uptime_pattern`
- `last_deployment_at`
- `metric_history`

Rule:
- inventory data is inventory-focused
- do not store MaxOps finding fields as the primary representation here when they belong in `ec2_maxops.json`
- long-form chart history belongs here, not in `ec2_maxops.json`

## Output 2: `ec2_maxops.json`

`ec2_maxops.json` is a flat list with one row per `inventory_id`.

Each row should contain:
- `inventory_id`
- `check_id` or `null`
- `finding_type` or `null`
- `title` or `null`
- `description` or `null`
- `severity` or `null`
- `confidence_score` or `null`
- `risk_score` or `null`
- `recommended_action` or `null`
- `recommended_actions`
- `available_actions`
- `potential_savings_monthly`
- `potential_savings_yearly`
- `evidence`
- `current_config`
- `target_config`
- `metadata`

Rules:
- every inventory row gets exactly one MaxOps row
- actionable instances get populated recommendation data
- healthy instances still get a neutral row with no recommendation
- the v1 model is one MaxOps row per instance, not many findings per instance

## Config Expectations

The JSON config should stay the main control surface.

Key sections:
- `generator`
- `regions`
- `distributions`
- `tag_dimensions`
- `naming`
- `usage_profiles`
- `finding_profiles`
- `action_catalog`

Use config to tune:
- instance count
- seed
- region spread
- environment spread
- tag diversity
- instance family mix
- usage profile mix
- finding behavior
- action options

Avoid hard-coding scenario distribution logic in Python unless it is true generator behavior.

## Realism Rules

The generated account should look like a real multi-team AWS estate.

That means:
- hundreds of instances
- mixed prod, dev, uat, sit, qa, and sandbox tags
- realistic owners, teams, cost centers, and business units
- mixed instance families and sizes
- mixed regions and AZs
- believable usage metrics

Recommendation realism:
- stopped instances should map to unused-style MaxOps rows
- non-Graviton migration suggestions should only apply to non-Graviton types
- busy production workloads should not be labeled idle
- healthy instances should remain visible through neutral MaxOps rows

## Separation From Test Payloads

Do not mix this system with `tests/payloads`.

Synthetic generation is for:
- realistic account datasets
- import experiments
- product demos
- long-form chart and trend history

Payload generation is for:
- adapter/check correctness testing
- preserving AWS response structure from real captures
- providing the seed shape for synthetic metric history generation

They solve different problems and should stay separate.

Critical rule:
- `synthetic_data/generated/...` artifacts may be fully synthetic
- `tests/payloads/...` artifacts must not be synthetic stand-ins
- synthetic outputs must never be copied into `tests/payloads/` or used to fake a missing AWS capture

If a payload capture has not been run against AWS yet, the correct state is “payloads not available yet,” not “invent some JSON that looks close enough.”

Clarification:
- payload generators may still apply limited metric overrides after capturing a real AWS baseline response
- that is part of the payload-generation workflow contract
- but those overrides must be layered on top of real captured payloads, not used as a substitute for capture
- synthetic generators under `synthetic_data/` remain the only place where fully invented datasets are acceptable

## Pattern To Reuse For Other Services

When replicating synthetic data generation for another AWS service, follow the same high-level pattern used by EC2 and S3:

1. Keep synthetic generation under `synthetic_data/`, not under `tests/payloads`.
2. Create a service config file such as `<service>_syn_config.json`.
3. Create a service generator such as `<service>_syn_data.py`.
4. Write generated artifacts into `synthetic_data/generated/<service>/`.
5. Add one dedicated test module for the synthetic generator.
6. Use payload fixtures as shape seeds when they exist, but do not copy test payload directories directly into output.

The synthetic pipeline exists for:
- realistic account datasets
- UI demos
- DB import experiments
- product development without live AWS

It does not exist for:
- adapter correctness testing
- preserving raw captured AWS payloads as test fixtures
- replacing the `tests/payloads` contract

## Reusable Service Contract

Each synthetic service generator should expose the same core interface:
- `generate_dataset(config, count_override=None, seed_override=None) -> GeneratedArtifacts`
- `write_artifacts(artifacts, output_dir) -> None`
- CLI entrypoint:
  - `python3 synthetic_data/<service>_syn_data.py`
  - optional `--count`
  - optional `--seed`
  - optional `--config`
  - optional `--output-dir`

Each service should produce:
- one inventory-style JSON file
- one MaxOps-style JSON file

Rules:
- keep exactly one inventory row and one MaxOps row per synthetic resource
- use `inventory_id` as the join key
- keep healthy resources visible through neutral MaxOps rows
- keep the v1 model to one MaxOps row per resource, not many findings per resource

## File And Folder Pattern

For a new service named `<service>`, follow this layout:
- config
  - `synthetic_data/<service>_syn_config.json`
- generator
  - `synthetic_data/<service>_syn_data.py`
- outputs
  - `synthetic_data/generated/<service>/inventory.json`
  - `synthetic_data/generated/<service>/<service>_maxops.json`
- tests
  - `tests/test_<service>_synthetic_data.py`

## RDS Synthetic Data

The RDS synthetic generator follows the same two-file pattern used by EC2 and S3.

Source of truth:
- [rds_syn_config.json](rds_syn_config.json)
- [rds_syn_data.py](rds_syn_data.py)

Default generated artifacts:
- `synthetic_data/generated/rds`

The RDS flow is scoped to non-Aurora DB instances and currently supports the same checks covered by the RDS payload fixtures:
- `rds_idle_databases`
- `rds_non_graviton_instance_class`

### RDS Inventory Contract

`synthetic_data/generated/rds/inventory.json` is one top-level object with:
- `generated_at`
- `account_id`
- `inventory_count`
- `aws_payload`
- `instances`

`aws_payload` stays close to a real `describe_db_instances` response and includes:
- `DBInstances`
- `TagDetails`
- `MetricDetails`

Each AWS-like DB instance also includes `InventoryId` as the bridge to MaxOps data.

Each flattened instance row in `instances` should contain:
- `inventory_id`
- `resource_id`
- `resource_name`
- `resource_type`
- `account_id`
- `region`
- `availability_zone`
- `engine`
- `db_instance_class`
- `state`
- `created_at`
- `tags`
- `metadata`

`metadata` should keep the rich synthetic details such as:
- engine and version
- storage settings
- subnet and security-group metadata
- average utilization values
- long-form metric history
- owner and business tags

### RDS MaxOps Contract

`synthetic_data/generated/rds/rds_maxops.json` is a flat list with one row per `inventory_id`.

Rules:
- keep exactly one MaxOps row per RDS instance
- idle instances should map to `rds_idle_databases`
- non-Graviton classes should map to `rds_non_graviton_instance_class`
- healthy instances should still get neutral rows

### RDS Variety Rules

The synthetic RDS dataset should be broader than the live capture set.

Defaults should include:
- multiple non-Aurora engines:
  - `postgres`
  - `mysql`
  - `mariadb`
- a visible mix of Graviton and non-Graviton instance classes
- at least `db.t4g.micro` in the Graviton pool
- additional non-Graviton classes such as `db.t3.micro`, `db.m5.large`, and `db.r5.large`

Engine and instance-class variety should be configured in `rds_syn_config.json`, not hard-coded deep in the generator.

If the service needs service-specific naming for the inventory file contents, keep the file name `inventory.json` and vary the internal list key instead:
- EC2 uses `instances`
- S3 uses `buckets`

## ElastiCache Synthetic Data

ElastiCache follows the same synthetic-data split as EC2, RDS, and S3, but it models two resource types in one service dataset:
- standalone cache clusters
- replication groups

Source of truth:
- [elasticache_syn_config.json](elasticache_syn_config.json)
- [elasticache_syn_data.py](elasticache_syn_data.py)

Default generated artifacts:
- `synthetic_data/generated/elasticache`

Current ElastiCache contract:
- generate exactly two JSON files under `synthetic_data/generated/elasticache/`
- `inventory.json`
- `elasticache_maxops.json`

The join key is:
- `inventory_id`

`inventory.json` must contain:
- `generated_at`
- `account_id`
- `inventory_count`
- `aws_payload`
- `resources`

`aws_payload` should contain:
- `CacheClusters`
- `ReplicationGroups`
- `MetricDetails`

Each AWS-like resource in `CacheClusters` or `ReplicationGroups` also includes `InventoryId` as the bridge field to MaxOps data.

The flat `resources` list is the import-friendly combined list for both resource types.

Each resource row must include:
- `inventory_id`
- `resource_id`
- `resource_name`
- `resource_type`
- `account_id`
- `region`
- `availability_zone`
- `state`
- `tags`
- `metadata`

`resource_type` must distinguish:
- `elasticache_cluster`
- `elasticache_replication_group`

`metadata` should carry the rich synthetic details used by import demos and UI work, including:
- `engine`
- `engine_version`
- `cache_node_type`
- `num_cache_nodes`
- `member_clusters`
- `num_node_groups`
- `replicas_per_node_group`
- `usage_profile`
- `monthly_cost_estimate`
- `owner`
- `team`
- `business_unit`
- `criticality`
- `metric_history`

`elasticache_maxops.json` is one row per `inventory_id` and follows the same broad MaxOps row contract as the other services:
- actionable resources get one finding row
- healthy resources still get a neutral row with `check_id = null`
- do not duplicate full metric history into MaxOps metadata

The ElastiCache flow should cover the current service checks:
- `elasticache_low_item_count`
- `elasticache_non_graviton_instance_class`
- `elasticache_redis_convertible_to_valkey`

Variety rules:
- include both Graviton and non-Graviton node types
- include both clusters and replication groups
- include Redis resources eligible for Valkey migration
- include older or ineligible Redis resources
- include low-item, healthy, and missing-metrics usage patterns

Seed-shape rule:
- use checked-in ElastiCache payload fixtures as structural seeds
- use those fixtures only for shape, not for copied identifiers or endpoints
- generated values such as resource ids, ARNs, endpoints, timestamps, metrics, and tags must be synthetic and deterministic

Default generation:

```bash
python3 synthetic_data/elasticache_syn_data.py
```

Override count or seed:

```bash
python3 synthetic_data/elasticache_syn_data.py --count 60 --seed 42
```

Override config path:

```bash
python3 synthetic_data/elasticache_syn_data.py --config synthetic_data/elasticache_syn_config.json
```

Tests live in:
- [test_elasticache_synthetic_data.py](../tests/test_elasticache_synthetic_data.py)

They should validate:
- determinism
- two-file output contract
- combined resource inventory shape
- one-to-one join integrity on `inventory_id`
- healthy neutral MaxOps rows exist
- realism guards for low-item, non-Graviton, and Valkey-convertible findings
- no leakage of captured ElastiCache payload identifiers into generated output

## What Belongs In Config vs Python

Keep config as the main control surface.

Put in config:
- seed
- default count
- account and region defaults
- weighted distributions
- naming rules
- usage or profile families
- finding profiles
- action catalog
- service-specific realism knobs

Put in Python:
- deterministic generation mechanics
- shape transformation from seed payloads into synthetic outputs
- join integrity
- small helper logic that is true generator behavior

Avoid hard-coding business distributions in Python when they are really product-tuning inputs.

## How To Replicate This For Another Service

1. Confirm the service already has payload-backed fixtures or identify the seed shapes you need.
2. Decide the default synthetic resource count.
3. Define the inventory output shape for that resource type.
4. Define the companion MaxOps row shape.
5. Identify the resource profiles that matter for UI realism.
6. Map those profiles to finding profiles and neutral profiles.
7. Build seed-shape loaders from `tests/payloads/<service>/` if available.
8. Generate deterministic synthetic resources with realistic names, ownership, and metadata.
9. Derive one MaxOps row per resource.
10. Write the two output files.
11. Add generator tests for determinism, shape, join integrity, coverage, and realism guards.

## Recommended Pattern For Service Modeling

For each new service, model it in three layers:

1. seed shapes
   - real or payload-derived AWS-like structures
2. flattened inventory
   - import-friendly resource rows
3. MaxOps companion data
   - finding or neutral rows for UI/product behavior

This layering is the core pattern followed by both existing synthetic generators:
- AWS-like shape for realism and importer alignment
- flattened inventory for app consumption
- MaxOps row for findings/recommendations

## Testing Pattern To Reuse

Every new service synthetic generator should have tests that validate:
- determinism for identical config + seed
- exact output file contract
- required inventory keys
- required MaxOps keys
- one-to-one join integrity on `inventory_id`
- healthy neutral rows exist
- service-specific realism guards
- generated data does not accidentally collapse into only flagged or only healthy resources

## How To Know You Followed The Pattern Correctly

The synthetic flow is following the intended pattern if:
- it is clearly separate from payload generation
- it produces exactly two primary JSON artifacts
- each resource has one inventory row and one MaxOps row
- the output is deterministic under a fixed seed
- config drives the dataset shape more than hard-coded Python branching
- the dataset looks believable for demos, not just mechanically valid

## Workflow

Default generation:

```bash
python3 synthetic_data/ec2_syn_data.py
```

Override count or seed:

```bash
python3 synthetic_data/ec2_syn_data.py --count 500 --seed 42
```

Override config path:

```bash
python3 synthetic_data/ec2_syn_data.py --config synthetic_data/ec2_syn_config.json
```

## Testing Expectations

Tests live in:
- [test_ec2_synthetic_data.py](../tests/test_ec2_synthetic_data.py)

They should validate:
- determinism
- two-file output contract
- AWS-like inventory shape
- flattened import shape
- one-to-one join integrity on `inventory_id`
- neutral MaxOps rows for healthy instances
- realism guards such as stopped-to-unused and non-Graviton migration targeting

## S3 Synthetic Data

S3 follows the same synthetic-data split as EC2, but for bucket-centric UI/demo datasets.

Source of truth:
- [s3_syn_config.json](s3_syn_config.json)
- [s3_syn_data.py](s3_syn_data.py)

Default generated artifacts:
- `synthetic_data/generated/s3`

Current S3 contract:
- generate exactly two JSON files under `synthetic_data/generated/s3/`
- `inventory.json`
- `s3_maxops.json`

The join key is:
- `inventory_id`

`inventory.json` must contain:
- `generated_at`
- `account_id`
- `inventory_count`
- `aws_payload`
- `buckets`

`aws_payload` is list-buckets-centered and includes structured per-bucket companion details.

Each bucket row in `buckets` must include:
- `inventory_id`
- `resource_id`
- `resource_name`
- `resource_type`
- `account_id`
- `region`
- `state`
- `tags`
- `metadata`

`s3_maxops.json` is one row per `inventory_id` and mirrors the broad EC2 MaxOps contract:
- actionable buckets get one finding row
- healthy buckets still get a neutral row with `check_id = null`

S3 synthetic generation uses checked-in S3 payload fixtures as shape seeds, but remains separate from `tests/payloads/s3`.

Default generation:

```bash
python3 synthetic_data/s3_syn_data.py
```

Override count or seed:

```bash
python3 synthetic_data/s3_syn_data.py --count 50 --seed 42
```

Override config path:

```bash
python3 synthetic_data/s3_syn_data.py --config synthetic_data/s3_syn_config.json
```

Tests live in:
- [test_s3_synthetic_data.py](../tests/test_s3_synthetic_data.py)
