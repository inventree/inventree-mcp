"""MCP tools for discovering what's filterable/searchable on the list tools."""

from __future__ import annotations

from mcp.server.fastmcp.exceptions import ToolError

from ..filter_introspection import describe_filterset
from ..mcp_server import mcp


def _part_list() -> type:
    from part.api import PartList

    return PartList


def _stock_list() -> type:
    from stock.api import StockList

    return StockList


def _stock_location_list() -> type:
    from stock.api import StockLocationList

    return StockLocationList


def _category_list() -> type:
    from part.api import CategoryList

    return CategoryList


def _purchase_order_list() -> type:
    from order.api import PurchaseOrderList

    return PurchaseOrderList


def _purchase_order_line_list() -> type:
    from order.api import PurchaseOrderLineItemList

    return PurchaseOrderLineItemList


def _sales_order_list() -> type:
    from order.api import SalesOrderList

    return SalesOrderList


def _sales_order_line_list() -> type:
    from order.api import SalesOrderLineItemList

    return SalesOrderLineItemList


def _sales_order_allocation_list() -> type:
    from order.api import SalesOrderAllocationList

    return SalesOrderAllocationList


def _build_order_list() -> type:
    from build.api import BuildList

    return BuildList


def _build_line_list() -> type:
    from build.api import BuildLineList

    return BuildLineList


def _build_item_list() -> type:
    from build.api import BuildItemList

    return BuildItemList


# Values are loader functions, not the view classes directly, so each import
# stays lazy (matches tools/*.py's own per-call imports) - importing e.g.
# part.api at module level risks AppRegistryNotReady if InvenTree's plugin
# registry scans this module before Django's app registry is ready.
_RESOURCE_LOADERS = {
    "part": _part_list,
    "stock": _stock_list,
    "location": _stock_location_list,
    "category": _category_list,
    "purchase_order": _purchase_order_list,
    "purchase_order_line": _purchase_order_line_list,
    "sales_order": _sales_order_list,
    "sales_order_line": _sales_order_line_list,
    "sales_order_allocation": _sales_order_allocation_list,
    "build_order": _build_order_list,
    "build_line": _build_line_list,
    "build_item": _build_item_list,
}


@mcp.tool()
def describe_filters(resource: str) -> dict:
    """Describe the search/filter/ordering options available for a resource.

    Does not touch any InvenTree data - this is static metadata about the
    corresponding list tool's capabilities, read directly from InvenTree's
    own filter definitions (so it can't drift out of date).

    Args:
        resource: one of "part", "stock", "location", "category",
            "purchase_order", "purchase_order_line", "sales_order",
            "sales_order_line", "sales_order_allocation", "build_order",
            "build_line", "build_item" - matches list_parts / list_stock_items
            / list_locations / list_categories / list_purchase_orders /
            list_purchase_order_lines / list_sales_orders /
            list_sales_order_lines / list_sales_order_allocations /
            list_build_orders / list_build_lines / list_build_items.

    Returns a dict with:
        search_fields: fields matched by that list tool's `search` argument.
        ordering_fields: fields usable via filters={"ordering": "<field>"}
            (prefix with '-' for descending, e.g. "-in_stock").
        filters: {name: {type, label, choices}} - pass any of these keys
            directly in that list tool's `filters` argument, e.g.
            filters={"is_variant": true, "ordering": "-in_stock"}.
    """
    loader = _RESOURCE_LOADERS.get(resource)

    if loader is None:
        raise ToolError(
            f"Unknown resource {resource!r}. Choose one of: "
            f"{', '.join(_RESOURCE_LOADERS)}"
        )

    return describe_filterset(loader())
