# Policies

**Policies** back the Checks Manager UI (`/policies`) — one policy definition is auto-generated per registered check at seed time, giving every check a corresponding policy record, execution history, and filter configuration.

Policies let you:

- Apply resource filters so a check only flags resources matching your criteria (tags, naming patterns, thresholds)
- Snooze or permanently exempt specific resources from a check
- Review execution history and cost-savings totals per policy

This is the layer to use when a check is directionally correct but too broad for your environment — rather than disabling it outright in Settings, scope it down with a filter.
