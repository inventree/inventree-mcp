"""Tests for the InvenTreeMCP plugin.

These focus on the one thing the community reference MCP plugin gets wrong:
permission enforcement. Every tool proxies through the real DRF view classes
(see proxy.py), so a user without the relevant role must be rejected the same
way the normal REST API would reject them - holding a valid, authenticated
credential must not be enough on its own.
"""

from __future__ import annotations

import datetime
from typing import ClassVar

from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from InvenTree.unit_test import InvenTreeTestCase
from mcp.server.fastmcp.exceptions import ToolError
from oauth2_provider.models import AccessToken, Application
from part.api import PartList
from part.models import Part, PartCategory
from part.serializers import PartSerializer
from plugin import registry

from . import context
from .mcp_server import mcp
from .proxy import call_view
from .schema_introspection import paginated_schema, serializer_schema
from .tools.categories import list_categories
from .tools.parts import get_part, list_parts
from .tools.stock import list_stock_items


@override_settings(PLUGIN_TESTING_SETUP=True)
class MCPToolPermissionTest(InvenTreeTestCase):
    """Verify MCP tools respect InvenTree's role-based permissions."""

    roles: ClassVar[list[str]] = [
        "part.view",
        "part_category.view",
        "stock.view",
        "stock_location.view",
    ]

    @classmethod
    def setUpTestData(cls):
        """Create shared fixture data and a second, permission-less user."""
        super().setUpTestData()

        cls.category = PartCategory.objects.create(
            name="Widgets", description="Test category"
        )
        cls.part = Part.objects.create(
            name="Test Part", description="A part for MCP tests", category=cls.category
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

    async def test_no_bound_user_fails_closed(self):
        """Calling a tool with no bound user (e.g. outside of a real MCP request) must fail."""
        with self.assertRaises(PermissionError):
            await list_parts()

    async def test_limit_is_clamped(self):
        """A caller-supplied limit above the cap must not be passed straight through."""
        self._as(self.user)
        result = await list_parts(limit=10_000)
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


class OutputSchemaTest(InvenTreeTestCase):
    """Verify tool output schemas are derived from the real serializers, not left blank.

    Without output_schemas.apply(), FastMCP can't derive a schema from our
    tools' `-> dict` return annotation and reports outputSchema: null - see
    schema_introspection.py / output_schemas.py for why and how.
    """

    roles: ClassVar[list[str]] = ["part.view"]

    def test_serializer_schema_maps_common_field_types(self):
        schema = serializer_schema(PartSerializer)
        props = schema["properties"]

        self.assertEqual(props["active"]["type"], "boolean")
        self.assertEqual(props["description"]["type"], "string")
        # 'category' is a nullable FK -> integer-or-null, not just integer.
        self.assertEqual(props["category"]["type"], ["integer", "null"])

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

        self.assertIsNotNone(tools["list_parts"].outputSchema)
        self.assertIn("results", tools["list_parts"].outputSchema["properties"])

        self.assertIsNotNone(tools["get_part"].outputSchema)
        self.assertIn("name", tools["get_part"].outputSchema["properties"])

    async def test_call_tool_still_returns_real_data(self):
        """Regression test: declaring output_schema without a matching output_model breaks every real call.

        mcp.list_tools() (tested above) only exercises tool *listing* -
        FastMCP separately validates every actual result against
        fn_metadata.output_model when a tool is *called*
        (func_metadata.py's convert_result()), and raises
        "Output model must be set if output schema is defined" if a schema
        was attached with no model. Must go through mcp.call_tool() (not
        call the Python function directly) to exercise that path.
        """
        context.set_current_user(self.user)
        self.addCleanup(context.set_current_user, None)

        result = await mcp.call_tool("list_parts", {"limit": 1})

        # call_tool() returns (content_blocks, structured_content) once an
        # output_model is attached.
        self.assertIsInstance(result, tuple)
        _content, structured = result
        self.assertIn("results", structured)
