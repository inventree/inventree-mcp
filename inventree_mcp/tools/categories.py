"""MCP tools for querying InvenTree PartCategory data."""

from __future__ import annotations

from ..mcp_server import mcp
from ..proxy import call_view
from ._common import clamp_limit


@mcp.tool()
def list_categories(
    search: str | None = None,
    parent: int | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """List part categories.

    Args:
        search: free-text search against name/description.
        parent: restrict to direct children of this PartCategory ID.
        limit: maximum number of results to return (capped at 100).
        offset: pagination offset.
    """
    from part.api import CategoryList

    params: dict = {"limit": clamp_limit(limit), "offset": offset}

    if search is not None:
        params["search"] = search
    if parent is not None:
        params["parent"] = parent

    return call_view(CategoryList, "GET", "/api/part/category/", query_params=params)


@mcp.tool()
def get_category(category_id: int) -> dict:
    """Get full detail for a single part category by its ID."""
    from part.api import CategoryDetail

    return call_view(
        CategoryDetail, "GET", f"/api/part/category/{category_id}/", pk=category_id
    )
