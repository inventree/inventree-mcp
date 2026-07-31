"""MCP tools for discovering what's filterable/searchable/expandable on the list and get tools."""

from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError

from ..expand_introspection import describe_output_options
from ..filter_introspection import describe_filterset
from ..mcp_server import mcp
from ..view_resolution import resolve_view


def _part_list() -> type | None:
    return resolve_view("part.api", "PartList")


def _stock_list() -> type | None:
    return resolve_view("stock.api", "StockList")


def _stock_location_list() -> type | None:
    return resolve_view("stock.api", "StockLocationList")


def _category_list() -> type | None:
    return resolve_view("part.api", "CategoryList")


def _purchase_order_list() -> type | None:
    return resolve_view("order.api", "PurchaseOrderList")


def _purchase_order_line_list() -> type | None:
    return resolve_view("order.api", "PurchaseOrderLineItemList")


def _sales_order_list() -> type | None:
    return resolve_view("order.api", "SalesOrderList")


def _sales_order_line_list() -> type | None:
    return resolve_view("order.api", "SalesOrderLineItemList")


def _sales_order_allocation_list() -> type | None:
    return resolve_view("order.api", "SalesOrderAllocationList")


def _build_order_list() -> type | None:
    return resolve_view("build.api", "BuildList")


def _build_line_list() -> type | None:
    return resolve_view("build.api", "BuildLineList")


def _build_item_list() -> type | None:
    return resolve_view("build.api", "BuildItemList")


def _company_list() -> type | None:
    return resolve_view("company.api", "CompanyList")


def _contact_list() -> type | None:
    return resolve_view("company.api", "ContactList")


def _address_list() -> type | None:
    return resolve_view("company.api", "AddressList")


def _manufacturer_part_list() -> type | None:
    return resolve_view("company.api", "ManufacturerPartList")


def _supplier_part_list() -> type | None:
    return resolve_view("company.api", "SupplierPartList")


def _bom_item_list() -> type | None:
    return resolve_view("part.api", "BomList")


def _bom_substitute_list() -> type | None:
    return resolve_view("part.api", "BomItemSubstituteList")


def _attachment_list() -> type | None:
    return resolve_view("common.api", "AttachmentList")


def _parameter_list() -> type | None:
    return resolve_view("common.api", "ParameterList")


def _parameter_template_list() -> type | None:
    return resolve_view("common.api", "ParameterTemplateList")


def _return_order_list() -> type | None:
    return resolve_view("order.api", "ReturnOrderList")


def _return_order_line_list() -> type | None:
    return resolve_view("order.api", "ReturnOrderLineItemList")


def _stock_tracking_list() -> type | None:
    return resolve_view("stock.api", "StockTrackingList")


def _stock_test_result_list() -> type | None:
    return resolve_view("stock.api", "StockItemTestResultList")


def _project_code_list() -> type | None:
    return resolve_view("common.api", "ProjectCodeList")


# Values are loader functions, not the view classes directly, so each import
# stays lazy (matches tools/*.py's own per-call imports) - importing e.g.
# part.api at module level risks AppRegistryNotReady if InvenTree's plugin
# registry scans this module before Django's app registry is ready. Each
# loader now goes through view_resolution.resolve_view() rather than a bare
# `from x.api import Y`, so a class that's been renamed or removed in the
# running InvenTree core (a version mismatch between this plugin and core)
# resolves to None instead of raising ImportError - callers below and in
# tool_visibility.py treat that the same as "not visible"/"not permitted".
# Not module-private (no leading underscore): tool_visibility.py also reads
# this to resolve a resource's view class for a permission check, rather
# than duplicating 27 loader functions.
RESOURCE_LOADERS = {
    "part": _part_list,
    "stock": _stock_list,
    "location": _stock_location_list,
    "category": _category_list,
    "purchase_order": _purchase_order_list,
    "purchase_order_line": _purchase_order_line_list,
    "sales_order": _sales_order_list,
    "sales_order_line": _sales_order_line_list,
    "sales_order_allocation": _sales_order_allocation_list,
    "build_order": _build_order_list,
    "build_line": _build_line_list,
    "build_item": _build_item_list,
    "company": _company_list,
    "contact": _contact_list,
    "address": _address_list,
    "manufacturer_part": _manufacturer_part_list,
    "supplier_part": _supplier_part_list,
    "bom_item": _bom_item_list,
    "bom_substitute": _bom_substitute_list,
    "attachment": _attachment_list,
    "parameter": _parameter_list,
    "parameter_template": _parameter_template_list,
    "return_order": _return_order_list,
    "return_order_line": _return_order_line_list,
    "stock_tracking": _stock_tracking_list,
    "stock_test_result": _stock_test_result_list,
    "project_code": _project_code_list,
}


