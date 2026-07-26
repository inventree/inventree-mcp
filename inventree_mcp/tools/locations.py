"""MCP tools for querying InvenTree StockLocation data."""

from __future__ import annotations

from typing import Any

from ..mcp_server import mcp
from ..proxy import call_view
from ._common import build_query_params


@mcp.tool()
async def list_locations(
    search: str | None = None,
    parent: int | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """List stock locations.

    Args:
        search: free-text search against name/description.
        parent: restrict to direct children of this StockLocation ID.
        filters: additional filter/ordering parameters beyond the named arguments
            above - call describe_filters("location") to see what's available.
        limit: maximum number of results to return (capped at 100).
        offset: pagination offset.
    """
    from stock.api import StockLocationList

    base: dict[str, Any] = {}
    if search is not None:
        base["search"] = search
    if parent is not None:
        base["parent"] = parent

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        StockLocationList, "GET", "/api/stock/location/", query_params=params
    )


@mcp.tool()
async def get_location(location_id: int) -> dict:
    """Get full detail for a single stock location by its ID."""
    from stock.api import StockLocationDetail

    return await call_view(
        StockLocationDetail,
        "GET",
        f"/api/stock/location/{location_id}/",
        pk=location_id,
    )
