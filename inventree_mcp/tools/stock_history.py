"""MCP tools for querying InvenTree stock history: tracking entries and test results.

Both resources are read-only historical records attached to a stock item -
StockItemTracking is the generic audit trail (created/moved/split/status
changed/...), StockItemTestResult is QA pass/fail data recorded against a
PartTestTemplate. Neither can be created or edited via these tools even once
write tools exist elsewhere in this plugin - InvenTree itself only ever
creates tracking entries as a side effect of other actions.
"""

from __future__ import annotations

from typing import Any

from ..mcp_server import mcp
from ..proxy import call_view
from ._common import build_query_params


@mcp.tool()
async def list_stock_tracking(
    item: int | None = None,
    part: int | None = None,
    user: int | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """List stock tracking entries - the audit trail of what happened to stock items.

    Each entry records one historical event for a stock item (created,
    moved, split, merged, status changed, ...) - this is what InvenTree's UI
    shows as a stock item's "history" tab. Entries are created automatically
    by other actions; there is no way to create one directly.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_stock_tracking's return
    value - use its `pk` field with get_stock_tracking to fetch full detail
    for one of them. Each entry's `deltas` field holds event-specific data
    (e.g. quantity change, location change) whose keys vary by tracking
    type - see the `tracking_type`/`label` fields to interpret it.

    Args:
        item: restrict to tracking entries for this StockItem ID.
        part: restrict to tracking entries for stock items of this Part ID.
            Combine with filters={"include_variants": true} to also include
            variants of that part.
        user: restrict to entries recorded by this User ID.
        ordering: field to sort results by ('-' prefix for descending, omit
            it for ascending, e.g. "-date" for most recent first). Call
            describe_filters("stock_tracking") and check its ordering_fields
            list for valid values - an unrecognized field is silently
            ignored (no error, no sort) rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("stock_tracking") to see what's
            available, e.g. filters={"min_date": "2024-01-01"}.
        limit: maximum number of results to return (capped at 100).
        offset: pagination offset.
    """
    from stock.api import StockTrackingList

    base: dict[str, Any] = {}
    if item is not None:
        base["item"] = item
    if part is not None:
        base["part"] = part
    if user is not None:
        base["user"] = user
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        StockTrackingList, "GET", "/api/stock/track/", query_params=params
    )


@mcp.tool()
async def get_stock_tracking(
    tracking_id: int, filters: dict[str, Any] | None = None
) -> dict:
    """Get full detail for a single stock tracking entry by its ID.

    Returns the same object shape as one entry in list_stock_tracking's
    `results` array. Get a valid ID from list_stock_tracking (its `pk`
    field) if you don't already have one.

    Args:
        tracking_id: the StockItemTracking entry's database ID.
        filters: optional-field toggles beyond what's returned by default -
            call describe_filters("stock_tracking") and check its
            optional_fields.

    Raises:
        ToolError: no tracking entry exists with that ID, or the caller
            doesn't have permission to view it.
    """
    from stock.api import StockTrackingDetail

    return await call_view(
        StockTrackingDetail,
        "GET",
        f"/api/stock/track/{tracking_id}/",
        pk=tracking_id,
        query_params=filters,
    )


@mcp.tool()
async def list_stock_test_results(
    stock_item: int | None = None,
    template: int | None = None,
    result: bool | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """List stock item test results (QA pass/fail records).

    Each entry records the outcome of one test (defined by a
    PartTestTemplate - see get_part's `test_templates` or
    describe_filters("stock_test_result") for how to look templates up) run
    against a specific, usually-serialized stock item.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_stock_test_result's return
    value - use its `pk` field with get_stock_test_result to fetch full
    detail for one of them.

    Args:
        stock_item: restrict to results for this StockItem ID. Combine with
            filters={"include_installed": true} to also include results for
            items installed underneath it.
        template: restrict to results for this PartTestTemplate ID.
        result: True for passing results, False for failing ones. Omit to
            include both.
        ordering: field to sort results by ('-' prefix for descending, omit
            it for ascending). Call describe_filters("stock_test_result")
            and check its ordering_fields list for valid values - an
            unrecognized field is silently ignored (no error, no sort)
            rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("stock_test_result") to see
            what's available, e.g. filters={"build": <id>} for results
            recorded against stock from a specific build order.
        limit: maximum number of results to return (capped at 100).
        offset: pagination offset.
    """
    from stock.api import StockItemTestResultList

    base: dict[str, Any] = {}
    if stock_item is not None:
        base["stock_item"] = stock_item
    if template is not None:
        base["template"] = template
    if result is not None:
        base["result"] = result
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        StockItemTestResultList, "GET", "/api/stock/test/", query_params=params
    )


@mcp.tool()
async def get_stock_test_result(
    result_id: int, filters: dict[str, Any] | None = None
) -> dict:
    """Get full detail for a single stock item test result by its ID.

    Returns the same object shape as one entry in list_stock_test_results's
    `results` array. Get a valid ID from list_stock_test_results (its `pk`
    field) if you don't already have one.

    Args:
        result_id: the StockItemTestResult's database ID.
        filters: optional-field toggles beyond what's returned by default -
            call describe_filters("stock_test_result") and check its
            optional_fields.

    Raises:
        ToolError: no test result exists with that ID, or the caller
            doesn't have permission to view it.
    """
    from stock.api import StockItemTestResultDetail

    return await call_view(
        StockItemTestResultDetail,
        "GET",
        f"/api/stock/test/{result_id}/",
        pk=result_id,
        query_params=filters,
    )
