"""Filter tools/list results down to what the current caller can actually use.

Every tool is always *registered* (so its schema/description stays a single
source of truth - see output_schemas.py) - but a caller without the
underlying API endpoint's permission would only ever get a ToolError from
actually calling it. This makes a real tools/list request reflect that up
front: a tool is only listed if the current user (and, for OAuth2 requests,
their token's scope) can really reach the resource it wraps - determined by
running the *real* permission check via proxy.user_has_access() (RolePermission
/ OAuth2 scope, the same InvenTree.permissions machinery call_view() itself
relies on), not a hand-rolled guess.

This is a discovery-time convenience, not a new security boundary:
proxy.call_view() remains the only real enforcement point, and still runs in
full for every actual tool call regardless of what tools/list showed - a
client that calls a "hidden" tool by name gets exactly the same ToolError it
always did (see MCPServer.call_tool()'s own tool lookup, which never
consults this module). Don't rely on this module to prevent access to
anything; it only prevents *advertising* access that doesn't exist.

Why a real permission check via "GET" rather than a literal HTTP OPTIONS
request (the obvious-looking shortcut): InvenTree's OAuth2 scope resolver
(map_scope() in InvenTree/permissions.py) hardcodes OPTIONS to a generic
"g:read" scope for every view regardless of resource, while GET requires the
real resource-specific scope (e.g. "r:view:part"). An OPTIONS-based check
would show a tool as available to an OAuth2 token scoped down to "g:read"
only, even though the real GET call that tool actually makes would then be
rejected - silently defeating the exact "narrow a token below the user's
role" scenario this plugin exists to support correctly. Checking "GET"
instead sidesteps that gap entirely (and is also correct for the
role/RolePermission path, which maps OPTIONS and GET to the same "view"
permission anyway - see proxy.user_has_access()'s docstring for the full
comparison).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from mcp.types import ListToolsResult, PaginatedRequestParams

from . import proxy
from .context import has_current_user
from .mcp_server import mcp
from .tools.discovery import RESOURCE_LOADERS

if TYPE_CHECKING:
    from mcp.server.context import ServerRequestContext

# Maps every gated tool name to the discovery.py resource key whose List
# view's GET permission determines whether it's shown. list_X/get_X
# deliberately share one entry: InvenTree's per-resource permission (role or
# OAuth2 scope) doesn't vary between a resource's List and Detail view in
# practice, and checking only the List view halves the number of real
# permission checks a single tools/list call has to run. A tool with no
# entry here (e.g. describe_filters - pure metadata, no underlying view) is
# always shown, and so is any *future* tool accidentally left out of this
# map - see test_every_gated_tool_has_a_visibility_entry in test_mcp.py for
# why an omission fails loudly instead of silently either hiding or
# over-exposing a tool.
_TOOL_RESOURCES: dict[str, str] = {
    "list_parts": "part",
    "get_part": "part",
    "list_categories": "category",
    "get_category": "category",
    "list_stock_items": "stock",
    "get_stock_item": "stock",
    "list_locations": "location",
    "get_location": "location",
    "list_purchase_orders": "purchase_order",
    "get_purchase_order": "purchase_order",
    "list_purchase_order_lines": "purchase_order_line",
    "get_purchase_order_line": "purchase_order_line",
    "list_sales_orders": "sales_order",
    "get_sales_order": "sales_order",
    "list_sales_order_lines": "sales_order_line",
    "get_sales_order_line": "sales_order_line",
    "list_sales_order_allocations": "sales_order_allocation",
    "get_sales_order_allocation": "sales_order_allocation",
    "list_build_orders": "build_order",
    "get_build_order": "build_order",
    "list_build_lines": "build_line",
    "get_build_line": "build_line",
    "list_build_items": "build_item",
    "get_build_item": "build_item",
    "list_companies": "company",
    "get_company": "company",
    "list_contacts": "contact",
    "get_contact": "contact",
    "list_addresses": "address",
    "get_address": "address",
    "list_manufacturer_parts": "manufacturer_part",
    "get_manufacturer_part": "manufacturer_part",
    "list_supplier_parts": "supplier_part",
    "get_supplier_part": "supplier_part",
    "list_bom_items": "bom_item",
    "get_bom_item": "bom_item",
    "list_bom_substitutes": "bom_substitute",
    "get_bom_substitute": "bom_substitute",
    "list_attachments": "attachment",
    "get_attachment": "attachment",
    "list_parameters": "parameter",
    "get_parameter": "parameter",
    "list_parameter_templates": "parameter_template",
    "get_parameter_template": "parameter_template",
    "list_return_orders": "return_order",
    "get_return_order": "return_order",
    "list_return_order_lines": "return_order_line",
    "get_return_order_line": "return_order_line",
    "list_stock_tracking": "stock_tracking",
    "get_stock_tracking": "stock_tracking",
    "list_stock_test_results": "stock_test_result",
    "get_stock_test_result": "stock_test_result",
    "list_project_codes": "project_code",
    "get_project_code": "project_code",
}


async def visible_tool_names(names: Iterable[str]) -> set[str]:
    """Return the subset of *names* the current bound user can actually call.

    A tool with no entry in _TOOL_RESOURCES (e.g. describe_filters) is
    always included. If no user is bound at all (e.g. static introspection
    outside a real MCP request - see OutputSchemaTest in test_mcp.py, which
    calls mcp.list_tools() directly without binding one), every name is
    returned unfiltered, since there's no caller to filter *by* - this is
    not a real request path, so there's nothing unsafe about it.
    """
    if not has_current_user():
        return set(names)

    visible: set[str] = set()
    resource_access: dict[str, bool] = {}

    for name in names:
        resource = _TOOL_RESOURCES.get(name)
        if resource is None:
            visible.add(name)
            continue

        if resource not in resource_access:
            view_cls = RESOURCE_LOADERS[resource]()
            resource_access[resource] = view_cls is not None and (
                await proxy.user_has_access(view_cls, "GET")
            )

        if resource_access[resource]:
            visible.add(name)

    return visible


def apply() -> None:
    """Make the real (low-level, transport-facing) tools/list handler permission-aware.

    MCPServer.list_tools() is left untouched as an attribute - it's reassigned
    *around*, not in place, specifically so existing tests that call
    mcp.list_tools() directly to introspect the full, unfiltered registry
    (e.g. "every tool has an output_schema") keep working unchanged. What
    this actually overrides is the low-level mcp.server.lowlevel.Server's
    registered "tools/list" request handler - the thing a real client's
    tools/list request reaches over the wire (see mcp_transport.py) - by
    re-registering it via add_request_handler(), the same "no public API for
    this, reach into internals" approach output_schemas.apply() already uses
    for a different MCPServer internal. mcp 2.0's rewrite replaced the
    lowlevel Server's decorator-based `.list_tools()` registration with
    add_request_handler(method, params_type, handler) - the handler itself
    now takes (ctx, params) and must return a ListToolsResult, rather than a
    bare list[Tool].
    """
    unfiltered_list_tools = mcp.list_tools

    async def filtered_list_tools(
        ctx: ServerRequestContext, params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        tools = await unfiltered_list_tools()
        names = await visible_tool_names(tool.name for tool in tools)
        return ListToolsResult(tools=[tool for tool in tools if tool.name in names])

    mcp._lowlevel_server.add_request_handler(
        "tools/list", PaginatedRequestParams, filtered_list_tools
    )
