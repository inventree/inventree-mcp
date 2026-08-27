"""Tests for the InvenTreeMCP plugin.

These focus on the one thing the community reference MCP plugin gets wrong:
permission enforcement. Every tool proxies through the real DRF view classes
(see proxy.py), so a user without the relevant role must be rejected the same
way the normal REST API would reject them - holding a valid, authenticated
credential must not be enough on its own.
"""

from __future__ import annotations

import datetime
import json
import unittest
from typing import Any, ClassVar
from unittest.mock import patch

import jsonschema
from asgiref.sync import sync_to_async
from build.models import Build, BuildItem
from build.serializers import BuildSerializer
from common.models import Attachment, Parameter, ParameterTemplate, ProjectCode
from common.serializers import ProjectCodeSerializer
from company.models import Address, Company, Contact, ManufacturerPart, SupplierPart
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.test import Client, override_settings
from django.utils import timezone
from InvenTree.unit_test import InvenTreeTestCase
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult
from oauth2_provider.models import AccessToken, Application
from order.models import (
    PurchaseOrder,
    PurchaseOrderLineItem,
    ReturnOrder,
    ReturnOrderLineItem,
    SalesOrder,
    SalesOrderAllocation,
    SalesOrderLineItem,
    SalesOrderShipment,
)
from part.api import PartList
from part.models import BomItem, BomItemSubstitute, Part, PartCategory, PartTestTemplate
from part.serializers import PartBriefSerializer, PartSerializer
from plugin import registry
from stock.models import (
    StockItem,
    StockItemTestResult,
    StockItemTracking,
    StockLocation,
)
from users.models import ApiToken

from . import context, tool_visibility, view_resolution
from .filter_introspection import _default_ordering_fields
from .mcp_server import mcp
from .proxy import call_view
from .schema_introspection import paginated_schema, serializer_schema
from .settings import get_plugin_setting
from .tools._common import DEFAULT_LIMIT, MAX_LIMIT, build_query_params, clamp_limit
from .tools.attachments import get_attachment, list_attachments
from .tools.bom import (
    get_bom_item,
    get_bom_substitute,
    list_bom_items,
    list_bom_substitutes,
)
from .tools.build_orders import (
    get_build_item,
    get_build_line,
    get_build_order,
    list_build_items,
    list_build_lines,
    list_build_orders,
)
from .tools.categories import get_category, list_categories
from .tools.companies import (
    get_address,
    get_company,
    get_contact,
    list_addresses,
    list_companies,
    list_contacts,
)
from .tools.discovery import RESOURCE_LOADERS, describe_filters
from .tools.locations import get_location, list_locations
from .tools.parameters import (
    get_parameter,
    get_parameter_template,
    list_parameter_templates,
    list_parameters,
)
from .tools.parts import get_part, list_parts
from .tools.project_codes import get_project_code, list_project_codes
from .tools.purchase_orders import (
    get_purchase_order,
    get_purchase_order_line,
    list_purchase_order_lines,
    list_purchase_orders,
)
from .tools.return_orders import (
    get_return_order,
    get_return_order_line,
    list_return_order_lines,
    list_return_orders,
)
from .tools.sales_orders import (
    get_sales_order,
    get_sales_order_allocation,
    get_sales_order_line,
    list_sales_order_allocations,
    list_sales_order_lines,
    list_sales_orders,
)
from .tools.stock import get_stock_item, list_stock_items
from .tools.stock_history import (
    get_stock_test_result,
    get_stock_tracking,
    list_stock_test_results,
    list_stock_tracking,
)
from .tools.supplier_parts import (
    get_manufacturer_part,
    get_supplier_part,
    list_manufacturer_parts,
    list_supplier_parts,
)


