"""MCP tools for discovering what's filterable/searchable on the list tools."""

from __future__ import annotations

from mcp.server.fastmcp.exceptions import ToolError

from ..filter_introspection import describe_filterset
from ..mcp_server import mcp

_RESOURCES = ("part", "stock", "location", "category")


@mcp.tool()
def describe_filters(resource: str) -> dict:
    """Describe the search/filter/ordering options available for a resource.

    Does not touch any InvenTree data - this is static metadata about the
    corresponding list tool's capabilities, read directly from InvenTree's
    own filter definitions (so it can't drift out of date).

    Args:
        resource: one of "part", "stock", "location", "category" - matches
            list_parts / list_stock_items / list_locations / list_categories.

    Returns a dict with:
        search_fields: fields matched by that list tool's `search` argument.
        ordering_fields: fields usable via filters={"ordering": "<field>"}
            (prefix with '-' for descending, e.g. "-in_stock").
        filters: {name: {type, label, choices}} - pass any of these keys
            directly in that list tool's `filters` argument, e.g.
            filters={"is_variant": true, "ordering": "-in_stock"}.
    """
    if resource == "part":
        from part.api import PartList

        view_cls = PartList
    elif resource == "stock":
        from stock.api import StockList

        view_cls = StockList
    elif resource == "location":
        from stock.api import StockLocationList

        view_cls = StockLocationList
    elif resource == "category":
        from part.api import CategoryList

        view_cls = CategoryList
    else:
        raise ToolError(
            f"Unknown resource {resource!r}. Choose one of: {', '.join(_RESOURCES)}"
        )

    return describe_filterset(view_cls)
