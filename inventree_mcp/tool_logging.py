"""Optionally log every MCP tool call, gated by the MCP_LOG_TOOL_CALLS plugin setting.

Off by default (see core.py) - tool arguments can be verbose and this is meant
as an opt-in debugging aid, not a permanent audit log. When enabled, every
call is logged with at least the tool name (what the user asked for), plus
the calling user and arguments for real debugging value, and a second line on
completion with the outcome and duration.

This deliberately does not touch mcp.call_tool (the MCPServer-level method) -
tests that call it directly (see test_mcp.py) exercise tool dispatch without
the transport/logging layer, same as tool_visibility.py leaves mcp.list_tools
untouched for the same reason. What actually sees every real client
tools/call request (see mcp_transport.py) is the low-level
mcp.server.lowlevel.Server's registered "tools/call" request handler, so this
reaches into that internal and re-registers it - the same "no public API for
this" approach output_schemas.py and tool_visibility.py already use.

mcp 2.0's rewrite moved the exception -> CallToolResult(is_error=True)
normalization that used to live in the lowlevel Server's `.call_tool()`
decorator into MCPServer._handle_call_tool() itself (a (ctx, params) ->
CallToolResult | InputRequiredResult handler, registered on the lowlevel
server via add_request_handler() rather than a decorator). Wrapping
_handle_call_tool (instead of reimplementing its exception handling here)
keeps that normalization - including its MCPError passthrough - as the
library's problem, not ours; it's still the same "outside mcp.call_tool"
layering as before, since _handle_call_tool calls self.call_tool(...), i.e.
the untouched instance method.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import structlog
from asgiref.sync import sync_to_async
from mcp.types import CallToolRequestParams, CallToolResult

from .context import get_current_user, has_current_user
from .mcp_server import mcp
from .settings import get_plugin_setting

if TYPE_CHECKING:
    from mcp.server.context import ServerRequestContext
    from mcp.types import InputRequiredResult

logger = structlog.get_logger("inventree")


def _caller_label() -> str:
    """Best-effort identity for a log line - never raises, even with no bound user."""
    if not has_current_user():
        return "anonymous"
    return get_current_user().username


def _error_text(result: CallToolResult) -> str:
    """Best-effort text summary of a failed CallToolResult, for the warning log line."""
    return "; ".join(
        block.text for block in result.content if getattr(block, "type", None) == "text"
    )


def apply() -> None:
    """Make every real tools/call request go through logging when enabled.

    Re-registers the low-level Server's "tools/call" request handler with a
    wrapper around the existing one (captured before overriding it). See the
    module docstring for why this wraps MCPServer._handle_call_tool rather
    than mcp.call_tool directly, and why that's still the correct layer to
    intercept.
    """
    unlogged_call_tool = mcp._handle_call_tool

    async def logged_call_tool(
        ctx: ServerRequestContext, params: CallToolRequestParams
    ) -> CallToolResult | InputRequiredResult:
        name = params.name
        arguments = params.arguments or {}

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
            return await unlogged_call_tool(ctx, params)

        caller = _caller_label()
        logger.info(
            "MCP tool call: '%s' by '%s' with arguments %s", name, caller, arguments
        )
        started = time.monotonic()

        # _handle_call_tool normalizes ordinary tool failures into a
        # CallToolResult(is_error=True) rather than raising - it only lets
        # MCPError (a protocol-level error) propagate - so both branches
        # below have to be checked to log every failure, not just this one.
        try:
            result = await unlogged_call_tool(ctx, params)
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
        if isinstance(result, CallToolResult) and result.is_error:
            logger.warning(
                "MCP tool call failed: '%s' by '%s' after %.1fms: %s",
                name,
                caller,
                elapsed_ms,
                _error_text(result),
            )
        else:
            logger.info(
                "MCP tool call succeeded: '%s' by '%s' in %.1fms",
                name,
                caller,
                elapsed_ms,
            )
        return result

    mcp._lowlevel_server.add_request_handler(
        "tools/call", CallToolRequestParams, logged_call_tool
    )