@override_settings(PLUGIN_TESTING_SETUP=True)
class MCPToolPermissionTest(InvenTreeTestCase):
    """Verify MCP tools respect InvenTree's role-based permissions."""

    roles: ClassVar[list[str]] = [
        "part.view",
        "part_category.view",
        "stock.view",
        "stock_location.view",
        "purchase_order.view",
        "sales_order.view",
        "return_order.view",
        "build.view",
    ]

    @classmethod
    def setUpTestData(cls):
        """Create shared fixture data and a second, permission-less user."""
        super().setUpTestData()

        cls.category = PartCategory.objects.create(
            name="Widgets", description="Test category"
        )
        cls.part = Part.objects.create(
            name="Test Part",
            description="A part for MCP tests",
            category=cls.category,
            salable=True,
            purchaseable=True,
            testable=True,
        )
        cls.location = StockLocation.objects.create(
            name="Test Location", description="A location for MCP tests"
        )
        cls.stock_item = StockItem.objects.create(
            part=cls.part, quantity=10, location=cls.location
        )

        # --- Purchase order fixtures ---
        cls.supplier = Company.objects.create(
            name="Test Supplier", is_supplier=True, is_customer=False
        )
        cls.supplier_part = SupplierPart.objects.create(
            part=cls.part, supplier=cls.supplier, SKU="MCP-TEST-SKU"
        )
        cls.purchase_order = PurchaseOrder.objects.create(
            supplier=cls.supplier, reference="PO-MCP-0001"
        )
        cls.po_line = PurchaseOrderLineItem.objects.create(
            part=cls.supplier_part, order=cls.purchase_order, quantity=50
        )

        # --- Company/contact/address/manufacturer part fixtures ---
        cls.contact = Contact.objects.create(
            company=cls.supplier, name="Test Contact", email="contact@example.org"
        )
        cls.address = Address.objects.create(
            company=cls.supplier, title="Test Address", line1="1 Test Street"
        )
        cls.manufacturer = Company.objects.create(
            name="Test Manufacturer", is_manufacturer=True
        )
        cls.manufacturer_part = ManufacturerPart.objects.create(
            part=cls.part, manufacturer=cls.manufacturer, MPN="MCP-TEST-MPN"
        )

        # --- Sales order fixtures ---
        cls.customer = Company.objects.create(
            name="Test Customer", is_customer=True, is_supplier=False
        )
        cls.sales_order = SalesOrder.objects.create(
            customer=cls.customer, reference="SO-MCP-0001"
        )
        cls.so_line = SalesOrderLineItem.objects.create(
            order=cls.sales_order, part=cls.part, quantity=5
        )
        cls.shipment = cls.sales_order.shipments.first()
        if cls.shipment is None:
            cls.shipment = SalesOrderShipment.objects.create(
                order=cls.sales_order, reference="1"
            )
        cls.allocation_stock_item = StockItem.objects.create(
            part=cls.part, quantity=100, location=cls.location
        )
        cls.so_allocation = SalesOrderAllocation.objects.create(
            quantity=5,
            line=cls.so_line,
            item=cls.allocation_stock_item,
            shipment=cls.shipment,
        )

        # --- Build order fixtures ---
        cls.assembly_part = Part.objects.create(
            name="Test Assembly",
            description="Assembly for MCP tests",
            assembly=True,
            is_template=True,
        )
        # MPTT's tree_id assignment for new root nodes can collide across
        # separate TestCase classes in the same test run (a pre-existing
        # django-mptt/test-isolation quirk, not something this plugin
        # controls) - rebuild() forces consistent, non-colliding tree state
        # before the BOM relationship check below relies on tree_id.
        Part.objects.rebuild()
        cls.part.refresh_from_db()
        cls.assembly_part.refresh_from_db()
        cls.bom_item = BomItem.objects.create(
            part=cls.assembly_part, sub_part=cls.part, quantity=1
        )
        cls.build = Build.objects.create(
            part=cls.assembly_part, reference="BO-0001", quantity=10
        )
        # BuildLine objects are auto-created (one per BOM item) via a
        # post_save signal on Build - see Build.create_build_line_items().
        cls.build_line = cls.build.build_lines.first()
        cls.build_item = BuildItem.objects.create(
            build_line=cls.build_line, stock_item=cls.allocation_stock_item, quantity=1
        )

        # --- BOM fixtures ---
        # Mark cls.bom_item inherited so it's picked up by variant parts too
        # - this is the case list_bom_items(part=...) has to resolve
        # specially, see tools/bom.py's module docstring.
        cls.bom_item.inherited = True
        cls.bom_item.save()
        cls.assembly_variant = Part.objects.create(
            name="Test Assembly Variant",
            description="Variant of the assembly, for inherited-BOM tests",
            variant_of=cls.assembly_part,
            assembly=True,
        )
        cls.substitute_part = Part.objects.create(
            name="Test Substitute Part",
            description="A substitute component for MCP tests",
            component=True,
        )
        cls.bom_substitute = BomItemSubstitute.objects.create(
            bom_item=cls.bom_item, part=cls.substitute_part
        )

        # --- Attachment/Parameter fixtures ---
        # Both model_type fields are real ContentType FKs at the ORM level,
        # despite each serializing over the wire as a plain string in a
        # *different* format per resource - see tools/attachments.py's and
        # tools/parameters.py's module docstrings for why they're not
        # interchangeable.
        cls.attachment = Attachment.objects.create(
            model_type="part",
            model_id=cls.part.pk,
            link="https://example.org/datasheet.pdf",
            comment="Test datasheet",
        )
        cls.parameter_template = ParameterTemplate.objects.create(
            name="Resistance",
            units="ohm",
            model_type=ContentType.objects.get_for_model(Part),
        )
        cls.parameter = Parameter.objects.create(
            model_type=ContentType.objects.get_for_model(Part),
            model_id=cls.part.pk,
            template=cls.parameter_template,
            data="100",
        )

        # --- Return order fixtures ---
        cls.return_order = ReturnOrder.objects.create(
            customer=cls.customer, reference="RMA-MCP-0001"
        )
        cls.ro_line = ReturnOrderLineItem.objects.create(
            order=cls.return_order, item=cls.stock_item
        )

        # --- Stock tracking / test result fixtures ---
        cls.stock_tracking = StockItemTracking.objects.create(
            item=cls.stock_item, notes="Test tracking entry"
        )
        cls.test_template = PartTestTemplate.objects.create(
            part=cls.part, test_name="Continuity Test"
        )
        cls.stock_test_result = StockItemTestResult.objects.create(
            stock_item=cls.stock_item, template=cls.test_template, result=True
        )

        # --- Project code fixtures ---
        cls.project_code = ProjectCode.objects.create(
            code="MCP-PROJ", description="Test project code"
        )

        # A second user, deliberately given no roles at all.
        cls.no_access_user = get_user_model().objects.create_user(
            username="noaccess", password="password", email="noaccess@example.org"
        )

        # Real OAuth2 tokens for cls.user, at two different scopes - used to
        # prove a token can narrow access *below* what the user's role would
        # otherwise allow (see the oauth2_bridge.py tests below).
        cls.oauth2_app, _ = Application.objects.get_or_create(
            name="mcp-test-app",
            defaults={
                "client_type": "confidential",
                "authorization_grant_type": "client-credentials",
                "user": cls.user,
            },
        )
        expires = timezone.now() + datetime.timedelta(hours=1)
        # PartList requires *both* scopes together - the 'part' table is
        # associated with both the 'part' and 'build' rulesets (BOM/build
        # features touch parts too), so InvenTree's own dynamic scope
        # resolution combines them into one required set, not alternatives.
        cls.oauth2_token_with_part_scope = AccessToken.objects.create(
            user=cls.user,
            application=cls.oauth2_app,
            token="mcp-test-token-part-view",
            expires=expires,
            scope="r:view:part r:view:build",
        )
        cls.oauth2_token_without_part_scope = AccessToken.objects.create(
            user=cls.user,
            application=cls.oauth2_app,
            token="mcp-test-token-general-read",
            expires=expires,
            scope="g:read",
        )

        # PLUGIN_TESTING_SETUP (see class decorator) is what lets a
        # pip-installed, entry-point-distributed plugin like this one be
        # discovered inside a test run at all - without it the registry has
        # no record of 'inventree-mcp' and registry.get_plugin() stays None.
        # registry.get_plugin() also filters on active state by default, so
        # activate it too, or set_setting() in the tests below still gets None.
        registry.reload_plugins(full_reload=True, collect=True)
        registry.set_plugin_state("inventree-mcp", True)

    def _as(self, user):
        """Bind *user* as the acting MCP user for the duration of the current test.

        Uses a plain set() rather than context.reset_current_user(): Django's
        async test wrapper runs addCleanup callbacks outside the test
        coroutine's own contextvars.Context, and a Token can only be reset in
        the exact Context that produced it.
        """
        context.set_current_user(user)
        self.addCleanup(context.set_current_user, None)

    async def test_authorized_user_can_list_parts(self):
        self._as(self.user)
        result = await list_parts(search="Test Part")
        names = [p["name"] for p in result["results"]]
        self.assertIn("Test Part", names)

    async def test_authorized_user_can_get_part(self):
        self._as(self.user)
        result = await get_part(self.part.pk)
        self.assertEqual(result["name"], "Test Part")

    async def test_unauthorized_user_cannot_list_or_get_parts(self):
        """A user without the 'part' view role must not see part data via MCP."""
        self._as(self.no_access_user)

        with self.assertRaises(ToolError):
            await list_parts()

        with self.assertRaises(ToolError):
            await get_part(self.part.pk)

    async def test_unauthorized_user_cannot_list_categories_or_stock(self):
        self._as(self.no_access_user)

        with self.assertRaises(ToolError):
            await list_categories()

        with self.assertRaises(ToolError):
            await list_stock_items()

    async def test_authorized_user_can_get_category(self):
        self._as(self.user)
        result = await get_category(self.category.pk)
        self.assertEqual(result["name"], "Widgets")

    async def test_authorized_user_can_list_and_get_stock_items(self):
        self._as(self.user)

        listed = await list_stock_items(part=self.part.pk)
        ids = [s["pk"] for s in listed["results"]]
        self.assertIn(self.stock_item.pk, ids)

        detail = await get_stock_item(self.stock_item.pk)
        self.assertEqual(detail["part"], self.part.pk)

    async def test_optional_fields_can_be_toggled_via_filters(self):
        """`filters` doubles as the optional-field toggle described by
        describe_filters()'s `optional_fields` - regression test that the
        real API actually honors it end-to-end, on both a get_* tool (added
        specifically to carry this) and a list_* tool (which already passed
        `filters` straight through as query params).
        """
        self._as(self.user)

        # part_detail default_include=True on StockItemSerializer - present
        # unless explicitly turned off.
        default_detail = await get_stock_item(self.stock_item.pk)
        self.assertIn("part_detail", default_detail)

        without_detail = await get_stock_item(
            self.stock_item.pk, filters={"part_detail": False}
        )
        self.assertNotIn("part_detail", without_detail)

        # location_detail default_include=False - absent unless requested.
        listed_default = await list_stock_items(part=self.part.pk)
        self.assertNotIn("location_detail", listed_default["results"][0])

        listed_with_location = await list_stock_items(
            part=self.part.pk, filters={"location_detail": True}
        )
        self.assertIn("location_detail", listed_with_location["results"][0])

    async def test_ordering_argument_sorts_results(self):
        """The named `ordering` argument must actually sort the real queryset, not just be accepted.

        cls.stock_item (quantity=10) and cls.allocation_stock_item
        (quantity=100) share the same part - a real end-to-end check that
        `ordering="-quantity"`/`"quantity"` changes which one comes first,
        not just that the query param reaches the view unrejected.
        """
        self._as(self.user)

        descending = await list_stock_items(part=self.part.pk, ordering="-quantity")
        self.assertEqual(descending["results"][0]["pk"], self.allocation_stock_item.pk)

        ascending = await list_stock_items(part=self.part.pk, ordering="quantity")
        self.assertEqual(ascending["results"][0]["pk"], self.stock_item.pk)

    async def test_ordering_combined_with_limit_gives_top_n(self):
        """The documented "top N by X" pattern: ordering + limit together."""
        self._as(self.user)

        top_one = await list_stock_items(
            part=self.part.pk, ordering="-quantity", limit=1
        )
        self.assertEqual(len(top_one["results"]), 1)
        self.assertEqual(top_one["results"][0]["pk"], self.allocation_stock_item.pk)

    async def test_ordering_argument_reaches_every_list_tool(self):
        """The deep sort-order tests above only exercise list_stock_items - every other
        list tool's own `if ordering is not None: base["ordering"] = ordering` line
        still needs at least one real call with ordering set, or it's dead code as
        far as the test suite can tell. Spot-checks all 24 list tools at once.

        Uses each resource's own real ordering_fields (via describe_filters) rather
        than a hardcoded field name per tool, so this can't silently drift out of
        sync if a wrapped view's declared ordering_fields ever change.
        """
        self._as(self.user)

        list_tools_by_resource = {
            "part": list_parts,
            "category": list_categories,
            "stock": list_stock_items,
            "location": list_locations,
            "purchase_order": list_purchase_orders,
            "purchase_order_line": list_purchase_order_lines,
            "sales_order": list_sales_orders,
            "sales_order_line": list_sales_order_lines,
            "sales_order_allocation": list_sales_order_allocations,
            "build_order": list_build_orders,
            "build_line": list_build_lines,
            "build_item": list_build_items,
            "company": list_companies,
            "contact": list_contacts,
            "address": list_addresses,
            "manufacturer_part": list_manufacturer_parts,
            "supplier_part": list_supplier_parts,
            "bom_item": list_bom_items,
            "bom_substitute": list_bom_substitutes,
            "attachment": list_attachments,
            "parameter": list_parameters,
            "parameter_template": list_parameter_templates,
            "return_order": list_return_orders,
            "return_order_line": list_return_order_lines,
            "stock_tracking": list_stock_tracking,
            "stock_test_result": list_stock_test_results,
            "project_code": list_project_codes,
        }

        for resource, tool_fn in list_tools_by_resource.items():
            ordering_fields = describe_filters(resource)["ordering_fields"]
            self.assertTrue(
                ordering_fields, f"{resource} reports no ordering_fields to test with"
            )
            result = await tool_fn(ordering=ordering_fields[0], limit=1)
            self.assertIn(
                "results", result, f"{resource}'s list tool rejected `ordering`"
            )

    async def test_authorized_user_can_list_and_get_locations(self):
        self._as(self.user)

        listed = await list_locations(search="Test Location")
        names = [location["name"] for location in listed["results"]]
        self.assertIn("Test Location", names)

        detail = await get_location(self.location.pk)
        self.assertEqual(detail["name"], "Test Location")

    async def test_unauthorized_user_cannot_get_category_stock_or_location(self):
        """Denial coverage for the detail/get tools, not just the list ones above."""
        self._as(self.no_access_user)

        with self.assertRaises(ToolError):
            await get_category(self.category.pk)

        with self.assertRaises(ToolError):
            await get_stock_item(self.stock_item.pk)

        with self.assertRaises(ToolError):
            await list_locations()

        with self.assertRaises(ToolError):
            await get_location(self.location.pk)

    async def test_no_bound_user_fails_closed(self):
        """Calling a tool with no bound user (e.g. outside of a real MCP request) must fail."""
        with self.assertRaises(PermissionError):
            await list_parts()

    async def test_limit_is_clamped(self):
        """A caller-supplied limit above the cap must not be passed straight through."""
        self._as(self.user)
        result = await list_parts(limit=10_000)
        self.assertLessEqual(len(result["results"]), 100)

    async def test_filters_dict_reaches_the_real_view(self):
        """The generic `filters` argument must actually apply against the real API, not be ignored."""
        self._as(self.user)

        active = await list_parts(search="Test Part", filters={"active": True})
        self.assertIn("Test Part", [p["name"] for p in active["results"]])

        inactive_only = await list_parts(search="Test Part", filters={"active": False})
        self.assertNotIn("Test Part", [p["name"] for p in inactive_only["results"]])

    async def test_filters_dict_reaches_build_and_order_views(self):
        """Spot-check the same filters-passthrough behaviour for the new order/build tools."""
        self._as(self.user)

        outstanding = await list_purchase_orders(
            supplier=self.supplier.pk, filters={"outstanding": True}
        )
        refs = [o["reference"] for o in outstanding["results"]]
        self.assertIn("PO-MCP-0001", refs)

        not_outstanding = await list_purchase_orders(
            supplier=self.supplier.pk, filters={"outstanding": False}
        )
        self.assertNotIn(
            "PO-MCP-0001", [o["reference"] for o in not_outstanding["results"]]
        )

        allocated = await list_sales_order_lines(
            order=self.sales_order.pk, filters={"allocated": True}
        )
        self.assertIn(self.so_line.pk, [line["pk"] for line in allocated["results"]])

    async def test_filters_cannot_bypass_limit_clamp(self):
        """filters={"limit": ...} must not override clamp_limit()'s pagination cap."""
        self._as(self.user)

        result = await list_parts(filters={"limit": 10_000})
        self.assertLessEqual(len(result["results"]), 100)

    async def test_read_only_setting_blocks_writes_by_default(self):
        """Even a fully-permissioned user cannot write while MCP_READ_ONLY is on (the default)."""
        self._as(self.user)

        with self.assertRaises(ToolError) as cm:
            await call_view(PartList, "POST", "/api/part/", data={})

        self.assertIn("read-only", str(cm.exception).lower())

    async def test_read_only_setting_can_be_disabled(self):
        """Disabling MCP_READ_ONLY lets a write reach the real view (and its own validation)."""
        self._as(self.user)

        def _disable_read_only():
            plugin = registry.get_plugin("inventree-mcp")
            plugin.set_setting("MCP_READ_ONLY", False)
            return plugin

        # registry/setting lookups are sync Django ORM work - must be bridged
        # the same way proxy.call_view() bridges tool calls (see AGENTS.md).
        plugin = await sync_to_async(_disable_read_only)()
        self.addCleanup(plugin.set_setting, "MCP_READ_ONLY", True)

        with self.assertRaises(ToolError) as cm:
            await call_view(PartList, "POST", "/api/part/", data={})

        self.assertNotIn("read-only", str(cm.exception).lower())

    async def test_oauth2_token_scope_can_narrow_below_user_role(self):
        """A token missing the relevant scope is rejected even though the user's role allows it.

        self.user has the 'part.view' role (see roles above), so this would
        succeed under plain role-based access - the point is that an OAuth2
        token can restrict a user's access below their normal role, not just
        confirm it.
        """
        context.set_current_user(
            self.user, oauth2_token=self.oauth2_token_without_part_scope
        )
        self.addCleanup(context.set_current_user, None)

        with self.assertRaises(ToolError):
            await list_parts()

    async def test_oauth2_token_with_matching_scope_succeeds(self):
        """A token that does carry the relevant scope is allowed, same as plain role access."""
        context.set_current_user(
            self.user, oauth2_token=self.oauth2_token_with_part_scope
        )
        self.addCleanup(context.set_current_user, None)

        result = await list_parts(search="Test Part")
        names = [p["name"] for p in result["results"]]
        self.assertIn("Test Part", names)

    async def test_authorized_user_can_list_and_get_purchase_orders(self):
        self._as(self.user)

        listed = await list_purchase_orders(supplier=self.supplier.pk)
        refs = [o["reference"] for o in listed["results"]]
        self.assertIn("PO-MCP-0001", refs)

        detail = await get_purchase_order(self.purchase_order.pk)
        self.assertEqual(detail["reference"], "PO-MCP-0001")

    async def test_authorized_user_can_list_and_get_purchase_order_lines(self):
        self._as(self.user)

        listed = await list_purchase_order_lines(order=self.purchase_order.pk)
        ids = [line["pk"] for line in listed["results"]]
        self.assertIn(self.po_line.pk, ids)

        detail = await get_purchase_order_line(self.po_line.pk)
        self.assertEqual(detail["order"], self.purchase_order.pk)

    async def test_authorized_user_can_list_and_get_sales_orders(self):
        self._as(self.user)

        listed = await list_sales_orders(customer=self.customer.pk)
        refs = [o["reference"] for o in listed["results"]]
        self.assertIn("SO-MCP-0001", refs)

        detail = await get_sales_order(self.sales_order.pk)
        self.assertEqual(detail["reference"], "SO-MCP-0001")

    async def test_authorized_user_can_list_and_get_sales_order_lines(self):
        self._as(self.user)

        listed = await list_sales_order_lines(order=self.sales_order.pk)
        ids = [line["pk"] for line in listed["results"]]
        self.assertIn(self.so_line.pk, ids)

        detail = await get_sales_order_line(self.so_line.pk)
        self.assertEqual(detail["order"], self.sales_order.pk)

    async def test_authorized_user_can_list_and_get_sales_order_allocations(self):
        self._as(self.user)

        listed = await list_sales_order_allocations(order=self.sales_order.pk)
        ids = [alloc["pk"] for alloc in listed["results"]]
        self.assertIn(self.so_allocation.pk, ids)

        detail = await get_sales_order_allocation(self.so_allocation.pk)
        self.assertEqual(detail["line"], self.so_line.pk)

    async def test_authorized_user_can_list_and_get_build_orders(self):
        self._as(self.user)

        listed = await list_build_orders(part=self.assembly_part.pk)
        refs = [b["reference"] for b in listed["results"]]
        self.assertIn("BO-0001", refs)

        detail = await get_build_order(self.build.pk)
        self.assertEqual(detail["reference"], "BO-0001")

    async def test_authorized_user_can_list_and_get_build_lines(self):
        self._as(self.user)

        listed = await list_build_lines(build=self.build.pk)
        ids = [line["pk"] for line in listed["results"]]
        self.assertIn(self.build_line.pk, ids)

        detail = await get_build_line(self.build_line.pk)
        self.assertEqual(detail["build"], self.build.pk)

    async def test_authorized_user_can_list_and_get_build_items(self):
        self._as(self.user)

        listed = await list_build_items(build=self.build.pk)
        ids = [item["pk"] for item in listed["results"]]
        self.assertIn(self.build_item.pk, ids)

        detail = await get_build_item(self.build_item.pk)
        self.assertEqual(detail["stock_item"], self.allocation_stock_item.pk)

    async def test_unauthorized_user_cannot_access_purchase_sales_or_build_data(self):
        """Denial coverage for all three new order/build domains at once."""
        self._as(self.no_access_user)

        with self.assertRaises(ToolError):
            await list_purchase_orders()
        with self.assertRaises(ToolError):
            await get_purchase_order(self.purchase_order.pk)
        with self.assertRaises(ToolError):
            await list_purchase_order_lines()
        with self.assertRaises(ToolError):
            await get_purchase_order_line(self.po_line.pk)

        with self.assertRaises(ToolError):
            await list_sales_orders()
        with self.assertRaises(ToolError):
            await get_sales_order(self.sales_order.pk)
        with self.assertRaises(ToolError):
            await list_sales_order_lines()
        with self.assertRaises(ToolError):
            await get_sales_order_line(self.so_line.pk)
        with self.assertRaises(ToolError):
            await list_sales_order_allocations()
        with self.assertRaises(ToolError):
            await get_sales_order_allocation(self.so_allocation.pk)

        with self.assertRaises(ToolError):
            await list_build_orders()
        with self.assertRaises(ToolError):
            await get_build_order(self.build.pk)
        with self.assertRaises(ToolError):
            await list_build_lines()
        with self.assertRaises(ToolError):
            await get_build_line(self.build_line.pk)
        with self.assertRaises(ToolError):
            await list_build_items()
        with self.assertRaises(ToolError):
            await get_build_item(self.build_item.pk)

    async def test_authorized_user_can_list_and_get_bom_items(self):
        self._as(self.user)

        listed = await list_bom_items(part=self.assembly_part.pk)
        ids = [item["pk"] for item in listed["results"]]
        self.assertIn(self.bom_item.pk, ids)

        detail = await get_bom_item(self.bom_item.pk)
        self.assertEqual(detail["sub_part"], self.part.pk)

    async def test_list_bom_items_resolves_inherited_rows_for_variants(self):
        """A BomItem marked inherited=True must show up when querying a *variant*
        of the part it's defined on, not just that part itself - and the
        returned row's own `part` field stays the template's ID, not the
        variant's (see tools/bom.py's module docstring).
        """
        self._as(self.user)

        listed = await list_bom_items(part=self.assembly_variant.pk)
        rows = {item["pk"]: item for item in listed["results"]}

        self.assertIn(self.bom_item.pk, rows)
        inherited_row = rows[self.bom_item.pk]
        self.assertTrue(inherited_row["inherited"])
        self.assertEqual(inherited_row["part"], self.assembly_part.pk)
        self.assertNotEqual(inherited_row["part"], self.assembly_variant.pk)

    async def test_list_bom_items_uses_filter_finds_consuming_assemblies(self):
        self._as(self.user)

        listed = await list_bom_items(uses=self.part.pk)
        ids = [item["pk"] for item in listed["results"]]
        self.assertIn(self.bom_item.pk, ids)

    async def test_authorized_user_can_list_and_get_bom_substitutes(self):
        self._as(self.user)

        listed = await list_bom_substitutes(bom_item=self.bom_item.pk)
        ids = [sub["pk"] for sub in listed["results"]]
        self.assertIn(self.bom_substitute.pk, ids)

        detail = await get_bom_substitute(self.bom_substitute.pk)
        self.assertEqual(detail["part"], self.substitute_part.pk)

    async def test_unauthorized_user_cannot_access_bom_data(self):
        self._as(self.no_access_user)

        with self.assertRaises(ToolError):
            await list_bom_items()
        with self.assertRaises(ToolError):
            await get_bom_item(self.bom_item.pk)
        with self.assertRaises(ToolError):
            await list_bom_substitutes()
        with self.assertRaises(ToolError):
            await get_bom_substitute(self.bom_substitute.pk)

    async def test_authorized_user_can_list_and_get_attachments(self):
        self._as(self.user)

        listed = await list_attachments(model_type="part", model_id=self.part.pk)
        ids = [a["pk"] for a in listed["results"]]
        self.assertIn(self.attachment.pk, ids)

        detail = await get_attachment(self.attachment.pk)
        self.assertEqual(detail["comment"], "Test datasheet")

        # cls.attachment is a link, not an uploaded file, so is_image=False
        # must include it and is_image=True must exclude it.
        not_images = await list_attachments(is_image=False)
        self.assertIn(self.attachment.pk, [a["pk"] for a in not_images["results"]])
        images_only = await list_attachments(is_image=True)
        self.assertNotIn(self.attachment.pk, [a["pk"] for a in images_only["results"]])

    async def test_unauthorized_user_can_still_read_attachments(self):
        """Deliberately the opposite assertion from every other resource's denial test.

        AttachmentList/Detail have no RolePermission/RuleSet gate on reads -
        only IsAuthenticatedOrReadScope (any authenticated user) - see
        tools/attachments.py's module docstring. A zero-role user must still
        succeed here; asserting ToolError (the pattern used everywhere else
        in this file) would be testing for the wrong thing and would mask a
        real regression if this view's permissions ever tightened.
        """
        self._as(self.no_access_user)

        listed = await list_attachments(model_type="part", model_id=self.part.pk)
        ids = [a["pk"] for a in listed["results"]]
        self.assertIn(self.attachment.pk, ids)

        detail = await get_attachment(self.attachment.pk)
        self.assertEqual(detail["pk"], self.attachment.pk)

    async def test_authorized_user_can_list_and_get_parameters(self):
        self._as(self.user)

        listed = await list_parameters(model_type="part.part", model_id=self.part.pk)
        ids = [p["pk"] for p in listed["results"]]
        self.assertIn(self.parameter.pk, ids)

        detail = await get_parameter(self.parameter.pk)
        self.assertEqual(detail["data"], "100")

        by_template = await list_parameters(template=self.parameter_template.pk)
        self.assertIn(self.parameter.pk, [p["pk"] for p in by_template["results"]])

    async def test_authorized_user_can_list_and_get_parameter_templates(self):
        self._as(self.user)

        listed = await list_parameter_templates(search="Resistance")
        names = [t["name"] for t in listed["results"]]
        self.assertIn("Resistance", names)

        detail = await get_parameter_template(self.parameter_template.pk)
        self.assertEqual(detail["units"], "ohm")

    async def test_unauthorized_user_can_still_read_parameters(self):
        """Same "opposite of every other resource" case as attachments, above -
        ParameterList/Detail and ParameterTemplateList/Detail have no
        RolePermission/RuleSet gate on reads either.
        """
        self._as(self.no_access_user)

        listed = await list_parameters(model_type="part.part", model_id=self.part.pk)
        ids = [p["pk"] for p in listed["results"]]
        self.assertIn(self.parameter.pk, ids)

        detail = await get_parameter(self.parameter.pk)
        self.assertEqual(detail["pk"], self.parameter.pk)

        templates = await list_parameter_templates()
        self.assertTrue(templates["results"])

    async def test_authorized_user_can_list_and_get_companies(self):
        self._as(self.user)

        listed = await list_companies(is_supplier=True)
        names = [c["name"] for c in listed["results"]]
        self.assertIn("Test Supplier", names)

        detail = await get_company(self.supplier.pk)
        self.assertEqual(detail["name"], "Test Supplier")

    async def test_authorized_user_can_list_and_get_contacts(self):
        self._as(self.user)

        listed = await list_contacts(company=self.supplier.pk)
        ids = [c["pk"] for c in listed["results"]]
        self.assertIn(self.contact.pk, ids)

        detail = await get_contact(self.contact.pk)
        self.assertEqual(detail["name"], "Test Contact")

    async def test_authorized_user_can_list_and_get_addresses(self):
        self._as(self.user)

        listed = await list_addresses(company=self.supplier.pk)
        ids = [a["pk"] for a in listed["results"]]
        self.assertIn(self.address.pk, ids)

        detail = await get_address(self.address.pk)
        self.assertEqual(detail["title"], "Test Address")

    async def test_authorized_user_can_list_and_get_manufacturer_parts(self):
        self._as(self.user)

        listed = await list_manufacturer_parts(part=self.part.pk)
        ids = [mp["pk"] for mp in listed["results"]]
        self.assertIn(self.manufacturer_part.pk, ids)

        detail = await get_manufacturer_part(self.manufacturer_part.pk)
        self.assertEqual(detail["MPN"], "MCP-TEST-MPN")

    async def test_authorized_user_can_list_and_get_supplier_parts(self):
        self._as(self.user)

        listed = await list_supplier_parts(supplier=self.supplier.pk)
        ids = [sp["pk"] for sp in listed["results"]]
        self.assertIn(self.supplier_part.pk, ids)

        detail = await get_supplier_part(self.supplier_part.pk)
        self.assertEqual(detail["SKU"], "MCP-TEST-SKU")

    async def test_unauthorized_user_cannot_access_company_catalog_data(self):
        self._as(self.no_access_user)

        with self.assertRaises(ToolError):
            await list_companies()
        with self.assertRaises(ToolError):
            await get_company(self.supplier.pk)
        with self.assertRaises(ToolError):
            await list_contacts()
        with self.assertRaises(ToolError):
            await get_contact(self.contact.pk)
        with self.assertRaises(ToolError):
            await list_addresses()
        with self.assertRaises(ToolError):
            await get_address(self.address.pk)
        with self.assertRaises(ToolError):
            await list_manufacturer_parts()
        with self.assertRaises(ToolError):
            await get_manufacturer_part(self.manufacturer_part.pk)
        with self.assertRaises(ToolError):
            await list_supplier_parts()
        with self.assertRaises(ToolError):
            await get_supplier_part(self.supplier_part.pk)

    async def test_authorized_user_can_list_and_get_return_orders(self):
        self._as(self.user)

        listed = await list_return_orders(customer=self.customer.pk)
        refs = [o["reference"] for o in listed["results"]]
        self.assertIn("RMA-MCP-0001", refs)

        detail = await get_return_order(self.return_order.pk)
        self.assertEqual(detail["reference"], "RMA-MCP-0001")

    async def test_authorized_user_can_list_and_get_return_order_lines(self):
        self._as(self.user)

        listed = await list_return_order_lines(order=self.return_order.pk)
        ids = [line["pk"] for line in listed["results"]]
        self.assertIn(self.ro_line.pk, ids)

        detail = await get_return_order_line(self.ro_line.pk)
        self.assertEqual(detail["order"], self.return_order.pk)

    async def test_unauthorized_user_cannot_access_return_order_data(self):
        self._as(self.no_access_user)

        with self.assertRaises(ToolError):
            await list_return_orders()
        with self.assertRaises(ToolError):
            await get_return_order(self.return_order.pk)
        with self.assertRaises(ToolError):
            await list_return_order_lines()
        with self.assertRaises(ToolError):
            await get_return_order_line(self.ro_line.pk)

    async def test_authorized_user_can_list_and_get_stock_tracking(self):
        self._as(self.user)

        listed = await list_stock_tracking(item=self.stock_item.pk)
        ids = [entry["pk"] for entry in listed["results"]]
        self.assertIn(self.stock_tracking.pk, ids)

        detail = await get_stock_tracking(self.stock_tracking.pk)
        self.assertEqual(detail["item"], self.stock_item.pk)

    async def test_authorized_user_can_list_and_get_stock_test_results(self):
        self._as(self.user)

        listed = await list_stock_test_results(stock_item=self.stock_item.pk)
        ids = [r["pk"] for r in listed["results"]]
        self.assertIn(self.stock_test_result.pk, ids)

        detail = await get_stock_test_result(self.stock_test_result.pk)
        self.assertEqual(detail["template"], self.test_template.pk)

    async def test_unauthorized_user_cannot_access_stock_history_data(self):
        """StockItemTracking/StockItemTestResult are both mapped to the 'stock'
        ruleset (users/ruleset.py) - a zero-role user must be denied here the
        same as list_stock_items.
        """
        self._as(self.no_access_user)

        with self.assertRaises(ToolError):
            await list_stock_tracking()
        with self.assertRaises(ToolError):
            await get_stock_tracking(self.stock_tracking.pk)
        with self.assertRaises(ToolError):
            await list_stock_test_results()
        with self.assertRaises(ToolError):
            await get_stock_test_result(self.stock_test_result.pk)

    async def test_authorized_user_can_list_and_get_project_codes(self):
        self._as(self.user)

        listed = await list_project_codes(search="MCP-PROJ")
        codes = [c["code"] for c in listed["results"]]
        self.assertIn("MCP-PROJ", codes)

        detail = await get_project_code(self.project_code.pk)
        self.assertEqual(detail["code"], "MCP-PROJ")

    async def test_unauthorized_user_can_still_read_project_codes(self):
        """Deliberately the opposite assertion from most other resources' denial tests.

        ProjectCodeList/Detail use IsStaffOrReadOnlyScope (see
        tools/project_codes.py's module docstring) - any authenticated user
        can read, not just staff or a specific-role holder. Asserting
        ToolError here (the pattern used everywhere else) would mask a real
        regression if this view's permissions ever tightened.
        """
        self._as(self.no_access_user)

        listed = await list_project_codes(search="MCP-PROJ")
        self.assertIn("MCP-PROJ", [c["code"] for c in listed["results"]])

        detail = await get_project_code(self.project_code.pk)
        self.assertEqual(detail["pk"], self.project_code.pk)

    async def test_tools_list_reflects_oauth2_scope_narrowing(self):
        """Regression test for a real design bug caught during development, not a
        hypothetical: a literal HTTP OPTIONS-based capability check (the obvious
        first idea for this feature) would have shown list_parts as available to
        *any* OAuth2 token, because InvenTree's map_scope()
        (InvenTree/permissions.py) hardcodes OPTIONS to a generic "g:read" scope
        for every resource, while the real GET call requires the resource-specific
        scope - here, "r:view:part" (and, per a pre-existing scope-combination
        quirk, "r:view:build" too - see cls.oauth2_token_with_part_scope's own
        comment). tool_visibility.py checks "GET" instead specifically to avoid
        this - see proxy.user_has_access()'s docstring for the full comparison.
        """
        context.set_current_user(
            self.user, oauth2_token=self.oauth2_token_without_part_scope
        )
        self.addCleanup(context.set_current_user, None)

        names = await tool_visibility.visible_tool_names(
            tool.name for tool in await mcp.list_tools()
        )
        self.assertNotIn("list_parts", names)

    async def test_tools_list_shows_tool_when_oauth2_scope_matches(self):
        """Positive counterpart to the test above - a token that *does* carry
        the required scope must still see the tool, not just fail closed.
        """
        context.set_current_user(
            self.user, oauth2_token=self.oauth2_token_with_part_scope
        )
        self.addCleanup(context.set_current_user, None)

        names = await tool_visibility.visible_tool_names(
            tool.name for tool in await mcp.list_tools()
        )
        self.assertIn("list_parts", names)


