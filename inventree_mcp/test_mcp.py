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

from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.test import Client, override_settings
from django.utils import timezone
from InvenTree.unit_test import InvenTreeTestCase
from mcp.server.fastmcp.exceptions import ToolError
from oauth2_provider.models import AccessToken, Application
from part.api import PartList
from part.models import Part, PartCategory
from part.serializers import PartSerializer
from plugin import registry
from stock.models import StockItem, StockLocation
from users.models import ApiToken

from . import context
from .mcp_server import mcp
from .proxy import call_view
from .schema_introspection import paginated_schema, serializer_schema
from .settings import get_plugin_setting
from .tools._common import DEFAULT_LIMIT, MAX_LIMIT, build_query_params, clamp_limit
from .tools.categories import get_category, list_categories
from .tools.discovery import describe_filters
from .tools.locations import get_location, list_locations
from .tools.parts import get_part, list_parts
from .tools.stock import get_stock_item, list_stock_items


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
        cls.location = StockLocation.objects.create(
            name="Test Location", description="A location for MCP tests"
        )
        cls.stock_item = StockItem.objects.create(
            part=cls.part, quantity=10, location=cls.location
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

    def test_describe_filters_rejects_unknown_resource(self):
        with self.assertRaises(ToolError):
            describe_filters("not-a-real-resource")


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
