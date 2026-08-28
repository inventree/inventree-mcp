"""MCP tools for querying InvenTree StockLocation data."""

from __future__ import annotations

from typing import Any

from ..mcp_server import mcp
from ..proxy import call_view
from ..view_resolution import resolve_view
from ._common import build_query_params


@mcp.tool()
async def list_locations(
    search: str | None = None,
    parent: int | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 100,
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
        ordering: field to sort results by, e.g. "name" ('-' prefix for
            descending, omit it for ascending). Call describe_filters("location")
            and check its ordering_fields list for valid values - an
            unrecognized field is silently ignored (no error, no sort) rather
            than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("location") to see what's available.
        limit: maximum number of results to return - defaults to 100 (the
            maximum) to minimize round trips for large result sets; pass a
            smaller value to page through results in smaller batches.
        offset: pagination offset.
    """
    base: dict[str, Any] = {}
    if search is not None:
        base["search"] = search
    if parent is not None:
        base["parent"] = parent
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        resolve_view("stock.api", "StockLocationList"),
        "GET",
        "/api/stock/location/",
        query_params=params,
    )


@mcp.tool()
async def get_location(location_id: int, filters: dict[str, Any] | None = None) -> dict:
    """Get full detail for a single stock location by its ID.

    Returns the same object shape as one entry in list_locations's
    `results` array. Get a valid ID from list_locations (its `pk` field)
    if you don't already have one.

    Args:
        location_id: the StockLocation's database ID.
        filters: optional-field toggles beyond what's returned by default -
            call describe_filters("location") and check its optional_fields.

    Raises:
        ToolError: no location exists with that ID, or the caller doesn't
            have permission to view it.
    """
    return await call_view(
        resolve_view("stock.api", "StockLocationDetail"),
        "GET",
        f"/api/stock/location/{location_id}/",
        pk=location_id,
        query_params=filters,
    )