class ToolVisibilityTest(InvenTreeTestCase):
    """Verify tools/list only advertises tools the current caller can actually use.

    This is discovery-time filtering, not a new permission boundary -
    proxy.call_view() remains the one real enforcement point (see
    tool_visibility.py's module docstring), and MCPToolPermissionTest above
    already covers that a tool hidden here still cleanly rejects a direct
    call by name.
    """

    roles: ClassVar[list[str]] = ["part.view"]

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.no_access_user = get_user_model().objects.create_user(
            username="tool_visibility_no_access",
            password="password",
            email="tool-visibility-no-access@example.org",
        )

    def _as(self, user):
        context.set_current_user(user)
        self.addCleanup(context.set_current_user, None)

    async def test_ungated_tools_are_always_visible_to_a_zero_role_user(self):
        """Exact-set assertion (not just "contains") - these are specifically the
        tools whose underlying views have no RolePermission/RuleSet gate at all
        (attachments, parameters, parameter templates, project codes - see each
        tool module's own docstring) plus describe_filters, which has no
        underlying view. Anything else appearing here would be a real
        over-exposure bug.
        """
        self._as(self.no_access_user)

        names = await tool_visibility.visible_tool_names(
            tool.name for tool in await mcp.list_tools()
        )

        self.assertEqual(
            names,
            {
                "describe_filters",
                "list_attachments",
                "get_attachment",
                "list_parameters",
                "get_parameter",
                "list_parameter_templates",
                "get_parameter_template",
                "list_project_codes",
                "get_project_code",
            },
        )

    async def test_role_holder_sees_the_matching_gated_tools(self):
        self._as(self.user)

        names = await tool_visibility.visible_tool_names(
            tool.name for tool in await mcp.list_tools()
        )

        self.assertIn("list_parts", names)
        self.assertIn("get_part", names)
        # A role this user does NOT hold must stay hidden.
        self.assertNotIn("list_purchase_orders", names)
        self.assertNotIn("list_stock_items", names)

    async def test_purchase_order_role_holder_sees_purchase_order_tools(self):
        """Spot-check a different role than the class-level part.view - filtering
        must reflect exactly which roles a user holds, not just "has some role".
        Grants the role directly onto cls.user's existing group rather than via
        a second user/group, since RuleSet permission checks are re-evaluated
        fresh on every visible_tool_names() call (no stale-cache risk - see
        InvenTree.cache.get_session_cache()'s per-request-thread scoping, not
        persistent across calls).
        """
        await sync_to_async(self.assignRole)(
            role="purchase_order.view", group=self.group
        )
        self._as(self.user)

        names = await tool_visibility.visible_tool_names(
            tool.name for tool in await mcp.list_tools()
        )

        self.assertIn("list_purchase_orders", names)
        self.assertIn("get_purchase_order", names)
        self.assertIn("list_purchase_order_lines", names)
        self.assertIn("get_purchase_order_line", names)
        # Still no access to a role this user was never granted.
        self.assertNotIn("list_stock_items", names)

    async def test_no_bound_user_returns_every_tool_unfiltered(self):
        """Static introspection outside a real MCP request (e.g. every other test
        in this file that calls mcp.list_tools() directly without binding a
        user) must not be treated as "zero access" - there's no caller to filter
        *by*, so nothing is filtered.
        """
        all_names = {tool.name for tool in await mcp.list_tools()}

        names = await tool_visibility.visible_tool_names(all_names)

        self.assertEqual(names, all_names)

    async def test_unauthenticated_bound_identity_sees_only_tools_needing_no_auth(self):
        """A real request that resolved to an unauthenticated identity (e.g.
        REQUIRE_AUTH disabled and no credentials sent) is a real caller, not
        "no request" - unlike the unfiltered case above, it must be filtered
        down to only the tools with no underlying view at all
        (describe_filters). Even the tools that need no specific *role*
        (list_attachments, list_parameters, list_project_codes, ...) still
        require *some* authenticated user via call_view(), so an
        unauthenticated identity must not see them either - regression test
        for context.has_bound_identity() vs has_current_user().
        """
        context.set_current_user(AnonymousUser())
        self.addCleanup(context.set_current_user, None)

        names = await tool_visibility.visible_tool_names(
            tool.name for tool in await mcp.list_tools()
        )

        self.assertEqual(names, {"describe_filters"})

    async def test_unavailable_resource_hides_its_tools_without_crashing(self):
        """A resource whose loader can't resolve its view class (e.g. a
        version mismatch between this plugin and the running InvenTree core
        - see view_resolution.resolve_view()) must be treated as "not
        visible", not raise and take down the whole tools/list response for
        every tool, gated or not.
        """
        self._as(self.user)

        with patch.dict(RESOURCE_LOADERS, {"part": lambda: None}):
            names = await tool_visibility.visible_tool_names(
                tool.name for tool in await mcp.list_tools()
            )

        self.assertNotIn("list_parts", names)
        self.assertNotIn("get_part", names)
        # An ungated tool (no underlying view to fail) must be unaffected.
        self.assertIn("describe_filters", names)

    async def test_every_gated_tool_has_a_visibility_entry(self):
        """Guard against a future tool shipping without a _TOOL_RESOURCES entry.

        An omission wouldn't break anything today - visible_tool_names()
        treats an unmapped name as always-visible (see its docstring for why
        that's the safe default, not fail-closed) - but it would silently
        defeat filtering for that one tool, and only a targeted check like
        this one would catch it before a user did.
        """
        all_names = {tool.name for tool in await mcp.list_tools()}
        unmapped = (
            all_names - set(tool_visibility._TOOL_RESOURCES) - {"describe_filters"}
        )

        self.assertEqual(unmapped, set())


