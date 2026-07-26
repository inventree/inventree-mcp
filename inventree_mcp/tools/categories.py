"""MCP tools for querying InvenTree PartCategory data."""

from __future__ import annotations

from typing import Any

from ..mcp_server import mcp
from ..proxy import call_view
from ._common import build_query_params


@mcp.tool()
async def list_categories(
    search: str | None = None,
    parent: int | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """List part categories.

    Args:
        search: free-text search against name/description.
        parent: restrict to direct children of this PartCategory ID.
        filters: additional filter/ordering parameters beyond the named arguments
            above - call describe_filters("category") to see what's available.
        limit: maximum number of results to return (capped at 100).
        offset: pagination offset.
    """
    from part.api import CategoryList

    base: dict[str, Any] = {}
    if search is not None:
        base["search"] = search
    if parent is not None:
        base["parent"] = parent

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        CategoryList, "GET", "/api/part/category/", query_params=params
    )


@mcp.tool()
async def get_category(category_id: int) -> dict:
    """Get full detail for a single part category by its ID."""
    from part.api import CategoryDetail

    return await call_view(
        CategoryDetail, "GET", f"/api/part/category/{category_id}/", pk=category_id
    )
