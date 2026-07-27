"""Attach dynamically-derived output schemas to registered MCP tools.

FastMCP only derives a tool's outputSchema from its Python return type
annotation (see schema_introspection.py's module docstring for why that
doesn't work for us). There's no public FastMCP API to supply a schema
directly - @mcp.tool() / mcp.add_tool() only accept a `structured_output`
bool, not a schema override - so this reaches into the tool registry after
normal registration and sets Tool.fn_metadata.output_schema directly.

Tool.output_schema is a @cached_property reading fn_metadata.output_schema,
so this only works if it runs before anything has read a given tool's
output_schema for the first time - true here, since apply() runs at import
time (see mcp_server.py), before any real request can occur. This is pinned
to current FastMCP internals (_tool_manager, fn_metadata); re-verify by hand
if the `mcp` package version changes.

fn_metadata.output_model must also be set alongside output_schema: FastMCP
validates every *actual* tool result against output_model at call time
(func_metadata.py's convert_result(), independent of what output_schema
merely declares), and asserts if a schema is set with no model - a real
tool call raises "Output model must be set if output schema is defined"
otherwise. We deliberately use a fully permissive RootModel[dict[str, Any]]
here rather than a model matching our inferred schema field-for-field:
schema_introspection's mapping is a best-effort translation, not guaranteed
exact, and validating real InvenTree API responses against our own
possibly-imperfect guess could reject genuinely correct data. The declared
outputSchema (informative, shown to clients) and the runtime validation
model (permissive, never rejects real data) are deliberately decoupled.
"""

from __future__ import annotations

from typing import Any

from build.serializers import BuildItemSerializer, BuildLineSerializer, BuildSerializer
from common.serializers import (
    AttachmentSerializer,
    ParameterSerializer,
    ParameterTemplateSerializer,
)
from company.serializers import (
    AddressSerializer,
    CompanySerializer,
    ContactSerializer,
    ManufacturerPartSerializer,
    SupplierPartSerializer,
)
from order.serializers import (
    PurchaseOrderLineItemSerializer,
    PurchaseOrderSerializer,
    SalesOrderAllocationSerializer,
    SalesOrderLineItemSerializer,
    SalesOrderSerializer,
)
from part.serializers import (
    BomItemSerializer,
    BomItemSubstituteSerializer,
    CategorySerializer,
    PartSerializer,
)
from pydantic import RootModel
from stock.serializers import LocationSerializer, StockItemSerializer

from .mcp_server import mcp
from .schema_introspection import paginated_schema, serializer_schema

_PERMISSIVE_OUTPUT_MODEL = RootModel[dict[str, Any]]

_OUTPUT_SCHEMAS = {
    "list_parts": paginated_schema(PartSerializer),
    "get_part": serializer_schema(PartSerializer),
    "list_categories": paginated_schema(CategorySerializer),
    "get_category": serializer_schema(CategorySerializer),
    "list_stock_items": paginated_schema(StockItemSerializer),
    "get_stock_item": serializer_schema(StockItemSerializer),
    "list_locations": paginated_schema(LocationSerializer),
    "get_location": serializer_schema(LocationSerializer),
    "list_purchase_orders": paginated_schema(PurchaseOrderSerializer),
    "get_purchase_order": serializer_schema(PurchaseOrderSerializer),
    "list_purchase_order_lines": paginated_schema(PurchaseOrderLineItemSerializer),
    "get_purchase_order_line": serializer_schema(PurchaseOrderLineItemSerializer),
    "list_sales_orders": paginated_schema(SalesOrderSerializer),
    "get_sales_order": serializer_schema(SalesOrderSerializer),
    "list_sales_order_lines": paginated_schema(SalesOrderLineItemSerializer),
    "get_sales_order_line": serializer_schema(SalesOrderLineItemSerializer),
    "list_sales_order_allocations": paginated_schema(SalesOrderAllocationSerializer),
    "get_sales_order_allocation": serializer_schema(SalesOrderAllocationSerializer),
    "list_build_orders": paginated_schema(BuildSerializer),
    "get_build_order": serializer_schema(BuildSerializer),
    "list_build_lines": paginated_schema(BuildLineSerializer),
    "get_build_line": serializer_schema(BuildLineSerializer),
    "list_build_items": paginated_schema(BuildItemSerializer),
    "get_build_item": serializer_schema(BuildItemSerializer),
    "list_companies": paginated_schema(CompanySerializer),
    "get_company": serializer_schema(CompanySerializer),
    "list_contacts": paginated_schema(ContactSerializer),
    "get_contact": serializer_schema(ContactSerializer),
    "list_addresses": paginated_schema(AddressSerializer),
    "get_address": serializer_schema(AddressSerializer),
    "list_manufacturer_parts": paginated_schema(ManufacturerPartSerializer),
    "get_manufacturer_part": serializer_schema(ManufacturerPartSerializer),
    "list_supplier_parts": paginated_schema(SupplierPartSerializer),
    "get_supplier_part": serializer_schema(SupplierPartSerializer),
    "list_bom_items": paginated_schema(BomItemSerializer),
    "get_bom_item": serializer_schema(BomItemSerializer),
    "list_bom_substitutes": paginated_schema(BomItemSubstituteSerializer),
    "get_bom_substitute": serializer_schema(BomItemSubstituteSerializer),
    "list_attachments": paginated_schema(AttachmentSerializer),
    "get_attachment": serializer_schema(AttachmentSerializer),
    "list_parameters": paginated_schema(ParameterSerializer),
    "get_parameter": serializer_schema(ParameterSerializer),
    "list_parameter_templates": paginated_schema(ParameterTemplateSerializer),
    "get_parameter_template": serializer_schema(ParameterTemplateSerializer),
}


def apply() -> None:
    """Attach each tool's computed output schema to its registered Tool object."""
    for name, schema in _OUTPUT_SCHEMAS.items():
        tool = mcp._tool_manager.get_tool(name)
        if tool is not None:
            tool.fn_metadata.output_schema = schema
            tool.fn_metadata.output_model = _PERMISSIVE_OUTPUT_MODEL