class ViewResolutionTest(unittest.TestCase):
    """resolve_view() must tolerate a missing/renamed view class - the case a
    mismatched InvenTree core version (relative to this plugin) produces -
    instead of letting ImportError/AttributeError escape, and must not
    re-attempt (or re-log) an import it already knows fails.
    """

    def setUp(self):
        super().setUp()
        # _unresolved is a module-level cache that deliberately persists for
        # the life of the process (see resolve_view's docstring) - tests
        # must isolate their own entries from it explicitly rather than
        # relying on it emptying itself between tests.
        before = set(view_resolution._unresolved)
        self.addCleanup(view_resolution._unresolved.clear)
        self.addCleanup(view_resolution._unresolved.update, before)

    def test_resolves_a_real_view_class(self):
        self.assertIs(view_resolution.resolve_view("part.api", "PartList"), PartList)

    def test_returns_none_for_a_class_that_does_not_exist(self):
        self.assertIsNone(view_resolution.resolve_view("part.api", "NotARealViewClass"))

    def test_returns_none_for_a_module_that_does_not_exist(self):
        self.assertIsNone(view_resolution.resolve_view("not.a.real.module", "Whatever"))

    def test_caches_a_failure_without_re_importing(self):
        with patch(
            "inventree_mcp.view_resolution.import_module",
            side_effect=ImportError("boom"),
        ) as mock_import:
            self.assertIsNone(view_resolution.resolve_view("some.fake.module", "X"))
            self.assertIsNone(view_resolution.resolve_view("some.fake.module", "X"))

        mock_import.assert_called_once()


