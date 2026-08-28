"""Alpaca MCP server as the agent's research channel.

The hackathon requires using Alpaca's MCP server or its CLI. This project uses
both, and the split is deliberate rather than decorative:

* The **CLI** is the execution path. Orders are shell commands, which makes
  every trade reproducible and auditable, and multi-leg orders work correctly.
* The **MCP server** is the research path. It is what an AI assistant talks to,
  it exposes 41 tools over one session, and its output arrives wrapped in a
  trust envelope that marks it as data rather than instructions.

Execution deliberately does NOT go through MCP. The v2 server has an open bug
(alpacahq/alpaca-mcp-server#97) where multi-leg option orders arrive with the
`legs` array as a raw JSON string rather than a parsed list, so spreads fail.
Since every structure this agent trades is multi-leg, routing orders through
MCP would mean routing them through a known-broken code path.

Tool output is untrusted external data. It is parsed and used as numbers; it is
never interpreted as instructions, whatever the text inside it says.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from engine.config import SETTINGS

log = logging.getLogger(__name__)

#: Read-only toolsets. The server is never given the ability to place an order
#: in this configuration, so a confused model cannot trade through it.
RESEARCH_TOOLSETS = "account,assets,options-data,stock-data,news,corporate-actions"


def _server_env() -> dict[str, str]:
    return {
        "ALPACA_API_KEY": SETTINGS.api_key,
        "ALPACA_SECRET_KEY": SETTINGS.secret_key,
        "ALPACA_PAPER_TRADE": "true" if SETTINGS.paper else "false",
        "ALPACA_TOOLSETS": RESEARCH_TOOLSETS,
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
    }


def unwrap(payload: str) -> Any:
    """Strip the MCP trust envelope and return the Alpaca data underneath.

    Responses look like {"_alpaca_mcp_security": {...}, "data": {...}}. The
    envelope's own `instructions` field is metadata about how to treat the
    content; it is discarded, not followed.
    """
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return payload
    if isinstance(parsed, dict) and "data" in parsed and "_alpaca_mcp_security" in parsed:
        return parsed["data"]
    return parsed


@asynccontextmanager
async def session() -> AsyncIterator["MCPSession"]:
    """Open one MCP session over stdio. Starts the server, tears it down after."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command="uvx", args=["alpaca-mcp-server"], env=_server_env()
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            yield MCPSession(client)


class MCPSession:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def tools(self) -> list[str]:
        listing = await self._client.list_tools()
        return sorted(tool.name for tool in listing.tools)

    async def call(self, tool: str, args: dict[str, Any] | None = None) -> Any:
        result = await self._client.call_tool(tool, args or {})
        if not result.content:
            return None
        return unwrap(result.content[0].text)


async def gather_research(symbols: list[str], news_limit: int = 5) -> dict[str, Any]:
    """One MCP session, several reads: clock, news, and the account.

    Returns plain data for the strategist to reason over. Failures degrade to
    empty results rather than stopping the trading loop, because research is an
    input to a decision, not a precondition for safety.
    """
    out: dict[str, Any] = {"news": {}, "clock": None, "account": None, "error": None}
    try:
        async with session() as mcp:
            out["clock"] = await mcp.call("get_clock")
            out["account"] = await mcp.call("get_account_info")
            for symbol in symbols:
                try:
                    articles = await mcp.call(
                        "get_news", {"symbols": symbol, "limit": news_limit}
                    )
                    out["news"][symbol] = _summarise_news(articles)
                except Exception as exc:  # noqa: BLE001
                    log.debug("news read failed for %s: %s", symbol, exc)
                    out["news"][symbol] = []
    except Exception as exc:  # noqa: BLE001
        log.warning("MCP research unavailable: %s", exc)
        out["error"] = str(exc)
    return out


def _summarise_news(payload: Any) -> list[dict[str, str]]:
    """Reduce a news payload to headline and timestamp.

    Only these two fields are kept. Article bodies are attacker-controlled text
    from the open internet, and nothing downstream needs them.
    """
    items = payload.get("news", payload) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    summary = []
    for item in items[:10]:
        if not isinstance(item, dict):
            continue
        summary.append(
            {
                "headline": str(item.get("headline", ""))[:200],
                "created_at": str(item.get("created_at", "")),
            }
        )
    return summary


def research_sync(symbols: list[str]) -> dict[str, Any]:
    """Synchronous wrapper for use inside the trading loop."""
    try:
        return asyncio.run(gather_research(symbols))
    except RuntimeError as exc:  # already inside a loop
        log.warning("cannot run MCP research here: %s", exc)
        return {"news": {}, "clock": None, "account": None, "error": str(exc)}
