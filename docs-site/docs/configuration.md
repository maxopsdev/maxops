# Configuration

MaxOps reads configuration from environment variables (`maxops-backend/app/config.py`). Only variables actually read by the app are listed below — anything else in an old `.env` file is silently ignored (pydantic-settings `extra = "ignore"`).

## Application

| Variable | Default | Notes |
|---|---|---|
| `APP_NAME` | `MaxOps API` | |
| `DEBUG` | `false` | Also enables the debug-only `/seed/*` routes |
| `LOG_LEVEL` | `INFO` | |

## Database

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./maxops.db` | Relative `sqlite:///./...` paths resolve against the backend root |
| `PRICING_DATABASE_PATH` | `./maxops_pricing.db` | Where the bundled pricing artifact gets unpacked to |

## AWS credentials

Pick **one** option:

| Variable | Notes |
|---|---|
| `AWS_PROFILE` | Local AWS CLI profile name — recommended for local use |
| `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` | Works everywhere, no `~/.aws` needed |
| `AWS_USE_IAM_ROLE=true` + `AWS_ROLE_ARN` + `AWS_ROLE_SESSION_NAME` | For running the backend itself on EC2/ECS |
| `AWS_REGION` | Default `us-east-1` |

You can also configure or override AWS credentials from the onboarding wizard's IAM role step — see [AWS Setup & Permissions](aws-setup.md).

## Feature flags — the two that matter most

| Variable | Default | What it does |
|---|---|---|
| `TEST_ACTION` | `false` | Enables the UI's safe/no-op "test action" affordance |
| `MAXOPS_ENABLE_ACTIONS` | `false` | **Global gate for real (non-test) AWS actions** — stop/terminate/delete/modify. Nothing destructive can run until this is `true`, even if a check/action is individually enabled in Settings |

!!! warning
    Leave `MAXOPS_ENABLE_ACTIONS=false` until you're ready. See [Security Model](security.md).

## Cost data (optional)

| Variable | Default | Notes |
|---|---|---|
| `COST_EXPLORER_LOOKBACK_DAYS` | `90` | |
| `CUR_BUCKET_NAME` | *(empty)* | Only used if you wire up Cost & Usage Reports |
| `CUR_REPORT_PREFIX` | `cost-reports/` | |

See `maxops-backend/env.example` in the repository for the canonical, commented reference file.