class ResolveViewAnyTest(unittest.TestCase):
    """resolve_view_any() must fall back across a renamed-between-core-versions class.

    Covers the PurchaseOrderList/PurchaseOrderDetail ('stable') vs
    PurchaseOrderViewSet ('master', core PR #12317) case - tools/*.py can't
    hardcode either name alone without breaking on the other core version.
    """

    def setUp(self):
        super().setUp()
        # Same isolation reasoning as ViewResolutionTest.setUp - this cache
        # persists for the life of the process (see resolve_view_any's
        # docstring), so tests must not leak entries into each other.
        before = set(view_resolution._unresolved_any)
        self.addCleanup(view_resolution._unresolved_any.clear)
        self.addCleanup(view_resolution._unresolved_any.update, before)

    def test_resolves_the_first_candidate_that_exists(self):
        self.assertIs(
            view_resolution.resolve_view_any(
                "part.api", ["PartList", "NotARealViewClass"]
            ),
            PartList,
        )

    def test_falls_back_to_a_later_candidate(self):
        self.assertIs(
            view_resolution.resolve_view_any(
                "part.api", ["NotARealViewClass", "PartList"]
            ),
            PartList,
        )

    def test_returns_none_when_no_candidate_exists(self):
        self.assertIsNone(
            view_resolution.resolve_view_any(
                "part.api", ["NotARealViewClass", "AlsoNotReal"]
            )
        )

    def test_returns_none_for_a_module_that_does_not_exist(self):
        self.assertIsNone(
            view_resolution.resolve_view_any("not.a.real.module", ["Whatever"])
        )

    def test_caches_a_total_failure_without_re_importing(self):
        with patch(
            "inventree_mcp.view_resolution.import_module",
            side_effect=ImportError("boom"),
        ) as mock_import:
            self.assertIsNone(
                view_resolution.resolve_view_any("some.fake.module", ["X", "Y"])
            )
            self.assertIsNone(
                view_resolution.resolve_view_any("some.fake.module", ["X", "Y"])
            )

        mock_import.assert_called_once()


class CallViewImportSafetyTest(InvenTreeTestCase):
    """call_view(None, ...) must raise a clean ToolError, not an AttributeError.

    Exercises the case every tools/*.py call site hits when
    view_resolution.resolve_view() couldn't import a tool's underlying view
    class - proxy.call_view() is the one place that's turned into the same
    ToolError contract every other call_view() failure already has, rather
    than requiring each of the ~50 call sites to check for None themselves.
    """

    async def test_call_view_rejects_a_missing_view_class(self):
        with self.assertRaises(ToolError) as cm:
            await call_view(None, "GET", "/api/part/")

        self.assertIn("unavailable", str(cm.exception).lower())


