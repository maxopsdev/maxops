# MaxOps MCP Server

Standalone MCP server for MaxOps. It exposes MaxOps inventory, checks, snoozes, and snooze audit workflows to MCP clients.

## Setup

```bash
cd maxops-mcp
python -m venv .venv
.venv\Scripts\activate
pip install -e .
copy .env.example .env
```

Update `.env` if the backend is not running at `http://localhost:8000`.

## Run

Start the MaxOps backend first, then run:

```bash
maxops-mcp
```

The server runs over MCP stdio, which is the expected launch mode for desktop MCP clients.

## Write Tools

Write tools are disabled by default. To enable snooze and remove-snooze tools:

```env
MAXOPS_MCP_WRITE_ENABLED=true
```

Remove snooze requires a non-empty audit comment, matching the MaxOps API behavior.


## Test Client

After `pip install -e .`, you can test the MCP server without configuring a desktop client:

```bash
maxops-mcp-client --api-base-url http://localhost:8000/api/v1 smoke --resource-type ec2 --limit 3
```

List exposed tools:

```bash
maxops-mcp-client list-tools
```

Read resources:

```bash
maxops-mcp-client read-resource maxops://resource-types
maxops-mcp-client read-resource maxops://inventory/ec2
```

Call a tool with JSON arguments:

```bash
maxops-mcp-client call-tool get_inventory --arguments "{\"resource_type\":\"ec2\",\"snooze_state\":\"all\",\"limit\":5}"

maxops-mcp-client call-tool query_inventory --arguments "{\"resource_type\":\"ec2\",\"region\":\"us-east-1\",\"snooze_state\":\"not_snoozed\",\"sort_by\":\"monthly_cost\",\"sort_order\":\"desc\",\"limit\":10}"

maxops-mcp-client call-tool query_inventory --arguments "{\"resource_type\":\"ec2\",\"tag_key\":\"cost_center\",\"tag_value\":\"platform\",\"tag_value_mode\":\"equals\",\"limit\":10}"

maxops-mcp-client call-tool get_tag_cost_savings_summary --arguments "{\"tag_key\":\"cost_center\",\"tag_value\":\"CC-1202\",\"tag_value_mode\":\"equals\",\"include_details\":true,\"details_limit\":100}"

maxops-mcp-client call-tool list_rightsizer_resources --arguments "{\"resource_type\":\"ec2\",\"status\":\"actionable\"}"

maxops-mcp-client call-tool list_rightsize_recommendations --arguments "{\"resource_type\":\"ec2\",\"region\":\"us-east-1\",\"min_monthly_savings\":10,\"candidate_limit\":5}"

maxops-mcp-client call-tool get_rightsize_confidence_trend --arguments "{\"resource_type\":\"rds\",\"inventory_id\":42}"
```

To test write tools, explicitly enable write mode:

```bash
maxops-mcp-client --write-enabled call-tool remove_snooze --arguments "{\"resources\":[{\"resource_id\":\"i-xxxxxxxx\",\"resource_type\":\"ec2\"}],\"reason\":\"Testing MCP remove snooze\"}"
```
## Client Configuration Example

```json
{
  "mcpServers": {
    "maxops": {
      "command": "E:\\workspace\\opensource\\maxops\\maxops-mcp\\.venv\\Scripts\\maxops-mcp.exe",
      "env": {
        "MAXOPS_API_BASE_URL": "http://localhost:8000/api/v1",
        "MAXOPS_MCP_WRITE_ENABLED": "false"
      }
    }
  }
}
```

## Exposed Tools

- `list_resource_types`
- `list_checks`
- `get_latest_check_results`
- `get_inventory`
- `query_inventory`
- `query_check_results`
- `get_tag_cost_savings_summary`
- `snooze_resources`
- `remove_snooze`
- `run_check`
- `list_rightsizer_resource_types`
- `list_rightsizer_resources`
- `get_rightsizer_resource_detail`
- `list_rightsize_recommendations`
- `get_rightsize_recommendation`
- `get_rightsize_confidence_trend`

`query_inventory` and `get_inventory` also accept `resource_type: "all"` to aggregate across all supported resource types.

`list_rightsizer_resources` / `get_rightsizer_resource_detail` cover the Rightsizer hub and accept `resource_type` in `ec2, asg, ecs, rds, s3`.

`list_rightsize_recommendations` / `get_rightsize_recommendation` / `get_rightsize_confidence_trend` cover the deeper recommendations engine (tunable warning thresholds, confidence trend) and accept `resource_type` in `ec2, asg, elasticache, rds`. These are read-only: they evaluate persisted inventory and scan telemetry against local pricing and make no AWS calls or changes.

## Exposed Resources

- `maxops://resource-types`
- `maxops://dashboard/summary`
- `maxops://inventory/{resource_type}`
- `maxops://snoozes/{resource_type}`
- `maxops://rightsizer/{resource_type}`


