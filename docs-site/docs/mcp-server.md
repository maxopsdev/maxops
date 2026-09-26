# MCP Server

`maxops-mcp` is a standalone [Model Context Protocol](https://modelcontextprotocol.io/) server that exposes MaxOps to MCP-compatible AI clients (desktop assistants, IDE integrations). It talks to your already-running MaxOps backend over HTTP — it does **not** talk to AWS directly, and has no onboarding of its own.

!!! note "Set this up after the web app, not instead of it"
    The MCP server queries your backend's inventory, checks, and settings — it assumes the main app is already running and has completed onboarding (account/region configured, at least one scan run).

## Setup

```bash
cd maxops-mcp
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -e .
copy .env.example .env          # macOS/Linux: cp .env.example .env
```

Update `.env` if your backend isn't at `http://localhost:8000`. Then, with the backend running:

```bash
maxops-mcp
```

This runs over MCP stdio — the launch mode desktop MCP clients expect. A ready-made client config snippet is in the repository's `maxops-mcp/README.md`.

## Write tools are off by default

`snooze_resources` and `remove_snooze` require `MAXOPS_MCP_WRITE_ENABLED=true` in `.env`. Every other tool is read-only.

## What's exposed

15 tools covering inventory (`get_inventory`, `query_inventory`, `list_resource_types`), checks (`list_checks`, `run_check`, `query_check_results`, `get_latest_check_results`), cost (`get_tag_cost_savings_summary`), snoozing, and the Rightsizer/recommendations engine (`list_rightsizer_resources`, `list_rightsize_recommendations`, `get_rightsize_confidence_trend`, and related tools) — plus 5 MCP resources (`maxops://resource-types`, `maxops://dashboard/summary`, `maxops://inventory/{type}`, `maxops://snoozes/{type}`, `maxops://rightsizer/{type}`).

## Test without a desktop client

The package also installs a CLI test client:

```bash
maxops-mcp-client list-tools
maxops-mcp-client call-tool get_inventory --arguments "{\"resource_type\":\"ec2\",\"limit\":5}"
```

See `maxops-mcp/README.md` in the repository for the full tool list and more example calls.