class OutputSchemaTest(InvenTreeTestCase):
    """Verify tool output schemas are derived from the real serializers, not left blank.

    Without output_schemas.apply(), MCPServer can't derive a schema from our
    tools' `-> dict` return annotation and reports outputSchema: null - see
    schema_introspection.py / output_schemas.py for why and how.
    """

    roles: ClassVar[list[str]] = ["part.view"]

    def test_serializer_schema_maps_common_field_types(self):
        schema = serializer_schema(PartSerializer)
        props = schema["properties"]

        # Every concrete type is nullable, even when DRF's allow_null says
        # False - see schema_introspection.py's _field_schema() for why
        # allow_null can't be trusted for this (it governs input validation,
        # not what to_representation() can actually emit).
        self.assertEqual(props["active"]["type"], ["boolean", "null"])
        self.assertEqual(props["description"]["type"], ["string", "null"])
        self.assertEqual(props["category"]["type"], ["integer", "null"])

    def test_choice_field_with_integer_choices_allows_non_string_json_types(self):
        """Regression test for a real bug found via a live sweep of every list/get tool.

        `_field_schema()` used to map every ChoiceField to `{"type": "string"}`
        unconditionally, but a ChoiceField's actual runtime JSON type depends
        on its *choices*, not its field class - InvenTree has plenty of
        integer-keyed choice fields (custom status codes via
        generic.states.fields.CustomChoiceField, a ChoiceField subclass) that
        serialize as JSON numbers. `BuildSerializer.status_custom_key` broke
        `jsonschema.validate()` live ("20 is not of type 'string', 'null'")
        under the old mapping.
        """
        schema = serializer_schema(BuildSerializer)
        self.assertIn("integer", schema["properties"]["status_custom_key"]["type"])

    def test_decimal_field_allows_string_or_number(self):
        """Regression test for a real bug found via the same live sweep as above.

        DRF's plain `DecimalField` defaults to `coerce_to_string=True`
        (nothing in this codebase overrides the global
        `COERCE_DECIMAL_TO_STRING` setting) and serializes as a JSON string -
        but `InvenTree.serializers.InvenTreeMoneySerializer` (used for every
        money field) is *also* a `DecimalField` subclass that explicitly
        overrides `to_representation()` to return a float instead.
        `isinstance()` can't tell the two apart, so the schema has to accept
        both. `PartBriefSerializer.minimum_stock` (auto-generated, unlike
        `PartSerializer`'s own explicit `FloatField` override of the same
        name) broke `jsonschema.validate()` live ("'0.000000' is not of type
        'number', 'null'") under the old number-only mapping.
        """
        schema = serializer_schema(PartBriefSerializer)
        field_type = schema["properties"]["minimum_stock"]["type"]
        self.assertIn("string", field_type)
        self.assertIn("number", field_type)

    def test_nested_serializer_field_is_nullable(self):
        """Regression test for a real bug found via the same live sweep as above.

        Nested single-object serializer fields (e.g. `*_detail`) are
        commonly `None` in real responses - either the underlying FK is
        null, or (for OptionalField-based ones) the detail wasn't requested
        via an output option - but `_field_schema()`'s `BaseSerializer`
        branch didn't apply the same unconditional-nullable treatment as
        plain fields. `ProjectCodeSerializer.responsible_detail` (`None`
        whenever `responsible` isn't set) broke `jsonschema.validate()` live
        ("None is not of type 'object'") under the old mapping.
        """
        schema = serializer_schema(ProjectCodeSerializer)
        self.assertIn("null", schema["properties"]["responsible_detail"]["type"])

    def test_paginated_schema_wraps_results_array(self):
        schema = paginated_schema(PartSerializer)

        self.assertEqual(schema["properties"]["results"]["type"], "array")
        self.assertEqual(
            schema["properties"]["results"]["items"]["title"], "PartSerializer"
        )
        self.assertEqual(schema["required"], ["count", "next", "previous", "results"])

    async def test_registered_tools_report_output_schemas(self):
        """The live tool registry (as a real MCP client would see via tools/list) must have these attached."""
        tools = {tool.name: tool for tool in await mcp.list_tools()}

        self.assertIsNotNone(tools["list_parts"].output_schema)
        self.assertIn("results", tools["list_parts"].output_schema["properties"])

        self.assertIsNotNone(tools["get_part"].output_schema)
        self.assertIn("name", tools["get_part"].output_schema["properties"])

        self.assertIsNotNone(tools["list_purchase_orders"].output_schema)
        self.assertIsNotNone(tools["get_build_item"].output_schema)
        self.assertIsNotNone(tools["list_companies"].output_schema)
        self.assertIsNotNone(tools["get_supplier_part"].output_schema)
        self.assertIsNotNone(tools["list_return_orders"].output_schema)
        self.assertIsNotNone(tools["get_stock_tracking"].output_schema)
        self.assertIsNotNone(tools["list_project_codes"].output_schema)

    async def test_every_registered_tool_has_an_output_schema(self):
        """Guard against a new tool being added without a matching entry in output_schemas.py."""
        tools = await mcp.list_tools()

        missing = [
            tool.name
            for tool in tools
            if tool.name != "describe_filters" and tool.output_schema is None
        ]
        self.assertEqual(missing, [])

    async def test_call_tool_still_returns_real_data(self):
        """Regression test: declaring output_schema without a matching output_model breaks every real call.

        mcp.list_tools() (tested above) only exercises tool *listing* -
        MCPServer separately validates every actual result against
        fn_metadata.output_model when a tool is *called*
        (func_metadata.py's convert_result()), and raises
        "Output model must be set if output schema is defined" if a schema
        was attached with no model. Must go through mcp.call_tool() (not
        call the Python function directly) to exercise that path.
        """
        context.set_current_user(self.user)
        self.addCleanup(context.set_current_user, None)

        result = await mcp.call_tool("list_parts", {"limit": 1})

        # call_tool() returns a CallToolResult once an output_model is
        # attached (mcp 1.x returned a bare (content_blocks,
        # structured_content) tuple instead).
        self.assertIsInstance(result, CallToolResult)
        self.assertFalse(result.is_error)
        self.assertIn("results", result.structured_content)

    async def test_output_schema_accepts_real_nullable_field_values(self):
        """Regression test for a real bug found via a live trial run, not a hypothetical.

        `mcp.call_tool()` (used above) only exercises MCPServer's *permissive*
        output_model validation (func_metadata.py's convert_result()). mcp 1.x
        additionally ran a second, strict validation for every real request
        over HTTP - `jsonschema.validate(instance=structured_content,
        schema=tool.outputSchema)` inside mcp.server.lowlevel.server.py's
        call_tool handler - but mcp 2.0's rewrite dropped that path entirely
        (verified against the installed `mcp` package: a deliberately
        schema-violating structured result now comes back as
        `is_error=False`, not rejected). This test therefore validates our
        *declared* schema by hand instead, via jsonschema.validate() below -
        it's the only thing left enforcing that outputSchema actually
        describes what tools return. DRF's `allow_null` (what
        schema_introspection.py used to key off of) does NOT reliably predict
        what to_representation() can emit: PartSerializer declares IPN with
        allow_null=False, but Part.IPN is a nullable CharField - a real part
        with IPN=None broke every list_parts call with more than one page of
        real data ("None is not of type 'string'"), reproduced live against
        the running dev server, not just in a unit test. See
        schema_introspection.py's _field_schema().
        """
        context.set_current_user(self.user)
        self.addCleanup(context.set_current_user, None)

        part_with_null_ipn = await sync_to_async(Part.objects.create)(
            name="Null IPN Part",
            description="Regression fixture for null IPN",
            IPN=None,
        )

        tool = mcp._tool_manager.get_tool("get_part")
        result = await get_part(part_with_null_ipn.pk)

        self.assertIsNone(result["IPN"])
        jsonschema.validate(instance=result, schema=tool.output_schema)

    def test_format_constrained_fields_allow_blank_string(self):
        """`jsonschema.validate()` without a `format_checker` doesn't enforce
        `format` at all - some MCP clients do. DRF's `allow_blank` fields
        commonly emit `""` as their "not set" sentinel, and `""` never
        matches a format assertion like `uri`/`email`/`date`.
        """
        schema = serializer_schema(PartSerializer)
        checker = jsonschema.FormatChecker()

        for value in (None, "", "https://example.com/datasheet.pdf"):
            jsonschema.validate(
                instance=value,
                schema=schema["properties"]["link"],
                format_checker=checker,
            )

        # FileField/ImageField serialize as a server-relative media path, not
        # an absolute URI - "uri-reference" (not "uri") is required for this
        # to validate at all, blank or not.
        jsonschema.validate(
            instance="/media/part_images/0402.jpg",
            schema=schema["properties"]["image"],
            format_checker=checker,
        )

    def test_datetime_field_has_no_format_assertion(self):
        """InvenTree's `REST_FRAMEWORK['DATETIME_FORMAT']` setting overrides
        DRF's default ISO-8601 output to `"%Y-%m-%d %H:%M"` for every
        `DateTimeField` - a declared `"format": "date-time"` would reject
        every real value under a client that enforces the format keyword.
        """
        schema = serializer_schema(PartSerializer)
        pricing_updated_schema = schema["properties"]["pricing_updated"]

        self.assertNotIn("format", pricing_updated_schema)
        jsonschema.validate(
            instance="2026-07-06 13:24",
            schema=pricing_updated_schema,
            format_checker=jsonschema.FormatChecker(),
        )


