"""The MCPServer instance and tool registry for InvenTree MCP.

InvenTree's plugin registry walks every submodule of a plugin package at
discovery time (plugin.helpers.get_modules) and may pass arbitrary
module-level objects through inspect.getmembers() while looking for
InvenTreePlugin subclasses. Doing that to a live MCPServer instance can touch
lazily-computed properties (e.g. session_manager, which raises RuntimeError
if accessed before streamable_http_app() has ever been called) before the
server has ever been run. Setting __all__ = [] here keeps this instance out
of that scan without changing how it's imported elsewhere (`from .mcp_server
import mcp` still works - __all__ only affects `from module import *` and
the registry's own name-filtering).

stateless_http/json_response are no longer MCPServer constructor arguments
(mcp 2.0's breaking rewrite moved them onto streamable_http_app() /
run_streamable_http_async()) - they're passed directly to the
StreamableHTTPSessionManager mcp_transport.py constructs instead.
"""

from __future__ import annotations

__all__: list[str] = []

from mcp.server.mcpserver import MCPServer

mcp = MCPServer(
    name="InvenTree MCP",
    instructions="MCP server for querying InvenTree inventory management data.",
)

# Import tool modules for their side effect of registering @mcp.tool() functions.
# Must run after the tool imports above - output_schemas.apply() attaches
# output schemas to already-registered tools, and tool_visibility.apply()
# needs discovery.RESOURCE_LOADERS (defined once discovery.py has run).
from . import output_schemas, tool_logging, tool_visibility
from .tools import (  # noqa: F401
    attachments,
    bom,
    build_orders,
    categories,
    companies,
    discovery,
    locations,
    parameters,
    parts,
    project_codes,
    purchase_orders,
    return_orders,
    sales_orders,
    stock,
    stock_history,
    supplier_parts,
)

output_schemas.apply()
tool_visibility.apply()
tool_logging.apply()
