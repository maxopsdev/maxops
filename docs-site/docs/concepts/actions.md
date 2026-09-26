# Actions

An **action** is a write operation MaxOps can take against a flagged resource — stop, terminate, modify, delete, snapshot, tag, and similar, implemented per-service in `app/actions/handlers_<service>.py`.

## Two layers of gating

Actions are off by default, behind two independent gates:

1. **`MAXOPS_ENABLE_ACTIONS`** (environment variable, default `false`) — the global switch. Nothing destructive can run anywhere in the app until this is `true`.
2. **Per-action enablement in Settings** — even with the global gate on, each action can be individually enabled or disabled per resource type. A disabled action returns a `403` if something tries to execute it.

This means a fresh install cannot modify or delete anything in your AWS account until you deliberately opt in twice: once at the environment level, once per action.

## Test actions

Set `TEST_ACTION=true` to enable a safe, no-op "test action" affordance in the UI, useful for verifying the wiring end-to-end without touching real resources.

## Audit trail

Every action execution is recorded in the `action_executions` table, regardless of outcome.
