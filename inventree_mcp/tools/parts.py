"""MCP tools for querying InvenTree Part data.

Every tool here is a thin wrapper around the real part API views
(part.api.PartList / PartDetail) via proxy.call_view(), so permissions,
filtering, and serialization always match the regular REST API exactly.
"""

from __future__ import annotations

from typing import Any

from ..mcp_server import mcp
from ..proxy import call_view
from ._common import build_query_params


@mcp.tool()
async def list_parts(
    search: str | None = None,
    category: int | None = None,
    active: bool | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """List parts in the InvenTree database.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_part's return value - use
    its `pk` field with get_part to fetch full detail for one of them.

    Args:
        search: free-text search against name, description, IPN, and keywords.
        category: restrict to parts in this PartCategory ID (includes sub-categories).
        active: True for only active (non-discontinued) parts, False for only
            inactive ones, omit to include both.
        ordering: field to sort results by, e.g. "-in_stock" for highest stock
            first ('-' prefix for descending, omit it for ascending). Combine
            with limit to get a "top N by X" result, e.g. ordering="-in_stock",
            limit=5 for the 5 parts with the most stock. Call
            describe_filters("part") and check its ordering_fields list for
            valid values - an unrecognized field is silently ignored (no
            error, no sort) rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("part") to see what's available,
            e.g. filters={"is_variant": true}.
        limit: maximum number of results to return (capped at 100).
        offset: pagination offset.
    """
    from part.api import PartList

    base: dict[str, Any] = {}
    if search is not None:
        base["search"] = search
    if category is not None:
        base["category"] = category
    if active is not None:
        base["active"] = active
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(PartList, "GET", "/api/part/", query_params=params)


@mcp.tool()
async def get_part(part_id: int) -> dict:
    """Get full detail for a single part by its ID.

    Returns the same object shape as one entry in list_parts's `results`
    array. Get a valid ID from list_parts (its `pk` field) if you don't
    already have one.

    Args:
        part_id: the Part's database ID.

    Raises:
        ToolError: no part exists with that ID, or the caller doesn't have
            permission to view it.
    """
    from part.api import PartDetail

    return await call_view(PartDetail, "GET", f"/api/part/{part_id}/", pk=part_id)
