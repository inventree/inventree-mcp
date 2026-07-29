"""Optionally log every MCP tool call, gated by the MCP_LOG_TOOL_CALLS plugin setting.

Off by default (see core.py) - tool arguments can be verbose and this is meant
as an opt-in debugging aid, not a permanent audit log. When enabled, every
call is logged with at least the tool name (what the user asked for), plus
the calling user and arguments for real debugging value, and a second line on
completion with the outcome and duration.

This deliberately does not touch mcp.call_tool (the FastMCP-level method) -
tests that call it directly (see test_mcp.py) exercise tool dispatch without
the transport/logging layer, same as tool_visibility.py leaves mcp.list_tools
untouched for the same reason. What actually sees every real client
tools/call request (see mcp_transport.py) is the low-level
mcp.server.lowlevel.Server's registered CallToolRequest handler, so this
reaches into that internal and re-registers it - the same "no public API for
this" approach output_schemas.py and tool_visibility.py already use.
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from asgiref.sync import sync_to_async

from .context import get_current_user, has_current_user
from .mcp_server import mcp
from .settings import get_plugin_setting

logger = structlog.get_logger("inventree")


def _caller_label() -> str:
    """Best-effort identity for a log line - never raises, even with no bound user."""
    if not has_current_user():
        return "anonymous"
    return get_current_user().username


def apply() -> None:
    """Make every real tools/call request go through logging when enabled.

    Re-registers mcp._mcp_server's CallToolRequest handler with a wrapper
    around the existing one (captured before overriding it), preserving
    validate_input=False to match FastMCP's own registration
    (FastMCP.__init__ calls `self._mcp_server.call_tool(validate_input=False)`
    - FastMCP.call_tool() does its own, separate argument validation
    internally, so this must not change that).
    """
    unlogged_call_tool = mcp.call_tool

    async def logged_call_tool(name: str, arguments: dict[str, Any]) -> Any:
        # get_plugin_setting() does real ORM work - like proxy.py's
        # call_view(), this must be bridged via sync_to_async
        # (thread_sensitive=True, the default) rather than called directly
        # from this async handler, or Django raises SynchronousOnlyOperation.
        # get_plugin_setting() itself suppresses *all* exceptions and falls
        # back to its default - so calling it un-bridged doesn't crash, it
        # just always silently reads as disabled, which is worse.
        if not await sync_to_async(get_plugin_setting)(
            "MCP_LOG_TOOL_CALLS", default=False
        ):
            return await unlogged_call_tool(name, arguments)

        caller = _caller_label()
        logger.info(
            "MCP tool call: '%s' by '%s' with arguments %s", name, caller, arguments
        )
        started = time.monotonic()

        try:
            result = await unlogged_call_tool(name, arguments)
        except Exception as exc:
            elapsed_ms = (time.monotonic() - started) * 1000
            logger.warning(
                "MCP tool call failed: '%s' by '%s' after %.1fms: %s",
                name,
                caller,
                elapsed_ms,
                exc,
            )
            raise

        elapsed_ms = (time.monotonic() - started) * 1000
        logger.info(
            "MCP tool call succeeded: '%s' by '%s' in %.1fms", name, caller, elapsed_ms
        )
        return result

    mcp._mcp_server.call_tool(validate_input=False)(logged_call_tool)
