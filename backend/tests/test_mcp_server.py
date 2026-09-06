"""Phase 12 end-to-end test: actually launches the MCP server as a subprocess over
stdio and drives it with the real MCP client SDK — not just calling the tool
functions in-process. This is what proves the stdio wiring (and the `mcp_server`
package rename that avoids shadowing the installed `mcp` PyPI package) really works.
"""

import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def mcp_server_env(configured_db: Path) -> dict:
    env = dict(os.environ)
    env["ENGMEM_DATABASE_PATH"] = str(configured_db)
    env["ENGMEM_ATTACHMENTS_DIR"] = str(configured_db.parent / "attachments")
    return env


async def test_mcp_server_lists_and_calls_tools_over_real_stdio(mcp_server_env):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server.main"],
        env=mcp_server_env,
        cwd=str(REPO_ROOT),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = (await session.list_tools()).tools
            tool_names = {t.name for t in tools}
            assert "search_engineering_knowledge" in tool_names
            assert "create_incident" in tool_names
            assert "get_incident" in tool_names

            create_result = await session.call_tool(
                "create_incident",
                {"raw_problem": "MCP smoke test: Docker DNS resolution fails in containers."},
            )
            assert create_result.is_error is not True
            incident_id = create_result.structured_content["incident"]["id"]

            search_result = await session.call_tool(
                "search_engineering_knowledge", {"query": "Docker DNS resolution"}
            )
            assert search_result.is_error is not True
            found_ids = {r["incident_id"] for r in search_result.structured_content["results"]}
            assert incident_id in found_ids

            get_result = await session.call_tool("get_incident", {"incident_id": incident_id})
            assert get_result.structured_content["raw_problem"].startswith("MCP smoke test")
