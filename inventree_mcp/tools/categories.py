"""MCP tools for querying InvenTree PartCategory data."""

from __future__ import annotations

from typing import Any

from ..mcp_server import mcp
from ..proxy import call_view
from ..view_resolution import resolve_view
from ._common import build_query_params


@mcp.tool()
async def list_categories(
    search: str | None = None,
    parent: int | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """List part categories.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_category's return value -
    use its `pk` field with get_category to fetch full detail for one of
    them.

    Args:
        search: free-text search against name/description.
        parent: restrict to direct children of this PartCategory ID. Omit to
            get only top-level (root) categories by default - pass
            filters={"cascade": true} to include categories at every level
            instead, or filters={"parent": <id>, "cascade": true} to get all
            descendants of a specific category rather than just its direct
            children.
        ordering: field to sort results by, e.g. "name" ('-' prefix for
            descending, omit it for ascending). Call describe_filters("category")
            and check its ordering_fields list for valid values - an
            unrecognized field is silently ignored (no error, no sort) rather
            than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("category") to see what's available.
        limit: maximum number of results to return (capped at 100).
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
        resolve_view("part.api", "CategoryList"),
        "GET",
        "/api/part/category/",
        query_params=params,
    )


@mcp.tool()
async def get_category(category_id: int, filters: dict[str, Any] | None = None) -> dict:
    """Get full detail for a single part category by its ID.

    Returns the same object shape as one entry in list_categories's
    `results` array. Get a valid ID from list_categories (its `pk` field)
    if you don't already have one.

    Args:
        category_id: the PartCategory's database ID.
        filters: optional-field toggles beyond what's returned by default -
            call describe_filters("category") and check its optional_fields.

    Raises:
        ToolError: no category exists with that ID, or the caller doesn't
            have permission to view it.
    """
    return await call_view(
        resolve_view("part.api", "CategoryDetail"),
        "GET",
        f"/api/part/category/{category_id}/",
        pk=category_id,
        query_params=filters,
    )
