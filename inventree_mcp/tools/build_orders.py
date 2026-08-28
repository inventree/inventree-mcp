"""MCP tools for querying InvenTree Build (manufacturing order) data."""

from __future__ import annotations

from typing import Any

from ..mcp_server import mcp
from ..proxy import call_view
from ..view_resolution import resolve_view
from ._common import build_query_params


@mcp.tool()
async def list_build_orders(
    part: int | None = None,
    status: int | None = None,
    outstanding: bool | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List build orders (manufacturing orders).

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_build_order's return value
    - use its `pk` field with get_build_order to fetch full detail for one
    of them.

    Args:
        part: restrict to build orders assembling this Part ID.
        status: restrict to build orders with this numeric status code.
            Status codes are instance-specific - rather than guessing one,
            read the `status`/`status_text` fields on a build order you
            already have, or use filters={"outstanding": true} /
            filters={"overdue": true} for the common cases instead.
        outstanding: True for build orders that are still active (not yet
            complete/cancelled), False for the inverse. Omit to include both.
        ordering: field to sort results by, e.g. "-target_date" for the
            orders due soonest first ('-' prefix for descending, omit it for
            ascending). Combine with limit to get a "top N by X" result, e.g.
            ordering="target_date", limit=5 for the 5 most overdue open
            builds. Call describe_filters("build_order") and check its
            ordering_fields list for valid values - an unrecognized field is
            silently ignored (no error, no sort) rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("build_order") to see what's
            available, e.g. filters={"overdue": true}.
        limit: maximum number of results to return - defaults to 100 (the
            maximum) to minimize round trips for large result sets; pass a
            smaller value to page through results in smaller batches.
        offset: pagination offset.
    """
    base: dict[str, Any] = {}
    if part is not None:
        base["part"] = part
    if status is not None:
        base["status"] = status
    if outstanding is not None:
        base["outstanding"] = outstanding
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        resolve_view("build.api", "BuildList"),
        "GET",
        "/api/build/",
        query_params=params,
    )


@mcp.tool()
async def get_build_order(build_id: int, filters: dict[str, Any] | None = None) -> dict:
    """Get full detail for a single build order by its ID.

    Returns the same object shape as one entry in list_build_orders's
    `results` array. Get a valid ID from list_build_orders (its `pk` field)
    if you don't already have one. Use list_build_lines (filtering by
    `build`) to see the components this build order needs, and
    list_build_items (filtering by `build`) to see which stock has already
    been allocated against it.

    Args:
        build_id: the Build's database ID.
        filters: optional-field toggles beyond what's returned by default -
            call describe_filters("build_order") and check its
            optional_fields.

    Raises:
        ToolError: no build order exists with that ID, or the caller
            doesn't have permission to view it.
    """
    return await call_view(
        resolve_view("build.api", "BuildDetail"),
        "GET",
        f"/api/build/{build_id}/",
        pk=build_id,
        query_params=filters,
    )


@mcp.tool()
async def list_build_lines(
    build: int | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List build order line items - the components required to complete a build order.

    Each build line corresponds to one BOM item of the assembly being
    built, scaled to the build order's quantity. It tracks how much of that
    component has been allocated/consumed so far, distinct from stock
    allocations themselves (see list_build_items for those).

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_build_line's return value
    - use its `pk` field with get_build_line to fetch full detail for one
    of them.

    Args:
        build: restrict to lines on this Build (build order) ID. Omitting
            this returns build lines across every build order.
        ordering: field to sort results by ('-' prefix for descending, omit
            it for ascending). Call describe_filters("build_line") and
            check its ordering_fields list for valid values - an
            unrecognized field is silently ignored (no error, no sort)
            rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("build_line") to see what's
            available, e.g. filters={"allocated": false} for lines still
            needing stock, or filters={"consumable": false} to exclude
            consumable (non-tracked) components.
        limit: maximum number of results to return - defaults to 100 (the
            maximum) to minimize round trips for large result sets; pass a
            smaller value to page through results in smaller batches.
        offset: pagination offset.
    """
    base: dict[str, Any] = {}
    if build is not None:
        base["build"] = build
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        resolve_view("build.api", "BuildLineList"),
        "GET",
        "/api/build/line/",
        query_params=params,
    )


@mcp.tool()
async def get_build_line(line_id: int, filters: dict[str, Any] | None = None) -> dict:
    """Get full detail for a single build order line item by its ID.

    Returns the same object shape as one entry in list_build_lines's
    `results` array. Get a valid ID from list_build_lines (its `pk` field)
    if you don't already have one.

    Args:
        line_id: the BuildLine's database ID.
        filters: optional-field toggles beyond what's returned by default -
            call describe_filters("build_line") and check its optional_fields.

    Raises:
        ToolError: no build line exists with that ID, or the caller
            doesn't have permission to view it.
    """
    return await call_view(
        resolve_view("build.api", "BuildLineDetail"),
        "GET",
        f"/api/build/line/{line_id}/",
        pk=line_id,
        query_params=filters,
    )


@mcp.tool()
async def list_build_items(
    build: int | None = None,
    part: int | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List build order allocations - stock items reserved against build order lines.

    Each entry links a StockItem to a BuildLine (and optionally a specific
    build output via `install_into`) for some quantity. This is the read
    side of stock allocation - use list_build_lines's `allocated`/`quantity`
    fields to see the aggregate picture for a line, and this tool to see
    exactly which stock items make it up.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_build_item's return value
    - use its `pk` field with get_build_item to fetch full detail for one
    of them.

    Args:
        build: restrict to allocations on this Build (build order) ID.
        part: restrict to allocations of stock for this Part ID.
        ordering: field to sort results by ('-' prefix for descending, omit
            it for ascending). Call describe_filters("build_item") and
            check its ordering_fields list for valid values - an
            unrecognized field is silently ignored (no error, no sort)
            rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("build_item") to see what's
            available, e.g. filters={"output": null} for allocations not
            yet installed into a specific build output.
        limit: maximum number of results to return - defaults to 100 (the
            maximum) to minimize round trips for large result sets; pass a
            smaller value to page through results in smaller batches.
        offset: pagination offset.
    """
    base: dict[str, Any] = {}
    if build is not None:
        base["build"] = build
    if part is not None:
        base["part"] = part
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        resolve_view("build.api", "BuildItemList"),
        "GET",
        "/api/build/item/",
        query_params=params,
    )


@mcp.tool()
async def get_build_item(item_id: int, filters: dict[str, Any] | None = None) -> dict:
    """Get full detail for a single build order allocation by its ID.

    Returns the same object shape as one entry in list_build_items's
    `results` array. Get a valid ID from list_build_items (its `pk` field)
    if you don't already have one.

    Args:
        item_id: the BuildItem's database ID.
        filters: optional-field toggles beyond what's returned by default -
            call describe_filters("build_item") and check its optional_fields.

    Raises:
        ToolError: no allocation exists with that ID, or the caller doesn't
            have permission to view it.
    """
    return await call_view(
        resolve_view("build.api", "BuildItemDetail"),
        "GET",
        f"/api/build/item/{item_id}/",
        pk=item_id,
        query_params=filters,
    )