class DescribeFiltersTest(InvenTreeTestCase):
    """Verify describe_filters() reflects the real, live FilterSet definitions.

    No DB access or bound user needed - this is pure metadata introspection,
    same as the schema_introspection tests above.
    """

    def test_describe_filters_reflects_real_filterset(self):
        result = describe_filters("part")

        self.assertIn("name", result["search_fields"])
        self.assertIn("is_variant", result["filters"])
        self.assertEqual(result["filters"]["is_variant"]["type"], "boolean")

    def test_describe_filters_covers_order_and_build_resources(self):
        for resource in (
            "purchase_order",
            "purchase_order_line",
            "sales_order",
            "sales_order_line",
            "sales_order_allocation",
            "build_order",
            "build_line",
            "build_item",
        ):
            result = describe_filters(resource)
            self.assertIn("filters", result)
            self.assertTrue(result["filters"])

        self.assertIn("outstanding", describe_filters("purchase_order")["filters"])
        self.assertIn("allocated", describe_filters("sales_order_line")["filters"])
        self.assertIn("build", describe_filters("build_line")["filters"])

    def test_describe_filters_covers_company_catalog_resources(self):
        for resource in (
            "company",
            "contact",
            "address",
            "manufacturer_part",
            "supplier_part",
        ):
            result = describe_filters(resource)
            self.assertTrue(result["filters"])

        self.assertIn("is_supplier", describe_filters("company")["filters"])
        self.assertIn("has_stock", describe_filters("supplier_part")["filters"])

    def test_describe_filters_covers_bom_resources(self):
        result = describe_filters("bom_item")
        self.assertIn("inherited", result["filters"])
        self.assertIn("uses", result["filters"])
        self.assertIn("allow_variants", result["filters"])

        self.assertEqual(
            describe_filters("bom_substitute")["filters"],
            {"part": {"type": "integer (id)"}, "bom_item": {"type": "integer (id)"}},
        )

    def test_describe_filters_covers_attachment_and_parameter_resources(self):
        attachment_filters = describe_filters("attachment")["filters"]
        self.assertIn("model_type", attachment_filters)
        self.assertIn("model_id", attachment_filters)
        self.assertIn("is_image", attachment_filters)

        parameter_filters = describe_filters("parameter")["filters"]
        self.assertIn("model_id", parameter_filters)
        self.assertIn("template", parameter_filters)

        template_filters = describe_filters("parameter_template")["filters"]
        self.assertIn("units", template_filters)
        self.assertIn("has_choices", template_filters)

    def test_describe_filters_covers_filterset_fields_shorthand(self):
        """Contact/AddressList use DRF's filterset_fields shorthand, not a full
        filterset_class - regression test for the model-field fallback in
        filter_introspection.py's _model_field_filters().
        """
        self.assertEqual(
            describe_filters("contact")["filters"],
            {"company": {"type": "integer (id)"}},
        )
        self.assertEqual(
            describe_filters("address")["filters"],
            {"company": {"type": "integer (id)"}},
        )

    def test_describe_filters_covers_return_order_resources(self):
        for resource in ("return_order", "return_order_line"):
            result = describe_filters(resource)
            self.assertTrue(result["filters"])

        self.assertIn("outstanding", describe_filters("return_order")["filters"])
        self.assertIn("received", describe_filters("return_order_line")["filters"])

    def test_describe_filters_covers_stock_history_resources(self):
        tracking_filters = describe_filters("stock_tracking")["filters"]
        self.assertIn("item", tracking_filters)
        self.assertIn("user", tracking_filters)

        result_filters = describe_filters("stock_test_result")["filters"]
        self.assertIn("template", result_filters)
        self.assertIn("result", result_filters)

    def test_describe_filters_covers_project_code(self):
        result = describe_filters("project_code")
        self.assertIn("code", result["search_fields"])

    def test_describe_filters_reports_optional_fields(self):
        """optional_fields must reflect the real `output_options` declared on
        the view, read live from InvenTree.fields.InvenTreeOutputOption -
        same "can't drift" guarantee as filters/search/ordering above.
        """
        optional_fields = describe_filters("stock")["optional_fields"]

        self.assertTrue(optional_fields["part_detail"]["default_included"])
        self.assertFalse(optional_fields["location_detail"]["default_included"])
        self.assertIn("supplier_part_detail", optional_fields)
        self.assertIn("tests", optional_fields)
        # Every option carries a human-readable description an agent can
        # show/reason about, not just the bare flag name.
        self.assertTrue(optional_fields["part_detail"]["description"])

    def test_describe_filters_optional_fields_empty_for_flat_resources(self):
        """Resources with no expandable relations (no `output_options`
        declared on the view) must report an empty dict, not raise.
        """
        for resource in (
            "company",
            "contact",
            "address",
            "bom_substitute",
            "attachment",
            "parameter",
            "parameter_template",
            "project_code",
        ):
            self.assertEqual(describe_filters(resource)["optional_fields"], {})

    def test_describe_filters_rejects_unknown_resource(self):
        with self.assertRaises(ToolError):
            describe_filters("not-a-real-resource")

    def test_describe_filters_rejects_unavailable_resource(self):
        """A known resource whose loader can't resolve its view class (e.g. a
        version mismatch between this plugin and the running InvenTree core
        - see view_resolution.resolve_view()) must raise a clean ToolError,
        not an AttributeError from treating None as a view class.
        """
        with (
            patch.dict(RESOURCE_LOADERS, {"part": lambda: None}),
            self.assertRaises(ToolError),
        ):
            describe_filters("part")

    def test_describe_filters_falls_back_to_default_ordering_fields(self):
        """BomItemSubstituteList doesn't declare `ordering_fields` at all - regression
        test for filter_introspection.py's _default_ordering_fields() fallback.

        Before this, describe_filterset() read `getattr(view_cls,
        'ordering_fields', None) or []`, which couldn't tell "not declared"
        apart from "declared empty" and reported zero ordering fields either
        way - even though `ordering=part` genuinely sorts the real API
        (DRF's OrderingFilter defaults to any readable serializer field when
        a view doesn't declare ordering_fields, it doesn't disable ordering).
        """
        ordering_fields = describe_filters("bom_substitute")["ordering_fields"]

        self.assertIn("part", ordering_fields)
        self.assertIn("bom_item", ordering_fields)
        # part_detail's own source is 'part' - must be deduped to a single
        # 'part' entry, not listed twice under two different field names.
        self.assertEqual(ordering_fields.count("part"), 1)

        # A view that *does* declare ordering_fields must be unaffected by
        # the fallback path.
        self.assertEqual(
            describe_filters("bom_item")["ordering_fields"][:1], ["can_build"]
        )


class DefaultOrderingFieldsTest(unittest.TestCase):
    """Direct unit tests for filter_introspection._default_ordering_fields()'s edge cases.

    Every real view wrapped by this plugin either declares `ordering_fields`
    explicitly or (BomItemSubstituteList, covered above via describe_filters)
    has a serializer that instantiates cleanly with no write_only/wildcard
    fields - so the defensive branches below (no serializer_class at all, a
    serializer that can't be bare-instantiated, write_only/source='*' fields
    to skip) aren't reachable through any currently-wrapped resource. Plain
    unittest.TestCase against synthetic fixtures, same pattern as
    ClampLimitTest/BuildQueryParamsTest below - no DB needed since this
    function only introspects a serializer class.
    """

    def test_view_without_serializer_class_returns_empty(self):
        class FakeView:
            pass

        self.assertEqual(_default_ordering_fields(FakeView), [])

    def test_serializer_that_cannot_be_bare_instantiated_fails_soft(self):
        class RequiresArgsSerializer:
            def __init__(self, *args, **kwargs):
                raise TypeError("this serializer needs constructor args")

        class FakeView:
            serializer_class = RequiresArgsSerializer

        self.assertEqual(_default_ordering_fields(FakeView), [])

    def test_serializer_assertion_error_fails_soft(self):
        class MisconfiguredSerializer:
            def __init__(self, *args, **kwargs):
                raise AssertionError("e.g. a ModelSerializer missing Meta.model")

        class FakeView:
            serializer_class = MisconfiguredSerializer

        self.assertEqual(_default_ordering_fields(FakeView), [])

    def test_write_only_and_wildcard_source_fields_are_excluded(self):
        class FakeField:
            def __init__(self, source, write_only=False):
                self.source = source
                self.write_only = write_only

        class FakeSerializer:
            def __init__(self):
                self.fields = {
                    "password": FakeField("password", write_only=True),
                    "computed": FakeField("*"),
                    "name": FakeField("name"),
                }

        class FakeView:
            serializer_class = FakeSerializer

        self.assertEqual(_default_ordering_fields(FakeView), ["name"])


