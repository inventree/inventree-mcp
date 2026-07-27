"""The FastMCP server instance and tool registry for InvenTree MCP.

InvenTree's plugin registry walks every submodule of a plugin package at
discovery time (plugin.helpers.get_modules) and may pass arbitrary
module-level objects through inspect.getmembers() while looking for
InvenTreePlugin subclasses. Doing that to a live FastMCP instance can touch
lazily-computed properties (e.g. session_manager) before the server has ever
been run. Setting __all__ = [] here keeps this instance out of that scan
without changing how it's imported elsewhere (`from .mcp_server import mcp`
still works - __all__ only affects `from module import *` and the registry's
own name-filtering).
"""

from __future__ import annotations

__all__: list[str] = []

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    name="InvenTree MCP",
    instructions="MCP server for querying InvenTree inventory management data.",
    stateless_http=True,
    json_response=True,
)

# Import tool modules for their side effect of registering @mcp.tool() functions.
# Must run after the tool imports above - it attaches output schemas to
# already-registered tools. See output_schemas.py.
from . import output_schemas
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
    purchase_orders,
    sales_orders,
    stock,
    supplier_parts,
)

output_schemas.apply()
