"""MCP tools for querying InvenTree SalesOrder data."""

from __future__ import annotations

from typing import Any

from ..mcp_server import mcp
from ..proxy import call_view
from ._common import build_query_params


@mcp.tool()
async def list_sales_orders(
    customer: int | None = None,
    status: int | None = None,
    outstanding: bool | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """List sales orders.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_sales_order's return value
    - use its `pk` field with get_sales_order to fetch full detail for one
    of them.

    Args:
        customer: restrict to orders placed by this customer Company ID.
        status: restrict to orders with this numeric status code. Status
            codes are instance-specific - rather than guessing one, read the
            `status`/`status_text` fields on an order you already have, or
            use filters={"outstanding": true} / filters={"overdue": true}
            for the common cases instead.
        outstanding: True for orders that are still open (not yet
            shipped/cancelled), False for the inverse. Omit to include both.
        ordering: field to sort results by, e.g. "-target_date" for the
            orders due soonest first ('-' prefix for descending, omit it for
            ascending). Combine with limit to get a "top N by X" result, e.g.
            ordering="target_date", limit=5 for the 5 most overdue open
            orders. Call describe_filters("sales_order") and check its
            ordering_fields list for valid values - an unrecognized field is
            silently ignored (no error, no sort) rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("sales_order") to see what's
            available, e.g. filters={"overdue": true}.
        limit: maximum number of results to return (capped at 100).
        offset: pagination offset.
    """
    from order.api import SalesOrderList

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

    return await call_view(SalesOrderList, "GET", "/api/order/so/", query_params=params)


@mcp.tool()
async def get_sales_order(order_id: int) -> dict:
    """Get full detail for a single sales order by its ID.

    Returns the same object shape as one entry in list_sales_orders's
    `results` array. Get a valid ID from list_sales_orders (its `pk` field)
    if you don't already have one. Use list_sales_order_lines (filtering by
    `order`) to see this order's individual line items, and
    list_sales_order_allocations (filtering by `order`) to see which stock
    has been allocated against it.

    Args:
        order_id: the SalesOrder's database ID.

    Raises:
        ToolError: no sales order exists with that ID, or the caller
            doesn't have permission to view it.
    """
    from order.api import SalesOrderDetail

    return await call_view(
        SalesOrderDetail, "GET", f"/api/order/so/{order_id}/", pk=order_id
    )


@mcp.tool()
async def list_sales_order_lines(
    order: int | None = None,
    part: int | None = None,
    allocated: bool | None = None,
    completed: bool | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """List sales order line items.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_sales_order_line's return
    value - use its `pk` field with get_sales_order_line to fetch full
    detail for one of them.

    Args:
        order: restrict to lines on this SalesOrder ID.
        part: restrict to lines for this Part ID.
        allocated: True for lines whose allocated stock quantity has
            reached the line's ordered quantity, False for lines still
            needing more stock allocated. Omit to include both.
        completed: True for lines that have been fully shipped, False for
            lines still awaiting shipment. Omit to include both.
        ordering: field to sort results by ('-' prefix for descending, omit
            it for ascending). Call describe_filters("sales_order_line") and
            check its ordering_fields list for valid values - an
            unrecognized field is silently ignored (no error, no sort)
            rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("sales_order_line") to see what's
            available.
        limit: maximum number of results to return (capped at 100).
        offset: pagination offset.
    """
    from order.api import SalesOrderLineItemList

    base: dict[str, Any] = {}
    if order is not None:
        base["order"] = order
    if part is not None:
        base["part"] = part
    if allocated is not None:
        base["allocated"] = allocated
    if completed is not None:
        base["completed"] = completed
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        SalesOrderLineItemList, "GET", "/api/order/so-line/", query_params=params
    )


@mcp.tool()
async def get_sales_order_line(line_id: int) -> dict:
    """Get full detail for a single sales order line item by its ID.

    Returns the same object shape as one entry in list_sales_order_lines's
    `results` array. Get a valid ID from list_sales_order_lines (its `pk`
    field) if you don't already have one.

    Args:
        line_id: the SalesOrderLineItem's database ID.

    Raises:
        ToolError: no line item exists with that ID, or the caller doesn't
            have permission to view it.
    """
    from order.api import SalesOrderLineItemDetail

    return await call_view(
        SalesOrderLineItemDetail,
        "GET",
        f"/api/order/so-line/{line_id}/",
        pk=line_id,
    )


@mcp.tool()
async def list_sales_order_allocations(
    order: int | None = None,
    line: int | None = None,
    item: int | None = None,
    part: int | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """List sales order allocations - stock items reserved against sales order lines.

    Each allocation links a StockItem to a SalesOrderLineItem (and
    optionally a shipment) for some quantity. This is the read side of
    stock allocation - use list_sales_order_lines's `allocated`/`quantity`
    fields to see the aggregate picture for a line, and this tool to see
    exactly which stock items make it up.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_sales_order_allocation's
    return value - use its `pk` field with get_sales_order_allocation to
    fetch full detail for one of them.

    Args:
        order: restrict to allocations on this SalesOrder ID.
        line: restrict to allocations against this SalesOrderLineItem ID.
        item: restrict to allocations of this StockItem ID.
        part: restrict to allocations of stock for this Part ID.
        ordering: field to sort results by ('-' prefix for descending, omit
            it for ascending). Call describe_filters("sales_order_allocation")
            and check its ordering_fields list for valid values - an
            unrecognized field is silently ignored (no error, no sort)
            rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("sales_order_allocation") to see
            what's available, e.g. filters={"assigned_to_shipment": false}
            for allocations not yet assigned to a shipment.
        limit: maximum number of results to return (capped at 100).
        offset: pagination offset.
    """
    from order.api import SalesOrderAllocationList

    base: dict[str, Any] = {}
    if order is not None:
        base["order"] = order
    if line is not None:
        base["line"] = line
    if item is not None:
        base["item"] = item
    if part is not None:
        base["part"] = part
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        SalesOrderAllocationList,
        "GET",
        "/api/order/so-allocation/",
        query_params=params,
    )


@mcp.tool()
async def get_sales_order_allocation(allocation_id: int) -> dict:
    """Get full detail for a single sales order allocation by its ID.

    Returns the same object shape as one entry in
    list_sales_order_allocations's `results` array. Get a valid ID from
    list_sales_order_allocations (its `pk` field) if you don't already have
    one.

    Args:
        allocation_id: the SalesOrderAllocation's database ID.

    Raises:
        ToolError: no allocation exists with that ID, or the caller doesn't
            have permission to view it.
    """
    from order.api import SalesOrderAllocationDetail

    return await call_view(
        SalesOrderAllocationDetail,
        "GET",
        f"/api/order/so-allocation/{allocation_id}/",
        pk=allocation_id,
    )
