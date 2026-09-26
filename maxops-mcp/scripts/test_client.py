"""CLI client for testing the MaxOps MCP server over stdio."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


DEFAULT_SERVER_COMMAND = "maxops-mcp"


def default_server_command() -> str:
    """Prefer the server executable installed next to this client."""
    scripts_dir = Path(sys.executable).resolve().parent
    executable = "maxops-mcp.exe" if os.name == "nt" else "maxops-mcp"
    sibling = scripts_dir / executable
    if sibling.exists():
        return str(sibling)
    return DEFAULT_SERVER_COMMAND


def parse_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("JSON arguments must be an object")
    return parsed


def print_json(value: Any) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    print(json.dumps(value, indent=2, default=str))


def build_server_params(args: argparse.Namespace) -> StdioServerParameters:
    env = os.environ.copy()
    if args.api_base_url:
        env["MAXOPS_API_BASE_URL"] = args.api_base_url
    if args.write_enabled:
        env["MAXOPS_MCP_WRITE_ENABLED"] = "true"

    command = args.server_command or default_server_command()
    server_args = args.server_arg or []
    return StdioServerParameters(command=command, args=server_args, env=env)


async def run_client(args: argparse.Namespace) -> None:
    server_params = build_server_params(args)
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            if args.command == "list-tools":
                result = await session.list_tools()
                print_json(result)
                return

            if args.command == "list-resources":
                result = await session.list_resources()
                print_json(result)
                return

            if args.command == "list-prompts":
                result = await session.list_prompts()
                print_json(result)
                return

            if args.command == "call-tool":
                result = await session.call_tool(args.name, parse_json(args.arguments))
                print_json(result)
                return

            if args.command == "read-resource":
                result = await session.read_resource(args.uri)
                print_json(result)
                return

            if args.command == "smoke":
                print("Initializing MaxOps MCP smoke test...")
                tools = await session.list_tools()
                resources = await session.list_resources()
                resource_types = await session.call_tool("list_resource_types", {})
                latest = await session.call_tool("get_latest_check_results", {})
                inventory = await session.call_tool(
                    "get_inventory",
                    {"resource_type": args.resource_type, "snooze_state": "all", "limit": args.limit},
                )
                print_json(
                    {
                        "tools_count": len(tools.tools),
                        "resources_count": len(resources.resources),
                        "resource_types": resource_types.model_dump(mode="json"),
                        "latest_check_results": latest.model_dump(mode="json"),
                        "inventory_sample": inventory.model_dump(mode="json"),
                    }
                )
                return

            raise SystemExit(f"Unknown command: {args.command}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Test client for the MaxOps MCP server")
    parser.add_argument("--server-command", default=None, help="MCP server command to launch")
    parser.add_argument("--server-arg", action="append", default=[], help="Argument passed to the MCP server command")
    parser.add_argument("--api-base-url", default=None, help="Override MAXOPS_API_BASE_URL")
    parser.add_argument("--write-enabled", action="store_true", help="Set MAXOPS_MCP_WRITE_ENABLED=true for the server")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list-tools")
    subparsers.add_parser("list-resources")
    subparsers.add_parser("list-prompts")

    call_tool = subparsers.add_parser("call-tool")
    call_tool.add_argument("name")
    call_tool.add_argument("--arguments", default="{}", help="JSON object passed as tool arguments")

    read_resource = subparsers.add_parser("read-resource")
    read_resource.add_argument("uri")

    smoke = subparsers.add_parser("smoke")
    smoke.add_argument("--resource-type", default="ec2")
    smoke.add_argument("--limit", type=int, default=3)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        asyncio.run(run_client(args))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"MCP client failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
