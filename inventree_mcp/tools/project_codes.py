"""MCP tools for querying InvenTree ProjectCode data.

Project codes are a small tagging system - orders and builds can be
optionally linked to one, letting activity be grouped/reported on by
project rather than only by order type. Unlike most other resources here,
read access only requires an authenticated user (no dedicated role) -
InvenTree's own API treats project codes as staff-writable, everyone-
readable reference data.
"""

from __future__ import annotations

from typing import Any

from ..mcp_server import mcp
from ..proxy import call_view
from ._common import build_query_params


@mcp.tool()
async def list_project_codes(
    search: str | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """List project codes.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_project_code's return
    value - use its `pk` field with get_project_code to fetch full detail
    for one of them. That `pk` is also the value stored in a `project_code`
    field on purchase/sales/return orders and build orders.

    Args:
        search: free-text search against code/description.
        ordering: field to sort results by ('-' prefix for descending, omit
            it for ascending). Call describe_filters("project_code") and
            check its ordering_fields list for valid values - an
            unrecognized field is silently ignored (no error, no sort)
            rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("project_code") to see what's
            available, e.g. filters={"active": true}.
        limit: maximum number of results to return (capped at 100).
        offset: pagination offset.
    """
    from common.api import ProjectCodeList

    base: dict[str, Any] = {}
    if search is not None:
        base["search"] = search
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        ProjectCodeList, "GET", "/api/project-code/", query_params=params
    )


@mcp.tool()
async def get_project_code(
    project_code_id: int, filters: dict[str, Any] | None = None
) -> dict:
    """Get full detail for a single project code by its ID.

    Returns the same object shape as one entry in list_project_codes's
    `results` array. Get a valid ID from list_project_codes (its `pk`
    field) if you don't already have one.

    Args:
        project_code_id: the ProjectCode's database ID.
        filters: optional-field toggles beyond what's returned by default -
            call describe_filters("project_code") and check its
            optional_fields.

    Raises:
        ToolError: no project code exists with that ID, or the caller
            doesn't have permission to view it.
    """
    from common.api import ProjectCodeDetail

    return await call_view(
        ProjectCodeDetail,
        "GET",
        f"/api/project-code/{project_code_id}/",
        pk=project_code_id,
        query_params=filters,
    )
