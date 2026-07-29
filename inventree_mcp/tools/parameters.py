"""MCP tools for querying InvenTree Parameter/ParameterTemplate data.

Parameters are generic: a single model (`common.models.Parameter`) records a
named attribute value (e.g. a Part's "Resistance" or "Package") against
almost any other InvenTree record via a (`model_type`, `model_id`) pair,
rather than each resource having its own attribute table. A
`ParameterTemplate` defines the name/units/choices a Parameter references -
see list_parameter_templates.

See list_parameters' docstring for the exact `model_type` string format
this uses, and note it differs from `attachments.py`'s.
"""

from __future__ import annotations

from typing import Any

from ..mcp_server import mcp
from ..proxy import call_view
from ._common import build_query_params


@mcp.tool()
async def list_parameters(
    model_type: str | None = None,
    model_id: int | None = None,
    template: int | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """List parameters (named attribute values) recorded against InvenTree records.

    Parameters can be recorded against several InvenTree record types -
    parts, part categories, stock locations, builds, companies,
    manufacturer/supplier parts, purchase/sales/return/transfer orders,
    sales order shipments. Pass both `model_type` and `model_id` together to
    see everything recorded for one specific record, e.g. "what are part
    42's parameters?". Each entry's `template` links to a ParameterTemplate
    (see list_parameter_templates) which defines the parameter's name/units.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_parameter's return value -
    use its `pk` field with get_parameter to fetch full detail for one of
    them.

    Args:
        model_type: restrict to parameters on this record type, in
            "app_label.modelname" format, e.g. "part.part",
            "part.partcategory", "stock.stocklocation", "build.build",
            "company.company", "company.manufacturerpart",
            "company.supplierpart", "order.purchaseorder",
            "order.salesorder", "order.returnorder", "order.transferorder",
            "order.salesordershipment". Note this is a *different* string
            format from list_attachments' `model_type` (a plain lowercase
            model name) - the two generic-metadata systems don't share a
            format. If unsure of the exact string for a record you already
            have a parameter for, reuse that parameter's own `model_type`
            field rather than guessing.
        model_id: restrict to parameters on this specific record ID (use
            together with model_type to scope to one record).
        template: restrict to parameters using this ParameterTemplate ID -
            call list_parameter_templates to find one, e.g. to answer
            "which records have a Resistance parameter set?".
        ordering: field to sort results by ('-' prefix for descending, omit
            it for ascending). Call describe_filters("parameter") and
            check its ordering_fields list for valid values - an
            unrecognized field is silently ignored (no error, no sort)
            rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("parameter") to see what's
            available.
        limit: maximum number of results to return (capped at 100).
        offset: pagination offset.
    """
    from common.api import ParameterList

    base: dict[str, Any] = {}
    if model_type is not None:
        base["model_type"] = model_type
    if model_id is not None:
        base["model_id"] = model_id
    if template is not None:
        base["template"] = template
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(ParameterList, "GET", "/api/parameter/", query_params=params)


@mcp.tool()
async def get_parameter(
    parameter_id: int, filters: dict[str, Any] | None = None
) -> dict:
    """Get full detail for a single parameter by its ID.

    Returns the same object shape as one entry in list_parameters's
    `results` array. Get a valid ID from list_parameters (its `pk` field)
    if you don't already have one.

    Args:
        parameter_id: the Parameter's database ID.
        filters: optional-field toggles beyond what's returned by default -
            call describe_filters("parameter") and check its optional_fields.

    Raises:
        ToolError: no parameter exists with that ID, or the caller doesn't
            have permission to view it.
    """
    from common.api import ParameterDetail

    return await call_view(
        ParameterDetail,
        "GET",
        f"/api/parameter/{parameter_id}/",
        pk=parameter_id,
        query_params=filters,
    )


@mcp.tool()
async def list_parameter_templates(
    search: str | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """List parameter templates - the named attribute definitions parameters reference.

    A template defines a parameter's name, physical units, and (optionally)
    a fixed set of choices - it is not a value itself. Use list_parameters
    (filtering by `template`) to see the actual recorded values for a given
    template.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_parameter_template's
    return value - use its `pk` field with get_parameter_template to fetch
    full detail for one of them.

    Args:
        search: free-text search against name/description.
        ordering: field to sort results by ('-' prefix for descending, omit
            it for ascending). Call describe_filters("parameter_template")
            and check its ordering_fields list for valid values - an
            unrecognized field is silently ignored (no error, no sort)
            rather than rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("parameter_template") to see
            what's available, e.g. filters={"units": "V"} or
            filters={"has_choices": true}.
        limit: maximum number of results to return (capped at 100).
        offset: pagination offset.
    """
    from common.api import ParameterTemplateList

    base: dict[str, Any] = {}
    if search is not None:
        base["search"] = search
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        ParameterTemplateList, "GET", "/api/parameter/template/", query_params=params
    )


@mcp.tool()
async def get_parameter_template(
    template_id: int, filters: dict[str, Any] | None = None
) -> dict:
    """Get full detail for a single parameter template by its ID.

    Returns the same object shape as one entry in list_parameter_templates's
    `results` array. Get a valid ID from list_parameter_templates (its `pk`
    field) if you don't already have one.

    Args:
        template_id: the ParameterTemplate's database ID.
        filters: optional-field toggles beyond what's returned by default -
            call describe_filters("parameter_template") and check its
            optional_fields.

    Raises:
        ToolError: no parameter template exists with that ID, or the caller
            doesn't have permission to view it.
    """
    from common.api import ParameterTemplateDetail

    return await call_view(
        ParameterTemplateDetail,
        "GET",
        f"/api/parameter/template/{template_id}/",
        pk=template_id,
        query_params=filters,
    )
