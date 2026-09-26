# AWS Payload Generation Guide

## Purpose

The payload system records real AWS response shapes once and replays them in deterministic offline tests. It supports:

- adapter parsing tests
- check decision tests
- action request and response tests
- generator, sanitization, and cleanup contract tests

DynamoDB, EC2, EBS, ElastiCache, RDS, S3, CloudWatch, and VPC use the combined check and action capture workflow.

## Required Layout

Each service lives under `tests_generator/<service>/`:

- `resource_config.json`: AWS profile, region, placeholders, and capture resource definitions
- `checks.json`: check scenarios, expected matches, requested payload files, and permitted metric overrides
- `actions.json`: ordered action scenarios and expected AWS calls
- `tests_generator/payload_check_map.json`: repository-wide index of checks and scenarios expected to have payload coverage
- `<service>_resource_creation.py`: creates uniquely named and tagged capture resources and writes the state file
- `<service>_payloads_generator.py`: captures, sanitizes, and writes fixtures
- `<service>_resource_cleanup.py`: removes resources left by successful or failed capture runs

Generated fixtures live under:

```text
tests/payloads/<service>/<check_id>/<scenario>/
tests/payloads/<service>/actions/<action_key>/<scenario>/
```

Shared offline clients and fixture loaders belong in `tests/payload_helpers.py`.

### Capture gate

Every `<service>_resource_creation.py` and `<service>_payloads_generator.py`
entry point is a real-AWS capture command. Both require
`MAXOPS_RUN_AWS_INTEGRATION_TESTS=1` and the explicit `--apply` flag. The
scripts refuse before constructing a boto3 session when either gate is absent.

## Real AWS Provenance

Files under `tests/payloads/` must originate from real AWS calls made by the generator.

Allowed:

- sanitizing account IDs, resource IDs, names, and ARNs
- converting SDK datetime values to ISO strings
- applying narrow metric-value overrides to a real CloudWatch response

Not allowed:

- hand-authoring structural AWS responses
- fabricating successful mutation responses
- replacing `describe_*`, update, delete, backup, or policy responses with plausible JSON
- using generated demo data as an AWS payload fixture

Validation failures and simulated AWS errors should normally be ordinary unit tests. Real action fixtures are primarily for successful request/response contracts.

## Mandatory Capture Order

A combined generator must execute these phases:

1. Load configuration, manifests, and capture state.
2. Validate all referenced resource aliases.
3. Capture every check baseline before any resource mutation.
4. Write sanitized check fixtures.
5. Execute action scenarios in manifest order.
6. Record and sanitize each action request and real AWS response.
7. Verify the observed service, operation, and call order against `actions.json`.
8. Clean up from `finally`, whether capture succeeded or failed.

A check capture failure must prevent actions from running. An action failure must fail the command after cleanup.

The default command should run both phases and generate only missing or incomplete mapped check scenarios and action fixtures. A check scenario is incomplete when any file declared in `checks.json` is absent. An action fixture is incomplete when metadata or any recorded response file is absent.

`payload_check_map.json` tracks desired check coverage; fixture files track generated state. Do not add mutable generated/not-generated flags to the map because they can drift from the filesystem.

Services should expose full-regeneration flags and may expose phase flags for development and recovery:

- `--all-checks`: regenerate every mapped service check scenario
- `--all-actions`: regenerate every action fixture
- `--all`: regenerate all mapped checks and actions
- `--checks-only`: skip action execution
- `--actions-only`: skip check capture

For any integrated service:

```bash
MAXOPS_RUN_AWS_INTEGRATION_TESTS=1 python3 tests_generator/<service>/<service>_resource_creation.py create --apply
MAXOPS_RUN_AWS_INTEGRATION_TESTS=1 python3 tests_generator/<service>/<service>_payloads_generator.py --apply
```

Development-only phase selection:

```bash
python3 tests_generator/<service>/<service>_payloads_generator.py --checks-only
python3 tests_generator/<service>/<service>_payloads_generator.py --actions-only
python3 tests_generator/<service>/<service>_payloads_generator.py --all-checks --all-actions
python3 tests_generator/<service>/<service>_payloads_generator.py --all
```

Each generator invocation performs cleanup. Create a fresh resource state before running another phase.

## Check Scenario Contract

`checks.json` remains the source of truth for check fixtures. A scenario should contain:

- `description`
- capture resource references
- expected check matches
- check parameters
- payload file names
- metric overrides only when deterministic CloudWatch values are required

Capture resources should be few, inexpensive, and reusable across check scenarios. Structural resource state must come from AWS.

The generator must intersect `checks.json` with the service entries in `payload_check_map.json`. It must fail when a manifest check is unmapped or the map names an unknown scenario.

## Action Scenario Contract

`actions.json` is ordered because actions can transition or delete shared resources. Each entry uses this shape:

