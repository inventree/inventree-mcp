"""MCP tools for querying InvenTree Part data.

Every tool here is a thin wrapper around the real part API views
(part.api.PartList / PartDetail) via proxy.call_view(), so permissions,
filtering, and serialization always match the regular REST API exactly.
"""

from __future__ import annotations

from ..mcp_server import mcp
from ..proxy import call_view
from ._common import clamp_limit


@mcp.tool()
def list_parts(
    search: str | None = None,
    category: int | None = None,
    active: bool | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """List parts in the InvenTree database.

    Args:
        search: free-text search against name, description, IPN, and keywords.
        category: restrict to parts in this PartCategory ID (includes sub-categories).
        active: filter by active/inactive status.
        limit: maximum number of results to return (capped at 100).
        offset: pagination offset.
    """
    from part.api import PartList

    params: dict = {"limit": clamp_limit(limit), "offset": offset}

    if search is not None:
        params["search"] = search
    if category is not None:
        params["category"] = category
    if active is not None:
        params["active"] = active

    return call_view(PartList, "GET", "/api/part/", query_params=params)


@mcp.tool()
def get_part(part_id: int) -> dict:
    """Get full detail for a single part by its ID."""
    from part.api import PartDetail

    return call_view(PartDetail, "GET", f"/api/part/{part_id}/", pk=part_id)
