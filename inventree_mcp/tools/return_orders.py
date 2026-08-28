"""MCP tools for querying InvenTree ReturnOrder data.

A return order tracks stock items a customer is sending back - each line
item references a specific StockItem (not a Part/SupplierPart like purchase
and sales order lines do), since a return is always about a particular,
already-serialized-or-not physical item coming back into the business.
"""

from __future__ import annotations

from typing import Any

from ..mcp_server import mcp
from ..proxy import call_view
from ..view_resolution import resolve_view
from ._common import build_query_params


@mcp.tool()
async def list_return_orders(
    customer: int | None = None,
    status: int | None = None,
    outstanding: bool | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List return orders.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_return_order's return value
    - use its `pk` field with get_return_order to fetch full detail for one
    of them.

    Args:
        customer: restrict to orders raised against this customer Company ID.
        status: restrict to orders with this numeric status code. Status
            codes are instance-specific - rather than guessing one, read the
            `status`/`status_text` fields on an order you already have, or
            use filters={"outstanding": true} / filters={"overdue": true}
            for the common cases instead.
        outstanding: True for orders that are still open (not yet
            complete/cancelled), False for the inverse. Omit to include both.
        ordering: field to sort results by, e.g. "-target_date" for the
            orders due soonest first ('-' prefix for descending, omit it for
            ascending). Combine with limit to get a "top N by X" result. Call
            describe_filters("return_order") and check its ordering_fields
            list for valid values - an unrecognized field is silently
            ignored (no error, no sort) rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("return_order") to see what's
            available, e.g. filters={"overdue": true}.
        limit: maximum number of results to return - defaults to 100 (the
            maximum) to minimize round trips for large result sets; pass a
            smaller value to page through results in smaller batches.
        offset: pagination offset.
    """
    base: dict[str, Any] = {}
    if customer is not None:
        base["customer"] = customer
    if status is not None:
        base["status"] = status
    if outstanding is not None:
        base["outstanding"] = outstanding
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        resolve_view("order.api", "ReturnOrderList"),
        "GET",
        "/api/order/ro/",
        query_params=params,
    )


@mcp.tool()
async def get_return_order(
    order_id: int, filters: dict[str, Any] | None = None
) -> dict:
    """Get full detail for a single return order by its ID.

    Returns the same object shape as one entry in list_return_orders's
    `results` array. Get a valid ID from list_return_orders (its `pk` field)
    if you don't already have one. Use list_return_order_lines (filtering by
    `order`) to see this order's individual line items.

    Args:
        order_id: the ReturnOrder's database ID.
        filters: optional-field toggles beyond what's returned by default -
            call describe_filters("return_order") and check its
            optional_fields.

    Raises:
        ToolError: no return order exists with that ID, or the caller
            doesn't have permission to view it.
    """
    return await call_view(
        resolve_view("order.api", "ReturnOrderDetail"),
        "GET",
        f"/api/order/ro/{order_id}/",
        pk=order_id,
        query_params=filters,
    )


@mcp.tool()
async def list_return_order_lines(
    order: int | None = None,
    item: int | None = None,
    received: bool | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List return order line items.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_return_order_line's return
    value - use its `pk` field with get_return_order_line to fetch full
    detail for one of them.

    Args:
        order: restrict to lines on this ReturnOrder ID.
        item: restrict to the line for this StockItem ID (a return order
            line always references one specific stock item, not a Part).
        received: True for lines whose item has already been received back,
            False for lines still awaiting return. Omit to include both.
        ordering: field to sort results by ('-' prefix for descending, omit
            it for ascending). Call describe_filters("return_order_line")
            and check its ordering_fields list for valid values - an
            unrecognized field is silently ignored (no error, no sort)
            rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("return_order_line") to see
            what's available, e.g. filters={"outcome": <code>} for lines
            with a specific return outcome recorded.
        limit: maximum number of results to return - defaults to 100 (the
            maximum) to minimize round trips for large result sets; pass a
            smaller value to page through results in smaller batches.
        offset: pagination offset.
    """
    base: dict[str, Any] = {}
    if order is not None:
        base["order"] = order
    if item is not None:
        base["item"] = item
    if received is not None:
        base["received"] = received
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        resolve_view("order.api", "ReturnOrderLineItemList"),
        "GET",
        "/api/order/ro-line/",
        query_params=params,
    )


@mcp.tool()
async def get_return_order_line(
    line_id: int, filters: dict[str, Any] | None = None
) -> dict:
    """Get full detail for a single return order line item by its ID.

    Returns the same object shape as one entry in list_return_order_lines's
    `results` array. Get a valid ID from list_return_order_lines (its `pk`
    field) if you don't already have one.

    Args:
        line_id: the ReturnOrderLineItem's database ID.
        filters: optional-field toggles beyond what's returned by default -
            call describe_filters("return_order_line") and check its
            optional_fields.

    Raises:
        ToolError: no line item exists with that ID, or the caller doesn't
            have permission to view it.
    """
    return await call_view(
        resolve_view("order.api", "ReturnOrderLineItemDetail"),
        "GET",
        f"/api/order/ro-line/{line_id}/",
        pk=line_id,
        query_params=filters,
    )