```json
{
  "action_key": {
    "scenario": "success",
    "check_id": "service_check_id",
    "description": "What real transition is captured.",
    "resource_ref": "capture_resource_alias",
    "parameters": {},
    "expected_status": "submitted",
    "expected_message": "Stable handler message.",
    "expected_calls": [
      {
        "service": "aws-service-name",
        "operation": "sdk_operation_name"
      }
    ]
  }
}
```

Service-specific fields, such as a GSI name, may be added when needed. Dynamic values should use placeholders from `resource_config.json`.

Each generated action directory must contain:

- one response file per AWS call, prefixed with its call number
- `capture_metadata.json`

Action metadata records:

- action key, check ID, scenario, resource ID, region, and parameters
- expected status and message
- the sanitized handler response
- ordered AWS calls with exact sanitized kwargs and response file names

## Recording And Replay

Action capture must execute the production handler through the production action registry. Wrap the real boto3 session with recording clients that:

- delegate each call to AWS
- retain exact kwargs
- retain the real response
- record `ClientError` responses when handlers intentionally probe for optional state
- record paginator invocations and all returned pages as one ordered logical call
- preserve cross-service call order

Offline replay clients must:

- consume one shared ordered call queue
- assert service and operation identity
- assert exact request kwargs
- return the corresponding captured response
- raise captured `ClientError` responses at the same call position
- replay captured paginator pages
- fail on extra, missing, or reordered calls

Response-only tests are insufficient because they do not detect incorrect action parameters.

When only some action fixtures are missing, execute the complete ordered action manifest to preserve prerequisite state transitions, but write only missing action directories. Full regeneration writes every action directory.

## Resource Reuse

Reuse a check resource for actions only when all check payloads have already been captured and the action order remains valid.

Use an action-only resource when:

- the check resource starts with unsuitable values
- one action would invalidate another scenario
- the action is destructive and another action still needs the resource
- restoring the original state is unreliable or more expensive than a disposable resource

Rules:

- perform reversible transitions before destructive operations
- wait for asynchronous updates to return to a usable state
- perform deletion actions last
- give every resource a unique capture-run suffix and identifying tags
- track action-created resources, including backups and autoscaling configuration, in capture state

For DynamoDB, check tables stay at minimum capacity. A separate `4 RCU / 4 WCU` table supports meaningful capacity reductions, and a separate table isolates backup-and-delete.

## Sanitization

Sanitize both action request kwargs and responses. Replace:

- account IDs
- generated resource names and IDs
- ARNs containing generated names or account IDs
- action-created identifiers such as backup ARNs

Preserve AWS field names, nesting, status values, and response metadata. Sanitization must be recursive and deterministic.

Tests must reject known capture-time identifiers and service-specific identifier patterns.

## Cleanup And Failure Handling

Cleanup must be idempotent and tolerate resources already removed by captured actions.

Track and remove:

- base capture resources
- action-only resources
- action-created backups or snapshots
- autoscaling policies and scalable targets
- any other secondary resources created by handlers

Delete dependent resources before parent resources. Treat the AWS service's not-found errors as successful cleanup. Return a structured cleanup summary and fail generation when real cleanup errors remain.

If a multi-call action partially succeeds, record newly created cleanup artifacts immediately from completed calls before re-raising the failure.

## Adding Another Service

1. Inventory the production checks, action handlers, SDK services, parameters, and call ordering.
2. Define the minimum real capture resources in `resource_config.json`.
3. Add deterministic check scenarios to `checks.json`.
4. Add ordered successful action scenarios to `actions.json`.
5. Decide which resources can be reused after check capture and which actions need disposable resources.
6. Implement real recording clients around the production action registry path.
7. Extend cleanup for every resource an action can create.
8. Add strict replay support in `tests/payload_helpers.py`.
9. Add manifest tests, payload-backed check tests, and payload-backed action tests.
10. Run the generator against AWS, inspect sanitization, and check in only generator-produced fixtures.

Do not copy DynamoDB-specific operations into another service. Reuse its lifecycle, recording, replay, sanitization, and testing contracts.

## Current Integrations

- DynamoDB captures all eight table, GSI, billing, capacity, autoscaling, backup, and deletion actions.
- EC2 captures stop and terminate after all instance check fixtures.
- EBS captures IOPS reduction, DLM lifecycle creation, and snapshot-before-delete. Volume downsizing is intentionally excluded.
- ElastiCache captures downsize, Graviton migration, Valkey upgrade, and deletion.
- RDS captures Graviton migration and the shared registry `stop` action using RDS check metadata.
- S3 captures all eleven lifecycle, logging, inventory, replication, and bucket-deletion actions.
- CloudWatch captures retention, alarm deletion, noisy-alarm choices, and duplicate consolidation.
- VPC captures gateway endpoint creation and all three explicit flow-log remediation choices.

All generators use `payload_check_map.json` for desired check coverage, generate only missing or incomplete fixtures by default, and expose the same phase and full-regeneration flags.
