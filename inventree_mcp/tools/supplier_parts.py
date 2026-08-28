"""MCP tools for querying InvenTree ManufacturerPart/SupplierPart data.

These are the purchasing-side catalog: a ManufacturerPart links an internal
Part to a manufacturer's part number (MPN), and a SupplierPart links a Part
(optionally via a specific ManufacturerPart) to a supplier's SKU and pricing.
"""

from __future__ import annotations

from typing import Any

from ..mcp_server import mcp
from ..proxy import call_view
from ..view_resolution import resolve_view
from ._common import build_query_params


@mcp.tool()
async def list_manufacturer_parts(
    part: int | None = None,
    manufacturer: int | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List manufacturer parts - links between an internal Part and a manufacturer's MPN.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_manufacturer_part's return
    value - use its `pk` field with get_manufacturer_part to fetch full
    detail for one of them.

    Args:
        part: restrict to manufacturer parts for this internal Part ID.
        manufacturer: restrict to manufacturer parts from this manufacturer
            Company ID.
        ordering: field to sort results by ('-' prefix for descending, omit
            it for ascending). Call describe_filters("manufacturer_part")
            and check its ordering_fields list for valid values - an
            unrecognized field is silently ignored (no error, no sort)
            rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("manufacturer_part") to see
            what's available, e.g. filters={"MPN": "ABC-123"}.
        limit: maximum number of results to return - defaults to 100 (the
            maximum) to minimize round trips for large result sets; pass a
            smaller value to page through results in smaller batches.
        offset: pagination offset.
    """
    base: dict[str, Any] = {}
    if part is not None:
        base["part"] = part
    if manufacturer is not None:
        base["manufacturer"] = manufacturer
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        resolve_view("company.api", "ManufacturerPartList"),
        "GET",
        "/api/company/part/manufacturer/",
        query_params=params,
    )


@mcp.tool()
async def get_manufacturer_part(
    manufacturer_part_id: int, filters: dict[str, Any] | None = None
) -> dict:
    """Get full detail for a single manufacturer part by its ID.

    Returns the same object shape as one entry in
    list_manufacturer_parts's `results` array. Get a valid ID from
    list_manufacturer_parts (its `pk` field) if you don't already have one.

    Args:
        manufacturer_part_id: the ManufacturerPart's database ID.
        filters: optional-field toggles beyond what's returned by default -
            call describe_filters("manufacturer_part") and check its
            optional_fields.

    Raises:
        ToolError: no manufacturer part exists with that ID, or the caller
            doesn't have permission to view it.
    """
    return await call_view(
        resolve_view("company.api", "ManufacturerPartDetail"),
        "GET",
        f"/api/company/part/manufacturer/{manufacturer_part_id}/",
        pk=manufacturer_part_id,
        query_params=filters,
    )


@mcp.tool()
async def list_supplier_parts(
    part: int | None = None,
    supplier: int | None = None,
    manufacturer_part: int | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List supplier parts - a supplier's SKU/pricing for a Part.

    This is the purchasing catalog: what a given supplier calls a part, and
    at what SKU. Purchase order lines reference these by ID (see
    list_purchase_order_lines's `part` argument).

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_supplier_part's return
    value - use its `pk` field with get_supplier_part to fetch full detail
    for one of them.

    Args:
        part: restrict to supplier parts for this internal Part ID.
        supplier: restrict to supplier parts from this supplier Company ID.
        manufacturer_part: restrict to supplier parts linked to this
            ManufacturerPart ID.
        ordering: field to sort results by ('-' prefix for descending, omit
            it for ascending). Call describe_filters("supplier_part") and
            check its ordering_fields list for valid values - an
            unrecognized field is silently ignored (no error, no sort)
            rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("supplier_part") to see what's
            available, e.g. filters={"has_stock": true} or
            filters={"SKU": "SKU-123"}.
        limit: maximum number of results to return - defaults to 100 (the
            maximum) to minimize round trips for large result sets; pass a
            smaller value to page through results in smaller batches.
        offset: pagination offset.
    """
    base: dict[str, Any] = {}
    if part is not None:
        base["part"] = part
    if supplier is not None:
        base["supplier"] = supplier
    if manufacturer_part is not None:
        base["manufacturer_part"] = manufacturer_part
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        resolve_view("company.api", "SupplierPartList"),
        "GET",
        "/api/company/part/",
        query_params=params,
    )


@mcp.tool()
async def get_supplier_part(
    supplier_part_id: int, filters: dict[str, Any] | None = None
) -> dict:
    """Get full detail for a single supplier part by its ID.

    Returns the same object shape as one entry in list_supplier_parts's
    `results` array. Get a valid ID from list_supplier_parts (its `pk`
    field) if you don't already have one.

    Args:
        supplier_part_id: the SupplierPart's database ID.
        filters: optional-field toggles beyond what's returned by default -
            call describe_filters("supplier_part") and check its
            optional_fields, e.g. filters={"supplier_detail": true}.

    Raises:
        ToolError: no supplier part exists with that ID, or the caller
            doesn't have permission to view it.
    """
    return await call_view(
        resolve_view("company.api", "SupplierPartDetail"),
        "GET",
        f"/api/company/part/{supplier_part_id}/",
        pk=supplier_part_id,
        query_params=filters,
    )
