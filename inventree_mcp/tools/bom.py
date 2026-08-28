"""MCP tools for querying InvenTree Bill of Materials (BOM) data.

**Inherited BOM items** (read this before querying by `part`): a BomItem
row's `inherited` flag means it applies not just to the assembly it's
defined on, but to every *variant* of that assembly too (variants are
parts linked via `variant_of`, e.g. a template "Widget" and its variant
"Widget - Blue"). `list_bom_items(part=...)` resolves this the same way
InvenTree's own UI/API does: it returns both the rows defined directly on
`part` *and* any `inherited=true` rows defined on `part`'s ancestor
(template) parts.

This means a returned row's own `part` field can differ from the `part`
you queried by - an inherited row's `part` is the *template* part it was
actually defined on, not the variant you asked about. Don't assume
`row["part"] == the part you filtered by`; check `row["inherited"]`
instead to know whether a row applies directly or via inheritance.
`allow_variants` is a separate, unrelated flag - it controls whether
*variants of the sub_part* (the component) can be substituted at build
time, not whether the BomItem itself is inherited.
"""

from __future__ import annotations

from typing import Any

from ..mcp_server import mcp
from ..proxy import call_view
from ..view_resolution import resolve_view
from ._common import build_query_params


@mcp.tool()
async def list_bom_items(
    part: int | None = None,
    uses: int | None = None,
    category: int | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List BOM (Bill of Materials) items - the components required to build an assembly.

    Each entry links an assembly (`part`) to one required component
    (`sub_part`) and a quantity. This is the design-time BOM, independent
    of any specific build order - see list_build_lines for a BOM scaled to
    an in-progress build order's quantity.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_bom_item's return value -
    use its `pk` field with get_bom_item to fetch full detail for one of
    them. Use list_bom_substitutes (filtering by `bom_item`) to see
    permitted substitute parts for a given line.

    Args:
        part: restrict to BOM items for this assembly Part ID. This
            resolves the *effective* BOM: it includes rows defined
            directly on `part` plus any `inherited=true` rows defined on
            an ancestor (template) part - see this module's docstring for
            why an inherited row's own `part` field won't equal the ID you
            passed here, and how to tell the two cases apart with each
            row's `inherited` field.
        uses: reverse lookup - restrict to BOM items where this Part ID is
            actually consumed, whether as the directly-specified sub_part,
            as a variant of it (when a row's `allow_variants` is true), or
            as a specified substitute. Answers "what assemblies use this
            part?" as opposed to `part`'s "what does this assembly use?".
        category: restrict to BOM items whose component (sub_part) belongs
            to this PartCategory ID (includes subcategories).
        ordering: field to sort results by, e.g. "-quantity" for the
            largest-quantity components first ('-' prefix for descending,
            omit it for ascending). Combine with limit to get a "top N by X"
            result, e.g. ordering="-quantity", limit=5 for the 5 highest-
            quantity components of an assembly. Call
            describe_filters("bom_item") and check its ordering_fields list
            for valid values - an unrecognized field is silently ignored (no
            error, no sort) rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("bom_item") to see what's
            available, e.g. filters={"validated": false} for lines whose
            checksum hasn't been confirmed, or filters={"available_stock": true}.
        limit: maximum number of results to return - defaults to 100 (the
            maximum) to minimize round trips for large result sets; pass a
            smaller value to page through results in smaller batches.
        offset: pagination offset.
    """
    base: dict[str, Any] = {}
    if part is not None:
        base["part"] = part
    if uses is not None:
        base["uses"] = uses
    if category is not None:
        base["category"] = category
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        resolve_view("part.api", "BomList"), "GET", "/api/bom/", query_params=params
    )


@mcp.tool()
async def get_bom_item(bom_item_id: int, filters: dict[str, Any] | None = None) -> dict:
    """Get full detail for a single BOM item by its ID.

    Returns the same object shape as one entry in list_bom_items's
    `results` array. Get a valid ID from list_bom_items (its `pk` field)
    if you don't already have one.

    Args:
        bom_item_id: the BomItem's database ID.
        filters: optional-field toggles beyond what's returned by default -
            call describe_filters("bom_item") and check its optional_fields.

    Raises:
        ToolError: no BOM item exists with that ID, or the caller doesn't
            have permission to view it.
    """
    return await call_view(
        resolve_view("part.api", "BomDetail"),
        "GET",
        f"/api/bom/{bom_item_id}/",
        pk=bom_item_id,
        query_params=filters,
    )


@mcp.tool()
async def list_bom_substitutes(
    bom_item: int | None = None,
    part: int | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List BOM item substitutes - alternative parts permitted in place of a BOM line's sub_part.

    A substitute is a directly-specified alternative, distinct from
    variant substitution (see list_bom_items's `allow_variants` field,
    which permits variants of the sub_part itself without a substitute
    row existing).

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_bom_substitute's return
    value - use its `pk` field with get_bom_substitute to fetch full
    detail for one of them.

    Args:
        bom_item: restrict to substitutes defined for this BomItem ID.
        part: restrict to substitute entries permitting this Part ID.
        ordering: field to sort results by ('-' prefix for descending, omit
            it for ascending). Call describe_filters("bom_substitute") and
            check its ordering_fields list for valid values - an
            unrecognized field is silently ignored (no error, no sort)
            rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("bom_substitute") to see what's
            available.
        limit: maximum number of results to return - defaults to 100 (the
            maximum) to minimize round trips for large result sets; pass a
            smaller value to page through results in smaller batches.
        offset: pagination offset.
    """
    base: dict[str, Any] = {}
    if bom_item is not None:
        base["bom_item"] = bom_item
    if part is not None:
        base["part"] = part
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        resolve_view("part.api", "BomItemSubstituteList"),
        "GET",
        "/api/bom/substitute/",
        query_params=params,
    )


@mcp.tool()
async def get_bom_substitute(
    substitute_id: int, filters: dict[str, Any] | None = None
) -> dict:
    """Get full detail for a single BOM item substitute by its ID.

    Returns the same object shape as one entry in list_bom_substitutes's
    `results` array. Get a valid ID from list_bom_substitutes (its `pk`
    field) if you don't already have one.

    Args:
        substitute_id: the BomItemSubstitute's database ID.
        filters: optional-field toggles beyond what's returned by default -
            call describe_filters("bom_substitute") and check its
            optional_fields.

    Raises:
        ToolError: no substitute entry exists with that ID, or the caller
            doesn't have permission to view it.
    """
    return await call_view(
        resolve_view("part.api", "BomItemSubstituteDetail"),
        "GET",
        f"/api/bom/substitute/{substitute_id}/",
        pk=substitute_id,
        query_params=filters,
    )