@override_settings(PLUGIN_TESTING_SETUP=True)
class MCPTransportTest(InvenTreeTestCase):
    """HTTP-level regression tests for mcp_transport.py.

    Everything here was originally verified by hand (curl + the real mcp
    client SDK) while tracking down three separate bugs - see AGENTS.md's
    OAuth2 section (auth_exempt requirement, SessionAuthentication/CSRF
    interaction, request.body caching). None of that was a repeatable
    regression test until now - all the other tests in this file call tool
    functions directly and never touch MCPView.dispatch() at all.

    Must use Client(enforce_csrf_checks=True): Django's test Client disables
    CSRF checking entirely by default, which would silently mask the
    SessionAuthentication/CSRF bug this class specifically guards against
    (confirmed the hard way during development - the default Client made a
    since-fixed regression look like it was passing).
    """

    URL = "/plugin/inventree-mcp/mcp/"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.api_token = ApiToken.objects.create(
            user=cls.user, name="transport-test-token"
        ).key

        app, _ = Application.objects.get_or_create(
            name="mcp-transport-test-app",
            defaults={
                "client_type": "confidential",
                "authorization_grant_type": "client-credentials",
                "user": cls.user,
            },
        )
        cls.oauth2_token = AccessToken.objects.create(
            user=cls.user,
            application=app,
            token="mcp-transport-test-token",
            expires=timezone.now() + datetime.timedelta(hours=1),
            scope="g:read",
        ).token

        registry.reload_plugins(full_reload=True, collect=True)
        registry.set_plugin_state("inventree-mcp", True)

    @staticmethod
    def _initialize_body() -> str:
        return json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1"},
            },
        })

    def _post(self, client: Client | None = None, **headers) -> Any:
        client = client or Client(enforce_csrf_checks=True)
        return client.post(
            self.URL,
            data=self._initialize_body(),
            content_type="application/json",
            HTTP_ACCEPT="application/json, text/event-stream",
            **headers,
        )

    @staticmethod
    def _list_tools_body() -> str:
        return json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })

    def _list_tools(self, client: Client | None = None, **headers) -> Any:
        client = client or Client(enforce_csrf_checks=True)
        return client.post(
            self.URL,
            data=self._list_tools_body(),
            content_type="application/json",
            HTTP_ACCEPT="application/json, text/event-stream",
            **headers,
        )

    def test_unauthenticated_request_rejected_by_default(self):
        """REQUIRE_AUTH defaults to True - no credential must be cleanly rejected, not crash."""
        response = self._post()

        self.assertEqual(response.status_code, 401)
        self.assertIn("error", json.loads(response.content))

    def test_invalid_credential_rejected(self):
        """A malformed/unknown token must be rejected cleanly, not crash the view."""
        response = self._post(HTTP_AUTHORIZATION="Token not-a-real-token")

        self.assertEqual(response.status_code, 401)

    def test_session_only_auth_is_rejected(self):
        """SessionAuthentication is deliberately excluded (see AGENTS.md) - a logged-in
        browser session alone, with no Token/Basic/OAuth2 credential, must not
        authenticate this endpoint.
        """
        client = Client(enforce_csrf_checks=True)
        client.login(username=self.username, password=self.password)

        response = self._post(client=client)

        self.assertEqual(response.status_code, 401)

    def test_api_token_auth_succeeds(self):
        response = self._post(HTTP_AUTHORIZATION=f"Token {self.api_token}")

        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertEqual(body["result"]["serverInfo"]["name"], "InvenTree MCP")

    def test_oauth2_bearer_auth_succeeds(self):
        """Regression guard for three separate bugs at once - see class docstring.

        Any one of them regressing changes this specific outcome:
        - auth_exempt missing -> 401 before dispatch() ever runs.
        - SessionAuthentication not excluded -> 403 "CSRF Failed: CSRF cookie not set."
        - request.body not cached before initialize_request() -> 500 RawPostDataException.
        """
        response = self._post(HTTP_AUTHORIZATION=f"Bearer {self.oauth2_token}")

        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertEqual(body["result"]["serverInfo"]["name"], "InvenTree MCP")

    def test_hop_by_hop_response_headers_are_stripped(self):
        """Regression test for _HOP_BY_HOP_HEADERS in mcp_transport.py.

        The ASGI session manager can emit hop-by-hop headers (RFC 7230 6.1) -
        e.g. "Connection" - as part of its response, which WSGI servers like
        the stdlib `runserver` reject outright since only the server itself
        is allowed to control connection handling. Patches
        StreamableHTTPSessionManager.handle_request directly (rather than
        relying on the real MCP protocol to happen to emit one) so this
        doesn't depend on the session manager's internals continuing to
        produce a hop-by-hop header on some particular request shape.
        """

        async def fake_handle_request(self, scope, receive, send):
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"connection", b"keep-alive"),
                ],
            })
            await send({
                "type": "http.response.body",
                "body": b'{"jsonrpc": "2.0", "id": 1, "result": {}}',
            })

        with patch(
            "mcp.server.streamable_http_manager.StreamableHTTPSessionManager"
            ".handle_request",
            new=fake_handle_request,
        ):
            response = self._post(HTTP_AUTHORIZATION=f"Token {self.api_token}")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Connection", response)
        self.assertEqual(response["Content-Type"], "application/json")

    def test_tools_list_over_http_is_filtered_by_permission(self):
        """End-to-end regression test for tool_visibility.apply()'s wiring into
        the real low-level server.

        ToolVisibilityTest (elsewhere in this file) already covers
        visible_tool_names() directly, but that alone doesn't prove the
        override actually reaches a real client's tools/list request over the
        wire, rather than only affecting some MCPServer-level helper a real
        request never touches - exactly the "tested via mcp.call_tool() isn't
        tested for real" distinction this codebase has been bitten by before
        (see AGENTS.md's Output schemas section).

        cls.user has zero roles here (MCPTransportTest.roles is never set -
        see UserMixin's default), so only the ungated tools should appear.
        """
        response = self._list_tools(HTTP_AUTHORIZATION=f"Token {self.api_token}")

        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        names = {tool["name"] for tool in body["result"]["tools"]}

        self.assertIn("describe_filters", names)
        self.assertIn("list_project_codes", names)
        self.assertNotIn("list_parts", names)
        self.assertNotIn("list_purchase_orders", names)


@override_settings(PLUGIN_TESTING_SETUP=True)
class ToolLoggingTest(InvenTreeTestCase):
    """Verify MCP_LOG_TOOL_CALLS gates tool_logging.apply()'s real HTTP-level hook.

    Same "must prove it reaches the real low-level handler, not just some
    MCPServer-level helper a real request never touches" concern as
    MCPTransportTest.test_tools_list_over_http_is_filtered_by_permission
    above - tool_logging.apply() re-registers the low-level CallToolRequest
    handler, which mcp.call_tool() (used by every other tool-calling test in
    this file, e.g. OutputSchemaTest) deliberately bypasses - see
    tool_logging.py's module docstring for why.
    """

    URL = "/plugin/inventree-mcp/mcp/"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.api_token = ApiToken.objects.create(
            user=cls.user, name="tool-logging-test-token"
        ).key

        registry.reload_plugins(full_reload=True, collect=True)
        registry.set_plugin_state("inventree-mcp", True)

    @staticmethod
    def _call_tool_body(name: str, arguments: dict[str, Any] | None = None) -> str:
        return json.dumps({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })

    def _call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        return Client(enforce_csrf_checks=True).post(
            self.URL,
            data=self._call_tool_body(name, arguments),
            content_type="application/json",
            HTTP_ACCEPT="application/json, text/event-stream",
            HTTP_AUTHORIZATION=f"Token {self.api_token}",
        )

    def _enable_logging(self) -> None:
        plugin = registry.get_plugin("inventree-mcp")
        plugin.set_setting("MCP_LOG_TOOL_CALLS", True)
        self.addCleanup(plugin.set_setting, "MCP_LOG_TOOL_CALLS", False)

    def test_tool_call_is_not_logged_by_default(self):
        """MCP_LOG_TOOL_CALLS defaults to False - logging must be fully opt-in."""
        with patch("inventree_mcp.tool_logging.logger") as mock_logger:
            response = self._call_tool("list_project_codes")

        self.assertEqual(response.status_code, 200)
        mock_logger.info.assert_not_called()

    def test_successful_tool_call_is_logged_when_enabled(self):
        self._enable_logging()

        with patch("inventree_mcp.tool_logging.logger") as mock_logger:
            response = self._call_tool("list_project_codes", {"limit": 1})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_logger.info.call_count, 2)

        start_args = mock_logger.info.call_args_list[0].args
        self.assertIn("list_project_codes", start_args)
        self.assertIn(self.username, start_args)

        end_args = mock_logger.info.call_args_list[1].args
        self.assertIn("succeeded", end_args[0])
        self.assertIn("list_project_codes", end_args)
        self.assertIn(self.username, end_args)

    def test_failed_tool_call_is_logged_when_enabled(self):
        """A ToolError (e.g. not found) must still be logged, not swallowed silently."""
        self._enable_logging()

        with patch("inventree_mcp.tool_logging.logger") as mock_logger:
            response = self._call_tool("get_project_code", {"project_code_id": 999_999})

        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertTrue(body["result"]["isError"])

        # Only the start line goes through .info - the completion line for a
        # failed call is logged at .warning instead (tool_logging.py's
        # `except` branch), a deliberate severity distinction from the
        # .info-only "succeeded" case above, so it can be filtered/alerted on
        # separately in real logs.
        self.assertEqual(mock_logger.info.call_count, 1)
        self.assertEqual(mock_logger.warning.call_count, 1)

        end_args = mock_logger.warning.call_args_list[0].args
        self.assertIn("failed", end_args[0])
        self.assertIn("get_project_code", end_args)


class PluginSettingsTest(InvenTreeTestCase):
    """Verify get_plugin_setting() fails safe - both REQUIRE_AUTH and MCP_READ_ONLY
    depend on this for their fail-*closed* (i.e. restrictive) behaviour, so "can't
    resolve the setting" must never quietly mean "unrestricted".
    """

    def test_returns_default_when_plugin_unresolvable(self):
        with patch("inventree_mcp.settings._get_plugin_instance", return_value=None):
            self.assertTrue(get_plugin_setting("REQUIRE_AUTH"))
            self.assertTrue(get_plugin_setting("MCP_READ_ONLY"))
            # The default parameter itself must be respected, not hardcoded True.
            self.assertFalse(get_plugin_setting("REQUIRE_AUTH", default=False))

    def test_returns_default_when_setting_lookup_raises(self):
        class _BrokenPlugin:
            def get_setting(self, key):
                raise RuntimeError("simulated failure")

        with patch(
            "inventree_mcp.settings._get_plugin_instance",
            return_value=_BrokenPlugin(),
        ):
            self.assertTrue(get_plugin_setting("REQUIRE_AUTH"))

    def test_returns_real_value_when_plugin_resolvable(self):
        with patch("inventree_mcp.settings._get_plugin_instance") as mock_get:
            mock_get.return_value.get_setting.return_value = False
            self.assertFalse(get_plugin_setting("MCP_READ_ONLY"))


class ClampLimitTest(unittest.TestCase):
    """Direct unit tests for the pagination cap every list tool relies on.

    Exercised indirectly elsewhere (e.g. test_filters_cannot_bypass_limit_clamp
    above), but the boundary values themselves weren't asserted directly.
    """

    def test_non_positive_values_fall_back_to_default(self):
        self.assertEqual(clamp_limit(0), DEFAULT_LIMIT)
        self.assertEqual(clamp_limit(-5), DEFAULT_LIMIT)

    def test_values_within_range_pass_through_unchanged(self):
        self.assertEqual(clamp_limit(1), 1)
        self.assertEqual(clamp_limit(50), 50)
        self.assertEqual(clamp_limit(MAX_LIMIT), MAX_LIMIT)

    def test_values_above_max_are_capped(self):
        self.assertEqual(clamp_limit(MAX_LIMIT + 1), MAX_LIMIT)
        self.assertEqual(clamp_limit(10_000), MAX_LIMIT)


class BuildQueryParamsTest(unittest.TestCase):
    """Direct unit tests for the filters/limit/offset merge order.

    test_filters_cannot_bypass_limit_clamp (above) already covers this via a
    real tool call; this asserts the merge order itself in isolation.
    """

    def test_filters_merge_over_base(self):
        params = build_query_params(
            {"search": "x"}, {"active": True}, limit=10, offset=0
        )

        self.assertEqual(params["search"], "x")
        self.assertTrue(params["active"])

    def test_filters_cannot_override_pagination(self):
        params = build_query_params(
            {}, {"limit": 99_999, "offset": 500}, limit=10, offset=0
        )

        self.assertEqual(params["limit"], 10)
        self.assertEqual(params["offset"], 0)

    def test_none_filters_is_safe(self):
        params = build_query_params({"a": 1}, None, limit=5, offset=0)

        self.assertEqual(params, {"a": 1, "limit": 5, "offset": 0})
