# Architecture

This document covers how `maxops-backend` is laid out and how a scan flows end to end. It assumes you've read the root [README.md](../README.md) and [SETUP_GUIDE.md](../SETUP_GUIDE.md).

## The projects in this repo

| Project | Language | Talks to | Notes |
|---|---|---|---|
| `maxops-backend/` | Python 3.13, FastAPI | AWS APIs, SQLite | The whole product |
| `maxops-frontend/` | React 18 + TS + Vite + Tailwind | backend `:8000` | Single-page app |
| `maxops-mcp/` | Python, FastMCP | backend `/api/v1` over HTTP | stdio MCP server, self-contained |
| `maxops-plugin/` | Vanilla JS, MV2/MV3 | backend `/api/v1/inventory/ec2/overview` | AWS-console overlay browser extension |
| `maxops.dev/` | Static HTML/CSS/JS | nothing | Marketing landing page |

Every satellite talks to the backend only over `localhost` — there is no shared code between them.

## Backend layered architecture

```
app/main.py
  ├─ startup_checks.log_pricing_db_status()   # warns if maxops_pricing.db absent
  ├─ database.init_db()                       # SQLAlchemy create_all + ad-hoc ALTERs
  ├─ database.seed_db_if_empty()              # loads seed_data/*.yaml as default policies
  └─ routers under /api/v1

app/api/routes/     HTTP layer
app/services/       business logic
app/checks/         112 self-registering check functions across 32 resource types
app/actions/        write-side handlers (stop/terminate/modify/delete) per service
app/adapters/aws/   single boto3 facade — every AWS call goes through here
app/pricing/        per-service cost math on top of the street-pricing DB
app/models/         SQLAlchemy tables
rightsizers/        pure algorithm packages (EC2/ASG/RDS/ElastiCache) — no DB, no boto3
pricing/cur/        Athena/CUR reader (backend-only, experimental, no UI yet)
```

### The `rightsizers/` purity boundary

`rightsizers/` is a **pure** package: deterministic functions over already-normalized telemetry, with no AWS calls and no database access. `app/services/{ec2,asg,rds,elasticache}_rightsizer.py` are the **impure** adapters — they fetch data through `app/adapters/aws/` and feed it into the pure layer. This separation is the single most contributor-friendly property of the codebase: the pure layer is trivially unit-testable with plain data fixtures, no AWS account or mocking framework required.

## The primary user flow

```
1. ONBOARDING                    frontend /onboarding  →  POST /settings/onboarding
   ├─ Welcome → pricing DB gate (GET/POST /onboarding/pricing-database[/unpack])
   ├─ RoleCreationView → POST /onboarding/iam/read-only-role
   │     services/iam_onboarding_service.py → creates a real read-only IAM role
   │     (or POST /onboarding/iam/use-existing-role to register an existing role
   │     ARN without any IAM writes, for credentials that can't create roles)
   ├─ EnvironmentSetupView → account/region/thresholds
   └─ ChecksExecutionView → POST /onboarding/run-all-checks
         → POST /onboarding/save-results   (persists OnboardingExecution + results)

   The app is hard-gated: App.tsx redirects every route to /onboarding until
   onboarding_completed AND account AND region are all set.

2. SCAN / INVENTORY              POST /scans/start  →  services/scan_service.py
   ├─ require_account_region()                    (utils/settings_guard)
   ├─ CachedAWSAdapter wraps AWSAdapter            (dedupes describe* calls per scan)
   ├─ check enablement filter                      (services/settings_service.is_check_enabled)
   ├─ inventory collection                         → *_inventory tables + resource_tags
   ├─ rightsizing metric enrichment                → CloudWatch percentiles, trend caches
   ├─ pricing application                          → services/street_pricing.py (reads maxops_pricing.db)
   ├─ check execution                              → checks/registry.execute_check()
   └─ finding storage                              → maxops_inventory (unified findings)
   Progress polled via GET /scans/{execution_id}

3. VIEW                          frontend Dashboard + per-service Overview pages
   GET /inventory/{ec2,s3,rds,elasticache,asg,dynamodb,ebs}/overview
        → services/inventory_service.py (one get_*_overview per service)
   GET /checks/latest-results, /checks/last-runs

4. RIGHTSIZE                     frontend /rightsizer
   GET /rightsizer/resource-types
   GET /rightsizer/resources/{type}[/{id}]        → services/rightsizer_service.py
   GET /recommendations/{asg,rds,elasticache}/rightsize[/{id}][/trend]
        → services/{asg,rds,elasticache}_rightsizer.py
        → rightsizers/<svc>/  (pure: normalization → evaluation → risk → warnings)
   Classification: ACTIONABLE / CONDITIONAL / OPPORTUNITY / DEFERRED

5. TUNE                          frontend /policies ("Checks Manager") + /settings
   GET/PUT /checks/{id}/filters                    → utils/check_filter_applier.py
   POST /checks/{id}/resources/{rid}/snooze        → utils/resource_snooze.py
   GET/PUT /settings, /settings/catalog            → services/settings_service.py
   CRUD /policies, /policy-templates, /policy-filters

6. ACT                           CheckDetails → ResourceActionPanel
   POST /checks/{check_id}/actions                 → app/actions/registry.py
        gated by app_settings.actions_enabled (MAXOPS_ENABLE_ACTIONS) and the
        per-action enabled flag in Settings (services/settings_service.is_action_enabled)
        → handlers_<service>.py (stop, terminate, modify, delete, snapshot, tag…)
        → ActionExecution row

7. EXTERNAL CONSUMERS
   maxops-mcp    → GET /mcp/*   (write tools off by default)
   maxops-plugin → GET /inventory/ec2/overview
```

## Data model

| Group | Tables | Notes |
|---|---|---|
| Inventory | `maxops_inventory`, `ec2_inventory`, `s3_inventory`, `rds_inventory`, `elasticache_inventory`, `asg_inventory`, `ebs_inventory`, `dynamodb_inventory`, `resource_tags` | The spine — one row per discovered AWS resource, plus normalized tags |
| Settings / onboarding | `user_settings` (legacy, kept for migration), `account_settings`, `check_settings`, `action_settings`, `onboarding_executions`, `onboarding_check_results`, `check_filters`, `resource_exemptions` | Per-account scope, thresholds, and per-check/per-action enablement |
| Policies | `policies`, `policy_executions`, `policy_execution_results`, `policy_cost_savings` | Backs the Checks Manager UI |
| Actions | `action_executions` | Audit trail of every action MaxOps has taken |
| Pricing | `pricing_cache` + external `maxops_pricing.db` | Street pricing, unpacked from the bundled artifact |

The database schema is self-managing: `database.init_db()` runs `Base.metadata.create_all()` on every startup plus a set of additive `ALTER TABLE` statements for older local databases (`database._sync_existing_schema`). The `alembic/` migrations in this repo exist as a historical record and are not invoked automatically — if you add a column to an existing table's model, also add the matching `ALTER TABLE` to `_sync_existing_schema` so existing local databases pick it up.
