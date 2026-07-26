"""MCP tools for querying InvenTree ManufacturerPart/SupplierPart data.

These are the purchasing-side catalog: a ManufacturerPart links an internal
Part to a manufacturer's part number (MPN), and a SupplierPart links a Part
(optionally via a specific ManufacturerPart) to a supplier's SKU and pricing.
"""

from __future__ import annotations

from typing import Any

from ..mcp_server import mcp
from ..proxy import call_view
from ._common import build_query_params


@mcp.tool()
async def list_manufacturer_parts(
    part: int | None = None,
    manufacturer: int | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 25,
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
        filters: additional filter/ordering parameters beyond the named
            arguments above - call describe_filters("manufacturer_part") to
            see what's available, e.g. filters={"MPN": "ABC-123"}.
        limit: maximum number of results to return (capped at 100).
        offset: pagination offset.
    """
    from company.api import ManufacturerPartList

    base: dict[str, Any] = {}
    if part is not None:
        base["part"] = part
    if manufacturer is not None:
        base["manufacturer"] = manufacturer

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        ManufacturerPartList,
        "GET",
        "/api/company/part/manufacturer/",
        query_params=params,
    )


@mcp.tool()
async def get_manufacturer_part(manufacturer_part_id: int) -> dict:
    """Get full detail for a single manufacturer part by its ID.

    Returns the same object shape as one entry in
    list_manufacturer_parts's `results` array. Get a valid ID from
    list_manufacturer_parts (its `pk` field) if you don't already have one.

    Args:
        manufacturer_part_id: the ManufacturerPart's database ID.

    Raises:
        ToolError: no manufacturer part exists with that ID, or the caller
            doesn't have permission to view it.
    """
    from company.api import ManufacturerPartDetail

    return await call_view(
        ManufacturerPartDetail,
        "GET",
        f"/api/company/part/manufacturer/{manufacturer_part_id}/",
        pk=manufacturer_part_id,
    )


@mcp.tool()
async def list_supplier_parts(
    part: int | None = None,
    supplier: int | None = None,
    manufacturer_part: int | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 25,
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
        filters: additional filter/ordering parameters beyond the named
            arguments above - call describe_filters("supplier_part") to see
            what's available, e.g. filters={"has_stock": true} or
            filters={"SKU": "SKU-123"}.
        limit: maximum number of results to return (capped at 100).
        offset: pagination offset.
    """
    from company.api import SupplierPartList

    base: dict[str, Any] = {}
    if part is not None:
        base["part"] = part
    if supplier is not None:
        base["supplier"] = supplier
    if manufacturer_part is not None:
        base["manufacturer_part"] = manufacturer_part

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        SupplierPartList, "GET", "/api/company/part/", query_params=params
    )


@mcp.tool()
async def get_supplier_part(supplier_part_id: int) -> dict:
    """Get full detail for a single supplier part by its ID.

    Returns the same object shape as one entry in list_supplier_parts's
    `results` array. Get a valid ID from list_supplier_parts (its `pk`
    field) if you don't already have one.

    Args:
        supplier_part_id: the SupplierPart's database ID.

    Raises:
        ToolError: no supplier part exists with that ID, or the caller
            doesn't have permission to view it.
    """
    from company.api import SupplierPartDetail

    return await call_view(
        SupplierPartDetail,
        "GET",
        f"/api/company/part/{supplier_part_id}/",
        pk=supplier_part_id,
    )
