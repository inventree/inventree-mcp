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
    filters: dict[str, Any] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """List stock items.

    Args:
        part: restrict to stock of this Part ID (includes variants of the part).
        location: restrict to stock held at this StockLocation ID.
        in_stock: filter by whether the item currently counts as "in stock".
        filters: additional filter/ordering parameters beyond the named arguments
            above - call describe_filters("stock") to see what's available, e.g.
            filters={"low_stock": true, "ordering": "-quantity"}.
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

    params = build_query_params(base, filters, limit, offset)

    return await call_view(StockList, "GET", "/api/stock/", query_params=params)


@mcp.tool()
async def get_stock_item(stock_item_id: int) -> dict:
    """Get full detail for a single stock item by its ID."""
    from stock.api import StockDetail

    return await call_view(
        StockDetail, "GET", f"/api/stock/{stock_item_id}/", pk=stock_item_id
    )
