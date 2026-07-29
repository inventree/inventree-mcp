"""MCP tools for querying InvenTree StockItem data."""

from __future__ import annotations

from typing import Any

from ..mcp_server import mcp
from ..proxy import call_view
from ._common import build_query_params


@mcp.tool()
async def list_stock_items(
    part: int | None = None,
    location: int | None = None,
    in_stock: bool | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """List stock items.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_stock_item's return value -
    use its `pk` field with get_stock_item to fetch full detail for one of
    them.

    Args:
        part: restrict to stock of this Part ID (includes variants of the part).
        location: restrict to stock held at this StockLocation ID.
        in_stock: True for only items that are actually usable right now -
            quantity > 0, not allocated to a sales order/customer/build, not
            currently mid-build, and in an "available" status (excludes e.g.
            rejected/destroyed/lost stock). False for the inverse. Omit to
            include both.
        ordering: field to sort results by, e.g. "-quantity" for highest
            quantity first ('-' prefix for descending, omit it for
            ascending). Combine with limit to get a "top N by X" result, e.g.
            ordering="-quantity", limit=5 for the 5 largest stock items. Call
            describe_filters("stock") and check its ordering_fields list for
            valid values - an unrecognized field is silently ignored (no
            error, no sort) rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("stock") to see what's available,
            e.g. filters={"low_stock": true}.
        limit: maximum number of results to return (capped at 100).
        offset: pagination offset.
    """
    from stock.api import StockList

    base: dict[str, Any] = {}
    if part is not None:
        base["part"] = part
    if location is not None:
        base["location"] = location
    if in_stock is not None:
        base["in_stock"] = in_stock
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(StockList, "GET", "/api/stock/", query_params=params)


@mcp.tool()
async def get_stock_item(
    stock_item_id: int, filters: dict[str, Any] | None = None
) -> dict:
    """Get full detail for a single stock item by its ID.

    Returns the same object shape as one entry in list_stock_items's
    `results` array. Get a valid ID from list_stock_items (its `pk` field)
    if you don't already have one.

    Args:
        stock_item_id: the StockItem's database ID.
        filters: optional-field toggles beyond what's returned by default -
            call describe_filters("stock") and check its optional_fields,
            e.g. filters={"tests": true} to include test results inline.

    Raises:
        ToolError: no stock item exists with that ID, or the caller doesn't
            have permission to view it.
    """
    from stock.api import StockDetail

    return await call_view(
        StockDetail,
        "GET",
        f"/api/stock/{stock_item_id}/",
        pk=stock_item_id,
        query_params=filters,
    )
