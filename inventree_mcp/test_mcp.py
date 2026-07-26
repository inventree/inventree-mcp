"""Tests for the InvenTreeMCP plugin.

These focus on the one thing the community reference MCP plugin gets wrong:
permission enforcement. Every tool proxies through the real DRF view classes
(see proxy.py), so a user without the relevant role must be rejected the same
way the normal REST API would reject them - holding a valid, authenticated
credential must not be enough on its own.
"""

from __future__ import annotations

from typing import ClassVar

from django.contrib.auth import get_user_model
from InvenTree.unit_test import InvenTreeTestCase
from mcp.server.fastmcp.exceptions import ToolError
from part.models import Part, PartCategory

from . import context
from .tools.categories import list_categories
from .tools.parts import get_part, list_parts
from .tools.stock import list_stock_items


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

    def _as(self, user):
        """Bind *user* as the acting MCP user for the duration of the current test."""
        token = context.set_current_user(user)
        self.addCleanup(context.reset_current_user, token)

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
