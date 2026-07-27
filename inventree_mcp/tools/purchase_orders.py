"""MCP tools for querying InvenTree PurchaseOrder data."""

from __future__ import annotations

from typing import Any

from ..mcp_server import mcp
from ..proxy import call_view
from ._common import build_query_params


@mcp.tool()
async def list_purchase_orders(
    supplier: int | None = None,
    status: int | None = None,
    outstanding: bool | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """List purchase orders.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_purchase_order's return
    value - use its `pk` field with get_purchase_order to fetch full detail
    for one of them.

    Args:
        supplier: restrict to orders placed with this supplier Company ID.
        status: restrict to orders with this numeric status code. Status
            codes are instance-specific - rather than guessing one, read the
            `status`/`status_text` fields on an order you already have, or
            use filters={"outstanding": true} / filters={"overdue": true}
            for the common cases instead.
        outstanding: True for orders that are still open (not yet
            complete/cancelled), False for the inverse. Omit to include both.
        ordering: field to sort results by, e.g. "-target_date" for the
            orders due soonest first ('-' prefix for descending, omit it for
            ascending). Combine with limit to get a "top N by X" result, e.g.
            ordering="target_date", limit=5 for the 5 most overdue open
            orders. Call describe_filters("purchase_order") and check its
            ordering_fields list for valid values - an unrecognized field is
            silently ignored (no error, no sort) rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("purchase_order") to see what's
            available, e.g. filters={"overdue": true}.
        limit: maximum number of results to return (capped at 100).
        offset: pagination offset.
    """
    from order.api import PurchaseOrderList

    base: dict[str, Any] = {}
    if supplier is not None:
        base["supplier"] = supplier
    if status is not None:
        base["status"] = status
    if outstanding is not None:
        base["outstanding"] = outstanding
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        PurchaseOrderList, "GET", "/api/order/po/", query_params=params
    )


@mcp.tool()
async def get_purchase_order(order_id: int) -> dict:
    """Get full detail for a single purchase order by its ID.

    Returns the same object shape as one entry in list_purchase_orders's
    `results` array. Get a valid ID from list_purchase_orders (its `pk`
    field) if you don't already have one. Use list_purchase_order_lines
    (filtering by `order`) to see this order's individual line items.

    Args:
        order_id: the PurchaseOrder's database ID.

    Raises:
        ToolError: no purchase order exists with that ID, or the caller
            doesn't have permission to view it.
    """
    from order.api import PurchaseOrderDetail

    return await call_view(
        PurchaseOrderDetail, "GET", f"/api/order/po/{order_id}/", pk=order_id
    )


@mcp.tool()
async def list_purchase_order_lines(
    order: int | None = None,
    part: int | None = None,
    received: bool | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """List purchase order line items.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_purchase_order_line's
    return value - use its `pk` field with get_purchase_order_line to fetch
    full detail for one of them.

    Args:
        order: restrict to lines on this PurchaseOrder ID.
        part: restrict to lines for this SupplierPart ID (the
            supplier-specific part number, not the internal Part ID - use
            filters={"base_part": <id>} to filter by the internal Part
            instead).
        received: True for lines that have received their full ordered
            quantity, False for lines still pending receipt. Omit to
            include both.
        ordering: field to sort results by ('-' prefix for descending, omit
            it for ascending). Call describe_filters("purchase_order_line")
            and check its ordering_fields list for valid values - an
            unrecognized field is silently ignored (no error, no sort)
            rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("purchase_order_line") to see
            what's available.
        limit: maximum number of results to return (capped at 100).
        offset: pagination offset.
    """
    from order.api import PurchaseOrderLineItemList

    base: dict[str, Any] = {}
    if order is not None:
        base["order"] = order
    if part is not None:
        base["part"] = part
    if received is not None:
        base["received"] = received
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        PurchaseOrderLineItemList, "GET", "/api/order/po-line/", query_params=params
    )


@mcp.tool()
async def get_purchase_order_line(line_id: int) -> dict:
    """Get full detail for a single purchase order line item by its ID.

    Returns the same object shape as one entry in
    list_purchase_order_lines's `results` array. Get a valid ID from
    list_purchase_order_lines (its `pk` field) if you don't already have one.

    Args:
        line_id: the PurchaseOrderLineItem's database ID.

    Raises:
        ToolError: no line item exists with that ID, or the caller doesn't
            have permission to view it.
    """
    from order.api import PurchaseOrderLineItemDetail

    return await call_view(
        PurchaseOrderLineItemDetail,
        "GET",
        f"/api/order/po-line/{line_id}/",
        pk=line_id,
    )