@mcp.tool()
def describe_filters(resource: str) -> dict:
    """Describe the search/filter/ordering/expansion options available for a resource.

    Call this before list_*/get_* calls where you need something beyond
    their named arguments - filtering the result set, sorting it, or
    inlining a related object's full detail to avoid a second round trip.
    Does not touch any InvenTree data - this is static metadata about the
    corresponding list/get tools' capabilities, read directly from
    InvenTree's own filter and output-option definitions (so it can't drift
    out of date).

    Args:
        resource: one of "part", "stock", "location", "category",
            "purchase_order", "purchase_order_line", "sales_order",
            "sales_order_line", "sales_order_allocation", "build_order",
            "build_line", "build_item", "company", "contact", "address",
            "manufacturer_part", "supplier_part", "bom_item",
            "bom_substitute", "attachment", "parameter",
            "parameter_template", "return_order", "return_order_line",
            "stock_tracking", "stock_test_result", "project_code" -
            matches list_parts / list_stock_items / list_locations /
            list_categories / list_purchase_orders /
            list_purchase_order_lines / list_sales_orders /
            list_sales_order_lines / list_sales_order_allocations /
            list_build_orders / list_build_lines / list_build_items /
            list_companies / list_contacts / list_addresses /
            list_manufacturer_parts / list_supplier_parts /
            list_bom_items / list_bom_substitutes / list_attachments /
            list_parameters / list_parameter_templates /
            list_return_orders / list_return_order_lines /
            list_stock_tracking / list_stock_test_results /
            list_project_codes (and each list_* tool's get_* counterpart -
            optional_fields below applies to both).

    Returns a dict with:
        search_fields: fields matched by that list tool's `search` argument.
        ordering_fields: valid values for that list tool's `ordering`
            argument (prefix with '-' for descending, e.g. "-in_stock").
            Combine with `limit` for a "top N by X" result.
        filters: {name: {type, label, choices}} - pass any of these keys
            directly in that list tool's `filters` argument, e.g.
            filters={"is_variant": true}.
        optional_fields: {name: {default_included, description}} - fields
            that are *not* returned by default (unless default_included is
            true), but can be inlined into every result by passing the key
            as a boolean in `filters` - on both the list tool and its get_*
            counterpart, e.g. filters={"part_detail": true} adds a full
            nested `part` object to each stock item instead of just its ID,
            saving a separate get_part call per result. Empty for resources
            with no expandable relations.
    """
    loader = RESOURCE_LOADERS.get(resource)

    if loader is None:
        raise ToolError(
            f"Unknown resource {resource!r}. Choose one of: "
            f"{', '.join(RESOURCE_LOADERS)}"
        )

    view_cls = loader()

    if view_cls is None:
        raise ToolError(
            f"{resource!r} is unavailable on this InvenTree instance (its "
            "API endpoint could not be found - likely a version mismatch "
            "between this plugin and the running InvenTree core)."
        )

    return {
        **describe_filterset(view_cls),
        "optional_fields": describe_output_options(view_cls),
    }
