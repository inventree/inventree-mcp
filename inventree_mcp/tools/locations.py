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

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_location's return value -
    use its `pk` field with get_location to fetch full detail for one of
    them.

    Args:
        search: free-text search against name/description.
        parent: restrict to direct children of this StockLocation ID. Omit to
            get only top-level (root) locations by default - pass
            filters={"cascade": true} to include locations at every level
            instead, or filters={"parent": <id>, "cascade": true} to get all
            descendants of a specific location rather than just its direct
            children.
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
    """Get full detail for a single stock location by its ID.

    Returns the same object shape as one entry in list_locations's
    `results` array. Get a valid ID from list_locations (its `pk` field)
    if you don't already have one.

    Args:
        location_id: the StockLocation's database ID.

    Raises:
        ToolError: no location exists with that ID, or the caller doesn't
            have permission to view it.
    """
    from stock.api import StockLocationDetail

    return await call_view(
        StockLocationDetail,
        "GET",
        f"/api/stock/location/{location_id}/",
        pk=location_id,
    )
