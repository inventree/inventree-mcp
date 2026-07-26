"""MCP tools for querying InvenTree StockItem data."""

from __future__ import annotations

from ..mcp_server import mcp
from ..proxy import call_view
from ._common import clamp_limit


@mcp.tool()
def list_stock_items(
    part: int | None = None,
    location: int | None = None,
    in_stock: bool | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """List stock items.

    Args:
        part: restrict to stock of this Part ID (includes variants of the part).
        location: restrict to stock held at this StockLocation ID.
        in_stock: filter by whether the item currently counts as "in stock".
        limit: maximum number of results to return (capped at 100).
        offset: pagination offset.
    """
    from stock.api import StockList

    params: dict = {"limit": clamp_limit(limit), "offset": offset}

    if part is not None:
        params["part"] = part
    if location is not None:
        params["location"] = location
    if in_stock is not None:
        params["in_stock"] = in_stock

    return call_view(StockList, "GET", "/api/stock/", query_params=params)


@mcp.tool()
def get_stock_item(stock_item_id: int) -> dict:
    """Get full detail for a single stock item by its ID."""
    from stock.api import StockDetail

    return call_view(
        StockDetail, "GET", f"/api/stock/{stock_item_id}/", pk=stock_item_id
    )
