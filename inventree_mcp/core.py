"""MCP server for InvenTree"""

from typing import ClassVar

from plugin import InvenTreePlugin
from plugin.mixins import SettingsMixin, UrlsMixin

from . import PLUGIN_VERSION


class InvenTreeMCP(SettingsMixin, UrlsMixin, InvenTreePlugin):
    """InvenTreeMCP - custom InvenTree plugin."""

    # Plugin metadata
    TITLE = "InvenTree MCP"
    NAME = "InvenTreeMCP"
    SLUG = "inventree-mcp"
    DESCRIPTION = "MCP server for InvenTree"
    VERSION = PLUGIN_VERSION

    # Additional project information
    AUTHOR = "Oliver Walters"
    WEBSITE = "https://github.com/inventree/inventree-mcp"
    LICENSE = "MIT"

    # Optionally specify supported InvenTree versions
    # MIN_VERSION = '0.18.0'
    # MAX_VERSION = '2.0.0'

    # Plugin settings (from SettingsMixin)
    # Ref: https://docs.inventree.org/en/latest/plugins/mixins/settings/
    SETTINGS: ClassVar[dict] = {
        "REQUIRE_AUTH": {
            "name": "Require Authentication",
            "description": "Reject unauthenticated requests to the MCP endpoint. Disable only for local testing.",
            "validator": bool,
            "default": True,
        },
        "MCP_READ_ONLY": {
            "name": "Read Only",
            "description": "Block all write actions via the MCP endpoint, regardless of the calling user's permissions.",
            "validator": bool,
            "default": True,
        },
    }

    # Custom URL endpoints (from UrlsMixin)
    # Ref: https://docs.inventree.org/en/latest/plugins/mixins/urls/
    def setup_urls(self):
        """Configure custom URL endpoints for this plugin."""
        from .mcp_transport import urlpatterns

        return urlpatterns
